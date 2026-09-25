"""Results report (this period vs the one before) and industry quick-setup."""
import re

import pytest
from httpx import ASGITransport, AsyncClient

from backend import industry_presets as ip

pytestmark = pytest.mark.asyncio


async def _at(db, sql, *args):
    async with db.transaction() as tx:
        await tx.execute(sql, *args)


async def test_results_split_by_period(clean_db):
    db = clean_db
    recent = await db.create_lead({"business_name": "Recent Co", "email": "r@co.com"})
    old = await db.create_lead({"business_name": "Old Co", "email": "o@co.com"})
    await _at(db, "UPDATE leads SET created_at = datetime('now', '-2 days'), sent_at = datetime('now', '-1 days') WHERE id = ?", recent)
    await _at(db, "UPDATE leads SET created_at = datetime('now', '-10 days'), sent_at = datetime('now', '-9 days') WHERE id = ?", old)
    await _at(db, "INSERT INTO reply_inbox (lead_id, from_email, created_at) VALUES (?, 'r@co.com', datetime('now', '-1 days'))", recent)
    await _at(db, "INSERT INTO whatsapp_messages (lead_id, chat_id, direction, body, source, created_at) "
                  "VALUES (?, '8801', 'IN', 'hi', 'inbound', datetime('now', '-3 days'))", recent)
    await _at(db, "INSERT INTO whatsapp_messages (lead_id, chat_id, direction, body, source, created_at) "
                  "VALUES (?, '8801', 'OUT', 'hello', 'campaign', datetime('now', '-3 days'))", recent)
    for status, when in (("INTERESTED", "-1 days"), ("MEETING", "-1 days"), ("WON", "-8 days")):
        await _at(db, "INSERT INTO lead_stage_history (lead_id, to_status, changed_by, created_at) "
                      "VALUES (?, ?, 'test', datetime('now', ?))", recent, status, when)

    r = await db.get_results_report(7)
    assert r["days"] == 7
    assert r["current"] == {"leads_found": 1, "messages_sent": 2, "replies": 2,
                            "interested": 1, "meetings": 1, "won": 0}
    assert r["previous"] == {"leads_found": 1, "messages_sent": 1, "replies": 0,
                             "interested": 0, "meetings": 0, "won": 1}


async def test_results_endpoint_bounds(clean_db):
    from backend.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/api/stats/results?days=30")).json()["days"] == 30
        assert (await c.get("/api/stats/results?days=0")).status_code == 422


def test_presets_have_what_the_wizard_needs():
    assert len(ip.PRESETS) >= 6
    for p in ip.PRESETS:
        assert p["id"] and p["label"] and p["services"] and p["customers"] and p["problems"]
    # labels match the client onboarding's sector list, so a chosen sector finds its preset
    assert ip.preset_for_sector("Web design & development")["id"] == "web_design"
    assert ip.preset_for_sector("Something else") is None


def test_build_dna_uses_the_owner_facts_and_invents_no_claims():
    dna = ip.build_dna("web_design", "Pixel Forge", "Dhaka", ["Shopify stores", "Landing pages"])
    assert "Pixel Forge" in dna and "Dhaka" in dna and "Shopify stores" in dna
    assert "## Ideal customers" in dna and "## Why choose us" in dna
    assert not re.search(r"\$|%|guarantee|ROI|#1|best in", dna, re.I)
    with pytest.raises(ValueError):
        ip.build_dna("nope", "X", "Y", [], "")


async def test_presets_endpoint(clean_db):
    from backend.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        body = (await c.get("/api/settings/industry-presets")).json()
        assert {"id", "label", "services", "customers", "problems"} <= set(body[0])
        r = await c.post("/api/settings/industry-presets/web_design/dna",
                         json={"business_name": "Pixel Forge", "city": "Dhaka", "services": ["Landing pages"]})
        assert r.status_code == 200 and "Pixel Forge" in r.json()["dna"]
        assert (await c.post("/api/settings/industry-presets/nope/dna", json={"business_name": "X"})).status_code == 404
