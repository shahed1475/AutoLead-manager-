import pytest
from httpx import ASGITransport, AsyncClient

from backend.routers import discovery as discovery_router_module

pytestmark = pytest.mark.asyncio


class _FakeQueue:
    def __init__(self, full=False):
        self.full = full
        self.enqueued = []

    def enqueue_nowait(self, job_type, payload, handler):
        if self.full:
            return False
        self.enqueued.append((job_type, payload))
        return True


async def test_create_search_returns_run_id_and_queued_status(clean_db, monkeypatch):
    monkeypatch.setattr(discovery_router_module, "get_queue", lambda: _FakeQueue())
    from backend.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/discovery/search", json={
            "query": "dental clinics", "niche": "dental clinics", "city": "LA", "target_count": 10,
        })

    assert resp.status_code == 200
    body = resp.json()
    assert "run_id" in body
    assert body["status"] == "QUEUED"


async def test_create_search_503_when_queue_unavailable(clean_db, monkeypatch):
    monkeypatch.setattr(discovery_router_module, "get_queue", lambda: None)
    from backend.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/discovery/search", json={
            "query": "dental clinics", "city": "LA",
        })
    assert resp.status_code == 503


async def test_create_search_503_when_queue_full(clean_db, monkeypatch):
    monkeypatch.setattr(discovery_router_module, "get_queue", lambda: _FakeQueue(full=True))
    from backend.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/discovery/search", json={
            "query": "dental clinics", "city": "LA",
        })
    assert resp.status_code == 503


async def test_get_status_404_for_missing_run(clean_db):
    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/discovery/search/999999")
    assert resp.status_code == 404


async def test_get_status_sources_planned_is_a_list_not_a_json_string(clean_db):
    """Regression: sources_planned must round-trip as a real JSON array, not
    a raw JSON-text string the frontend would have to double-parse."""
    db = clean_db
    run_id = await db.create_discovery_run({
        "mode": "QUICK", "raw_query": "dental", "niche": "dental", "city": "LA",
        "target_count": 10, "sources_planned": ["GOOGLE_MAPS", "YELLOW_PAGES"],
    })
    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/discovery/search/{run_id}")
    body = resp.json()
    assert body["sources_planned"] == ["GOOGLE_MAPS", "YELLOW_PAGES"]
    assert isinstance(body["sources_planned"], list)


async def test_get_status_returns_run_row(clean_db, monkeypatch):
    db = clean_db
    run_id = await db.create_discovery_run({
        "mode": "QUICK", "raw_query": "dental", "niche": "dental", "city": "LA", "target_count": 10,
    })
    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/discovery/search/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "QUEUED"


async def test_get_results_returns_leads_found_by_run(clean_db):
    db = clean_db
    run_id = await db.create_discovery_run({
        "mode": "QUICK", "raw_query": "dental", "niche": "dental", "city": "LA", "target_count": 10,
    })
    lead_id, _, _ = await db.create_or_merge_lead(
        {"business_name": "Result Co", "email": "r@x.com"}, source="GOOGLE_MAPS", run_id=run_id,
    )
    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/discovery/search/{run_id}/results")
    assert resp.status_code == 200
    body = resp.json()
    assert body["results_count"] == 1
    assert body["leads"][0]["id"] == lead_id


async def test_get_results_404_for_missing_run(clean_db):
    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/discovery/search/999999/results")
    assert resp.status_code == 404


async def test_cancel_transitions_queued_to_cancelled(clean_db):
    db = clean_db
    run_id = await db.create_discovery_run({
        "mode": "QUICK", "raw_query": "dental", "niche": "dental", "city": "LA", "target_count": 10,
    })
    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/discovery/search/{run_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"


async def test_cancel_is_noop_on_terminal_status(clean_db):
    db = clean_db
    run_id = await db.create_discovery_run({
        "mode": "QUICK", "raw_query": "dental", "niche": "dental", "city": "LA", "target_count": 10,
    })
    await db.update_discovery_run(run_id, {"status": "COMPLETED"})
    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/discovery/search/{run_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "COMPLETED"  # unchanged, not reopened


# ── /search/active — reconnection after navigation/refresh ────────────────

async def test_active_search_null_when_none(clean_db):
    from backend.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/discovery/search/active")
    assert resp.status_code == 200
    assert resp.json() is None


async def test_active_search_returns_running_quick_run(clean_db):
    db = clean_db
    run_id = await db.create_discovery_run({
        "mode": "QUICK", "raw_query": "dental", "niche": "dental", "city": "Akron", "target_count": 10,
    })
    await db.update_discovery_run(run_id, {"status": "RUNNING"})
    from backend.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/discovery/search/active")
    assert resp.json()["id"] == run_id


async def test_active_search_ignores_campaign_mode_runs(clean_db):
    db = clean_db
    await db.create_discovery_run({"mode": "CAMPAIGN", "raw_query": "x", "niche": "x", "city": "Y", "target_count": 5})
    from backend.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/discovery/search/active")
    assert resp.json() is None  # CAMPAIGN planner bookkeeping is not a Quick Search to reconnect to
