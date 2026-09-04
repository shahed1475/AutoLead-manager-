"""Phase 2 — lead metadata (source_type / research_status / email_status) + list filters."""
import pytest

from backend import database as db

pytestmark = pytest.mark.asyncio


async def test_new_meta_columns_exist(clean_db):
    lead_id = await db.create_lead({"business_name": "Meta Co", "phone": "5550001111"})
    lead = await db.get_lead_by_id(lead_id)
    assert "source_type" in lead
    assert "research_status" in lead
    assert "email_status" in lead
    assert lead["research_status"] == "NOT_STARTED"   # column default


async def test_create_or_merge_lead_persists_source_type(clean_db):
    lead_id, is_new, _ = await db.create_or_merge_lead(
        {"business_name": "Auto Co", "phone": "5552223333", "source_type": "automation"},
        source="GOOGLE_MAPS", source_identifier="5552223333",
    )
    assert is_new
    assert (await db.get_lead_by_id(lead_id))["source_type"] == "automation"


async def test_get_leads_filters_by_source_type(clean_db):
    await db.create_lead({"business_name": "A", "phone": "5550000001", "source_type": "automation"})
    await db.create_lead({"business_name": "M", "phone": "5550000002", "source_type": "manual"})
    res = await db.get_leads(source_type="automation")
    assert [l["business_name"] for l in res["items"]] == ["A"]


async def test_get_leads_filters_by_research_status(clean_db):
    a = await db.create_lead({"business_name": "R1", "phone": "5550000003"})
    await db.create_lead({"business_name": "R2", "phone": "5550000004"})
    await db.update_lead(a, {"research_status": "COMPLETED"})
    res = await db.get_leads(research_status="COMPLETED")
    assert [l["business_name"] for l in res["items"]] == ["R1"]


async def test_get_leads_filters_by_source(clean_db):
    await db.create_lead({"business_name": "G", "phone": "5550000005", "source": "GOOGLE_MAPS"})
    await db.create_lead({"business_name": "Y", "phone": "5550000006", "source": "YELLOW_PAGES"})
    res = await db.get_leads(source="YELLOW_PAGES")
    assert [l["business_name"] for l in res["items"]] == ["Y"]
