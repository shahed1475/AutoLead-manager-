import pytest
from httpx import ASGITransport, AsyncClient

from backend.routers import automation as auto_router

pytestmark = pytest.mark.asyncio


class _FakeQueue:
    def __init__(self):
        self.jobs = []

    def enqueue_nowait(self, jt, payload, handler):
        self.jobs.append(jt)
        return True


async def _client():
    from backend.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _async(v):
    return v


def _combined_csv() -> bytes:
    return b"City,State,Niche\nNew York,NY,Hair Salon\nMiami,FL,Barber Shop\n"


async def test_preview_does_not_persist(clean_db):
    db = clean_db
    async with await _client() as c:
        resp = await c.post("/api/automation/import/preview",
                            files={"file": ("l.csv", _combined_csv(), "text/csv")})
    assert resp.status_code == 200
    body = resp.json()
    assert body["combinations"] == 4
    assert body["n_locations"] == 2 and body["n_niches"] == 2
    assert await db.get_automation_queue() == []          # nothing written


async def test_preview_422_on_bad_file(clean_db):
    async with await _client() as c:
        resp = await c.post("/api/automation/import/preview",
                            files={"file": ("x.csv", b"City,State\nNY,NY\n", "text/csv")})
    assert resp.status_code == 422


async def test_confirm_builds_queue_and_resets_position(clean_db):
    db = clean_db
    await db.get_automation_state()
    await db.update_automation_state({"current_position": 9, "total_count": 40})
    payload = {"locations": [{"city": "NY", "state": "NY"}, {"city": "LA", "state": "CA"}],
               "niches": ["Hair Salon"], "filename": "l.csv", "layout": "combined"}
    async with await _client() as c:
        resp = await c.post("/api/automation/import/confirm", json=payload)
    assert resp.status_code == 200
    q = await db.get_automation_queue()
    assert [i["position"] for i in q] == [0, 1]
    state = await db.get_automation_state()
    assert state["current_position"] == 0 and state["today_count"] == 0
    assert state["total_count"] == 40                      # preserved


async def test_confirm_replaces_existing_queue(clean_db):
    db = clean_db
    await db.get_automation_state()
    await db.bulk_insert_automation_queue([{"position": i, "niche": "old", "city": "x", "state": "y"} for i in range(3)])
    payload = {"locations": [{"city": "NY", "state": "NY"}], "niches": ["New"], "filename": "l.csv", "layout": "combined"}
    async with await _client() as c:
        await c.post("/api/automation/import/confirm", json=payload)
    q = await db.get_automation_queue()
    assert len(q) == 1 and q[0]["niche"] == "New"


async def test_start_respects_daily_limit(clean_db, monkeypatch):
    db = clean_db
    monkeypatch.setattr(auto_router, "get_queue", lambda: _FakeQueue())
    await db.get_automation_state()
    await db.bulk_insert_automation_queue([{"position": 0, "niche": "n", "city": "c", "state": "s"}])
    await db.update_automation_state({"today_count": 500, "queue_total": 1})
    monkeypatch.setattr(auto_router, "get_automation_settings",
                        lambda: _async({"automation_daily_limit": 500, "automation_enabled": True,
                                        "automation_timezone": "UTC", "automation_start_time": "07:00",
                                        "automation_duration_hours": 4}))
    async with await _client() as c:
        resp = await c.post("/api/automation/start")
    assert resp.status_code == 409


async def test_start_503_when_no_queue(clean_db):
    async with await _client() as c:
        resp = await c.post("/api/automation/start")
    assert resp.status_code == 503


async def test_reset_requires_confirm(clean_db):
    async with await _client() as c:
        no = await c.post("/api/automation/reset")
        yes = await c.post("/api/automation/reset?confirm=true")
    assert no.status_code == 400
    assert yes.status_code == 200


async def test_pause_resume_stop_transitions(clean_db, monkeypatch):
    db = clean_db
    monkeypatch.setattr(auto_router, "get_queue", lambda: _FakeQueue())
    monkeypatch.setattr(auto_router, "kick_slice_now", lambda q: _async(True))
    await db.get_automation_state()
    await db.update_automation_state({"status": "RUNNING"})
    async with await _client() as c:
        assert (await c.post("/api/automation/pause")).json()["status"] == "PAUSED"
        assert (await c.post("/api/automation/resume")).json()["status"] in ("SCHEDULED", "RUNNING")
        assert (await c.post("/api/automation/stop")).json()["status"] == "STOPPED"


