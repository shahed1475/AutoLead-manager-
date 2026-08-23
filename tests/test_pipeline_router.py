import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


async def test_board_groups_leads_by_stage(clean_db):
    from backend.main import app
    db = clean_db
    await db.create_lead({"business_name": "New Co", "status": "PENDING"})
    await db.create_lead({"business_name": "Meeting Co", "status": "MEETING"})
    await db.create_lead({"business_name": "Skipped Co", "status": "SKIPPED"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/pipeline/board")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["NEW"]) == 1
    assert len(body["MEETING"]) == 1
    assert "SKIPPED" not in body


async def test_manual_stage_move_writes_history(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Movable Co", "status": "REPLIED"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/leads/{lead_id}/stage", json={"to_status": "INTERESTED", "reason": "manual drag"})

    assert resp.status_code == 200
    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] == "INTERESTED"
    history = await db.get_stage_history(lead_id)
    assert history[0]["changed_by"] == "operator"
    assert history[0]["reason"] == "manual drag"


async def test_manual_stage_move_400_for_do_not_contact_lead(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Suppressed Co", "status": "DO_NOT_CONTACT"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/leads/{lead_id}/stage", json={"to_status": "MEETING"})

    assert resp.status_code == 400
    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] == "DO_NOT_CONTACT"


async def test_manual_stage_move_400_for_non_board_status(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Bad Target Co", "status": "REPLIED"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/leads/{lead_id}/stage", json={"to_status": "ENRICHED"})

    assert resp.status_code == 400
    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] == "REPLIED"


async def test_manual_stage_move_404_for_missing_lead(clean_db):
    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/leads/999999/stage", json={"to_status": "MEETING"})
    assert resp.status_code == 404


async def test_stage_history_endpoint_returns_newest_first(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "History Endpoint Co", "status": "REPLIED"})
    await db.set_lead_stage(lead_id, "INTERESTED", "system", "first")
    await db.set_lead_stage(lead_id, "MEETING", "operator", "second")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/leads/{lead_id}/stage-history")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["reason"] == "second"
