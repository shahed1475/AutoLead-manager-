"""
Multiple decision makers per business + custom target titles.

Every person kept must have a name AND title that appear on the page — the
LLM is never trusted to name someone on its own.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from backend import database as db
from backend.research_agent import agent as agent_mod
from backend.research_agent import extraction
from backend.research_agent import llm as llm_mod
from backend.research_agent.evidence import record_decision_maker
from backend.research_agent.models import (
    MAX_TARGET_TITLES,
    DecisionMaker,
    ResearchLead,
    match_target_title,
    resolve_target_titles,
    sanitize_target_titles,
)
from backend.research_agent.validation import validate_no_fabrication
from backend.routers import research_agent as ra_router_module

from ._fakes import ScriptedBrowser, ok


def _cfg(**overrides):
    base = {
        "research_agent_max_actions_per_lead": 12,
        "research_agent_max_searches_per_lead": 4,
        "research_agent_max_pages_per_lead": 5,
        "research_agent_max_time_per_lead_seconds": 180,
        "research_agent_max_total_leads": 20,
        "research_agent_max_consecutive_failures": 3,
        "research_agent_max_geographic_units": 5,
        "research_agent_page_timeout_ms": 20000,
        "research_agent_headless": True,
        "research_agent_max_decision_makers": 5,
    }
    base.update({f"research_agent_{k}": v for k, v in overrides.items()})
    return base


# ── Title helpers ────────────────────────────────────────────────────────

def test_sanitize_target_titles_cleans_dedups_and_caps():
    titles = sanitize_target_titles(
        ["Head of Marketing", "  head of marketing ", "HR Director!!<>", "", 7, "x"]
        + [f"Title {i}" for i in range(20)]
    )
    assert titles[0] == "Head of Marketing"
    assert titles[1] == "HR Director"
    assert all("<" not in t and ">" not in t for t in titles)
    assert len(titles) == MAX_TARGET_TITLES
    assert sum(t.lower() == "head of marketing" for t in titles) == 1


def test_resolve_target_titles_prefers_custom_over_niche():
    assert resolve_target_titles("dental clinics") == ("Dentist", "Owner", "Practice Manager", "Office Manager")
    assert resolve_target_titles("dental clinics", ["HR Director"]) == ("HR Director",)
    assert resolve_target_titles("dental clinics", ["  "]) == resolve_target_titles("dental clinics")


@pytest.mark.parametrize("page_title,expected", [
    ("Practice Owner", "Owner"),
    ("Founder & Chief Executive Officer", "CEO"),
    ("CEO", "CEO"),
    ("HR Director", "Human Resources Director"),
    ("Receptionist", None),
])
def test_match_target_title_handles_words_and_abbreviations(page_title, expected):
    assert match_target_title(page_title, ["CEO", "Owner", "Human Resources Director"]) == expected


# ── Extraction pre-filter ───────────────────────────────────────────────

def test_find_role_sentences_uses_custom_titles_and_keeps_card_names():
    text = "Leadership\nMaria Gomez\nHead of Growth\nTom Reed\nBrand Strategist\nContact us today."
    plain = extraction.find_role_sentences(text)
    assert not any("Brand Strategist" in s for s in plain)
    hits = extraction.find_role_sentences(text, extra_titles=["Brand Strategist", "Head of Growth"])
    joined = " | ".join(hits)
    assert "Maria Gomez — Head of Growth" in joined
    assert "Tom Reed — Brand Strategist" in joined


# ── LLM parsing ─────────────────────────────────────────────────────────

async def test_extract_decision_makers_parses_nested_json_in_prose(monkeypatch):
    async def fake_llm(prompt, cfg, **kwargs):
        return ('Here you go: {"people": [{"name": "Ana Silva", "title": "Owner"}, '
                '{"name": "unknown", "title": "CEO"}, {"name": "Bo Chen", "title": "CTO"}]} done')
    monkeypatch.setattr(llm_mod, "_call_llm_raw", fake_llm)
    people = await llm_mod.extract_decision_makers("text", ["Owner"], cfg={"x": 1})
    assert people == [{"name": "Ana Silva", "title": "Owner"}, {"name": "Bo Chen", "title": "CTO"}]


async def test_extract_decision_makers_returns_empty_on_failure(monkeypatch):
    async def boom(prompt, cfg, **kwargs):
        raise RuntimeError("ollama down")
    monkeypatch.setattr(llm_mod, "_call_llm_raw", boom)
    assert await llm_mod.extract_decision_makers("text", ["Owner"], cfg={"x": 1}) == []


# ── Evidence: dedup, cap, primary ───────────────────────────────────────

def test_record_decision_maker_dedups_caps_and_picks_primary_by_priority():
    lead = ResearchLead(business_name="Acme")
    titles = ("CEO", "Head of Marketing")
    assert record_decision_maker(lead, "Jane Roe", "Office Manager", titles, "website", max_count=3)
    assert lead.management_contact_name == "Jane Roe"          # only person so far
    assert record_decision_maker(lead, "John Smith", "Head of Marketing", titles, "website", max_count=3)
    assert lead.management_contact_name == "John Smith"        # matches a target title
    assert not record_decision_maker(lead, "Dr. John Smith", "Head of Marketing", titles, "website", max_count=3)
    assert record_decision_maker(lead, "Amy Lin", "Founder & CEO", titles, "website", max_count=3)
    assert lead.management_contact_name == "Amy Lin"           # highest-priority title wins
    assert lead.management_title == "Founder & CEO"
    assert not record_decision_maker(lead, "Extra Person", "CEO", titles, "website", max_count=3)  # cap
    assert [d.name for d in lead.decision_makers] == ["Jane Roe", "John Smith", "Amy Lin"]
    assert [d.is_primary for d in lead.decision_makers] == [False, False, True]
    assert validate_no_fabrication(lead) == []


def test_first_name_only_mention_merges_into_the_full_name():
    """Real-run regression: the homepage said "Jonathan" / "Sara", the About
    page "Jonathan Windham" / "Sara Wickey" — that is two people, not four."""
    lead = ResearchLead(business_name="Blackhawk")
    titles = ("Founder", "Head of Marketing")
    assert record_decision_maker(lead, "Jonathan", "Founder, CEO", titles, "website", source_url="https://b.test/")
    assert record_decision_maker(lead, "Sara", "President", titles, "website", source_url="https://b.test/")
    assert not record_decision_maker(lead, "Jonathan Windham", "Founder, CEO", titles, "website",
                                     source_url="https://b.test/about/")
    assert not record_decision_maker(lead, "Sara Wickey", "President", titles, "website",
                                     source_url="https://b.test/about/")
    assert not record_decision_maker(lead, "Jonathan", "Founder", titles, "website")  # already known
    assert [d.name for d in lead.decision_makers] == ["Jonathan Windham", "Sara Wickey"]
    assert lead.decision_makers[0].source_url == "https://b.test/about/"
    assert lead.management_contact_name == "Jonathan Windham"   # primary upgraded to the full name
    assert lead.management_title == "Founder, CEO"
    assert validate_no_fabrication(lead) == []


def test_validate_no_fabrication_flags_decision_maker_without_evidence():
    lead = ResearchLead(business_name="Acme")
    lead.decision_makers.append(DecisionMaker(name="Ghost Person", title="CEO"))
    assert any("Ghost Person" in v for v in validate_no_fabrication(lead))


# ── Agent loop ──────────────────────────────────────────────────────────

LEADERSHIP_PAGE = """
Brightline Logistics — About us
Our leadership team
Amy Lin is our Founder and CEO.
Carlos Diaz, Head of Marketing, leads brand and growth.
Priya Nair — HR Director
Call (337) 555-0142 or write to hello@brightline.test
"""


def _single_page_browser(text=LEADERSHIP_PAGE):
    return ScriptedBrowser(lambda a: ok(a.action, url="https://brightline.test", text=text, links=[], headings=[])
                           if a.action == "extract_page_text"
                           else ok(a.action, url="https://brightline.test", title="Brightline"))


async def _finish(state_summary, cfg=None):
    return agent_mod.AgentAction(action="finish_research", reason="done")


async def test_agent_keeps_every_named_decision_maker_and_rejects_invented_ones(monkeypatch):
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", _finish)

    async def people(text, target_titles, business_name=None, cfg=None):
        return [
            {"name": "Amy Lin", "title": "Founder and CEO"},
            {"name": "Carlos Diaz", "title": "Head of Marketing"},
            {"name": "Priya Nair", "title": "HR Director"},
            {"name": "Invented Person", "title": "CFO"},          # not on the page
            {"name": "Carlos Diaz", "title": "Chief Revenue Officer"},  # duplicate person
            {"name": "Priya Nair", "title": "Chief Legal Officer"},     # title not on page
        ]
    monkeypatch.setattr(agent_mod.llm_mod, "extract_decision_makers", people)

    a = agent_mod.ResearchAgent(_single_page_browser(), _cfg(), "logistics",
                                target_titles=["Head of Marketing", "HR Director"])
    lead = await a.research_business({"business_name": "Brightline Logistics", "website": "https://brightline.test"})

    names = [d.name for d in lead.decision_makers]
    assert names == ["Amy Lin", "Carlos Diaz", "Priya Nair"]
    assert "Invented Person" not in names
    # primary = highest-priority CUSTOM title, not whoever was found first
    assert lead.management_contact_name == "Carlos Diaz"
    assert lead.management_title == "Head of Marketing"
    matched = {d.name: d.matched_title for d in lead.decision_makers}
    assert matched == {"Amy Lin": None, "Carlos Diaz": "Head of Marketing", "Priya Nair": "HR Director"}
    assert all(d.source_url == "https://brightline.test" for d in lead.decision_makers)
    assert validate_no_fabrication(lead) == []
    assert "Decision makers:" in (lead.research_notes or "")


async def test_agent_respects_max_decision_makers(monkeypatch):
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", _finish)

    async def people(text, target_titles, business_name=None, cfg=None):
        return [{"name": "Amy Lin", "title": "CEO"}, {"name": "Carlos Diaz", "title": "Head of Marketing"},
                {"name": "Priya Nair", "title": "HR Director"}]
    monkeypatch.setattr(agent_mod.llm_mod, "extract_decision_makers", people)

    a = agent_mod.ResearchAgent(_single_page_browser(), _cfg(max_decision_makers=2), "logistics")
    lead = await a.research_business({"business_name": "Brightline Logistics", "website": "https://brightline.test"})
    assert len(lead.decision_makers) == 2


async def test_fallback_searches_for_an_unmatched_custom_title(monkeypatch):
    async def llm_down(state_summary, cfg=None):
        return agent_mod.AgentAction(action="_llm_failed", reason="down")
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", llm_down)

    async def nobody(text, target_titles, business_name=None, cfg=None):
        return []
    monkeypatch.setattr(agent_mod.llm_mod, "extract_decision_makers", nobody)

    queries = []

    def _script(action):
        if action.action == "google_search":
            queries.append(action.params.get("query", ""))
            return ok("google_search", results=[])
        if action.action == "extract_page_text":
            return ok("extract_page_text", url="https://brightline.test",
                      text="Brightline Logistics. Call (337) 555-0142.", links=[], headings=[])
        return ok(action.action, url="https://brightline.test", title="Brightline")

    a = agent_mod.ResearchAgent(ScriptedBrowser(_script), _cfg(max_actions_per_lead=6), "logistics",
                                target_titles=["Head of Marketing"])
    await a.research_business({"business_name": "Brightline Logistics", "website": "https://brightline.test"})
    assert queries and "Head of Marketing" in queries[0]


# ── Persistence + API ───────────────────────────────────────────────────

class _FakeQueue:
    def __init__(self):
        self.enqueued = []

    def enqueue_nowait(self, job_type, payload, handler):
        self.enqueued.append((job_type, payload))
        return True


async def test_decision_makers_persist_and_show_in_results_and_csv(clean_db, monkeypatch):
    monkeypatch.setattr(ra_router_module, "get_queue", lambda: _FakeQueue())
    from backend.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/research-agent/start", json={
            "niche": "logistics", "city": "Austin", "target_count": 1,
            "target_titles": ["Head of Marketing", "HR Director", "head of marketing"],
        })
        assert resp.status_code == 200
        session_id = resp.json()["session_id"]
        session = await db.get_research_session(session_id)
        assert session["target_titles"] == '["Head of Marketing", "HR Director"]'

        await db.save_research_result(
            session_id,
            {"business_name": "Brightline", "management_contact_name": "Carlos Diaz",
             "management_title": "Head of Marketing", "research_status": "COMPLETE"},
            [], decision_makers=[
                {"name": "Amy Lin", "title": "CEO", "matched_title": None, "is_primary": False,
                 "source_url": "https://brightline.test", "confidence": 0.65},
                {"name": "Carlos Diaz", "title": "Head of Marketing", "matched_title": "Head of Marketing",
                 "is_primary": True, "source_url": "https://brightline.test", "confidence": 0.65},
            ],
        )

        body = (await client.get(f"/api/research-agent/{session_id}/results")).json()
        dms = body["results"][0]["decision_makers"]
        assert [d["name"] for d in dms] == ["Carlos Diaz", "Amy Lin"]   # primary first
        assert dms[0]["is_primary"] == 1

        csv_text = (await client.get(f"/api/research-agent/{session_id}/results.csv")).text
        assert "decision_makers" in csv_text.splitlines()[0]
        assert "Carlos Diaz (Head of Marketing); Amy Lin (CEO)" in csv_text


async def test_titles_endpoint_returns_niche_defaults(clean_db):
    from backend.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get("/api/research-agent/titles", params={"niche": "dental clinics"})).json()
    assert body["default_titles"] == ["Dentist", "Owner", "Practice Manager", "Office Manager"]
    assert "Head of Marketing" in body["suggestions"]
    assert body["max_titles"] == MAX_TARGET_TITLES
