import sqlite3

import pytest

from backend import database as db_module
from backend.discovery.merge_dedup import basic_validate, merge_and_save

# Note: no module-level `pytestmark = pytest.mark.asyncio` — this file mixes
# sync and async tests, and pytest.ini's asyncio_mode=auto already runs the
# async ones correctly without the marker.


async def test_new_lead_creates_and_records_provenance(clean_db):
    db = clean_db
    lead_id, is_new, reason = await db.create_or_merge_lead(
        {"business_name": "Acme Dental", "email": "info@acmedental.com", "city": "LA"},
        source="GOOGLE_MAPS", source_identifier="acmedental.com", run_id=None,
    )
    assert is_new is True
    assert reason is None
    sources = await db.get_lead_sources(lead_id)
    assert len(sources) == 1
    assert sources[0]["source"] == "GOOGLE_MAPS"


async def test_duplicate_by_email_merges_not_discards(clean_db):
    db = clean_db
    lead_id1, is_new1, _ = await db.create_or_merge_lead(
        {"business_name": "Acme Dental", "email": "info@acmedental.com"},
        source="GOOGLE_MAPS", run_id=None,
    )
    assert is_new1 is True

    # Second source finds the same business (same email) but with a phone
    # number the first source didn't have.
    lead_id2, is_new2, reason = await db.create_or_merge_lead(
        {"business_name": "Acme Dental", "email": "info@acmedental.com", "phone": "+15551234567", "website": "https://acmedental.com"},
        source="YELLOW_PAGES", run_id=None,
    )
    assert is_new2 is False
    assert reason == "email"
    assert lead_id2 == lead_id1  # same lead, not a duplicate row

    lead = await db.get_lead_by_id(lead_id1)
    assert lead["phone"] == "+15551234567"  # filled in from the merge
    assert lead["website"] == "https://acmedental.com"

    sources = await db.get_lead_sources(lead_id1)
    assert {s["source"] for s in sources} == {"GOOGLE_MAPS", "YELLOW_PAGES"}


async def test_duplicate_by_phone_merges(clean_db):
    db = clean_db
    lead_id1, _, _ = await db.create_or_merge_lead(
        {"business_name": "Bob's Salon", "phone": "5559998888"}, source="GOOGLE_MAPS", run_id=None,
    )
    lead_id2, is_new, reason = await db.create_or_merge_lead(
        {"business_name": "Bob's Salon", "phone": "5559998888", "email": "bob@salon.com"},
        source="GOOGLE_SEARCH", run_id=None,
    )
    assert is_new is False
    assert reason == "phone"
    assert lead_id2 == lead_id1
    lead = await db.get_lead_by_id(lead_id1)
    assert lead["email"] == "bob@salon.com"


async def test_duplicate_by_website_merges_and_labels_reason_correctly(clean_db):
    """Regression: a website-only match must be labeled 'website', not
    mislabeled 'name_city' just because email/phone didn't match."""
    db = clean_db
    lead_id1, _, _ = await db.create_or_merge_lead(
        {"business_name": "Website Match Co", "website": "https://websitematchco.biz"},
        source="GOOGLE_SEARCH", run_id=None,
    )
    lead_id2, is_new, reason = await db.create_or_merge_lead(
        {"business_name": "Totally Different Name LLC", "website": "https://websitematchco.biz", "phone": "5551234567"},
        source="BING_SEARCH", run_id=None,
    )
    assert is_new is False
    assert reason == "website"
    assert lead_id2 == lead_id1
    lead = await db.get_lead_by_id(lead_id1)
    assert lead["phone"] == "5551234567"  # still merged in despite the name mismatch


async def test_duplicate_by_fuzzy_name_city_merges(clean_db):
    db = clean_db
    lead_id1, _, _ = await db.create_or_merge_lead(
        {"business_name": "The Corner Cafe", "city": "Austin"}, source="GOOGLE_MAPS", run_id=None,
    )
    lead_id2, is_new, reason = await db.create_or_merge_lead(
        {"business_name": "Corner Cafe", "city": "Austin", "website": "https://cornercafe.example"},
        source="YELP", run_id=None,
    )
    assert is_new is False
    assert reason == "name_city"
    assert lead_id2 == lead_id1


