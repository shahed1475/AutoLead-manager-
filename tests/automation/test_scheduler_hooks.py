from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.automation import scheduler_hooks as hooks

pytestmark = pytest.mark.asyncio


class _FakeQueue:
    def __init__(self):
        self.jobs = []

    def enqueue_nowait(self, jt, payload, handler):
        self.jobs.append(jt)
        return True


def _settings(**over):
    base = {"automation_enabled": True, "automation_daily_limit": 500,
            "automation_start_time": "07:00", "automation_timezone": "America/New_York",
            "automation_duration_hours": 4, "automation_per_item_target": 100,
            "automation_max_retries": 2}
    base.update(over)

    async def _f():
        return base

    return _f


async def test_tick_noop_when_disabled(clean_db, monkeypatch):
    q = _FakeQueue()
    monkeypatch.setattr(hooks, "get_automation_settings", _settings(automation_enabled=False))
    monkeypatch.setattr(hooks, "get_queue", lambda: q)
    await hooks.automation_tick()
    assert q.jobs == []


async def test_tick_noop_before_start_time(clean_db, monkeypatch):
    db = clean_db
    await db.get_automation_state()
    await db.update_automation_state({"queue_total": 5})
    q = _FakeQueue()
    monkeypatch.setattr(hooks, "get_automation_settings", _settings(automation_start_time="23:59"))
    monkeypatch.setattr(hooks, "get_queue", lambda: q)
    monkeypatch.setattr(hooks, "_now_local",
                        lambda tzname: datetime.now(ZoneInfo(tzname)).replace(hour=5, minute=0))
    await hooks.automation_tick()
    assert q.jobs == []


async def test_tick_enqueues_and_resets_today_count_on_new_day(clean_db, monkeypatch):
    db = clean_db
    await db.get_automation_state()
    await db.update_automation_state({"today_count": 123, "today_date": "2000-01-01",
                                     "current_position": 7, "total_count": 900, "queue_total": 20})
    q = _FakeQueue()
    monkeypatch.setattr(hooks, "get_automation_settings", _settings(automation_start_time="00:00"))
    monkeypatch.setattr(hooks, "get_queue", lambda: q)
    await hooks.automation_tick()
    assert q.jobs == ["AUTOMATION"]
    state = await db.get_automation_state()
    assert state["today_count"] == 0
    assert state["current_position"] == 7        # position preserved — resume, not restart
    assert state["total_count"] == 900


async def test_tick_noop_if_already_ran_today(clean_db, monkeypatch):
    db = clean_db
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    await db.get_automation_state()
    await db.update_automation_state({"today_date": today, "status": "COMPLETED", "queue_total": 5})
    q = _FakeQueue()
    monkeypatch.setattr(hooks, "get_automation_settings", _settings(automation_start_time="00:00"))
    monkeypatch.setattr(hooks, "get_queue", lambda: q)
    await hooks.automation_tick()
    assert q.jobs == []


async def test_tick_noop_when_no_queue(clean_db, monkeypatch):
    db = clean_db
    await db.get_automation_state()  # queue_total defaults to 0
    q = _FakeQueue()
    monkeypatch.setattr(hooks, "get_automation_settings", _settings(automation_start_time="00:00"))
    monkeypatch.setattr(hooks, "get_queue", lambda: q)
    await hooks.automation_tick()
    assert q.jobs == []


async def test_resume_running_slice_requeues_stuck_run(clean_db):
    db = clean_db
    await db.get_automation_state()
    await db.update_automation_state({"status": "RUNNING"})
    q = _FakeQueue()
    n = await hooks.resume_running_slice(q)
    assert n == 1 and q.jobs == ["AUTOMATION"]


async def test_resume_noop_when_not_running(clean_db):
    db = clean_db
    await db.get_automation_state()
    await db.update_automation_state({"status": "SCHEDULED"})
    q = _FakeQueue()
    assert await hooks.resume_running_slice(q) == 0
    assert q.jobs == []


async def test_kick_slice_now_enqueues(clean_db):
    db = clean_db
    await db.get_automation_state()
    q = _FakeQueue()
    assert await hooks.kick_slice_now(q) is True
    assert q.jobs == ["AUTOMATION"]
