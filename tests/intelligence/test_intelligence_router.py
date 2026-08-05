import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


async def test_get_research_404_when_lead_missing(clean_db):
    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/leads/999999/research")
    assert resp.status_code == 404


async def test_get_research_404_when_no_profile_yet(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "No Profile Co"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/leads/{lead_id}/research")
    assert resp.status_code == 404


async def test_get_research_returns_profile_and_evidence(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Has Profile Co"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE", "industry": "SaaS"})
    await db.add_research_evidence(profile_id, [
        {"agent_name": "company_research", "field_name": "industry",
         "source_type": "ai_inference", "source_url": None, "snippet": None},
    ])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/leads/{lead_id}/research")

    assert resp.status_code == 200
    body = resp.json()
    assert body["profile"]["industry"] == "SaaS"
    assert len(body["evidence"]) == 1
