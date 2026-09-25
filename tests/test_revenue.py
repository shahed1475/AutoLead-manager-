"""Real revenue: only money recorded on won deals, never replies × a guess."""
import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


def _client():
    from backend.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _lead(db, name, status="PENDING", source="GOOGLE_MAPS", **extra):
    lid = await db.create_lead({"business_name": name, "phone": name, "source": source, **extra})
    if status != "PENDING":
        await db.update_lead(lid, {"status": status})
    return lid


async def test_a_reply_is_not_revenue(clean_db):
    await _lead(clean_db, "Replied Co", status="REPLIED")
    s = await clean_db.get_dashboard_stats()
    assert s["replied"] == 1
    assert s["revenue_won"] == 0 and s["estimated_revenue"] == 0 and s["deals_won"] == 0
    assert s["currency"] == "USD"


async def test_deal_value_and_won_stamp(clean_db):
    lid = await _lead(clean_db, "Sky Dental", status="PROPOSAL")
    async with _client() as c:
        r = await c.put(f"/api/leads/{lid}/deal", json={"deal_value": 1200})
        assert r.status_code == 200 and r.json()["deal_value"] == 1200
        assert (await c.put(f"/api/leads/{lid}/deal", json={"deal_value": -5})).status_code == 422
        won = await c.post(f"/api/leads/{lid}/stage", json={"to_status": "WON", "deal_value": 1500})
        assert won.status_code == 200 and won.json()["deal_value"] == 1500 and won.json()["won_at"]
        back = await c.post(f"/api/leads/{lid}/stage", json={"to_status": "PROPOSAL"})
        assert back.json()["won_at"] is None and back.json()["deal_value"] == 1500   # value kept as the offer
        await c.post(f"/api/leads/{lid}/stage", json={"to_status": "WON"})
        cleared = await c.put(f"/api/leads/{lid}/deal", json={"deal_value": None})
        assert cleared.json()["deal_value"] is None


async def test_opted_out_lead_still_refused(clean_db):
    lid = await _lead(clean_db, "Stop Co", status="DO_NOT_CONTACT")
    async with _client() as c:
        r = await c.post(f"/api/leads/{lid}/stage", json={"to_status": "WON", "deal_value": 100})
    assert r.status_code == 400
    assert (await clean_db.get_lead_by_id(lid))["deal_value"] is None


async def test_stats_from_recorded_deals(clean_db):
    db = clean_db
    async with _client() as c:
        for name, value, src in (("A", 1000, "GOOGLE_MAPS"), ("B", 500, "GOOGLE_MAPS"), ("C", 0, "CSV_IMPORT")):
            lid = await _lead(db, name, status="PROPOSAL", source=src)
            await c.post(f"/api/leads/{lid}/stage", json={"to_status": "WON", "deal_value": value})
        open_id = await _lead(db, "Open", status="MEETING")
        await c.put(f"/api/leads/{open_id}/deal", json={"deal_value": 800})
        await _lead(db, "Unvalued", status="PROPOSAL")
        lost = await _lead(db, "Lost", status="PROPOSAL")
        await c.put(f"/api/leads/{lost}/deal", json={"deal_value": 900})
        await c.post(f"/api/leads/{lost}/stage", json={"to_status": "LOST"})
        await db.upsert_setting("revenue_currency", "BDT")

        s = await db.get_dashboard_stats()
        assert (s["revenue_won"], s["deals_won"], s["avg_won_deal"]) == (1500, 3, 500)
        assert (s["open_pipeline_value"], s["open_deals"]) == (800, 2)   # Meeting (valued) + Proposal (not valued yet)
        assert s["win_rate"] == 75.0 and s["currency"] == "BDT"

        rev = (await c.get("/api/stats/revenue")).json()
        assert rev["currency"] == "BDT" and rev["won_total"] == 1500
        assert rev["by_source"][0] == {"source": "GOOGLE_MAPS", "deals": 2, "revenue": 1500}
        assert {w["business_name"] for w in rev["recent_wins"]} == {"A", "B", "C"}

        results = (await c.get("/api/stats/results?days=7")).json()
        assert results["current"]["revenue_won"] == 1500 and results["previous"]["revenue_won"] == 0


async def test_currency_setting_is_validated(clean_db):
    async with _client() as c:
        assert (await c.put("/api/settings", json={"key": "revenue_currency", "value": "BDT"})).status_code == 200
        assert (await c.put("/api/settings", json={"key": "revenue_currency", "value": "XYZ"})).status_code == 422
        assert (await c.put("/api/settings", json={"key": "avg_deal_value", "value": "-3"})).status_code == 422
