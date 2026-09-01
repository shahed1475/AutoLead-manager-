"""
test_campaign_from_search.py — Phase 5B: Lead Search -> Email Campaign handoff.

Selected global leads are pushed into a NEW campaign via
POST /api/email-campaigns/from-search. The campaign lands DRAFT; nothing is
prepared or sent; the source `leads` rows are untouched.
"""
import json
from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


def _client():
    from backend.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def enabled(clean_db):
    await clean_db.upsert_setting("email_campaigns_enabled", "true")
    return clean_db


# ── Task 1: the mapping helper ────────────────────────────────────────────

async def test_valid_email_lead_maps_to_validated():
    from backend.email_campaigns.service import _lead_row_to_campaign_row
    row = _lead_row_to_campaign_row(7, {
        "id": 42, "business_name": "Acme Dental",
        "email": "Owner@Acme.com", "phone": "+15551234567",
        "website": "https://acme.com",
    })
    assert row["status"] == "VALIDATED"
    assert row["status_detail"] == ""
    assert row["lead_key"] == "7::owner@acme.com"
    assert row["lead_id"] == 42
    assert row["email"] == "owner@acme.com"
    assert row["company"] == "Acme Dental"
    assert row["first_name"] == "" and row["last_name"] == ""
    assert row["body_source"] == "ai" and row["provided_body"] is None


async def test_missing_email_lead_maps_to_missing_email():
    from backend.email_campaigns.service import _lead_row_to_campaign_row
    row = _lead_row_to_campaign_row(3, {
        "id": 9, "business_name": "No Email Co", "email": None, "phone": "5551110000",
    })
    assert row["status"] == "MISSING_EMAIL"
    assert row["lead_key"] == "3::lead::9"
    assert row["email"] is None


async def test_malformed_email_lead_maps_to_invalid_email():
    from backend.email_campaigns.service import _lead_row_to_campaign_row
    row = _lead_row_to_campaign_row(3, {
        "id": 9, "business_name": "Bad Co", "email": "not-an-email",
    })
    assert row["status"] == "INVALID_EMAIL"
    assert row["lead_key"] == "3::lead::9"


async def test_raw_json_preserves_original_fields_and_is_serialisable():
    from backend.email_campaigns.service import _lead_row_to_campaign_row
    lead = {
        "id": 1, "business_name": "Acme", "email": "a@acme.com",
        "phone": "555", "website": "https://acme.com", "source": "GOOGLE_MAPS",
        "score": 55, "created_at": datetime(2026, 9, 1, 12, 0, 0),
    }
    row = _lead_row_to_campaign_row(2, lead)
    parsed = json.loads(row["raw_json"])   # must not raise — default=str handles datetime
    assert parsed["business_name"] == "Acme"
    assert parsed["source"] == "GOOGLE_MAPS"
    assert parsed["score"] == 55
    assert parsed["_handoff"]["lead_id"] == 1
    assert parsed["_handoff"]["source"] == "lead_search"


# ── Task 2: add_leads_from_db ─────────────────────────────────────────────

def _svc():
    from backend.email_campaigns.service import get_email_campaign_service
    return get_email_campaign_service()


async def _make_lead(db, **over):
    data = {"business_name": "Biz", "email": None, "phone": None, "website": None}
    data.update(over)
    return await db.create_lead(data)


async def test_single_valid_lead_added(enabled):
    db = enabled
    svc = _svc()
    lid = await _make_lead(db, business_name="Acme Dental", email="owner@acme.com")
    camp = await svc.create_campaign({"name": "H"})
    summary = await svc.add_leads_from_db(camp["id"], [lid])

    assert summary == {
        "total_requested": 1, "added": 1, "skipped_existing": 0,
        "missing_email": 0, "invalid_email": 0, "not_found": [],
    }
    rows = await db.get_email_campaign_leads(camp["id"])
    assert len(rows) == 1
    assert rows[0]["lead_id"] == lid
    assert rows[0]["status"] == "VALIDATED"
    assert rows[0]["company"] == "Acme Dental"
    fresh = await db.get_email_campaign(camp["id"])
    assert fresh["status"] == "DRAFT"


