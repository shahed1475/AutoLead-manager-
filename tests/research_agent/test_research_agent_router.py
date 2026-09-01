import pytest
from httpx import ASGITransport, AsyncClient

from backend.routers import research_agent as ra_router_module
from backend.routers.research_agent import _compose_location
from backend.models import ResearchAgentStartRequest

# Note: no module-level `pytestmark = pytest.mark.asyncio` — this file mixes
# sync and async tests, and pytest.ini's asyncio_mode=auto already runs the
# async ones correctly without the marker.


class _FakeQueue:
    def __init__(self, full=False):
        self.full = full
        self.enqueued = []

    def enqueue_nowait(self, job_type, payload, handler):
        if self.full:
            return False
        self.enqueued.append((job_type, payload))
        return True


# ── _compose_location ────────────────────────────────────────────────────

def test_compose_location_prefers_city():
    req = ResearchAgentStartRequest(niche="dental clinics", city="Abbeville", state="LA", country="USA")
    assert _compose_location(req) == "Abbeville, LA, USA"


def test_compose_location_city_only():
    req = ResearchAgentStartRequest(niche="dental clinics", city="Abbeville")
    assert _compose_location(req) == "Abbeville"


def test_compose_location_falls_back_to_location_without_appending_country():
    """Regression: appending country to a broad-scope 'location' (e.g.
    'California') would inject a comma and make the geo planner's
    single-city heuristic misclassify a whole state as one specific city."""
    req = ResearchAgentStartRequest(niche="dental clinics", location="California", country="USA")
    assert _compose_location(req) == "California"


def test_compose_location_falls_back_to_country_alone():
    req = ResearchAgentStartRequest(niche="dental clinics", country="USA")
    assert _compose_location(req) == "USA"


# ── Router ───────────────────────────────────────────────────────────────

async def test_start_research_creates_session_and_enqueues(clean_db, monkeypatch):
    monkeypatch.setattr(ra_router_module, "get_queue", lambda: _FakeQueue())
    from backend.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/research-agent/start", json={
            "niche": "dental clinics", "city": "Abbeville", "country": "USA", "target_count": 5,
        })
    assert resp.status_code == 200
    body = resp.json()
    assert "session_id" in body
    assert body["status"] == "QUEUED"


async def test_start_research_422_when_no_location_given(clean_db, monkeypatch):
    monkeypatch.setattr(ra_router_module, "get_queue", lambda: _FakeQueue())
    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/research-agent/start", json={"niche": "dental clinics"})
    assert resp.status_code == 422


async def test_start_research_503_when_queue_unavailable(clean_db, monkeypatch):
    monkeypatch.setattr(ra_router_module, "get_queue", lambda: None)
    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/research-agent/start", json={"niche": "dental clinics", "city": "Abbeville"})
    assert resp.status_code == 503


async def test_get_status_404_for_missing_session(clean_db):
    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/research-agent/999999")
    assert resp.status_code == 404


async def test_get_results_includes_evidence(clean_db):
    db = clean_db
    session_id = await db.create_research_session({"niche": "dental clinics", "location": "Abbeville", "target_count": 5})
    result_id = await db.save_research_result(
        session_id, {"business_name": "Acme Dental", "research_status": "COMPLETE", "confidence": 0.8},
        [{"field_name": "business_name", "source_type": "discovery_seed", "confidence": 0.6, "status": "FOUND"}],
    )
    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/research-agent/{session_id}/results")
    assert resp.status_code == 200
    body = resp.json()
    assert body["results_count"] == 1
    assert body["results"][0]["id"] == result_id
    assert len(body["results"][0]["evidence"]) == 1


async def test_get_results_batches_evidence_lookup_not_n_plus_1(clean_db, monkeypatch):
    """Regression: results endpoint must issue exactly one evidence query
    for the whole page, not one per result row."""
    db = clean_db
    session_id = await db.create_research_session({"niche": "dental clinics", "location": "Abbeville", "target_count": 5})
    result_ids = []
    for i in range(3):
        rid = await db.save_research_result(
            session_id, {"business_name": f"Business {i}", "research_status": "COMPLETE", "confidence": 0.5},
            [{"field_name": "business_name", "source_type": "discovery_seed", "confidence": 0.6, "status": "FOUND"}]
            if i < 2 else [],  # third result has zero evidence rows
        )
        result_ids.append(rid)

    call_count = {"n": 0}
    real_batched = db.get_research_evidence_for_results

    async def counting_batched(ids):
        call_count["n"] += 1
        return await real_batched(ids)

    monkeypatch.setattr(db, "get_research_evidence_for_results", counting_batched)

    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/research-agent/{session_id}/results")

    assert resp.status_code == 200
    body = resp.json()
    assert call_count["n"] == 1  # exactly one batched call, not one per result
    assert len(body["results"]) == 3
    assert len(body["results"][0]["evidence"]) == 1
    assert len(body["results"][1]["evidence"]) == 1
    assert body["results"][2]["evidence"] == []  # zero-evidence result still shaped correctly


async def test_get_research_evidence_for_results_groups_correctly(clean_db):
    db = clean_db
    session_id = await db.create_research_session({"niche": "dental clinics", "location": "Abbeville", "target_count": 5})
    rid1 = await db.save_research_result(session_id, {"business_name": "A"}, [
        {"field_name": "business_name", "source_type": "x", "confidence": 0.5, "status": "FOUND"},
        {"field_name": "business_phone", "source_type": "x", "confidence": 0.5, "status": "FOUND"},
    ])
    rid2 = await db.save_research_result(session_id, {"business_name": "B"}, [])

    grouped = await db.get_research_evidence_for_results([rid1, rid2])
    assert len(grouped[rid1]) == 2
    assert grouped[rid2] == []