async def test_status_has_derived_fields(clean_db):
    db = clean_db
    await db.get_automation_state()
    await db.bulk_insert_automation_queue([{"position": i, "niche": "n", "city": "c", "state": "s"} for i in range(10)])
    await db.update_automation_state({"today_count": 65, "queue_total": 10})
    await db.update_automation_queue_item((await db.get_automation_queue())[0]["id"], {"status": "COMPLETED"})
    await db.update_automation_queue_item((await db.get_automation_queue())[1]["id"], {"status": "COMPLETED"})
    await db.update_automation_queue_item((await db.get_automation_queue())[2]["id"], {"status": "FAILED"})
    await db.update_automation_queue_item((await db.get_automation_queue())[3]["id"], {"status": "COMPLETED"})
    async with await _client() as c:
        body = (await c.get("/api/automation/status")).json()
    assert "progress_pct" in body and "searches_remaining" in body
    assert body["searches_completed"] == 4
    assert body["searches_remaining"] == 6


async def test_settings_validation(clean_db):
    async with await _client() as c:
        bad_tz = await c.put("/api/automation/settings", json={"automation_timezone": "Mars/Olympus"})
        bad_time = await c.put("/api/automation/settings", json={"automation_start_time": "7am"})
        ok = await c.put("/api/automation/settings", json={"automation_daily_limit": 250, "automation_enabled": True})
    assert bad_tz.status_code == 422
    assert bad_time.status_code == 422
    assert ok.status_code == 200
    assert ok.json()["settings"]["automation_daily_limit"] == 250


async def test_settings_rejects_nonpositive_daily_limit(clean_db):
    async with await _client() as c:
        zero = await c.put("/api/automation/settings", json={"automation_daily_limit": 0})
        neg = await c.put("/api/automation/settings", json={"automation_daily_limit": -5})
        huge = await c.put("/api/automation/settings", json={"automation_daily_limit": 10_000_000})
        ok = await c.put("/api/automation/settings", json={"automation_daily_limit": 250})
    assert zero.status_code == 422
    assert neg.status_code == 422
    assert huge.status_code == 422
    assert ok.status_code == 200


async def test_settings_rejects_out_of_range_duration_hours(clean_db):
    async with await _client() as c:
        neg = await c.put("/api/automation/settings", json={"automation_duration_hours": -1})
        too_long = await c.put("/api/automation/settings", json={"automation_duration_hours": 25})
        zero = await c.put("/api/automation/settings", json={"automation_duration_hours": 0})
        ok = await c.put("/api/automation/settings", json={"automation_duration_hours": 8})
    assert neg.status_code == 422
    assert too_long.status_code == 422
    assert zero.status_code == 200          # 0 = no limit, a valid choice
    assert ok.status_code == 200


async def test_settings_rejects_out_of_range_max_retries(clean_db):
    async with await _client() as c:
        neg = await c.put("/api/automation/settings", json={"automation_max_retries": -1})
        too_many = await c.put("/api/automation/settings", json={"automation_max_retries": 6})
        zero = await c.put("/api/automation/settings", json={"automation_max_retries": 0})
    assert neg.status_code == 422
    assert too_many.status_code == 422
    assert zero.status_code == 200


async def test_settings_rejects_oversized_per_item_target(clean_db):
    from backend.automation.config import _MAX_PER_ITEM_TARGET
    async with await _client() as c:
        too_big = await c.put("/api/automation/settings",
                              json={"automation_per_item_target": _MAX_PER_ITEM_TARGET + 1})
        ok = await c.put("/api/automation/settings",
                         json={"automation_per_item_target": _MAX_PER_ITEM_TARGET})
    assert too_big.status_code == 422
    assert ok.status_code == 200
    assert ok.json()["settings"]["automation_per_item_target"] == _MAX_PER_ITEM_TARGET


async def test_start_time_out_of_range_rejected_and_normalized(clean_db):
    async with await _client() as c:
        bad_h = await c.put("/api/automation/settings", json={"automation_start_time": "25:00"})
        bad_m = await c.put("/api/automation/settings", json={"automation_start_time": "09:60"})
        ok = await c.put("/api/automation/settings", json={"automation_start_time": "9:5"})
    assert bad_h.status_code == 422
    assert bad_m.status_code == 422
    assert ok.status_code == 200
    assert ok.json()["settings"]["automation_start_time"] == "09:05"


async def test_enable_toggle_schedules_a_stopped_automation(clean_db):
    db = clean_db
    await db.get_automation_state()
    await db.bulk_insert_automation_queue([{"position": 0, "niche": "n", "city": "c", "state": "s"}])
    await db.update_automation_state({"status": "STOPPED", "today_date": "2020-01-01", "queue_total": 1})
    async with await _client() as c:
        await c.put("/api/automation/settings", json={"automation_enabled": True})
    st = await db.get_automation_state()
    assert st["status"] == "SCHEDULED"
    assert st["today_date"] in ("", None)          # cleared so it can run today


