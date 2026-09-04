import pytest

pytestmark = pytest.mark.asyncio


async def test_automation_tick_job_registered():
    from backend import scheduler
    sch = scheduler.start_scheduler(9)
    try:
        assert sch.get_job("automation_tick") is not None
    finally:
        scheduler.stop_scheduler()


async def test_reconcile_automation_calls_resume(clean_db, monkeypatch):
    from backend.automation import scheduler_hooks

    called = {"n": 0}

    async def fake_resume(queue):
        called["n"] += 1
        return 0

    monkeypatch.setattr(scheduler_hooks, "resume_running_slice", fake_resume)

    import backend.main as main_mod
    await main_mod._reconcile_automation(object())
    assert called["n"] == 1
