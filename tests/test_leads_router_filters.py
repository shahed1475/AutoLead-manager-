"""Phase 2 — GET /api/leads gains source / source_type / research_status filters."""
import pytest
from httpx import ASGITransport, AsyncClient

from backend import database as db

pytestmark = pytest.mark.asyncio


async def _client():
    from backend.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_list_leads_filters_by_source_type(clean_db):
    await db.create_lead({"business_name": "Auto One", "phone": "5551110001", "source_type": "automation"})
    await db.create_lead({"business_name": "Manual One", "phone": "5551110002", "source_type": "manual"})
    async with await _client() as c:
        body = (await c.get("/api/leads", params={"source_type": "automation"})).json()
    assert [i["business_name"] for i in body["items"]] == ["Auto One"]


async def test_list_leads_filters_by_research_status(clean_db):
    lid = await db.create_lead({"business_name": "Researched", "phone": "5551110003"})
    await db.create_lead({"business_name": "Fresh", "phone": "5551110004"})
    await db.update_lead(lid, {"research_status": "COMPLETED"})
    async with await _client() as c:
        body = (await c.get("/api/leads", params={"research_status": "COMPLETED"})).json()
    assert [i["business_name"] for i in body["items"]] == ["Researched"]


async def test_lead_response_exposes_new_meta_fields(clean_db):
    lid = await db.create_lead({"business_name": "Fielded", "phone": "5551110005", "source_type": "manual"})
    async with await _client() as c:
        body = (await c.get(f"/api/leads/{lid}")).json()
    assert body["source_type"] == "manual"
    assert body["research_status"] == "NOT_STARTED"
    assert "email_status" in body
