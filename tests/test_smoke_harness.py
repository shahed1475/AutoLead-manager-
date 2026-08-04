import pytest

pytestmark = pytest.mark.asyncio


async def test_clean_db_creates_schema_and_supports_basic_write(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Harness Test Co", "email": "hi@harnesstest.io"})
    assert lead_id

    lead = await db.get_lead_by_id(lead_id)
    assert lead["business_name"] == "Harness Test Co"


async def test_clean_db_is_actually_clean_between_tests(clean_db):
    db = clean_db
    total = await db.get_dashboard_stats()
    assert total["total_leads"] == 0