async def test_disable_toggle_stops_a_scheduled_automation(clean_db):
    db = clean_db
    await db.get_automation_state()
    await db.update_automation_state({"status": "SCHEDULED"})
    async with await _client() as c:
        await c.put("/api/automation/settings", json={"automation_enabled": False})
    assert (await db.get_automation_state())["status"] == "STOPPED"


async def test_enable_toggle_leaves_paused_run_alone(clean_db):
    db = clean_db
    await db.get_automation_state()
    await db.update_automation_state({"status": "PAUSED"})
    async with await _client() as c:
        await c.put("/api/automation/settings", json={"automation_enabled": True})
    assert (await db.get_automation_state())["status"] == "PAUSED"


async def test_duration_change_recomputes_active_deadline(clean_db):
    from datetime import datetime, timedelta, timezone
    db = clean_db
    await db.get_automation_state()
    started = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=30)
    await db.update_automation_state({
        "status": "RUNNING",
        "last_run_started_at": started.isoformat(),
        "duration_deadline": (started + timedelta(hours=6)).isoformat(),
    })
    async with await _client() as c:
        await c.put("/api/automation/settings", json={"automation_duration_hours": 2})
    dl = datetime.fromisoformat((await db.get_automation_state())["duration_deadline"])
    assert abs((dl - (started + timedelta(hours=2))).total_seconds()) < 5


async def test_status_reports_next_run_reason(clean_db):
    db = clean_db
    await db.get_automation_state()
    await db.bulk_insert_automation_queue([{"position": 0, "niche": "n", "city": "c", "state": "s"}])
    await db.update_automation_state({"status": "STOPPED", "queue_total": 1})
    await db.upsert_setting("automation_enabled", "true")
    async with await _client() as c:
        body = (await c.get("/api/automation/status")).json()
    assert body["next_run_at"] is None
    assert "stopped" in body["next_run_reason"].lower()


async def test_status_next_run_computed_when_scheduled(clean_db):
    db = clean_db
    await db.get_automation_state()
    await db.bulk_insert_automation_queue([{"position": 0, "niche": "n", "city": "c", "state": "s"}])
    await db.update_automation_state({"status": "SCHEDULED", "queue_total": 1, "today_date": "2020-01-01"})
    await db.upsert_setting("automation_enabled", "true")
    await db.upsert_setting("automation_start_time", "23:59")
    await db.upsert_setting("automation_timezone", "UTC")
    async with await _client() as c:
        body = (await c.get("/api/automation/status")).json()
    assert body["next_run_at"] is not None


async def test_test_search_runs_without_advancing_progress(clean_db, monkeypatch):
    from backend.automation.lead_search_service import SearchResult
    db = clean_db
    monkeypatch.setattr(auto_router, "get_queue", lambda: _FakeQueue())
    await db.get_automation_state()
    await db.bulk_insert_automation_queue([{"position": 0, "niche": "Dentist", "city": "Reno", "state": "NV"}])
    await db.update_automation_state({"current_position": 0, "today_count": 0, "queue_total": 1})

    class _S:
        async def search_leads(self, niche, city, state, country, target):
            return SearchResult(new_leads=4, total_found=9, sources_used=["GOOGLE_MAPS"])

    monkeypatch.setattr(auto_router, "get_lead_search_service", lambda: _S())
    async with await _client() as c:
        body = (await c.post("/api/automation/test-search")).json()
    assert body["new_leads"] == 4 and body["niche"] == "Dentist"
    st = await db.get_automation_state()
    assert st["current_position"] == 0 and st["today_count"] == 0
    assert (await db.get_automation_queue())[0]["status"] == "PENDING"


async def test_test_search_503_when_no_queue(clean_db):
    async with await _client() as c:
        resp = await c.post("/api/automation/test-search")
    assert resp.status_code == 503


async def test_log_and_queue_endpoints(clean_db):
    db = clean_db
    await db.get_automation_state()
    await db.append_automation_log("INFO", "hello world")
    await db.bulk_insert_automation_queue([{"position": 0, "niche": "n", "city": "c", "state": "s"}])
    async with await _client() as c:
        log = (await c.get("/api/automation/log")).json()
        queue = (await c.get("/api/automation/queue")).json()
    assert log["lines"][0]["message"] == "hello world"
    assert len(queue["items"]) == 1
