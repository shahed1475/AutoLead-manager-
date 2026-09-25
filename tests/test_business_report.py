"""Business report: leads list freshness helpers (run filter, audit score),
technology detection, contacts from research, and batch audits."""
import json

import pytest
from httpx import ASGITransport, AsyncClient

from backend.audit import lead_audit as la
from backend.enrichment import technology

pytestmark = pytest.mark.asyncio


def _client():
    from backend.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _run_with(db, lead_ids):
    async with db.transaction() as tx:
        return await tx.fetchval(
            "INSERT INTO lead_runs (niche, location, target_count, steps, status, lead_ids) "
            "VALUES ('Dental', 'NYC', 5, '[\"collect\"]', 'COMPLETED', ?) RETURNING id", json.dumps(lead_ids))


# ── Leads list ────────────────────────────────────────────────────────────

async def test_list_filters_to_one_find_leads_run(clean_db):
    a = await clean_db.create_lead({"business_name": "A", "phone": "1"})
    b = await clean_db.create_lead({"business_name": "B", "phone": "2"})
    await clean_db.create_lead({"business_name": "C", "phone": "3"})
    run = await _run_with(clean_db, [a, b])
    empty = await _run_with(clean_db, [])
    async with _client() as c:
        got = (await c.get(f"/api/leads?run_id={run}")).json()
        assert got["total"] == 2 and {l["id"] for l in got["items"]} == {a, b}
        assert (await c.get(f"/api/leads?run_id={empty}")).json()["total"] == 0
        assert (await c.get("/api/leads?run_id=99999")).json()["total"] == 0
        assert (await c.get("/api/leads")).json()["total"] == 3


async def test_list_carries_latest_audit_score(clean_db):
    a = await clean_db.create_lead({"business_name": "A", "phone": "1"})
    await clean_db.save_lead_audit(a, 40, "[]")
    await clean_db.save_lead_audit(a, 85, "[]")
    async with _client() as c:
        [row] = (await c.get("/api/leads")).json()["items"]
    assert row["audit_score"] == 85


# ── Technology ────────────────────────────────────────────────────────────

WP_PAGE = (
    "<link rel='stylesheet' href='/wp-content/plugins/elementor/assets/css/frontend.min.css'>"
    "<script async src='https://www.googletagmanager.com/gtag/js?id=G-ABC123'></script>"
    "<script>fbq('init', '123');</script>"
    "<script src='https://embed.tawk.to/abc/default'></script>"
    "<a href='https://calendly.com/clinic'>Book</a>"
    "<meta name='generator' content='WordPress 6.5'>"
)


def test_detects_stack_with_evidence():
    found = {t["name"]: t for t in technology.detect(WP_PAGE)}
    assert {"WordPress", "Elementor", "Google Analytics", "Meta Pixel", "Tawk.to", "Calendly"} <= set(found)
    assert found["WordPress"]["category"] == "Website platform"
    assert found["Meta Pixel"]["category"] == "Analytics & ads"
    assert "fbq(" in found["Meta Pixel"]["evidence"]


def test_plain_page_has_no_technology():
    assert technology.detect("<html><body><p>Hello</p></body></html>") == []
    assert technology.detect(None) == []


# ── Report contacts ───────────────────────────────────────────────────────

async def _research(db, lead_id, **over):
    async with db.transaction() as tx:
        sid = await tx.fetchval("INSERT INTO lead_research_sessions (niche, location, target_count) VALUES ('d', 'x', 1) RETURNING id")
        row = {"session_id": sid, "lead_id": lead_id, "business_phone": "+12125550100",
               "business_email": None, "business_email_status": "SECURE_WEB_FORM",
               "management_contact_name": "Rhieu Garrett", "management_title": "Founder",
               "management_phone": "+12125550100", "management_email": None,
               "management_email_status": "NOT_FOUND_AFTER_SEARCH", "research_status": "COMPLETE"}
        row.update(over)
        cols = ", ".join(row)
        rid = await tx.fetchval(f"INSERT INTO lead_research_results ({cols}) VALUES ({', '.join('?' * len(row))}) RETURNING id",
                                *row.values())
        await tx.execute("INSERT INTO lead_research_decision_makers (result_id, name, title, source_url, is_primary) "
                         "VALUES (?, 'Rhieu Garrett', 'Founder', 'https://sky.example/about', 1)", rid)
        await tx.execute("INSERT INTO lead_research_decision_makers (result_id, name, title, source_url) "
                         "VALUES (?, 'Susanna', 'Office Manager', 'https://sky.example/team')", rid)


async def test_report_shows_people_from_research(clean_db, monkeypatch):
    lid = await clean_db.create_lead({"business_name": "Sky Dental", "website": "https://sky.example",
                                      "phone": "+12125550100", "address": "1 Main St, New York"})
    await _research(clean_db, lid)

    async def fake_analyze(url, timeout=10):
        return {"url": url, "has_ssl": True, "error": None, "raw_html": WP_PAGE, "page_title": "Sky",
                "meta_description": "", "has_contact_form": True, "has_phone_on_page": True,
                "cta_buttons": [], "social_media_links": ["facebook"], "has_whatsapp_link": False,
                "has_booking_link": True, "social_profiles": ["https://facebook.com/sky"],
                "emails_on_page": [], "phones_on_page": ["+12125550100"], "word_count": 900,
                "image_count": 12, "all_headings": ["Welcome"], "technology": technology.detect(WP_PAGE)}
    monkeypatch.setattr(la, "analyze_website", fake_analyze)

    r = await la.build_audit(lid)
    assert r["business"]["address"] == "1 Main St, New York"
    c = r["contacts"]
    assert c["researched"] is True
    assert [p["name"] for p in c["people"]] == ["Rhieu Garrett", "Susanna"]
    assert c["people"][0]["is_primary"] and c["people"][0]["source_url"] == "https://sky.example/about"
    assert c["primary"]["phone"] == "+12125550100" and c["primary"]["phone_is_business_line"] is True
    assert "not published" in c["primary"]["email_note"].lower()
    assert c["business_email_note"].lower().startswith("uses a contact form")
    assert {t["name"] for t in r["details"]["technology"]} >= {"WordPress", "Calendly"}
    assert r["details"]["social_profiles"] == ["https://facebook.com/sky"]


async def test_report_without_research_says_so(clean_db, monkeypatch):
    lid = await clean_db.create_lead({"business_name": "Plain Co"})
    r = await la.build_audit(lid)
    assert r["contacts"] == {"researched": False, "people": [], "primary": None,
                             "business_email": None, "business_email_note": None}
    assert r["details"]["technology"] == []


# ── Batch ─────────────────────────────────────────────────────────────────

async def test_batch_audits_every_lead_once(clean_db, monkeypatch):
    ids = [await clean_db.create_lead({"business_name": f"L{i}", "phone": str(i)}) for i in range(3)]
    await la.run_batch(ids)
    assert la.batch_status() == {"running": False, "done": 3, "total": 3, "failed": 0}
    for i in ids:
        assert (await clean_db.get_latest_lead_audit(i)) is not None
    async with _client() as c:
        r = await c.post("/api/leads/audit-batch", json={"lead_ids": ids[:1]})
        assert r.status_code == 202 and r.json()["total"] == 1
        assert (await c.post("/api/leads/audit-batch", json={"lead_ids": []})).status_code == 422
        progress = await c.get("/api/leads/audit-batch")
        assert progress.status_code == 200 and set(progress.json()) == {"running", "done", "total", "failed"}
