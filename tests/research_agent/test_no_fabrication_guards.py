"""
test_no_fabrication_guards.py — code-review hardening (Checkpoint 5B).

The deep-research loop must never let the LLM assert an unverified contact
fact, corrupt loop state, or record a noisy SERP hit as a researched value.
"""
import pytest

from backend.research_agent import agent as agent_mod
from backend.research_agent import evidence as evidence_mod
from backend.research_agent.models import AgentAction, ResearchLead
from backend.research_agent.validation import validate_no_fabrication

from ._fakes import ScriptedBrowser, ok

pytestmark = pytest.mark.asyncio


def _cfg(**o):
    base = {
        "research_agent_max_actions_per_lead": 8, "research_agent_max_searches_per_lead": 4,
        "research_agent_max_pages_per_lead": 5, "research_agent_max_time_per_lead_seconds": 180,
        "research_agent_max_total_leads": 20, "research_agent_max_consecutive_failures": 3,
        "research_agent_max_geographic_units": 5, "research_agent_page_timeout_ms": 20000,
        "research_agent_headless": True,
    }
    base.update({f"research_agent_{k}": v for k, v in o.items()})
    return base


# ── F1: record_finding must not write loop counters / status fields ────────

async def test_record_finding_rejects_non_research_fields():
    lead = ResearchLead(business_name="Acme")
    evidence_mod.record_finding(lead, "pages_visited", "lots", source_type="website")
    evidence_mod.record_finding(lead, "research_status", "COMPLETE", source_type="website")
    evidence_mod.record_finding(lead, "confidence", 1.0, source_type="website")
    assert lead.pages_visited == 0            # unchanged int
    assert lead.research_status == "PENDING"  # unchanged
    assert not any(e.field_name in ("pages_visited", "research_status", "confidence")
                   for e in lead.evidence)


# ── F2: LLM save_evidence cannot assert a verified contact fact ────────────

async def test_save_evidence_cannot_fabricate_a_management_email(monkeypatch):
    async def emit_save_evidence(state_summary, cfg=None):
        return AgentAction(action="save_evidence", params={
            "field_name": "management_email", "value": "ceo@acme.com",
            "confidence": 1.0, "status": "FOUND",
        })
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", emit_save_evidence)

    browser = ScriptedBrowser(lambda a: ok(a.action))
    agent = agent_mod.ResearchAgent(browser, _cfg(max_actions_per_lead=3), "dental clinics")
    lead = await agent.research_business({"business_name": "Acme", "city": "LA"})

    # the guessed value was NOT set on the lead...
    assert lead.management_email is None
    # ...so there is nothing for validate_no_fabrication to flag, and no
    # FOUND evidence row was created from a bare LLM assertion.
    assert validate_no_fabrication(lead) == []
    assert not any(e.field_name == "management_email" and e.status == "FOUND"
                   for e in lead.evidence)


async def test_save_evidence_rejects_malformed_email(monkeypatch):
    async def emit(state_summary, cfg=None):
        return AgentAction(action="save_evidence", params={
            "field_name": "business_email", "value": "not-an-email@@bad", "status": "FOUND",
        })
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", emit)
    browser = ScriptedBrowser(lambda a: ok(a.action))
    agent = agent_mod.ResearchAgent(browser, _cfg(max_actions_per_lead=2), "dental clinics")
    lead = await agent.research_business({"business_name": "Acme"})
    assert lead.business_email is None


# ── F3: email/phone never come from the LLM extractor ─────────────────────

