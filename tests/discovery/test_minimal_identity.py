"""Phase A — preserve incomplete businesses (spec §8, §25): MINIMAL identity tier."""
import pytest

from backend import database as db
from backend.discovery.merge_dedup import basic_validate, discovery_status_for, merge_and_save


# ── basic_validate: relaxed rule ──────────────────────────────────────────

def test_keeps_business_with_name_and_location_only():
    assert basic_validate({"business_name": "ABC Dental Clinic",
                           "niche": "dental clinic", "city": "Vermont"}) is True


def test_keeps_business_with_name_and_address_only():
    assert basic_validate({"business_name": "ABC Dental", "address": "1 Main St, Burlington"}) is True


def test_still_rejects_bare_name_with_no_signal():
    assert basic_validate({"business_name": "ABC Dental"}) is False


def test_still_rejects_missing_name():
    assert basic_validate({"business_name": "", "phone": "5551112222"}) is False


# ── discovery_status classification ──────────────────────────────────────

def test_full_when_any_contact_field_present():
    assert discovery_status_for({"business_name": "X", "phone": "5551112222"}) == "FULL"
    assert discovery_status_for({"business_name": "X", "website": "https://x.com"}) == "FULL"


def test_minimal_when_only_name_and_context():
    assert discovery_status_for({"business_name": "X", "city": "Reno", "niche": "dental"}) == "MINIMAL"


# ── merge_and_save persists discovery_status + upgrades MINIMAL -> FULL ───

async def test_minimal_lead_is_saved(clean_db):
    stats = await merge_and_save(
        [{"business_name": "Name Only Dental", "niche": "dental clinic", "city": "Burlington",
          "source": "GOOGLE_MAPS"}],
        run_id=None, default_source="GOOGLE_MAPS",
    )
    assert stats["new_count"] == 1
    lead = await db.get_lead_by_id(stats["unique_saved_ids"][0])
    assert lead["discovery_status"] == "MINIMAL"


async def test_later_contact_info_upgrades_minimal_to_full(clean_db):
    s1 = await merge_and_save(
        [{"business_name": "Upgrade Dental", "niche": "dental clinic", "city": "Burlington",
          "source": "GOOGLE_MAPS"}],
        run_id=None, default_source="GOOGLE_MAPS",
    )
    lid = s1["unique_saved_ids"][0]
    assert (await db.get_lead_by_id(lid))["discovery_status"] == "MINIMAL"

    await merge_and_save(
        [{"business_name": "Upgrade Dental", "niche": "dental clinic", "city": "Burlington",
          "phone": "8025551234", "source": "YELLOW_PAGES"}],
        run_id=None, default_source="YELLOW_PAGES",
    )
    lead = await db.get_lead_by_id(lid)
    assert lead["discovery_status"] == "FULL"
    assert lead["phone"] == "8025551234"


async def test_minimal_lead_excluded_from_outreach(clean_db):
    from backend.scoring.lead_scorer import filter_leads_for_outreach
    dc = clean_db
    minimal = await dc.create_lead({"business_name": "Cold Minimal", "city": "Reno",
                                    "discovery_status": "MINIMAL", "status": "SCORED",
                                    "score": 75, "score_label": "HOT"})
    full = await dc.create_lead({"business_name": "Warm Full", "city": "Reno", "phone": "7751112222",
                                 "discovery_status": "FULL", "status": "SCORED",
                                 "score": 75, "score_label": "HOT"})
    async with dc.get_db() as conn:
        for lid in (minimal, full):
            await conn.execute(
                "INSERT INTO scores (lead_id, final_score, category) VALUES (?, 75, 'HOT')", lid)
    ids = [l["id"] for l in await filter_leads_for_outreach()]
    assert minimal not in ids
    assert full in ids
