"""Phase 4 — POST /api/leads/research + per-lead research controls."""
import pytest
from httpx import ASGITransport, AsyncClient

from backend import database as db
from backend.routers import leads as leads_router

pytestmark = pytest.mark.asyncio


class _FakeQueue:
    def __init__(self):
        self.jobs = []

    def enqueue_nowait(self, jt, payload, handler):
        self.jobs.append(jt)
        return True


async def _client():
    from backend.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _lead(**over):
    d = {"business_name": "R Co", "phone": "5550007000", "website": "https://rco.example.org",
         "niche": "dental clinic", "city": "Boston"}
    d.update(over)
    return await db.create_lead(d)


async def test_bulk_research_queues_session(clean_db, monkeypatch):
    monkeypatch.setattr(leads_router, "get_queue", lambda: _FakeQueue())
    a = await _lead(phone="5550007001", website="https://a.example.org")
    b = await _lead(phone="5550007002", website="https://b.example.org")
    async with await _client() as c:
        resp = await c.post("/api/leads/research", json={"lead_ids": [a, b]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["queued"] == 2 and body["session_id"]
    for lid in (a, b):
        assert (await db.get_lead_by_id(lid))["research_status"] == "QUEUED"


async def test_single_lead_research(clean_db, monkeypatch):
    monkeypatch.setattr(leads_router, "get_queue", lambda: _FakeQueue())
    a = await _lead(phone="5550007003")
    async with await _client() as c:
        resp = await c.post(f"/api/leads/{a}/research")
    assert resp.status_code == 200
    assert (await db.get_lead_by_id(a))["research_status"] == "QUEUED"


async def test_research_503_when_queue_down(clean_db, monkeypatch):
    monkeypatch.setattr(leads_router, "get_queue", lambda: None)
    a = await _lead(phone="5550007004")
    async with await _client() as c:
        resp = await c.post("/api/leads/research", json={"lead_ids": [a]})
    assert resp.status_code == 503


async def test_exclude_from_research_toggle(clean_db):
    a = await _lead(phone="5550007005")
    async with await _client() as c:
        on = await c.post(f"/api/leads/{a}/research-exclude", json={"excluded": True})
        assert on.status_code == 200
        assert (await db.get_lead_by_id(a))["excluded_from_research"] == 1
        off = await c.post(f"/api/leads/{a}/research-exclude", json={"excluded": False})
        assert (await db.get_lead_by_id(a))["excluded_from_research"] == 0


async def test_excluded_lead_not_queued_by_bulk(clean_db, monkeypatch):
    monkeypatch.setattr(leads_router, "get_queue", lambda: _FakeQueue())
    a = await _lead(phone="5550007006", excluded_from_research=1)
    async with await _client() as c:
        resp = await c.post("/api/leads/research", json={"lead_ids": [a]})
    assert resp.json()["queued"] == 0