async def test_role_word_only_name_is_rejected(monkeypatch):
    async def only_extract(state_summary, cfg=None):
        return AgentAction(action="extract_page_text", params={})
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", only_extract)

    async def returns_a_title_as_a_name(text, missing, business_name=None, cfg=None):
        return {"management_contact_name": "Dentist Dr.", "management_title": "Dentist"}
    monkeypatch.setattr(agent_mod.llm_mod, "extract_fields", returns_a_title_as_a_name)

    PAGE = "Welcome. Our Dentist Dr. is here to help. Book online."
    browser = ScriptedBrowser(lambda a: ok(a.action, url="https://acme.test", text=PAGE, links=[], headings=[])
                              if a.action == "extract_page_text"
                              else ok(a.action, url="https://acme.test", title="Acme"))
    agent = agent_mod.ResearchAgent(browser, _cfg(max_actions_per_lead=4), "dental clinics")
    lead = await agent.research_business({"business_name": "Acme", "website": "https://acme.test"})

    assert lead.management_contact_name is None      # "Dentist Dr." is a title, not a name
    assert validate_no_fabrication(lead) == []


async def test_llm_extractor_email_phone_are_ignored(monkeypatch):
    async def only_extract(state_summary, cfg=None):
        return AgentAction(action="extract_page_text", params={})
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", only_extract)

    async def hallucinating_extract_fields(text, missing, business_name=None, cfg=None):
        return {
            "management_contact_name": "Dr. Real Name",   # OK — language-dependent
            "business_email": "guessed@pattern.com",       # must be ignored
            "business_phone": "+1 000 000 0000",           # must be ignored
        }
    monkeypatch.setattr(agent_mod.llm_mod, "extract_fields", hallucinating_extract_fields)

    PAGE = "Our practice. Dr. Real Name is our lead dentist. Welcome."
    browser = ScriptedBrowser(lambda a: ok(a.action, url="https://acme.test", text=PAGE, links=[], headings=[])
                              if a.action == "extract_page_text"
                              else ok(a.action, url="https://acme.test", title="Acme"))
    agent = agent_mod.ResearchAgent(browser, _cfg(max_actions_per_lead=4), "dental clinics")
    lead = await agent.research_business({"business_name": "Acme", "website": "https://acme.test"})

    assert lead.management_contact_name == "Dr. Real Name"   # kept
    assert lead.business_email is None                        # LLM email dropped
    assert lead.business_phone is None                        # LLM phone dropped
    assert validate_no_fabrication(lead) == []


# ── F5: SERP snippet blob is not mined for the business's phone/email ─────

async def test_serp_snippets_do_not_become_business_contact(monkeypatch):
    async def one_search(state_summary, cfg=None):
        return AgentAction(action="google_search", params={"query": "Acme Dental LA"})
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", one_search)

    browser = ScriptedBrowser(lambda a: ok("google_search", query="Acme Dental LA", results=[
        {"title": "Acme Dental - Yelp", "url": "https://yelp.com/biz/acme",
         "snippet": "Call (800) 555-0000 to book. reviews@yelp.com"},
    ]))
    agent = agent_mod.ResearchAgent(browser, _cfg(max_actions_per_lead=2, max_searches_per_lead=1), "dental clinics")
    lead = await agent.research_business({"business_name": "Acme Dental", "city": "LA"})
    assert lead.business_phone is None
    assert lead.business_email is None


# ── F4: a stray URL from a management search is not adopted as the website ─

async def test_management_search_url_not_adopted_as_website(monkeypatch):
    calls = {"n": 0}

    async def searches(state_summary, cfg=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return AgentAction(action="google_search", params={"query": '"Acme Dental" LA website'})
        return AgentAction(action="google_search", params={"query": '"Acme Dental" practice manager'})
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", searches)

    def _script(a):
        if calls["n"] <= 1:
            return ok("google_search", results=[])   # website search finds nothing
        return ok("google_search", results=[
            {"title": "Some LinkedIn-ish profile", "url": "https://randomnews.example/article-42", "snippet": ""},
        ])
    browser = ScriptedBrowser(_script)
    agent = agent_mod.ResearchAgent(browser, _cfg(max_actions_per_lead=3, max_searches_per_lead=3), "dental clinics")
    lead = await agent.research_business({"business_name": "Acme Dental", "city": "LA"})
    # the unrelated article URL must NOT have been recorded as the website
    assert lead.business_website != "https://randomnews.example/article-42"
