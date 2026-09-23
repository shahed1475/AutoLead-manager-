import pytest

from backend.research_agent import agent as agent_mod
from backend.research_agent.models import RESEARCH_COMPLETE, AgentAction
from backend.research_agent.validation import validate_no_fabrication

from ._fakes import ScriptedBrowser, err, ok

pytestmark = pytest.mark.asyncio

TEAM_PAGE_TEXT = """
Acme Family Dental
123 Main St, Abbeville, LA

Contact us at info@acmefamilydental.test or call (337) 555-0142.

Our Team
Dr. John Smith — Lead Dentist
Emma Papp — Office Manager, has been with the practice for 10 years.
"""


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
    }
    base.update({f"research_agent_{k}" if not k.startswith("research_agent_") else k: v for k, v in overrides.items()})
    return base


def _scripted_llm(actions):
    it = iter(actions)

    async def _decide(state_summary, cfg=None):
        try:
            return next(it)
        except StopIteration:
            return AgentAction(action="finish_research", reason="script exhausted")
    return _decide


async def _no_management_extraction(text, target_titles, business_name=None, cfg=None):
    return []


# ── 1. OBSERVE -> DECIDE -> ACT -> VALIDATE, multiple iterations, correct finish ──

async def test_full_loop_extracts_all_fields_and_finishes(monkeypatch):
    browser = ScriptedBrowser({
        "google_search": [ok("google_search", query="Acme Family Dental Abbeville", results=[
            {"title": "Acme Family Dental", "url": "https://acmefamilydental.test", "snippet": ""},
        ])],
        "open_url": [ok("open_url", url="https://acmefamilydental.test", title="Acme Family Dental")],
        "extract_page_text": [ok("extract_page_text", url="https://acmefamilydental.test", text=TEAM_PAGE_TEXT)],
    })
    actions = [
        AgentAction(action="google_search", params={"query": "Acme Family Dental Abbeville"}),
        AgentAction(action="open_url", params={"url": "https://acmefamilydental.test"}),
        AgentAction(action="extract_page_text", params={}),
        AgentAction(action="finish_research", params={"reason": "found enough"}),
    ]
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", _scripted_llm(actions))

    async def fake_extract_people(text, target_titles, business_name=None, cfg=None):
        return [{"name": "Emma Papp", "title": "Office Manager"}]
    monkeypatch.setattr(agent_mod.llm_mod, "extract_decision_makers", fake_extract_people)

    a = agent_mod.ResearchAgent(browser, _cfg(), "dental clinics")
    lead = await a.research_business({"business_name": "Acme Family Dental", "city": "Abbeville", "state": "LA", "country": "USA"})

    # brief category 8 — full field checklist
    assert lead.city == "Abbeville"
    assert lead.state == "LA"
    assert lead.country == "USA"
    assert lead.business_name == "Acme Family Dental"
    assert lead.business_website == "https://acmefamilydental.test"
    assert lead.business_phone  # extracted via regex from TEAM_PAGE_TEXT
    assert lead.business_email == "info@acmefamilydental.test"
    assert lead.management_contact_name == "Emma Papp"
    assert lead.management_title == "Office Manager"
    assert lead.management_phone == lead.business_phone  # inferred from business phone
    assert lead.management_phone_type == "BUSINESS"       # never presented as personal
    assert lead.research_status == RESEARCH_COMPLETE
    assert browser.calls == ["google_search", "open_url", "extract_page_text"]
    assert validate_no_fabrication(lead) == []  # every contact field traces to real evidence


# ── 2. Deep research: the loop must READ pages, not just open them ─────────

SITE_WITH_MAILTO = """
Abbeville Family Dental — Welcome

We are open Mon–Fri. Book online or call us.
"""


