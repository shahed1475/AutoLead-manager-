from datetime import datetime, timedelta, timezone

import pytest

from backend.automation import runner as runner_mod
from backend.automation.lead_search_service import SearchResult

pytestmark = pytest.mark.asyncio


def _utc_iso(dt):
    return dt.replace(tzinfo=None).isoformat()


async def _async(v):
    return v


async def _seed_queue(db, n, niche="Hair Salon"):
    await db.get_automation_state()
    await db.bulk_insert_automation_queue(
        [{"position": i, "niche": niche, "city": f"City{i}", "state": "TX"} for i in range(n)]
    )
    await db.update_automation_state({"queue_total": n})


def _settings(**over):
    base = {"automation_daily_limit": 999, "automation_duration_hours": 0,
            "automation_per_item_target": 100, "automation_max_retries": 0,
            "automation_enabled": True}
    base.update(over)
    return lambda: _async(base)


def _fake_service(per_call_new=3):
    calls = {"n": 0}

    class _S:
        async def search_leads(self, niche, city, state, country, target):
            calls["n"] += 1
            return SearchResult(new_leads=per_call_new, total_found=per_call_new, sources_used=["GOOGLE_MAPS"])

    return _S(), calls


async def test_stops_at_daily_limit_final_item_overshoots(clean_db, monkeypatch):
    db = clean_db
    await _seed_queue(db, 10)
    await db.update_automation_state({"status": "SCHEDULED", "today_count": 0})
    svc, calls = _fake_service(per_call_new=4)
    monkeypatch.setattr(runner_mod, "get_lead_search_service", lambda: svc)
    monkeypatch.setattr(runner_mod, "get_automation_settings", _settings(automation_daily_limit=10))
    await runner_mod.run_automation_slice()
    state = await db.get_automation_state()
    assert state["status"] == "LIMIT_REACHED"
    assert state["today_count"] == 12          # 3 items * 4, final overshoots past 10
    assert calls["n"] == 3
    assert state["current_position"] == 3


async def test_stops_at_duration_deadline(clean_db, monkeypatch):
    db = clean_db
    await _seed_queue(db, 10)
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db.update_automation_state({"status": "SCHEDULED", "duration_deadline": _utc_iso(past)})
    svc, calls = _fake_service()
    monkeypatch.setattr(runner_mod, "get_lead_search_service", lambda: svc)
    monkeypatch.setattr(runner_mod, "get_automation_settings", _settings(automation_duration_hours=4))
    await runner_mod.run_automation_slice()
    assert (await db.get_automation_state())["status"] == "SCHEDULED"
    assert calls["n"] == 0                       # deadline already passed -> no work


async def test_position_persisted_after_every_item(clean_db, monkeypatch):
    db = clean_db
    await _seed_queue(db, 3)
    await db.update_automation_state({"status": "SCHEDULED"})
    positions_seen = []
    svc, _ = _fake_service()
    real = svc.search_leads

    async def spy(*a, **kw):
        positions_seen.append((await db.get_automation_state())["current_position"])
        return await real(*a, **kw)

    svc.search_leads = spy
    monkeypatch.setattr(runner_mod, "get_lead_search_service", lambda: svc)
    monkeypatch.setattr(runner_mod, "get_automation_settings", _settings())
    await runner_mod.run_automation_slice()
    assert positions_seen == [0, 1, 2]
    assert (await db.get_automation_state())["status"] == "COMPLETED"


async def test_pause_stops_at_next_boundary(clean_db, monkeypatch):
    db = clean_db
    await _seed_queue(db, 5)
    await db.update_automation_state({"status": "SCHEDULED"})

    class _S:
        n = 0

        async def search_leads(self, *a, **kw):
            _S.n += 1
            if _S.n == 2:
                await db.update_automation_state({"status": "PAUSED"})
            return SearchResult(new_leads=1)

    monkeypatch.setattr(runner_mod, "get_lead_search_service", lambda: _S())
    monkeypatch.setattr(runner_mod, "get_automation_settings", _settings())
    await runner_mod.run_automation_slice()
    state = await db.get_automation_state()
    assert state["status"] == "PAUSED"
    assert state["current_position"] == 2      # item 0 and 1 done, paused before 2


async def test_transient_error_retried_then_item_failed_and_advances(clean_db, monkeypatch):
    db = clean_db
    await _seed_queue(db, 2)
    await db.update_automation_state({"status": "SCHEDULED"})
    monkeypatch.setattr(runner_mod, "_RETRY_BACKOFF_SECONDS", 0)

    class _S:
        async def search_leads(self, niche, city, *a, **kw):
            return SearchResult(error="always") if city == "City0" else SearchResult(new_leads=2)

    monkeypatch.setattr(runner_mod, "get_lead_search_service", lambda: _S())
    monkeypatch.setattr(runner_mod, "get_automation_settings", _settings(automation_max_retries=2))
    await runner_mod.run_automation_slice()
    q = await db.get_automation_queue()
    assert q[0]["status"] == "FAILED" and q[0]["attempts"] == 3
    assert q[1]["status"] == "COMPLETED"
    assert (await db.get_automation_state())["status"] == "COMPLETED"


async def test_retry_uses_a_smaller_target_each_attempt(clean_db, monkeypatch):
    db = clean_db
    await _seed_queue(db, 1)
    await db.update_automation_state({"status": "SCHEDULED"})
    monkeypatch.setattr(runner_mod, "_RETRY_BACKOFF_SECONDS", 0)
    targets = []

    class _S:
        async def search_leads(self, niche, city, state, country, target):
            targets.append(target)
            return SearchResult(error="always")

    monkeypatch.setattr(runner_mod, "get_lead_search_service", lambda: _S())
    monkeypatch.setattr(runner_mod, "get_automation_settings",
                        _settings(automation_per_item_target=40, automation_max_retries=2))
    await runner_mod.run_automation_slice()
    assert targets == [40, 20, 10]


async def test_never_raises_on_unexpected_error(clean_db, monkeypatch):
    db = clean_db
    await _seed_queue(db, 1)
    await db.update_automation_state({"status": "SCHEDULED"})

    class _S:
        async def search_leads(self, *a, **kw):
            raise KeyError("boom inside service call site")

    monkeypatch.setattr(runner_mod, "get_lead_search_service", lambda: _S())
    monkeypatch.setattr(runner_mod, "get_automation_settings", _settings())
    await runner_mod.run_automation_slice()          # must NOT raise
    assert (await db.get_automation_state())["status"] in ("SCHEDULED", "COMPLETED")


async def test_guard_exits_are_noops(clean_db, monkeypatch):
    db = clean_db
    await _seed_queue(db, 2)
    await db.update_automation_state({"status": "STOPPED"})
    monkeypatch.setattr(runner_mod, "get_automation_settings", _settings())
    await runner_mod.run_automation_slice()
    assert (await db.get_automation_state())["status"] == "STOPPED"  # untouched
