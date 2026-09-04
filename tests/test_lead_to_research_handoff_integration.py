"""Whole-feature integration: a lead travels Lead Search → Handoff → Research
Agent page, via the ONE backend handoff service (research_agent.handoff.handoff_leads).

Covers both entry points converging on that service:
  A. Manual   — POST /api/leads/research  (Leads page / Manual Search / Automation "send")
  B. Automatic — enrichment.maybe_auto_handoff  (persistent auto-handoff after discovery)

…plus the edge cases the connect-spec calls for: invalid lead id, duplicate
handoff, bad submission_source, queue unavailable, deleted lead, discovery
regression, and the /sessions API surface the Research Agent page reads.

Auth boundaries are intentionally not exercised: this app is single-tenant,
SQLite-only, no RBAC (CLAUDE.md) and /api/research-agent has no auth dependency.
"""
import json

import pytest
from httpx import ASGITransport, AsyncClient

from backend import database as db
from backend.discovery import enrichment as enr
from backend.research_agent import handoff

pytestmark = pytest.mark.asyncio


class _FakeQueue:
    """Stands in for the in-process JobQueue — records enqueues, never runs them."""

    def __init__(self):
        self.jobs = []

    def enqueue_nowait(self, job_type, payload, handler):
        self.jobs.append((job_type, payload))
        return True


async def _client():
    from backend.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _lead(**over):
    d = {
        "business_name": "Bright Smile Dental",
        "phone": "5551110000",
        "website": "https://brightsmile-dental.com",
        "niche": "dental clinic",
        "city": "Portland",
        "country": "USA",
    }
    d.update(over)
    return await db.create_lead(d)


# ── A. Manual path — POST /api/leads/research ─────────────────────────────

async def test_manual_handoff_creates_visible_handoff_session(clean_db, monkeypatch):
    monkeypatch.setattr("backend.routers.leads.get_queue", lambda: _FakeQueue())
    a = await _lead(phone="5551110001", website="https://a-dental.com")
    b = await _lead(phone="5551110002", website="https://b-dental.com")

    async with await _client() as c:
        res = (await c.post("/api/leads/research", json={"lead_ids": [a, b]})).json()

    assert res["queued"] == 2
    sid = res["session_id"]

    # session stored as a handoff, keyed by the STABLE lead PKs (never index/name)
    sess = await db.get_research_session(sid)
    assert sess["mode"] == "handoff"
    assert sess["submission_source"] == "manual"
    assert set(json.loads(sess["seed_lead_ids"])) == {a, b}

    # the two leads are now QUEUED against this session
    for lid in (a, b):
        row = await db.get_lead_by_id(lid)
        assert row["research_status"] == "QUEUED"
        assert row["last_research_session_id"] == sid

    # …and the Research Agent page's session list shows it as a handoff
    async with await _client() as c:
        body = (await c.get("/api/research-agent/sessions", params={"mode": "handoff"})).json()
    ids = [s["id"] for s in body["sessions"]]
    assert sid in ids
    shown = next(s for s in body["sessions"] if s["id"] == sid)
    assert shown["mode"] == "handoff" and shown["submission_source"] == "manual"

    # direct-navigation target (?session=<id>) resolves and exposes origin
    async with await _client() as c:
        detail = (await c.get(f"/api/research-agent/{sid}")).json()
    assert detail["mode"] == "handoff"
    assert detail["submission_source"] == "manual"


async def test_manual_handoff_from_automation_source_label(clean_db, monkeypatch):
    monkeypatch.setattr("backend.routers.leads.get_queue", lambda: _FakeQueue())
    a = await _lead(phone="5551110009")
    async with await _client() as c:
        res = (await c.post(
            "/api/leads/research",
            json={"lead_ids": [a], "submission_source": "manual_from_automation"},
        )).json()
    sess = await db.get_research_session(res["session_id"])
    assert sess["submission_source"] == "manual_from_automation"


# ── B. Automatic path — enrichment.maybe_auto_handoff ─────────────────────

async def test_automatic_handoff_uses_the_same_service_and_is_visible(clean_db, monkeypatch):
    await db.upsert_setting("research_handoff_mode", "automatic")
    # real _handoff_leads → real handoff.handoff_leads; only the queue is faked
    monkeypatch.setattr(enr, "get_queue", lambda: _FakeQueue())

    hot = await _lead(phone="5551120001")
    await db.update_lead(hot, {"score": 88, "score_label": "HOT"})
    cold = await _lead(phone="5551120002")
    await db.update_lead(cold, {"score": 20, "score_label": "COLD"})

    out = await enr.maybe_auto_handoff([hot, cold])
    assert out["auto_queued"] == 1
    sid = out["auto_session_id"]

    sess = await db.get_research_session(sid)
    assert sess["mode"] == "handoff"                       # same table, same shape
    assert sess["submission_source"] == "lead_search_automation"
    assert json.loads(sess["seed_lead_ids"]) == [hot]      # only the eligible one
    assert (await db.get_lead_by_id(hot))["research_status"] == "QUEUED"
    assert (await db.get_lead_by_id(cold))["research_status"] in (None, "NOT_STARTED")

    async with await _client() as c:
        body = (await c.get("/api/research-agent/sessions", params={"mode": "handoff"})).json()
    assert sid in [s["id"] for s in body["sessions"]]


