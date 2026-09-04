"""Connect handoff → Research Agent page: GET /api/research-agent/sessions."""
import pytest
from httpx import ASGITransport, AsyncClient

from backend import database as db
from backend.research_agent import handoff

pytestmark = pytest.mark.asyncio


class _FakeQueue:
    def enqueue_nowait(self, jt, payload, handler):
        return True


async def _client():
    from backend.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _discovery_session(niche="dental clinic", loc="Reno"):
    return await db.create_research_session({"niche": niche, "location": loc, "target_count": 5})


async def _handoff_session(clean_db):
    lid = await clean_db.create_lead({"business_name": "Clinic Z", "phone": "5551230000",
                                      "website": "https://clinicz.example.org", "niche": "dental clinic"})
    out = await handoff.handoff_leads(_FakeQueue(), [lid], submission_source="lead_search_automation")
    return out["session_id"]


# ── db helper ────────────────────────────────────────────────────────────

async def test_list_research_sessions_newest_first(clean_db):
    a = await _discovery_session(niche="first")
    b = await _discovery_session(niche="second")
    rows = await db.list_research_sessions(limit=10)
    assert [r["id"] for r in rows][:2] == [b, a]
    assert {"mode", "submission_source", "status", "niche", "leads_completed"} <= rows[0].keys()


async def test_list_research_sessions_filters_by_mode(clean_db):
    await _discovery_session()
    h = await _handoff_session(clean_db)
    handoffs = await db.list_research_sessions(mode="handoff")
    assert [r["id"] for r in handoffs] == [h]
    assert handoffs[0]["mode"] == "handoff"
    assert handoffs[0]["submission_source"] == "lead_search_automation"


async def test_list_research_sessions_paginates(clean_db):
    ids = [await _discovery_session(niche=f"n{i}") for i in range(5)]
    page1 = await db.list_research_sessions(limit=2, offset=0)
    page2 = await db.list_research_sessions(limit=2, offset=2)
    assert [r["id"] for r in page1] == ids[-1:-3:-1]
    assert [r["id"] for r in page2] == ids[-3:-5:-1]


# ── endpoint ─────────────────────────────────────────────────────────────

async def test_sessions_endpoint_shape(clean_db):
    await _discovery_session()
    await _handoff_session(clean_db)
    async with await _client() as c:
        body = (await c.get("/api/research-agent/sessions")).json()
    assert "sessions" in body and "has_more" in body
    assert len(body["sessions"]) == 2
    modes = {s["mode"] for s in body["sessions"]}
    assert modes == {"discovery", "handoff"}


async def test_sessions_endpoint_mode_filter(clean_db):
    await _discovery_session()
    await _handoff_session(clean_db)
    async with await _client() as c:
        body = (await c.get("/api/research-agent/sessions", params={"mode": "handoff"})).json()
    assert len(body["sessions"]) == 1 and body["sessions"][0]["mode"] == "handoff"


async def test_sessions_endpoint_has_more_flag(clean_db):
    for i in range(3):
        await _discovery_session(niche=f"n{i}")
    async with await _client() as c:
        body = (await c.get("/api/research-agent/sessions", params={"limit": 2})).json()
    assert len(body["sessions"]) == 2 and body["has_more"] is True


async def test_session_detail_exposes_origin(clean_db):
    h = await _handoff_session(clean_db)
    async with await _client() as c:
        body = (await c.get(f"/api/research-agent/{h}")).json()
    assert body["mode"] == "handoff"
    assert body["submission_source"] == "lead_search_automation"
