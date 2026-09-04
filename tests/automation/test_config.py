import pytest

from backend.automation import config as autocfg

pytestmark = pytest.mark.asyncio


async def test_defaults_when_no_db_override(clean_db):
    cfg = await autocfg.get_automation_settings()
    assert cfg["automation_enabled"] is False
    assert cfg["automation_daily_limit"] == 500
    assert cfg["automation_start_time"] == "07:00"
    assert cfg["automation_timezone"] == "America/New_York"
    assert cfg["automation_duration_hours"] == 4
    assert cfg["automation_per_item_target"] == 40
    assert cfg["automation_max_retries"] == 2


async def test_per_item_target_is_clamped_to_max(clean_db):
    db = clean_db
    await db.upsert_setting("automation_per_item_target", "500")
    cfg = await autocfg.get_automation_settings()
    assert cfg["automation_per_item_target"] == autocfg._MAX_PER_ITEM_TARGET


async def test_per_item_target_below_one_is_clamped_up(clean_db):
    db = clean_db
    await db.upsert_setting("automation_per_item_target", "0")
    cfg = await autocfg.get_automation_settings()
    assert cfg["automation_per_item_target"] == 1


async def test_daily_limit_clamped_into_range(clean_db):
    db = clean_db
    await db.upsert_setting("automation_daily_limit", "-50")
    assert (await autocfg.get_automation_settings())["automation_daily_limit"] == 1
    await db.upsert_setting("automation_daily_limit", "0")
    assert (await autocfg.get_automation_settings())["automation_daily_limit"] == 1
    await db.upsert_setting("automation_daily_limit", "99999999")
    assert (await autocfg.get_automation_settings())["automation_daily_limit"] == autocfg._MAX_DAILY_LIMIT


async def test_duration_hours_clamped_into_range(clean_db):
    db = clean_db
    await db.upsert_setting("automation_duration_hours", "-5")
    assert (await autocfg.get_automation_settings())["automation_duration_hours"] == 0
    await db.upsert_setting("automation_duration_hours", "100")
    assert (await autocfg.get_automation_settings())["automation_duration_hours"] == 24


async def test_max_retries_clamped_into_range(clean_db):
    db = clean_db
    await db.upsert_setting("automation_max_retries", "-1")
    assert (await autocfg.get_automation_settings())["automation_max_retries"] == 0
    await db.upsert_setting("automation_max_retries", "50")
    assert (await autocfg.get_automation_settings())["automation_max_retries"] == autocfg._MAX_RETRIES


async def test_db_override_wins_and_types_coerced(clean_db):
    db = clean_db
    await db.upsert_setting("automation_enabled", "true")
    await db.upsert_setting("automation_daily_limit", "250")
    await db.upsert_setting("automation_start_time", "06:30")
    cfg = await autocfg.get_automation_settings()
    assert cfg["automation_enabled"] is True
    assert cfg["automation_daily_limit"] == 250
    assert cfg["automation_start_time"] == "06:30"


async def test_bad_numeric_override_falls_back_to_default(clean_db):
    db = clean_db
    await db.upsert_setting("automation_daily_limit", "not-a-number")
    cfg = await autocfg.get_automation_settings()
    assert cfg["automation_daily_limit"] == 500
