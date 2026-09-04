"""
test_database_email_campaigns.py — Checkpoint 3A: the additive schema + pure
persistence layer for the PopupGenix Email Campaign module.

These tests only exercise database.py. No sending, no n8n, no routers.
"""
import json

import pytest

pytestmark = pytest.mark.asyncio


# ── schema exists / is additive ─────────────────────────────────────────────

async def test_email_campaign_tables_created(clean_db):
    db = clean_db
    async with db.get_db() as conn:
        rows = await conn.fetch(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'email_campaign%'"
        )
    names = {r["name"] for r in rows}
    assert names == {"email_campaigns", "email_campaign_leads", "email_campaign_runs",
                     "email_campaign_activity"}


async def test_existing_tables_untouched(clean_db):
    """The new schema block must not drop or alter pre-existing tables."""
    db = clean_db
    async with db.get_db() as conn:
        rows = await conn.fetch("SELECT name FROM sqlite_master WHERE type='table'")
    names = {r["name"] for r in rows}
    for required in ("leads", "campaigns", "replies", "reply_inbox", "app_settings",
                     "campaign_log", "messages", "automation_state"):
        assert required in names


async def test_init_db_is_idempotent(clean_db):
    """Re-running init_db (as the app does on every restart) must not error."""
    db = clean_db
    await db.init_db()
    await db.init_db()
    campaigns = await db.list_email_campaigns()
    assert campaigns == []


# ── email_campaigns CRUD ────────────────────────────────────────────────────

async def test_create_and_get_email_campaign_defaults(clean_db):
    db = clean_db
    cid = await db.create_email_campaign({"name": "Dental Outreach — Texas"})
    camp = await db.get_email_campaign(cid)
    assert camp["name"] == "Dental Outreach — Texas"
    assert camp["status"] == "DRAFT"
    assert camp["test_mode"] == 1
    assert camp["test_recipient"] == "shahedalfahad20@gmail.com"
    assert camp["ai_enabled"] == 1
    assert camp["total_leads"] == 0


async def test_create_email_campaign_with_config(clean_db):
    db = clean_db
    cid = await db.create_email_campaign({
        "name": "C", "description": "d", "test_mode": True,
        "config": {"ollama_model": "qwen3:4b", "max_email_length": 600},
    })
    camp = await db.get_email_campaign(cid)
    assert json.loads(camp["config_json"])["ollama_model"] == "qwen3:4b"


async def test_update_email_campaign_status_and_counts(clean_db):
    db = clean_db
    cid = await db.create_email_campaign({"name": "C"})
    ok = await db.update_email_campaign(cid, {"status": "RUNNING", "sent_count": 5})
    assert ok is True
    camp = await db.get_email_campaign(cid)
    assert camp["status"] == "RUNNING"
    assert camp["sent_count"] == 5
    assert camp["updated_at"] is not None


async def test_update_email_campaign_ignores_unknown_fields(clean_db):
    db = clean_db
    cid = await db.create_email_campaign({"name": "C"})
    ok = await db.update_email_campaign(cid, {"totally_made_up": 1})
    assert ok is False  # nothing writable → no-op


async def test_list_email_campaigns_newest_first(clean_db):
    db = clean_db
    a = await db.create_email_campaign({"name": "A"})
    b = await db.create_email_campaign({"name": "B"})
    lst = await db.list_email_campaigns()
    assert [c["id"] for c in lst] == [b, a]


async def test_delete_email_campaign_cascades(clean_db):
    db = clean_db
    cid = await db.create_email_campaign({"name": "C"})
    await db.bulk_insert_email_campaign_leads(cid, [
        {"lead_key": f"{cid}::a@x.com", "email": "a@x.com", "raw": {"email": "a@x.com"}},
    ])
    await db.create_email_campaign_run(cid, "run-1")
    assert await db.delete_email_campaign(cid) is True
    assert await db.get_email_campaign(cid) is None
    assert await db.get_email_campaign_leads(cid) == []
    assert await db.list_email_campaign_runs(cid) == []


# ── email_campaign_leads ────────────────────────────────────────────────────

async def test_build_lead_key(clean_db):
    db = clean_db
    assert db.build_email_campaign_lead_key(7, "  A@X.COM ") == "7::a@x.com"
    k = db.build_email_campaign_lead_key(7, "", "Riley", "Poe", "Poe Dental", 3)
    assert k == "7::noemail::riley-poe-poe-dental::3"
    assert db.build_email_campaign_lead_key(7, None, "", "", "", 1) == "7::noemail::unknown::1"


