import pytest

from backend.discovery import quick_search as qs_module
from backend.discovery.adapters import AdapterResult
from backend.discovery.planner import DiscoveryPlan
from backend.discovery.quick_search import run_quick_search

pytestmark = pytest.mark.asyncio


class _FakeAdapter:
    def __init__(self, enabled=True):
        self.enabled = enabled


class _FakeRegistry:
    """Controllable registry double — maps source name -> AdapterResult (or a list of them, consumed in order)."""

    def __init__(self, results_by_source):
        self._results_by_source = results_by_source
        self.calls = []

    def get(self, name):
        return _FakeAdapter(enabled=name in self._results_by_source)

    async def execute(self, name, niche, city, country, budget, cfg, log_fn=None):
        self.calls.append(name)
        queue = self._results_by_source.get(name, [])
        if isinstance(queue, list) and queue and isinstance(queue[0], AdapterResult):
            return queue.pop(0)
        return queue


def _fake_plan(sources, variants=None):
    return DiscoveryPlan(
        intent="LOCAL_BUSINESS", confidence=0.9,
        recommended_sources=sources, query_variants=variants or ["dental"],
        mode="QUICK", classification_method="rule_based",
    )


async def _make_run(db, target_count=20):
    return await db.create_discovery_run({
        "mode": "QUICK", "raw_query": "dental", "niche": "dental",
        "city": "LA", "country": "", "target_count": target_count,
    })


async def _patch_common(monkeypatch, plan, registry):
    class _FakePlanner:
        async def plan(self, *a, **kw):
            return plan

    monkeypatch.setattr(qs_module, "DiscoveryPlanner", _FakePlanner)
    monkeypatch.setattr(qs_module, "get_registry", lambda: registry)
    async def fake_scraper_cfg():
        return {}
    monkeypatch.setattr(qs_module, "_scraper_cfg", fake_scraper_cfg)


async def test_successful_run_completes_with_results(clean_db, monkeypatch):
    db = clean_db
    run_id = await _make_run(db)
    plan = _fake_plan(["GOOGLE_MAPS"])
    registry = _FakeRegistry({
        "GOOGLE_MAPS": AdapterResult(source="GOOGLE_MAPS", leads=[
            {"business_name": "Acme Dental", "email": "a@acme.com", "source": "GOOGLE_MAPS"},
            {"business_name": "Beta Dental", "email": "b@beta.com", "source": "GOOGLE_MAPS"},
        ]),
    })
    await _patch_common(monkeypatch, plan, registry)

    await run_quick_search({"run_id": run_id})

    run = await db.get_discovery_run(run_id)
    assert run["status"] == "COMPLETED"
    assert run["results_count"] == 2
    assert run["planner_intent"] == "LOCAL_BUSINESS"
    leads = await db.get_leads_for_discovery_run(run_id)
    assert len(leads) == 2


async def test_insufficient_results_falls_back_to_next_source(clean_db, monkeypatch):
    db = clean_db
    run_id = await _make_run(db, target_count=20)
    plan = _fake_plan(["GOOGLE_SEARCH", "BING_SEARCH"])
    registry = _FakeRegistry({
        "GOOGLE_SEARCH": AdapterResult(source="GOOGLE_SEARCH", leads=[]),  # 0 results — insufficient
        "BING_SEARCH": AdapterResult(source="BING_SEARCH", leads=[
            {"business_name": "Fallback Found Co", "email": "f@x.com", "source": "BING_SEARCH"},
        ]),
    })
    await _patch_common(monkeypatch, plan, registry)

    await run_quick_search({"run_id": run_id})

    assert registry.calls == ["GOOGLE_SEARCH", "BING_SEARCH"]  # tried the fallback
    run = await db.get_discovery_run(run_id)
    assert run["status"] == "COMPLETED"
    assert run["results_count"] == 1


async def test_no_results_from_any_source_completes_cleanly(clean_db, monkeypatch):
    db = clean_db
    run_id = await _make_run(db)
    plan = _fake_plan(["GOOGLE_MAPS", "YELLOW_PAGES"])
    registry = _FakeRegistry({
        "GOOGLE_MAPS": AdapterResult(source="GOOGLE_MAPS", leads=[]),
        "YELLOW_PAGES": AdapterResult(source="YELLOW_PAGES", leads=[]),
    })
    await _patch_common(monkeypatch, plan, registry)

    await run_quick_search({"run_id": run_id})

    run = await db.get_discovery_run(run_id)
    assert run["status"] == "COMPLETED"  # not FAILED — zero results is a valid outcome
    assert run["results_count"] == 0


async def test_cancelled_before_start_never_runs(clean_db, monkeypatch):
    db = clean_db
    run_id = await _make_run(db)
    await db.update_discovery_run(run_id, {"status": "CANCELLED"})

    registry = _FakeRegistry({"GOOGLE_MAPS": AdapterResult(source="GOOGLE_MAPS", leads=[{"business_name": "Should Not Run"}])})
    plan = _fake_plan(["GOOGLE_MAPS"])
    await _patch_common(monkeypatch, plan, registry)

    await run_quick_search({"run_id": run_id})

    assert registry.calls == []  # never touched a source
    run = await db.get_discovery_run(run_id)
    assert run["status"] == "CANCELLED"  # left as-is, not overwritten to RUNNING/COMPLETED


async def test_missing_run_id_does_not_raise(clean_db):
    # No such run — handler must return quietly, not raise (JobQueue would
    # only log it, and a raise here should never happen regardless).
    await run_quick_search({"run_id": 999999})


async def test_exception_during_run_marks_failed_not_stuck_running(clean_db, monkeypatch):
    db = clean_db
    run_id = await _make_run(db)

    class _ExplodingPlanner:
        async def plan(self, *a, **kw):
            raise RuntimeError("boom")

    monkeypatch.setattr(qs_module, "DiscoveryPlanner", _ExplodingPlanner)

    await run_quick_search({"run_id": run_id})

    run = await db.get_discovery_run(run_id)
    assert run["status"] == "FAILED"
    assert "boom" in run["error_message"]