async def test_loop_reads_the_page_even_when_llm_only_asks_for_find_links(monkeypatch):
    """The verification defect: the 8B model fixates on find_links and never
    picks extract_page_text, so nothing is ever mined. The loop must now
    deterministically read a page it has opened."""
    async def only_find_links(state_summary, cfg=None):
        return AgentAction(action="find_links", params={})
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", only_find_links)

    async def fake_extract_people(text, target_titles, business_name=None, cfg=None):
        return [{"name": "Emma Papp", "title": "Office Manager"}]
    monkeypatch.setattr(agent_mod.llm_mod, "extract_decision_makers", fake_extract_people)

    def _script(action):
        if action.action == "open_url":
            return ok("open_url", url="https://abbevilledental.test", title="Abbeville Family Dental")
        if action.action == "extract_page_text":
            return ok("extract_page_text", url="https://abbevilledental.test", text=TEAM_PAGE_TEXT,
                      links=[{"text": "Email us", "href": "mailto:office@abbevilledental.test"}],
                      headings=["Our Team"])
        if action.action == "find_links":
            return ok("find_links", links=[{"text": "Our Team", "href": "https://abbevilledental.test/team"}])
        return ok(action.action)

    browser = ScriptedBrowser(_script)
    cfg = _cfg(max_actions_per_lead=8, max_searches_per_lead=2)
    a = agent_mod.ResearchAgent(browser, cfg, "dental clinics")
    lead = await a.research_business({
        "business_name": "Abbeville Family Dental", "city": "Abbeville", "state": "LA",
        "country": "USA", "website": "https://abbevilledental.test",
    })

    assert "extract_page_text" in browser.calls           # the page was actually read
    assert lead.business_email == "office@abbevilledental.test"
    assert lead.management_contact_name == "Emma Papp"
    assert lead.management_title == "Office Manager"
    # every populated contact field traces to real evidence (no fabrication)
    assert validate_no_fabrication(lead) == []
    # and the email evidence is NOT a discovery seed — it came from the page
    email_ev = [e for e in lead.evidence if e.field_name == "business_email"]
    assert email_ev and email_ev[0].source_type != "discovery_seed"


async def test_loop_opens_a_relevant_page_when_management_still_missing(monkeypatch):
    """After reading the homepage with no owner/manager on it, the loop
    should follow an About/Team link rather than give up or spin."""
    async def only_find_links(state_summary, cfg=None):
        return AgentAction(action="find_links", params={})
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", only_find_links)

    async def extract_from_team_only(text, target_titles, business_name=None, cfg=None):
        if "Practice Manager" in text:
            return [{"name": "Sara Cole", "title": "Practice Manager"}]
        return []
    monkeypatch.setattr(agent_mod.llm_mod, "extract_decision_makers", extract_from_team_only)

    HOME = "Abbeville Family Dental. Call (337) 893-2614. About Us | Our Team | Contact"
    TEAM = "Our Team. Sara Cole is the Practice Manager and has run the office for 8 years."

    def _script(action):
        url = getattr(action, "params", {}).get("url", "")
        if action.action == "open_url":
            return ok("open_url", url=url or "https://abbevilledental.test", title="Abbeville Family Dental")
        if action.action == "extract_page_text":
            cur = browser._page.url
            if "team" in cur:
                return ok("extract_page_text", url=cur, text=TEAM, links=[], headings=["Our Team"])
            return ok("extract_page_text", url="https://abbevilledental.test", text=HOME,
                      links=[{"text": "Our Team", "href": "https://abbevilledental.test/team"},
                             {"text": "Shop", "href": "https://abbevilledental.test/shop"}],
                      headings=["Welcome"])
        if action.action == "find_links":
            return ok("find_links", links=[{"text": "Our Team", "href": "https://abbevilledental.test/team"}])
        return ok(action.action)

    browser = ScriptedBrowser(_script)
    # ScriptedBrowser._page.url needs to update as pages open — patch execute
    real_execute = browser.execute
    async def tracking_execute(action):
        res = await real_execute(action)
        if action.action == "open_url" and res.status == "success":
            browser._page.url = action.params.get("url") or res.data.get("url") or browser._page.url
        return res
    browser.execute = tracking_execute

    cfg = _cfg(max_actions_per_lead=10, max_pages_per_lead=4, max_searches_per_lead=2)
    a = agent_mod.ResearchAgent(browser, cfg, "dental clinics")
    lead = await a.research_business({
        "business_name": "Abbeville Family Dental", "city": "Abbeville",
        "website": "https://abbevilledental.test",
    })

    assert any("team" in c for c in [browser._page.url])  # ended up on / visited the team page
    assert lead.management_contact_name == "Sara Cole"
    assert lead.pages_visited >= 2
    assert validate_no_fabrication(lead) == []


