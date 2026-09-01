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
