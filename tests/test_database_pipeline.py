import pytest
from datetime import datetime, timedelta, timezone

pytestmark = pytest.mark.asyncio


async def test_set_lead_stage_writes_status_and_history(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Pipeline Co", "status": "REPLIED"})

    changed = await db.set_lead_stage(lead_id, "INTERESTED", "system", "SCHEDULE_MEETING recommended")

    assert changed is True
    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] == "INTERESTED"
    history = await db.get_stage_history(lead_id)
    assert len(history) == 1
    assert history[0]["from_status"] == "REPLIED"
    assert history[0]["to_status"] == "INTERESTED"
    assert history[0]["changed_by"] == "system"
    assert history[0]["reason"] == "SCHEDULE_MEETING recommended"


async def test_set_lead_stage_noop_when_same_status(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Same Stage Co", "status": "MEETING"})

    changed = await db.set_lead_stage(lead_id, "MEETING", "operator", "drag onto same column")

    assert changed is False
    history = await db.get_stage_history(lead_id)
    assert history == []


async def test_set_lead_stage_returns_false_for_missing_lead(clean_db):
    db = clean_db
    changed = await db.set_lead_stage(999999, "INTERESTED", "operator")
    assert changed is False


async def test_get_stage_history_newest_first(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "History Co", "status": "REPLIED"})

    await db.set_lead_stage(lead_id, "INTERESTED", "system", "first")
    await db.set_lead_stage(lead_id, "MEETING", "operator", "second")

    history = await db.get_stage_history(lead_id)
    assert len(history) == 2
    assert history[0]["reason"] == "second"
    assert history[1]["reason"] == "first"


async def test_get_board_leads_groups_by_stage_and_excludes_non_board_statuses(clean_db):
    db = clean_db
    pending_id  = await db.create_lead({"business_name": "New Co", "status": "PENDING"})
    sent_id     = await db.create_lead({"business_name": "Contacted Co", "status": "SENT"})
    replied_id  = await db.create_lead({"business_name": "Replied Co", "status": "REPLIED"})
    meeting_id  = await db.create_lead({"business_name": "Meeting Co", "status": "MEETING"})
    skipped_id  = await db.create_lead({"business_name": "Skipped Co", "status": "SKIPPED"})
    dnc_id      = await db.create_lead({"business_name": "DNC Co", "status": "DO_NOT_CONTACT"})

    board = await db.get_board_leads()

    board_ids = {lead["id"] for leads in board.values() for lead in leads}
    assert pending_id in board_ids
    assert sent_id in board_ids
    assert replied_id in board_ids
    assert meeting_id in board_ids
    assert skipped_id not in board_ids
    assert dnc_id not in board_ids

    new_ids = {lead["id"] for lead in board["NEW"]}
    assert pending_id in new_ids
    contacted_ids = {lead["id"] for lead in board["CONTACTED"]}
    assert sent_id in contacted_ids
    meeting_ids = {lead["id"] for lead in board["MEETING"]}
    assert meeting_id in meeting_ids


async def test_get_board_leads_includes_days_in_stage(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Aged Co", "status": "REPLIED"})

    board = await db.get_board_leads()
    replied = next(l for l in board["REPLIED"] if l["id"] == lead_id)
    assert "days_in_stage" in replied
    assert replied["days_in_stage"] >= 0


async def test_all_five_new_statuses_survive_restart_migration(clean_db):
    """Mirrors test_do_not_contact_status_survives_restart_migration from Phase 4 —
    every new status must be in the startup allow-list or it silently reverts
    to PENDING on the next process restart."""
    db = clean_db
    lead_ids = {}
    for status in ("INTERESTED", "MEETING", "PROPOSAL", "WON", "LOST"):
        lead_ids[status] = await db.create_lead({"business_name": f"{status} Co", "status": status})

    await db.init_db()  # simulates a process restart re-running migrations

    for status, lead_id in lead_ids.items():
        lead = await db.get_lead_by_id(lead_id)
        assert lead["status"] == status, f"{status} did not survive restart migration"