async def test_optional_fields_marked_not_found_after_search_when_nothing_is_found(monkeypatch):
    async def only_extract(state_summary, cfg=None):
        return AgentAction(action="extract_page_text", params={})
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", only_extract)
    monkeypatch.setattr(agent_mod.llm_mod, "extract_decision_makers", _no_management_extraction)

    BARE = "Abbeville Family Dental. 123 Main St. Phone (337) 893-2614. Open Mon-Fri."

    def _script(action):
        if action.action == "open_url":
            return ok("open_url", url="https://abbevilledental.test", title="Abbeville Family Dental")
        if action.action == "extract_page_text":
            return ok("extract_page_text", url="https://abbevilledental.test", text=BARE, links=[], headings=[])
        return ok(action.action)

    browser = ScriptedBrowser(_script)
    cfg = _cfg(max_actions_per_lead=6, max_searches_per_lead=2)
    a = agent_mod.ResearchAgent(browser, cfg, "dental clinics")
    lead = await a.research_business({
        "business_name": "Abbeville Family Dental", "city": "Abbeville",
        "website": "https://abbevilledental.test",
    })

    # phone was on the page -> FOUND; email + management were searched -> explicit NOT_FOUND_AFTER_SEARCH
    statuses = {e.field_name: e.status for e in lead.evidence}
    assert statuses.get("management_contact_name") == "NOT_FOUND_AFTER_SEARCH"
    assert statuses.get("management_email") == "NOT_FOUND_AFTER_SEARCH"
    assert lead.management_email_status == "NOT_FOUND_AFTER_SEARCH"
    assert lead.management_contact_name is None            # never fabricated
    assert validate_no_fabrication(lead) == []


async def test_forced_actions_skip_the_llm_call(monkeypatch):
    """Deterministic steps (open the known website, read the page just opened)
    must NOT pay an LLM round-trip — the loop decides them itself. A slow /
    timing-out Ollama otherwise costs the full timeout per forced step."""
    llm_calls = {"n": 0}

    async def counting_decide(state_summary, cfg=None):
        llm_calls["n"] += 1
        return AgentAction(action="finish_research", reason="llm was asked")
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", counting_decide)
    monkeypatch.setattr(agent_mod.llm_mod, "extract_decision_makers", _no_management_extraction)

    def _script(action):
        if action.action == "open_url":
            return ok("open_url", url="https://abbevilledental.test", title="Abbeville Family Dental")
        if action.action == "extract_page_text":
            return ok("extract_page_text", url="https://abbevilledental.test",
                      text="Abbeville Family Dental. Phone (337) 893-2614.", links=[], headings=[])
        return ok(action.action)

    browser = ScriptedBrowser(_script)
    a = agent_mod.ResearchAgent(browser, _cfg(max_actions_per_lead=5), "dental clinics")
    lead = await a.research_business({
        "business_name": "Abbeville Family Dental", "city": "Abbeville",
        "website": "https://abbevilledental.test",
    })

    # open_url (R1) and extract_page_text (R2) happened with ZERO llm calls;
    # the loop only consults the LLM once it has no deterministic move.
    assert "open_url" in browser.calls and "extract_page_text" in browser.calls
    assert browser.calls.index("open_url") < browser.calls.index("extract_page_text")
    assert llm_calls["n"] <= 1


async def test_discovery_filters_aggregator_and_listicle_results(monkeypatch):
    from backend.research_agent.planner import GeoTask
    browser = ScriptedBrowser({
        "google_search": [ok("google_search", query="dental clinics in Los Angeles", results=[
            {"title": "Best Dentists Near Me in Los Angeles, CA - Yelp", "url": "https://directory.test/best"},
            {"title": "10 Top Dental Clinics in LA", "url": "https://listicle.test/top-10"},
            {"title": "Abbeville Family Dental", "url": "https://abbevillefamilydental.test"},
            {"title": "Smile Bright Dental Care", "url": "https://smilebright.test"},
        ])],
    })
    a = agent_mod.ResearchAgent(browser, _cfg(), "dental clinics")
    geo = GeoTask(city="Los Angeles", state="CA", country="USA", raw_location="Los Angeles", target_count=10)
    candidates = await a.discover_candidates(geo, 10)
    names = [c["business_name"] for c in candidates]
    assert "Abbeville Family Dental" in names
    assert "Smile Bright Dental Care" in names
    assert not any("Best Dentists" in n or "Top Dental" in n for n in names)