async def test_existing_field_never_overwritten_on_merge(clean_db):
    db = clean_db
    lead_id, _, _ = await db.create_or_merge_lead(
        {"business_name": "Original Name Co", "email": "a@example.com", "city": "Miami"},
        source="GOOGLE_MAPS", run_id=None,
    )
    await db.create_or_merge_lead(
        {"business_name": "Different Name Co", "email": "a@example.com", "city": "Orlando"},
        source="BING_SEARCH", run_id=None,
    )
    lead = await db.get_lead_by_id(lead_id)
    # Existing non-null values win — city/business_name from the first source survive.
    assert lead["business_name"] == "Original Name Co"
    assert lead["city"] == "Miami"


async def test_merge_is_deterministic(clean_db):
    db = clean_db
    candidate_a = {"business_name": "Determin Co", "email": "d@determinco.biz"}
    candidate_b = {"business_name": "Determin Co", "email": "d@determinco.biz", "phone": "5551112222"}

    lead_id_a, _, _ = await db.create_or_merge_lead(candidate_a, source="GOOGLE_MAPS", run_id=None)
    lead_id_b, is_new, reason = await db.create_or_merge_lead(candidate_b, source="YELP", run_id=None)
    assert lead_id_a == lead_id_b
    assert is_new is False
    assert reason == "email"

    lead_after_first = await db.get_lead_by_id(lead_id_a)

    # Re-running the exact same second merge again must produce the same result.
    lead_id_c, is_new_c, reason_c = await db.create_or_merge_lead(candidate_b, source="YELP", run_id=None)
    lead_after_second = await db.get_lead_by_id(lead_id_c)
    assert lead_id_c == lead_id_a
    assert is_new_c is False
    assert reason_c == "email"
    assert lead_after_first["phone"] == lead_after_second["phone"] == "5551112222"


async def test_concurrent_insert_race_falls_back_to_merge_not_crash(clean_db, monkeypatch):
    """Simulates two concurrent Quick Search runs both finding no existing
    duplicate, then racing to insert the same email — the second insert
    hits the DB's unique-index IntegrityError. create_or_merge_lead must
    recover by re-resolving and merging, not raise."""
    db = clean_db
    real_insert_lead_row = db_module._insert_lead_row
    call_count = {"n": 0}

    async def flaky_insert_lead_row(conn, data):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Simulate: another coroutine already inserted this email
            # between our duplicate-check and our insert (via a real,
            # separately-committed row using create_lead directly — the
            # race we're modeling happens on a different connection/
            # transaction than the one create_or_merge_lead is using).
            await db_module.create_lead(dict(data))
            raise sqlite3.IntegrityError("UNIQUE constraint failed: leads.email")
        return await real_insert_lead_row(conn, data)

    monkeypatch.setattr(db_module, "_insert_lead_row", flaky_insert_lead_row)

    lead_id, is_new, reason = await db.create_or_merge_lead(
        {"business_name": "Race Co", "email": "race@raceco.biz"}, source="GOOGLE_MAPS", run_id=None,
    )
    assert is_new is False  # recovered via the race path, not a crash
    assert reason == "email"
    lead = await db.get_lead_by_id(lead_id)
    assert lead["business_name"] == "Race Co"


async def test_raw_snapshot_is_capped(clean_db):
    db = clean_db
    huge_field = "x" * 5000
    lead_id, _, _ = await db.create_or_merge_lead(
        {"business_name": "Big Snapshot Co", "email": "big@example.com", "address": huge_field},
        source="GOOGLE_MAPS", run_id=None,
    )
    sources = await db.get_lead_sources(lead_id)
    assert len(sources[0]["raw_snapshot"]) <= 2000


# ── merge_and_save (batch orchestration) ────────────────────────────────────

def test_basic_validate_rejects_missing_business_name():
    assert basic_validate({"business_name": "", "email": "a@b.com"}) is False


def test_basic_validate_rejects_no_contact_info():
    assert basic_validate({"business_name": "No Contact Co"}) is False


def test_basic_validate_accepts_website_only():
    assert basic_validate({"business_name": "Website Only Co", "website": "https://x.com"}) is True


async def test_merge_and_save_batch_reports_new_merged_rejected(clean_db):
    candidates = [
        {"business_name": "First Co", "email": "first@firstco.biz", "source": "GOOGLE_MAPS"},
        {"business_name": "First Co", "email": "first@firstco.biz", "phone": "5550001111", "source": "GOOGLE_SEARCH"},
        {"business_name": "", "source": "YELP"},  # invalid — no business_name
    ]
    stats = await merge_and_save(candidates, run_id=None, default_source="UNKNOWN")
    assert stats["new_count"] == 1
    assert stats["merged_count"] == 1
    assert stats["rejected_count"] == 1
    assert len(stats["unique_saved_ids"]) == 1
    assert stats["merge_reasons"] == {"email": 1}