async def test_get_research_evidence_for_results_empty_input():
    import backend.database as db
    assert await db.get_research_evidence_for_results([]) == {}


async def test_cancel_transitions_queued_to_cancelled(clean_db):
    db = clean_db
    session_id = await db.create_research_session({"niche": "dental clinics", "location": "Abbeville", "target_count": 5})
    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/research-agent/{session_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"


async def test_cancel_is_noop_on_terminal_status(clean_db):
    db = clean_db
    session_id = await db.create_research_session({"niche": "dental clinics", "location": "Abbeville", "target_count": 5})
    await db.update_research_session(session_id, {"status": "COMPLETED"})
    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/research-agent/{session_id}/cancel")
    assert resp.json()["status"] == "COMPLETED"


# ── Reconnection: /active + job outlives the page ─────────────────────────

async def _client():
    from backend.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_active_returns_null_when_no_sessions(clean_db):
    async with await _client() as client:
        resp = await client.get("/api/research-agent/active")
    assert resp.status_code == 200
    assert resp.json() is None


async def test_active_returns_running_session_after_losing_the_handle(clean_db, monkeypatch):
    """Simulates the frontend navigating away and back: the run id is gone
    from the page, but /active hands it straight back and the job was never
    touched."""
    monkeypatch.setattr(ra_router_module, "get_queue", lambda: _FakeQueue())
    async with await _client() as client:
        start = await client.post("/api/research-agent/start", json={
            "niche": "dental clinics", "city": "Akron", "target_count": 5,
        })
        session_id = start.json()["session_id"]

        # ...user navigates to Dashboard, Leads, back — no client state kept...
        active = await client.get("/api/research-agent/active")

    assert active.status_code == 200
    body = active.json()
    assert body["id"] == session_id
    assert body["status"] in ("QUEUED", "RUNNING")  # nothing cancelled it


async def test_active_returns_latest_completed_when_none_running(clean_db):
    db = clean_db
    old = await db.create_research_session({"niche": "a", "location": "X", "target_count": 1})
    await db.update_research_session(old, {"status": "COMPLETED"})
    new = await db.create_research_session({"niche": "b", "location": "Y", "target_count": 1})
    await db.update_research_session(new, {"status": "COMPLETED"})
    async with await _client() as client:
        resp = await client.get("/api/research-agent/active")
    assert resp.json()["id"] == new


# ── Cancellation keeps already-collected leads ───────────────────────────

async def test_cancel_does_not_delete_saved_results(clean_db):
    db = clean_db
    session_id = await db.create_research_session({"niche": "dental clinics", "location": "Akron", "target_count": 5})
    await db.update_research_session(session_id, {"status": "RUNNING"})
    await db.save_research_result(
        session_id, {"business_name": "Saved Co", "research_status": "COMPLETE", "confidence": 0.7}, [],
    )
    async with await _client() as client:
        await client.post(f"/api/research-agent/{session_id}/cancel")
        results = await client.get(f"/api/research-agent/{session_id}/results")
    assert results.json()["results_count"] == 1  # the lead survived the cancel


# ── Resume ───────────────────────────────────────────────────────────────

async def test_resume_rejects_non_resumable_session(clean_db):
    db = clean_db
    session_id = await db.create_research_session({"niche": "x", "location": "Y", "target_count": 5})
    await db.update_research_session(session_id, {"status": "FAILED", "resumable": 0})
    async with await _client() as client:
        resp = await client.post(f"/api/research-agent/{session_id}/resume")
    assert resp.status_code == 409


async def test_resume_requeues_resumable_session(clean_db, monkeypatch):
    db = clean_db
    fake_q = _FakeQueue()
    monkeypatch.setattr(ra_router_module, "get_queue", lambda: fake_q)
    session_id = await db.create_research_session({"niche": "x", "location": "Y", "target_count": 5})
    await db.update_research_session(session_id, {"status": "FAILED", "resumable": 1, "resume_count": 0})
    async with await _client() as client:
        resp = await client.post(f"/api/research-agent/{session_id}/resume")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "QUEUED"
    assert body["resume_count"] == 1
    assert len(fake_q.enqueued) == 1


# ── CSV export includes the full researched dataset ─────────────────────

async def test_results_csv_has_management_and_evidence_columns(clean_db):
    db = clean_db
    session_id = await db.create_research_session({"niche": "dental clinics", "location": "Akron", "target_count": 5})
    await db.save_research_result(
        session_id,
        {
            "business_name": "The Akron Dentist", "city": "Akron", "state": "OH",
            "business_phone": "+1-330-555-0100", "business_email": "smile@akrondentist.test",
            "business_website": "https://akrondentist.test",
            "management_contact_name": "Crystal", "management_phone": "+1-330-555-0100",
            "management_email": "smile@akrondentist.test", "research_status": "COMPLETE",
            "confidence": 0.7, "research_notes": "2 pages visited.",
        },
        [{"field_name": "business_email", "source_type": "website",
          "source_url": "https://akrondentist.test/contact", "confidence": 0.9, "status": "FOUND"}],
    )
    async with await _client() as client:
        resp = await client.get(f"/api/research-agent/{session_id}/results.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    text = resp.text
    header = text.splitlines()[0]
    for col in ("management_contact_name", "management_email", "business_email_status", "research_notes", "evidence_urls"):
        assert col in header
    assert "The Akron Dentist" in text
    assert "https://akrondentist.test/contact" in text  # evidence url made it into the row