# ── 4. Budgets ──────────────────────────────────────────────────────────────

async def test_max_actions_per_lead_enforced(monkeypatch):
    async def always_search(state_summary, cfg=None):
        return AgentAction(action="google_search", params={"query": "q"})
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", always_search)

    browser = ScriptedBrowser(lambda action: ok(action.action, results=[]))
    cfg = _cfg(max_actions_per_lead=3, max_searches_per_lead=10)
    a = agent_mod.ResearchAgent(browser, cfg, "dental clinics")
    lead = await a.research_business({"business_name": "X"})
    assert lead.actions_taken == 3


async def test_max_searches_per_lead_enforced(monkeypatch):
    async def always_search(state_summary, cfg=None):
        return AgentAction(action="google_search", params={"query": "q"})
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", always_search)

    browser = ScriptedBrowser(lambda action: ok(action.action, results=[]))
    cfg = _cfg(max_actions_per_lead=100, max_searches_per_lead=2)
    a = agent_mod.ResearchAgent(browser, cfg, "dental clinics")
    lead = await a.research_business({"business_name": "X"})
    assert lead.searches_taken == 2
    assert lead.actions_taken == 2  # the 3rd (budget-exceeding) request never executes


async def test_max_pages_per_lead_stops_navigation_but_keeps_extracting(monkeypatch):
    """Once the page budget is hit, further open_url/open_new_tab requests
    are redirected to extract_page_text instead of instantly discarding
    whatever the already-open page might still hold (see agent.py) — so
    pages_visited caps at the budget, but the loop keeps running
    (extracting, not navigating) until a different budget stops it."""
    async def always_open(state_summary, cfg=None):
        return AgentAction(action="open_url", params={"url": "https://x.test"})
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", always_open)
    monkeypatch.setattr(agent_mod.llm_mod, "extract_decision_makers", _no_management_extraction)

    browser = ScriptedBrowser(lambda action: ok(action.action, url="https://x.test", title="X", text=""))
    cfg = _cfg(max_actions_per_lead=6, max_pages_per_lead=2)
    a = agent_mod.ResearchAgent(browser, cfg, "dental clinics")
    lead = await a.research_business({"business_name": "X", "website": "https://x.test"})

    assert lead.pages_visited == 2                     # navigation budget still enforced
    assert lead.actions_taken == 6                      # loop kept making progress (extracting) until max_actions
    assert browser.calls.count("open_url") == 2          # no navigation past the budget
    assert browser.calls.count("extract_page_text") == 4  # redirected here instead of hard-stopping


async def test_max_time_per_lead_enforced(monkeypatch):
    times = iter([0, 0, 50, 50, 500])
    monkeypatch.setattr(agent_mod.time, "monotonic", lambda: next(times, 999999))

    async def always_search(state_summary, cfg=None):
        return AgentAction(action="google_search", params={"query": "q"})
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", always_search)

    browser = ScriptedBrowser(lambda action: ok(action.action, results=[]))
    cfg = _cfg(max_actions_per_lead=100, max_searches_per_lead=100, max_time_per_lead_seconds=100)
    a = agent_mod.ResearchAgent(browser, cfg, "dental clinics")
    lead = await a.research_business({"business_name": "X"})
    assert lead.actions_taken == 3  # loop breaks once elapsed time exceeds the budget


async def test_max_consecutive_failures_enforced(monkeypatch):
    async def always_search(state_summary, cfg=None):
        return AgentAction(action="google_search", params={"query": "q"})
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", always_search)

    browser = ScriptedBrowser(lambda action: err(action.action, "network fail"))
    cfg = _cfg(max_actions_per_lead=100, max_searches_per_lead=100, max_consecutive_failures=2)
    a = agent_mod.ResearchAgent(browser, cfg, "dental clinics")
    lead = await a.research_business({"business_name": "X"})
    assert lead.consecutive_failures == 2
    assert lead.actions_taken == 2