async def test_manual_and_automatic_converge_on_one_session_table(clean_db, monkeypatch):
    """Both paths write a row readable by the identical list query — no second
    implementation, no parallel table."""
    await db.upsert_setting("research_handoff_mode", "automatic")
    monkeypatch.setattr(enr, "get_queue", lambda: _FakeQueue())
    monkeypatch.setattr("backend.routers.leads.get_queue", lambda: _FakeQueue())

    m = await _lead(phone="5551130001")
    async with await _client() as c:
        r_manual = (await c.post("/api/leads/research", json={"lead_ids": [m]})).json()

    a = await _lead(phone="5551130002")
    await db.update_lead(a, {"score": 90, "score_label": "HOT"})
    r_auto = await enr.maybe_auto_handoff([a])

    handoffs = await db.list_research_sessions(mode="handoff", limit=50)
    by_id = {s["id"]: s for s in handoffs}
    assert by_id[r_manual["session_id"]]["submission_source"] == "manual"
    assert by_id[r_auto["auto_session_id"]]["submission_source"] == "lead_search_automation"


async def test_automatic_handoff_is_idempotent_across_runs(clean_db, monkeypatch):
    await db.upsert_setting("research_handoff_mode", "automatic")
    monkeypatch.setattr(enr, "get_queue", lambda: _FakeQueue())
    a = await _lead(phone="5551140001")
    await db.update_lead(a, {"score": 90, "score_label": "HOT"})

    first = await enr.maybe_auto_handoff([a])
    second = await enr.maybe_auto_handoff([a])          # lead already QUEUED
    assert first["auto_queued"] == 1
    assert second["auto_queued"] == 0
    assert second["auto_session_id"] is None


# ── Edge cases ───────────────────────────────────────────────────────────

async def test_invalid_lead_id_queues_nothing_and_creates_no_session(clean_db, monkeypatch):
    monkeypatch.setattr("backend.routers.leads.get_queue", lambda: _FakeQueue())
    async with await _client() as c:
        r = await c.post("/api/leads/research", json={"lead_ids": [99_999_999]})
    # a non-existent id is "skipped", not a hard error — the frontend toast
    # reports "nothing queued". No session, nothing enqueued.
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] is None and body["queued"] == 0
    assert body["skipped"] == [99_999_999]
    assert await db.list_research_sessions(limit=5) == []


async def test_empty_lead_list_is_422(clean_db, monkeypatch):
    monkeypatch.setattr("backend.routers.leads.get_queue", lambda: _FakeQueue())
    async with await _client() as c:
        r = await c.post("/api/leads/research", json={"lead_ids": []})
    assert r.status_code == 422


async def test_duplicate_manual_handoff_skips_second_time(clean_db, monkeypatch):
    monkeypatch.setattr("backend.routers.leads.get_queue", lambda: _FakeQueue())
    a = await _lead(phone="5551150001")
    async with await _client() as c:
        first = (await c.post("/api/leads/research", json={"lead_ids": [a]})).json()
        second = (await c.post("/api/leads/research", json={"lead_ids": [a]})).json()
    assert first["queued"] == 1
    # lead is QUEUED now → second call skips it, no new session
    assert second["queued"] == 0
    assert second["session_id"] is None
    assert a in second["skipped"]
    handoffs = await db.list_research_sessions(mode="handoff", limit=50)
    assert len(handoffs) == 1


async def test_bad_submission_source_falls_back_to_manual(clean_db, monkeypatch):
    monkeypatch.setattr("backend.routers.leads.get_queue", lambda: _FakeQueue())
    a = await _lead(phone="5551160001")
    async with await _client() as c:
        res = (await c.post(
            "/api/leads/research",
            json={"lead_ids": [a], "submission_source": "🕵️ hacker"},
        )).json()
    sess = await db.get_research_session(res["session_id"])
    assert sess["submission_source"] == "manual"


async def test_research_queue_unavailable_returns_503(clean_db, monkeypatch):
    monkeypatch.setattr("backend.routers.leads.get_queue", lambda: None)
    a = await _lead(phone="5551170001")
    async with await _client() as c:
        r = await c.post("/api/leads/research", json={"lead_ids": [a]})
    assert r.status_code == 503


async def test_automatic_handoff_queue_unavailable_is_safe(clean_db, monkeypatch):
    await db.upsert_setting("research_handoff_mode", "automatic")
    monkeypatch.setattr(enr, "get_queue", lambda: None)
    a = await _lead(phone="5551180001")
    await db.update_lead(a, {"score": 90, "score_label": "HOT"})
    out = await enr.maybe_auto_handoff([a])
    assert out["auto_queued"] == 0
    assert (await db.get_lead_by_id(a))["research_status"] in (None, "NOT_STARTED")


async def test_deleted_lead_id_is_skipped_not_crashed(clean_db):
    a = await _lead(phone="5551190001")
    await db.delete_lead(a)
    out = await handoff.handoff_leads(_FakeQueue(), [a])
    assert out["session_id"] is None
    assert out["queued"] == 0


async def test_sessions_endpoint_rejects_unknown_mode(clean_db):
    async with await _client() as c:
        r = await c.get("/api/research-agent/sessions", params={"mode": "sideways"})
    assert r.status_code == 422


# ── Regression: discovery sessions still behave ──────────────────────────

async def test_discovery_session_not_shown_as_handoff(clean_db):
    disc = await db.create_research_session(
        {"niche": "yoga studio", "location": "Austin", "target_count": 5}
    )
    handoffs = await db.list_research_sessions(mode="handoff", limit=50)
    assert disc not in [s["id"] for s in handoffs]

    disc_only = await db.list_research_sessions(mode="discovery", limit=50)
    row = next(s for s in disc_only if s["id"] == disc)
    assert row["mode"] == "discovery"
    assert row["submission_source"] is None
