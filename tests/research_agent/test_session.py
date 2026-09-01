import pytest

from backend.research_agent import session as session_mod
from backend.research_agent.models import RESEARCH_COMPLETE, RESEARCH_FAILED, ResearchLead

pytestmark = pytest.mark.asyncio


def _cfg():
    return {
        "research_agent_headless": True, "research_agent_max_actions_per_lead": 5,
        "research_agent_max_searches_per_lead": 2, "research_agent_max_pages_per_lead": 2,
        "research_agent_max_time_per_lead_seconds": 60, "research_agent_max_total_leads": 20,
        "research_agent_max_consecutive_failures": 3, "research_agent_max_geographic_units": 5,
        "research_agent_page_timeout_ms": 15000, "research_agent_save_to_leads": True,
    }


async def test_persisted_session_saves_results_and_merges_into_leads(clean_db, monkeypatch):
    db = clean_db
    session_id = await db.create_research_session({"niche": "dental clinics", "location": "Abbeville", "target_count": 2})

    completed_lead = ResearchLead(
        city="Abbeville", country="USA", business_name="Acme Dental",
        business_phone="5551234567", business_website="https://acme.test",
        research_status=RESEARCH_COMPLETE, confidence=0.8,
    )

    async def fake_run_research_session(niche, location, target_count, seed_businesses=None, cfg=None, on_lead_complete=None, on_action=None, on_progress=None, is_cancelled=None, already_processed=None, discovery_fallback=None):
        await on_lead_complete(completed_lead)
        return {"leads": [completed_lead], "failed_count": 0, "skipped_count": 0, "geo_tasks": [], "processed_keys": []}

    monkeypatch.setattr(session_mod, "run_research_session", fake_run_research_session)
    monkeypatch.setattr(session_mod, "get_research_config", lambda: _fake_cfg())

    await session_mod.run_research_session_persisted(session_id, "dental clinics", "Abbeville, USA", 2)

    session = await db.get_research_session(session_id)
    assert session["status"] == "COMPLETED"
    assert session["leads_found"] == 1
    assert session["leads_completed"] == 1
    assert session["leads_failed"] == 0

    results = await db.list_research_results(session_id)
    assert len(results) == 1
    assert results[0]["business_name"] == "Acme Dental"
    assert results[0]["lead_id"] is not None  # merged into the main leads table

    lead_row = await db.get_lead_by_id(results[0]["lead_id"])
    assert lead_row["business_name"] == "Acme Dental"
    assert lead_row["source"] == "BROWSER_RESEARCH_AGENT"


async def test_persisted_session_does_not_merge_failed_leads_into_leads_table(clean_db, monkeypatch):
    db = clean_db
    session_id = await db.create_research_session({"niche": "dental clinics", "location": "Abbeville", "target_count": 1})

    failed_lead = ResearchLead(business_name="Nothing Found Co", research_status=RESEARCH_FAILED, confidence=0.0)

    async def fake_run_research_session(niche, location, target_count, seed_businesses=None, cfg=None, on_lead_complete=None, on_action=None, on_progress=None, is_cancelled=None, already_processed=None, discovery_fallback=None):
        await on_lead_complete(failed_lead)
        return {"leads": [failed_lead], "failed_count": 0, "skipped_count": 0, "geo_tasks": [], "processed_keys": []}

    monkeypatch.setattr(session_mod, "run_research_session", fake_run_research_session)
    monkeypatch.setattr(session_mod, "get_research_config", lambda: _fake_cfg())

    await session_mod.run_research_session_persisted(session_id, "dental clinics", "Abbeville, USA", 1)

    results = await db.list_research_results(session_id)
    assert results[0]["lead_id"] is None  # FAILED leads never pollute the main leads table

    session = await db.get_research_session(session_id)
    assert session["leads_failed"] == 1
    assert session["leads_completed"] == 0


async def test_persisted_session_exception_marks_failed_not_stuck_running(clean_db, monkeypatch):
    db = clean_db
    session_id = await db.create_research_session({"niche": "dental clinics", "location": "Abbeville", "target_count": 1})

    async def broken_run_research_session(*a, **kw):
        raise RuntimeError("browser crashed")

    monkeypatch.setattr(session_mod, "run_research_session", broken_run_research_session)
    monkeypatch.setattr(session_mod, "get_research_config", lambda: _fake_cfg())

    await session_mod.run_research_session_persisted(session_id, "dental clinics", "Abbeville, USA", 1)

    session = await db.get_research_session(session_id)
    assert session["status"] == "FAILED"
    assert "browser crashed" in session["error_message"]