async def test_agent_never_loops_forever_even_with_finish_never_offered(monkeypatch):
    """Sanity ceiling: an LLM that never once suggests finish_research must
    still terminate — via the max_actions budget, not by hanging."""
    async def always_search(state_summary, cfg=None):
        return AgentAction(action="google_search", params={"query": "q"})
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", always_search)
    browser = ScriptedBrowser(lambda action: ok(action.action, results=[]))
    cfg = _cfg(max_actions_per_lead=5, max_searches_per_lead=5)
    a = agent_mod.ResearchAgent(browser, cfg, "dental clinics")
    lead = await a.research_business({"business_name": "X"})
    assert lead.actions_taken <= 5


# ── 3. Deterministic fallback when the LLM fails entirely ──────────────────

async def test_fallback_planner_makes_progress_when_llm_always_fails(monkeypatch):
    async def always_fail(state_summary, cfg=None):
        return AgentAction(action="_llm_failed", reason="ollama unreachable")
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", always_fail)
    monkeypatch.setattr(agent_mod.llm_mod, "extract_decision_makers", _no_management_extraction)

    browser = ScriptedBrowser({
        "google_search": [ok("google_search", results=[
            {"title": "Acme Family Dental", "url": "https://acmefamilydental.test", "snippet": ""},
        ])],
        "open_url": [ok("open_url", url="https://acmefamilydental.test", title="Acme Family Dental")],
    })
    cfg = _cfg(max_actions_per_lead=6, max_searches_per_lead=3)
    a = agent_mod.ResearchAgent(browser, cfg, "dental clinics")
    lead = await a.research_business({"business_name": "Acme Family Dental", "city": "Abbeville"})

    # Real progress happened via the rule-based fallback, with zero working LLM turns.
    assert "google_search" in browser.calls
    assert lead.business_website == "https://acmefamilydental.test"
    assert lead.research_status is not None


# ── 7. Failure isolation (session level) ────────────────────────────────────

async def test_one_failed_lead_does_not_stop_the_batch(monkeypatch):
    calls = {"n": 0}

    async def fake_research_business(self, hint, **kwargs):
        calls["n"] += 1
        if hint["business_name"] == "Broken Co":
            raise RuntimeError("simulated crash mid-research")
        from backend.research_agent.models import ResearchLead
        return ResearchLead(business_name=hint["business_name"], business_phone="555", business_website="https://x.test")

    monkeypatch.setattr(agent_mod.ResearchAgent, "research_business", fake_research_business)

    async def fake_expand_geography(location, target_count, max_units, cfg=None):
        from backend.research_agent.planner import GeoTask
        return [GeoTask(city=location, state=None, country=None, raw_location=location, target_count=target_count)]
    monkeypatch.setattr(agent_mod, "expand_geography", fake_expand_geography)

    class _FakeBrowserCtx:
        async def __aenter__(self):
            return ScriptedBrowser({})
        async def __aexit__(self, *exc):
            return False
    monkeypatch.setattr(agent_mod, "BrowserController", lambda **kw: _FakeBrowserCtx())

    seeds = [
        {"business_name": "Lead One", "city": "Abbeville"},
        {"business_name": "Broken Co", "city": "Abbeville"},
        {"business_name": "Lead Three", "city": "Abbeville"},
    ]
    result = await agent_mod.run_research_session(
        niche="dental clinics", location="Abbeville, USA", target_count=3,
        seed_businesses=seeds, cfg=_cfg(),
    )
    assert result["failed_count"] == 1
    assert len(result["leads"]) == 2
    assert {l.business_name for l in result["leads"]} == {"Lead One", "Lead Three"}
    assert calls["n"] == 3  # all three were attempted despite the failure


# ── 8. Resume: skip candidates already processed on a previous run ──────────