async def test_bulk_insert_dedupes_on_campaign_leadkey(clean_db):
    db = clean_db
    cid = await db.create_email_campaign({"name": "C"})
    rows = [
        {"lead_key": f"{cid}::a@x.com", "email": "A@X.com", "first_name": "Al",
         "raw": {"email": "A@X.com"}, "status": "VALIDATED"},
        {"lead_key": f"{cid}::a@x.com", "email": "a@x.com", "first_name": "Al",
         "raw": {"email": "a@x.com"}, "status": "VALIDATED"},  # duplicate lead_key
        {"lead_key": f"{cid}::b@x.com", "email": "b@x.com", "raw": {"email": "b@x.com"},
         "status": "VALIDATED"},
    ]
    inserted = await db.bulk_insert_email_campaign_leads(cid, rows)
    assert inserted == 2
    leads = await db.get_email_campaign_leads(cid)
    assert {l["email"] for l in leads} == {"a@x.com", "b@x.com"}


async def test_provided_body_round_trips_verbatim(clean_db):
    db = clean_db
    cid = await db.create_email_campaign({"name": "C"})
    body = "Hi Riley,\n\nExact text — с юникодом & <tags>.  Trailing spaces .   \n- Shahed"
    await db.bulk_insert_email_campaign_leads(cid, [{
        "lead_key": f"{cid}::r@x.com", "email": "r@x.com", "raw": {"email": "r@x.com", "body": body},
        "body_source": "provided", "provided_body": body, "status": "VALIDATED",
    }])
    lead = await db.get_email_campaign_lead(cid, f"{cid}::r@x.com")
    assert lead["body_source"] == "provided"
    assert lead["provided_body"] == body  # byte-for-byte


async def test_update_lead_status_and_count_by_status(clean_db):
    db = clean_db
    cid = await db.create_email_campaign({"name": "C"})
    await db.bulk_insert_email_campaign_leads(cid, [
        {"lead_key": f"{cid}::a@x.com", "email": "a@x.com", "raw": {}, "status": "VALIDATED"},
        {"lead_key": f"{cid}::b@x.com", "email": "b@x.com", "raw": {}, "status": "VALIDATED"},
        {"lead_key": f"{cid}::c", "email": None, "raw": {}, "status": "MISSING_EMAIL"},
    ])
    await db.update_email_campaign_lead(cid, f"{cid}::a@x.com", {
        "status": "SENT", "message_id": "abc123", "sent_at": "2026-08-31T00:00:00",
        "idempotency_key": f"{cid}:{cid}::a@x.com",
    })
    counts = await db.count_email_campaign_leads_by_status(cid)
    assert counts == {"SENT": 1, "VALIDATED": 1, "MISSING_EMAIL": 1}
    a = await db.get_email_campaign_lead(cid, f"{cid}::a@x.com")
    assert a["status"] == "SENT" and a["message_id"] == "abc123"


async def test_recount_email_campaign(clean_db):
    db = clean_db
    cid = await db.create_email_campaign({"name": "C"})
    await db.bulk_insert_email_campaign_leads(cid, [
        {"lead_key": f"{cid}::a", "email": "a@x.com", "raw": {}, "status": "SENT"},
        {"lead_key": f"{cid}::b", "email": "b@x.com", "raw": {}, "status": "SEND_FAILED"},
        {"lead_key": f"{cid}::c", "email": None, "raw": {}, "status": "MISSING_EMAIL"},
        {"lead_key": f"{cid}::d", "email": "d@x.com", "raw": {}, "status": "VALIDATED"},
    ])
    await db.recount_email_campaign(cid)
    camp = await db.get_email_campaign(cid)
    assert camp["total_leads"] == 4
    assert camp["valid_leads"] == 3          # 4 - 1 missing
    assert camp["sent_count"] == 1
    assert camp["failed_count"] == 1


# ── email_campaign_runs (idempotency) ──────────────────────────────────────

async def test_create_email_campaign_run_is_idempotent(clean_db):
    db = clean_db
    cid = await db.create_email_campaign({"name": "C"})
    r1 = await db.create_email_campaign_run(cid, "start-key-1", {"batch_size": 100})
    r2 = await db.create_email_campaign_run(cid, "start-key-1", {"batch_size": 999})
    assert r1["id"] == r2["id"]                      # same run — not a second start
    assert r2["batch_size"] == 100                   # first write wins
    runs = await db.list_email_campaign_runs(cid)
    assert len(runs) == 1


async def test_different_idempotency_key_makes_a_new_run(clean_db):
    db = clean_db
    cid = await db.create_email_campaign({"name": "C"})
    await db.create_email_campaign_run(cid, "key-a")
    await db.create_email_campaign_run(cid, "key-b")
    assert len(await db.list_email_campaign_runs(cid)) == 2


async def test_update_email_campaign_run(clean_db):
    db = clean_db
    cid = await db.create_email_campaign({"name": "C"})
    run = await db.create_email_campaign_run(cid, "k")
    ok = await db.update_email_campaign_run(run["id"], {
        "status": "SENDING", "processed_count": 10, "sent_count": 9, "failed_count": 1,
    })
    assert ok is True
    fresh = await db.get_email_campaign_run(run["id"])
    assert fresh["status"] == "SENDING" and fresh["sent_count"] == 9


# ── feature flag default ───────────────────────────────────────────────────

async def test_feature_flag_defaults_off(clean_db):
    from backend.config import Settings
    assert Settings().email_campaigns_enabled is False
