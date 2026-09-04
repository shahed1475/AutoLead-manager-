import pytest

pytestmark = pytest.mark.asyncio


async def test_automation_state_autocreates_row_one(clean_db):
    db = clean_db
    state = await db.get_automation_state()
    assert state["id"] == 1
    assert state["status"] == "IDLE"
    assert state["current_position"] == 0
    assert state["today_count"] == 0
    assert state["total_count"] == 0


async def test_update_automation_state_allow_list(clean_db):
    db = clean_db
    await db.get_automation_state()
    assert await db.update_automation_state({"status": "RUNNING", "today_count": 5})
    assert (await db.get_automation_state())["status"] == "RUNNING"
    # unknown / non-writable key is ignored, not an error
    assert not await db.update_automation_state({"nonsense_col": 1})


async def test_queue_bulk_insert_and_paginate(clean_db):
    db = clean_db
    items = [{"position": i, "niche": "Hair Salon", "city": f"City{i}", "state": "TX"} for i in range(5)]
    assert await db.bulk_insert_automation_queue(items) == 5
    page = await db.get_automation_queue(offset=0, limit=3)
    assert [p["position"] for p in page] == [0, 1, 2]
    counts = await db.count_automation_queue_by_status()
    assert counts["PENDING"] == 5


async def test_checkpoint_progress_is_atomic(clean_db):
    db = clean_db
    await db.get_automation_state()
    await db.bulk_insert_automation_queue([{"position": 0, "niche": "n", "city": "c", "state": "s"}])
    item = (await db.get_automation_queue())[0]
    await db.checkpoint_automation_progress(
        item["id"],
        {"status": "COMPLETED", "new_leads": 3, "leads_found": 4},
        {"current_position": 1, "today_count": 3, "total_count": 3, "queue_completed": 1},
    )
    assert (await db.get_automation_queue(status="COMPLETED"))[0]["new_leads"] == 3
    state = await db.get_automation_state()
    assert state["current_position"] == 1 and state["today_count"] == 3


async def test_reset_automation_queue(clean_db):
    db = clean_db
    await db.bulk_insert_automation_queue([{"position": 0, "niche": "n", "city": "c", "state": "s"}])
    item = (await db.get_automation_queue())[0]
    await db.update_automation_queue_item(item["id"], {"status": "COMPLETED", "new_leads": 9})
    await db.reset_automation_queue()
    reset = (await db.get_automation_queue())[0]
    assert reset["status"] == "PENDING" and reset["new_leads"] == 0


async def test_automation_log_prunes_to_cap(clean_db):
    db = clean_db
    for i in range(2010):
        await db.append_automation_log("INFO", f"line {i}")
    rows = await db.get_automation_log(limit=5000)
    assert len(rows) == 2000
    assert rows[0]["message"] == "line 2009"  # newest first


async def test_create_automation_import(clean_db):
    db = clean_db
    iid = await db.create_automation_import({
        "filename": "leads.csv", "layout": "combined",
        "n_locations": 3, "n_niches": 2, "n_combinations": 6,
    })
    assert isinstance(iid, int) and iid >= 1