def _seed_session_env(monkeypatch, researched):
    async def fake_research_business(self, hint, **kwargs):
        researched.append(hint["business_name"])
        from backend.research_agent.models import ResearchLead, RESEARCH_COMPLETE
        return ResearchLead(
            business_name=hint["business_name"], city=hint.get("city"),
            business_phone="555", business_website=hint.get("website") or f"https://{hint['business_name'].replace(' ', '').lower()}.test",
            research_status=RESEARCH_COMPLETE,
        )

    monkeypatch.setattr(agent_mod.ResearchAgent, "research_business", fake_research_business)

    async def fake_expand_geography(location, target_count, max_units, cfg=None):
        from backend.research_agent.planner import GeoTask
        return [GeoTask(city=location, state=None, country=None, raw_location=location, target_count=target_count)]
    monkeypatch.setattr(agent_mod, "expand_geography", fake_expand_geography)

    class _FakeBrowserCtx:
        async def __aenter__(self):
            return ScriptedBrowser({})
        async def __aexit__(self, *exc):
            return False
    monkeypatch.setattr(agent_mod, "BrowserController", lambda **kw: _FakeBrowserCtx())


async def test_already_processed_candidates_are_skipped(monkeypatch):
    researched = []
    _seed_session_env(monkeypatch, researched)
    seeds = [
        {"business_name": "Done Co", "city": "Akron", "website": "https://doneco.test"},
        {"business_name": "New Co", "city": "Akron", "website": "https://newco.test"},
    ]
    already = {agent_mod.candidate_key(seeds[0])}

    result = await agent_mod.run_research_session(
        niche="dental clinics", location="Akron", target_count=5,
        seed_businesses=seeds, cfg=_cfg(), already_processed=already,
    )
    assert researched == ["New Co"]          # the finished one was not re-researched
    assert result["skipped_count"] == 1
    assert len(result["leads"]) == 1
    assert agent_mod.candidate_key(seeds[1]) in result["processed_keys"]


async def test_progress_callback_reports_phase_city_and_counts(monkeypatch):
    researched = []
    _seed_session_env(monkeypatch, researched)
    events = []

    async def on_progress(updates):
        events.append(updates)

    await agent_mod.run_research_session(
        niche="dental clinics", location="Akron", target_count=2,
        seed_businesses=[{"business_name": "A", "city": "Akron"}, {"business_name": "B", "city": "Akron"}],
        cfg=_cfg(), on_progress=on_progress,
    )
    phases = [e.get("research_phase") for e in events if "research_phase" in e]
    assert "RESEARCH" in phases and "DONE" in phases
    assert any(e.get("current_city") == "Akron" for e in events)
    assert any(e.get("businesses_researched") for e in events)


# ── 9. Discovery fallback: native google_search blocked → scraper seeds ─────

async def test_discovery_fallback_used_when_google_search_blocked(monkeypatch):
    researched = []
    _seed_session_env(monkeypatch, researched)

    # The session builds its own BrowserController; make google_search return
    # "blocked" (the real-world Google consent/CAPTCHA wall) so native
    # discovery yields nothing.
    class _BlockedBrowserCtx:
        async def __aenter__(self):
            return ScriptedBrowser(lambda action: ActionResult(
                action=action.action, status="blocked", data={"query": action.params.get("query", "")},
            ))
        async def __aexit__(self, *exc):
            return False
    from backend.research_agent.actions import ActionResult
    monkeypatch.setattr(agent_mod, "BrowserController", lambda **kw: _BlockedBrowserCtx())

    fallback_calls = []

    async def fake_fallback(niche, location, country, limit):
        fallback_calls.append((niche, location, country, limit))
        return [
            {"business_name": "Akron Family Dental", "website": "https://akronfamilydental.test", "city": "Akron", "state": "OH", "country": "USA"},
            {"business_name": "Downtown Dental", "website": "https://downtowndental.test", "city": "Akron", "state": "OH", "country": "USA"},
        ]

    result = await agent_mod.run_research_session(
        niche="dental clinics", location="Akron", target_count=5, cfg=_cfg(),
        discovery_fallback=fake_fallback,
    )

    assert fallback_calls, "discovery_fallback should be invoked when native discovery is blocked"
    assert sorted(researched) == ["Akron Family Dental", "Downtown Dental"]
    assert len(result["leads"]) == 2