async def test_mixed_batch_statuses_and_counts(enabled):
    # NOTE: leads created via db.create_lead pass through _normalize_lead_fields,
    # which nulls a malformed email — so a DB-sourced lead is only ever
    # VALIDATED or MISSING_EMAIL. The INVALID_EMAIL branch of the helper is
    # covered by the pure unit test in the Task 1 block.
    db = enabled
    svc = _svc()
    good = await _make_lead(db, email="a@good.com")
    none = await _make_lead(db, email=None, phone="5550001111")
    camp = await svc.create_campaign({"name": "H"})
    summary = await svc.add_leads_from_db(camp["id"], [good, none])

    assert summary["total_requested"] == 2
    assert summary["added"] == 2
    assert summary["missing_email"] == 1
    assert summary["invalid_email"] == 0
    by_status = {r["status"] for r in await db.get_email_campaign_leads(camp["id"])}
    assert by_status == {"VALIDATED", "MISSING_EMAIL"}


async def test_raw_json_and_lead_link_persisted(enabled):
    db = enabled
    svc = _svc()
    lid = await _make_lead(db, business_name="Acme", email="a@acme.com",
                           website="https://acme.com", phone="+15551230000")
    camp = await svc.create_campaign({"name": "H"})
    await svc.add_leads_from_db(camp["id"], [lid])

    row = (await db.get_email_campaign_leads(camp["id"]))[0]
    assert row["lead_id"] == lid
    raw = json.loads(row["raw_json"])
    assert raw["business_name"] == "Acme"
    assert raw["website"] == "https://acme.com"
    assert raw["_handoff"]["lead_id"] == lid


async def test_source_lead_unchanged(enabled):
    db = enabled
    svc = _svc()
    lid = await _make_lead(db, business_name="Acme", email="a@acme.com")
    before = await db.get_lead_by_id(lid)
    camp = await svc.create_campaign({"name": "H"})
    await svc.add_leads_from_db(camp["id"], [lid])
    after = await db.get_lead_by_id(lid)
    assert dict(after) == dict(before)


async def test_re_adding_same_lead_is_skipped_not_duplicated(enabled):
    # `skipped_existing` comes from bulk_insert_email_campaign_leads' INSERT OR
    # IGNORE on (campaign_id, lead_key). The from-search endpoint always creates
    # a fresh campaign so this is only reachable on a repeat add_leads_from_db
    # call for the same campaign (a future "add to existing" caller / a retry).
    db = enabled
    svc = _svc()
    lid = await _make_lead(db, business_name="A", email="dupe@x.com")
    camp = await svc.create_campaign({"name": "H"})
    first = await svc.add_leads_from_db(camp["id"], [lid])
    second = await svc.add_leads_from_db(camp["id"], [lid])
    assert first["added"] == 1
    assert second["added"] == 0
    assert second["skipped_existing"] == 1
    assert len(await db.get_email_campaign_leads(camp["id"])) == 1


async def test_nonexistent_id_reported_not_fatal(enabled):
    db = enabled
    svc = _svc()
    lid = await _make_lead(db, email="a@acme.com")
    camp = await svc.create_campaign({"name": "H"})
    summary = await svc.add_leads_from_db(camp["id"], [lid, 999999])
    assert summary["added"] == 1
    assert summary["not_found"] == [999999]


async def test_duplicate_ids_deduped_in_total(enabled):
    db = enabled
    svc = _svc()
    lid = await _make_lead(db, email="a@acme.com")
    camp = await svc.create_campaign({"name": "H"})
    summary = await svc.add_leads_from_db(camp["id"], [lid, lid, lid])
    assert summary["total_requested"] == 1
    assert summary["added"] == 1


async def test_activity_row_written(enabled):
    db = enabled
    svc = _svc()
    lid = await _make_lead(db, email="a@acme.com")
    camp = await svc.create_campaign({"name": "H"})
    await svc.add_leads_from_db(camp["id"], [lid])
    events = [a["event"] for a in await db.get_email_campaign_activity(camp["id"])]
    assert "leads_added_from_search" in events


async def test_cannot_add_when_campaign_not_draft_or_ready(enabled):
    from backend.email_campaigns.service import EmailCampaignError
    db = enabled
    svc = _svc()
    lid = await _make_lead(db, email="a@acme.com")
    camp = await svc.create_campaign({"name": "H"})
    await db.update_email_campaign(camp["id"], {"status": "RUNNING"})
    with pytest.raises(EmailCampaignError):
        await svc.add_leads_from_db(camp["id"], [lid])
