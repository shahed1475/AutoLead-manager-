"""Phase 4 — research handoff: send existing leads to the Research Agent."""
import json

import pytest

from backend import database as db
from backend.research_agent import handoff

pytestmark = pytest.mark.asyncio


class _FakeQueue:
    def __init__(self):
        self.jobs = []

    def enqueue_nowait(self, jt, payload, handler):
        self.jobs.append((jt, payload))
        return True


async def _lead(**over):
    d = {"business_name": "Clinic X", "phone": "5551234000", "website": "https://clinicx.com",
         "niche": "dental clinic", "city": "Portland", "country": "USA"}
    d.update(over)
    return await db.create_lead(d)


# ── db helpers ────────────────────────────────────────────────────────────

async def test_set_leads_research_status_bulk(clean_db):
    a = await _lead()
    b = await _lead(phone="5559999999")
    await db.set_leads_research_status([a, b], "COMPLETED", session_id=7)
    for lid in (a, b):
        row = await db.get_lead_by_id(lid)
        assert row["research_status"] == "COMPLETED"
        assert row["last_research_session_id"] == 7


async def test_set_leads_research_status_respects_only_from(clean_db):
    a = await _lead()
    await db.update_lead(a, {"research_status": "COMPLETED"})
    await db.set_leads_research_status([a], "FAILED", only_from=("RESEARCHING", "QUEUED"))
    assert (await db.get_lead_by_id(a))["research_status"] == "COMPLETED"


async def test_create_research_session_stores_handoff_fields(clean_db):
    sid = await db.create_research_session({
        "niche": "dental clinic", "location": "Portland, ME", "target_count": 2,
        "mode": "handoff", "seed_businesses": [{"business_name": "A"}], "seed_lead_ids": [1, 2],
    })
    row = await db.get_research_session(sid)
    assert row["mode"] == "handoff"
    assert json.loads(row["seed_lead_ids"]) == [1, 2]
    assert json.loads(row["seed_businesses"])[0]["business_name"] == "A"


# ── handoff_leads ─────────────────────────────────────────────────────────

async def test_handoff_creates_session_and_queues_leads(clean_db):
    a = await _lead(phone="5551234001", website="https://a-clinic.com")
    b = await _lead(phone="5551234002", website="https://b-clinic.com")
    q = _FakeQueue()
    out = await handoff.handoff_leads(q, [a, b])
    assert out["queued"] == 2
    sess = await db.get_research_session(out["session_id"])
    assert sess["mode"] == "handoff"
    assert set(json.loads(sess["seed_lead_ids"])) == {a, b}
    assert len(json.loads(sess["seed_businesses"])) == 2
    for lid in (a, b):
        assert (await db.get_lead_by_id(lid))["research_status"] == "QUEUED"
    assert q.jobs and q.jobs[0][0] == "RESEARCH_AGENT"


async def test_seed_payload_carries_available_context_omits_nulls(clean_db):
    a = await _lead(phone="5551234060", website="https://a-clinic.com",
                    address="1 Main St", niche="dental clinic", city="Burlington",
                    google_place_id="ChIJ_test")
    out = await handoff.handoff_leads(_FakeQueue(), [a])
    sess = await db.get_research_session(out["session_id"])
    seed = json.loads(sess["seed_businesses"])[0]
    assert seed["niche"] == "dental clinic"
    assert seed["address"] == "1 Main St"
    assert seed["google_place_id"] == "ChIJ_test"
    assert "discovered_at" in seed
    assert "email" not in seed          # null field omitted, never a placeholder


async def test_handoff_records_submission_source(clean_db):
    a = await _lead(phone="5551234050")
    out = await handoff.handoff_leads(_FakeQueue(), [a], submission_source="lead_search_automation")
    sess = await db.get_research_session(out["session_id"])
    assert sess["submission_source"] == "lead_search_automation"
    assert (await db.get_lead_by_id(a))["research_submission_source"] == "lead_search_automation"


async def test_handoff_skips_already_active_lead(clean_db):
    a = await _lead()
    await db.update_lead(a, {"research_status": "RESEARCHING"})
    q = _FakeQueue()
    out = await handoff.handoff_leads(q, [a])
    assert out["queued"] == 0
    assert a in out["skipped"]
    assert not q.jobs


async def test_handoff_skips_excluded_lead(clean_db):
    a = await _lead(excluded_from_research=1)
    q = _FakeQueue()
    out = await handoff.handoff_leads(q, [a])
    assert out["queued"] == 0
    assert out["session_id"] is None


async def test_handoff_empty_input(clean_db):
    out = await handoff.handoff_leads(_FakeQueue(), [])
    assert out["session_id"] is None and out["queued"] == 0


# ── seed-lead status transitions driven by the persisted session ───────────

_CFG = {
    "research_agent_headless": True, "research_agent_max_actions_per_lead": 3,
    "research_agent_max_searches_per_lead": 1, "research_agent_max_pages_per_lead": 1,
    "research_agent_max_time_per_lead_seconds": 30, "research_agent_max_total_leads": 20,
    "research_agent_max_consecutive_failures": 3, "research_agent_max_geographic_units": 3,
    "research_agent_page_timeout_ms": 10000, "research_agent_save_to_leads": True,
}


async def test_seed_leads_finish_as_completed(clean_db, monkeypatch):
    from backend.research_agent import session as session_mod

    a = await _lead(website="https://done-clinic.com", phone="5551239001")
    sid = await db.create_research_session({
        "niche": "dental clinic", "location": "Portland, ME", "target_count": 1,
        "mode": "handoff",
        "seed_businesses": [{"business_name": "Clinic X", "_lead_id": a}],
        "seed_lead_ids": [a],
    })
    await db.set_leads_research_status([a], "QUEUED", session_id=sid)

    seen_status = {}

    async def fake_run(niche, location, target_count, seed_businesses=None, **kw):
        seen_status["mid"] = (await db.get_lead_by_id(a))["research_status"]
        return {"leads": [], "failed_count": 0, "skipped_count": 0, "geo_tasks": [], "processed_keys": []}

    async def _cfg():
        return dict(_CFG)

    monkeypatch.setattr(session_mod, "run_research_session", fake_run)
    monkeypatch.setattr(session_mod, "get_research_config", lambda: _cfg())

    await session_mod.run_research_session_persisted(
        sid, "dental clinic", "Portland, ME", 1,
        seed_businesses=[{"business_name": "Clinic X", "_lead_id": a}],
    )

    assert seen_status["mid"] == "RESEARCHING"          # flipped at session start
    row = await db.get_lead_by_id(a)
    assert row["research_status"] == "COMPLETED"          # flipped at session finish
    assert row["last_research_session_id"] == sid