async def test_discovery_fallback_not_called_when_native_discovery_returns_enough(monkeypatch):
    researched = []
    _seed_session_env(monkeypatch, researched)

    class _OkBrowserCtx:
        async def __aenter__(self):
            return ScriptedBrowser(lambda action: ok("google_search", query="", results=[
                {"title": "Real Dental Co", "url": "https://realdentalco.test", "snippet": ""},
                {"title": "Second Dental", "url": "https://seconddental.test", "snippet": ""},
            ]))
        async def __aexit__(self, *exc):
            return False
    monkeypatch.setattr(agent_mod, "BrowserController", lambda **kw: _OkBrowserCtx())

    called = []

    async def fake_fallback(niche, location, country, limit):
        called.append(True)
        return [{"business_name": "Never Used", "website": "https://never.test"}]

    # target_count == the number of real (non-aggregator) native candidates ->
    # native discovery is "enough", the scraper pipeline is not consulted.
    await agent_mod.run_research_session(
        niche="dental clinics", location="Akron", target_count=2, cfg=_cfg(),
        discovery_fallback=fake_fallback,
    )
    assert not called
    assert sorted(researched) == ["Real Dental Co", "Second Dental"]


async def test_thin_native_discovery_is_supplemented_and_deduped(monkeypatch):
    researched = []
    _seed_session_env(monkeypatch, researched)

    class _ThinBrowserCtx:
        async def __aenter__(self):
            return ScriptedBrowser(lambda action: ok("google_search", query="", results=[
                {"title": "Best Dentists Near Me in Akron", "url": "https://directory.test/best"},  # aggregator -> filtered
                {"title": "Real Dental Co", "url": "https://realdentalco.test", "snippet": ""},
            ]))
        async def __aexit__(self, *exc):
            return False
    monkeypatch.setattr(agent_mod, "BrowserController", lambda **kw: _ThinBrowserCtx())

    async def fake_fallback(niche, location, country, limit):
        return [
            {"business_name": "Real Dental Co", "website": "https://realdentalco.test"},  # dup of native -> deduped
            {"business_name": "Downtown Dental", "website": "https://downtowndental.test"},
        ]

    await agent_mod.run_research_session(
        niche="dental clinics", location="Akron", target_count=5, cfg=_cfg(),
        discovery_fallback=fake_fallback,
    )
    assert sorted(researched) == ["Downtown Dental", "Real Dental Co"]  # aggregator dropped, dup merged


async def test_scraper_fallback_aggregator_hints_are_filtered_and_geo_backfilled(monkeypatch):
    seen = {}

    async def fake_research_business(self, hint, **kw):
        seen[hint["business_name"]] = hint
        from backend.research_agent.models import ResearchLead
        return ResearchLead(business_name=hint["business_name"], business_phone="1",
                            business_website="https://x.test", city=hint.get("city"),
                            state=hint.get("state"))
    monkeypatch.setattr(agent_mod.ResearchAgent, "research_business", fake_research_business)

    async def fake_geo(location, target_count, max_units, cfg=None):
        from backend.research_agent.planner import GeoTask
        return [GeoTask(city="Los Angeles", state="California", country="USA",
                        raw_location="California", target_count=target_count)]
    monkeypatch.setattr(agent_mod, "expand_geography", fake_geo)

    class _Ctx:
        async def __aenter__(self): return ScriptedBrowser(lambda a: ok("google_search", results=[]))
        async def __aexit__(self, *e): return False
    monkeypatch.setattr(agent_mod, "BrowserController", lambda **k: _Ctx())

    async def fake_fallback(niche, location, country, limit):
        return [
            {"business_name": "Find Medi-Cal Dentists in San Francisco", "website": "https://dir.test/find"},  # aggregator
            {"business_name": "Sunset Dental", "website": "https://sunsetdental.test"},                          # real
        ]

    await agent_mod.run_research_session(
        niche="dental clinics", location="California", target_count=2, cfg=_cfg(),
        discovery_fallback=fake_fallback,
    )
    assert "Find Medi-Cal Dentists in San Francisco" not in seen   # aggregator hint dropped
    assert seen["Sunset Dental"]["state"] == "California"           # geo backfilled onto the hint
    assert seen["Sunset Dental"]["city"] == "Los Angeles"