async def test_save_to_leads_false_never_touches_main_leads_table(clean_db, monkeypatch):
    db = clean_db
    session_id = await db.create_research_session({"niche": "dental clinics", "location": "Abbeville", "target_count": 1})
    completed_lead = ResearchLead(
        business_name="Acme Dental", business_phone="555", business_website="https://acme.test",
        research_status=RESEARCH_COMPLETE, confidence=0.8,
    )

    async def fake_run_research_session(niche, location, target_count, seed_businesses=None, cfg=None, on_lead_complete=None, on_action=None, on_progress=None, is_cancelled=None, already_processed=None, discovery_fallback=None):
        await on_lead_complete(completed_lead)
        return {"leads": [completed_lead], "failed_count": 0, "skipped_count": 0, "geo_tasks": [], "processed_keys": []}

    cfg_no_merge = _cfg()
    cfg_no_merge["research_agent_save_to_leads"] = False

    monkeypatch.setattr(session_mod, "run_research_session", fake_run_research_session)
    monkeypatch.setattr(session_mod, "get_research_config", lambda: _async_return(cfg_no_merge))

    await session_mod.run_research_session_persisted(session_id, "dental clinics", "Abbeville, USA", 1)

    results = await db.list_research_results(session_id)
    assert results[0]["lead_id"] is None


async def _fake_cfg():
    return _cfg()


async def _async_return(value):
    return value


# ── Resume / interruption recovery ──────────────────────────────────────────

async def test_failed_session_is_marked_resumable(clean_db, monkeypatch):
    db = clean_db
    session_id = await db.create_research_session({"niche": "dental", "location": "Akron", "target_count": 5})

    async def boom(*a, **kw):
        raise RuntimeError("browser exploded")

    monkeypatch.setattr(session_mod, "run_research_session", boom)
    monkeypatch.setattr(session_mod, "get_research_config", lambda: _fake_cfg())

    await session_mod.run_research_session_persisted(session_id, "dental", "Akron", 5)

    s = await db.get_research_session(session_id)
    assert s["status"] == "FAILED"
    assert s["resumable"] == 1
    assert "browser exploded" in s["error_message"]


async def test_resume_passes_stored_processed_keys_as_already_processed(clean_db, monkeypatch):
    db = clean_db
    import json
    session_id = await db.create_research_session({"niche": "dental", "location": "Akron", "target_count": 5})
    await db.update_research_session(session_id, {
        "status": "FAILED", "resumable": 1, "resume_count": 1,
        "processed_keys": json.dumps(["site:doneco.test", "site:twodoneco.test"]),
    })

    seen = {}

    async def capture(niche, location, target_count, seed_businesses=None, cfg=None,
                      on_lead_complete=None, on_action=None, on_progress=None,
                      is_cancelled=None, already_processed=None, discovery_fallback=None):
        seen["already_processed"] = set(already_processed or ())
        return {"leads": [], "failed_count": 0, "skipped_count": 2, "geo_tasks": [],
                "processed_keys": list(already_processed or ())}

    monkeypatch.setattr(session_mod, "run_research_session", capture)
    monkeypatch.setattr(session_mod, "get_research_config", lambda: _fake_cfg())

    await session_mod.run_research_session_persisted(session_id, "dental", "Akron", 5)

    assert seen["already_processed"] == {"site:doneco.test", "site:twodoneco.test"}
    s = await db.get_research_session(session_id)
    assert s["status"] == "COMPLETED"


async def test_reconcile_interrupted_sessions_requeues_running_session(clean_db, monkeypatch):
    db = clean_db
    session_id = await db.create_research_session({"niche": "dental", "location": "Akron", "target_count": 5})
    await db.update_research_session(session_id, {"status": "RUNNING"})

    class _Q:
        def __init__(self): self.jobs = []
        def enqueue_nowait(self, jt, payload, handler): self.jobs.append(payload); return True

    q = _Q()
    n = await session_mod.reconcile_interrupted_sessions(q)
    assert n == 1
    assert q.jobs == [{"session_id": session_id}]
    s = await db.get_research_session(session_id)
    assert s["status"] == "QUEUED"
    assert s["resume_count"] == 1


async def test_reconcile_gives_up_after_max_resumes(clean_db, monkeypatch):
    db = clean_db
    session_id = await db.create_research_session({"niche": "dental", "location": "Akron", "target_count": 5})
    await db.update_research_session(session_id, {"status": "RUNNING", "resume_count": session_mod.MAX_RESUMES})

    class _Q:
        def enqueue_nowait(self, *a, **kw): return True

    n = await session_mod.reconcile_interrupted_sessions(_Q())
    assert n == 0
    s = await db.get_research_session(session_id)
    assert s["status"] == "FAILED"
    assert s["resumable"] == 1
