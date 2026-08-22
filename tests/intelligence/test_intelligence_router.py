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


# ─────────────────────────────────────────────────────────────────────────────
# Pain points / opportunities / evidence / analyze endpoints
# ─────────────────────────────────────────────────────────────────────────────

async def test_get_intelligence_404_when_lead_missing(clean_db):
    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/leads/999999/intelligence")
    assert resp.status_code == 404


async def test_get_intelligence_404_when_no_profile_yet(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "No Profile Co"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/leads/{lead_id}/intelligence")
    assert resp.status_code == 404


async def test_get_intelligence_returns_combined_payload(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Combined Co"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE", "industry": "Dental"})
    await db.replace_pain_points(profile_id, [
        {"title": "No SSL", "confidence": 0.9, "classification": "observed"},
    ])
    await db.replace_business_opportunities(profile_id, [
        {"area": "Appointment Scheduling", "title": "Make it easier", "confidence": 0.9},
    ])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/leads/{lead_id}/intelligence")

    assert resp.status_code == 200
    body = resp.json()
    assert body["profile"]["industry"] == "Dental"
    assert len(body["pain_points"]) == 1
    assert len(body["business_opportunities"]) == 1
    assert "evidence" in body


async def test_get_pain_points_endpoint(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "PP Co"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    await db.replace_pain_points(profile_id, [{"title": "X", "confidence": 0.5}])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/leads/{lead_id}/pain-points")

    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_get_opportunities_endpoint(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Opp Co"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    await db.replace_business_opportunities(profile_id, [{"area": "X", "title": "Y", "confidence": 0.5}])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/leads/{lead_id}/opportunities")

    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_get_evidence_endpoint(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Evidence Co"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    await db.add_research_evidence(profile_id, [
        {"agent_name": "pain_point", "field_name": "x", "source_type": "heuristic",
         "source_url": None, "snippet": "s"},
    ])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/leads/{lead_id}/evidence")

    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_analyze_pain_points_400_when_research_not_done(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Not Done Co"})
    await db.upsert_company_profile(lead_id, {"status": "RESEARCHING"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/leads/{lead_id}/pain-points/analyze")

    assert resp.status_code == 400


async def test_analyze_pain_points_success(clean_db, monkeypatch):
    import backend.routers.intelligence as intelligence_router
    from backend.main import app

    db = clean_db
    lead_id = await db.create_lead({"business_name": "Ready Co", "website": "https://readyco.io"})
    await db.upsert_company_profile(lead_id, {"status": "DONE"})

    async def fake_run_pain_point_analysis(lead):
        return {"lead_id": lead_id, "status": "DONE", "pain_points_found": 2, "opportunities_found": 1}

    monkeypatch.setattr(intelligence_router, "run_pain_point_analysis", fake_run_pain_point_analysis)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/leads/{lead_id}/pain-points/analyze")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "DONE"
    assert body["pain_points_found"] == 2
