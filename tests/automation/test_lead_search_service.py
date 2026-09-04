import pytest

from backend.automation import lead_search_service as svc

pytestmark = pytest.mark.asyncio


class _FakePlan:
    recommended_sources = ["GOOGLE_MAPS", "YELLOW_PAGES"]
    query_variants = ["dental clinic"]
    intent = "LOCAL_BUSINESS"


class _FakePlanner:
    async def plan(self, **kw):
        return _FakePlan()


class _FakeRegistry:
    def __init__(self, per_source):
        self._per_source = per_source

    def get(self, name):
        return object() if name in self._per_source else None

    async def execute(self, name, niche, city, country, budget, cfg, log_fn=None):
        from backend.discovery.adapters import AdapterResult
        return AdapterResult(source=name, leads=self._per_source.get(name, []))


async def _async(v):
    return v


async def test_counts_new_leads_from_merge(monkeypatch, clean_db):
    monkeypatch.setattr(svc, "DiscoveryPlanner", lambda: _FakePlanner())
    monkeypatch.setattr(svc, "get_registry", lambda: _FakeRegistry({
        "GOOGLE_MAPS": [{"business_name": "Bright Smiles Dental", "phone": "5551110000", "city": "Akron"}],
        "YELLOW_PAGES": [{"business_name": "Downtown Family Dentistry", "phone": "5552220000", "city": "Akron"}],
    }))
    monkeypatch.setattr(svc, "_scraper_cfg", lambda: _async({}))

    result = await svc.DiscoveryLeadSearch().search_leads("dental clinic", "Akron", "OH", "USA", 50)
    assert result.error is None
    assert result.new_leads == 2   # two distinct businesses, dedup keeps both
    assert set(result.sources_used) == {"GOOGLE_MAPS", "YELLOW_PAGES"}


async def test_source_exception_becomes_error_not_raise(monkeypatch, clean_db):
    monkeypatch.setattr(svc, "DiscoveryPlanner", lambda: _FakePlanner())

    class _Boom:
        def get(self, name):
            return object()

        async def execute(self, *a, **kw):
            raise RuntimeError("scraper died")

    monkeypatch.setattr(svc, "get_registry", lambda: _Boom())
    monkeypatch.setattr(svc, "_scraper_cfg", lambda: _async({}))

    result = await svc.DiscoveryLeadSearch().search_leads("dental", "Akron", "OH", "USA", 50)
    assert result.new_leads == 0
    assert result.error and "scraper died" in result.error


async def test_source_timeout_is_skipped_not_hung(monkeypatch, clean_db):
    import asyncio
    # Proportional timeout, forced tiny for the test.
    monkeypatch.setattr(svc, "_BASE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(svc, "_PER_LEAD_SECONDS", 0.0)
    monkeypatch.setattr(svc, "DiscoveryPlanner", lambda: _FakePlanner())

    class _Slow:
        def get(self, name):
            return object()

        async def execute(self, *a, **kw):
            await asyncio.sleep(1)
            from backend.discovery.adapters import AdapterResult
            return AdapterResult(source="X", leads=[])

    monkeypatch.setattr(svc, "get_registry", lambda: _Slow())
    monkeypatch.setattr(svc, "_scraper_cfg", lambda: _async({}))

    result = await svc.DiscoveryLeadSearch().search_leads("dental", "Akron", "OH", "USA", 50)
    assert result.new_leads == 0
    assert result.error and "timed out" in result.error


async def test_effective_target_is_capped(monkeypatch, clean_db):
    seen = {}

    class _RecordingRegistry:
        def get(self, name):
            return object() if name == "GOOGLE_MAPS" else None

        async def execute(self, name, niche, city, country, budget, cfg, log_fn=None):
            seen["budget"] = budget
            from backend.discovery.adapters import AdapterResult
            return AdapterResult(source=name, leads=[
                {"business_name": "X Dental", "phone": "5559990000", "city": "Akron"},
            ])

    monkeypatch.setattr(svc, "DiscoveryPlanner", lambda: _FakePlanner())
    monkeypatch.setattr(svc, "get_registry", lambda: _RecordingRegistry())
    monkeypatch.setattr(svc, "_scraper_cfg", lambda: _async({}))

    await svc.DiscoveryLeadSearch().search_leads("dental", "Akron", "OH", "USA", 500)
    assert seen["budget"] <= svc._EFFECTIVE_TARGET_CAP


async def test_creates_automation_discovery_run(monkeypatch, clean_db):
    db = clean_db
    monkeypatch.setattr(svc, "DiscoveryPlanner", lambda: _FakePlanner())
    monkeypatch.setattr(svc, "get_registry", lambda: _FakeRegistry({
        "GOOGLE_MAPS": [{"business_name": "Run Row Dental", "phone": "5551230000", "city": "Akron"}],
    }))
    monkeypatch.setattr(svc, "_scraper_cfg", lambda: _async({}))

    result = await svc.DiscoveryLeadSearch().search_leads("dental", "Akron", "OH", "USA", 50)
    assert result.run_id is not None
    run = await db.get_discovery_run(result.run_id)
    assert run["mode"] == "AUTOMATION"
    assert run["status"] == "COMPLETED"
    assert run["results_count"] == result.new_leads == 1


async def test_state_backfilled_and_source_tagged(monkeypatch, clean_db):
    db = clean_db
    captured = {}

    async def fake_merge(candidates, run_id=None, default_source="UNKNOWN"):
        captured["candidates"] = candidates
        return {"new_count": len(candidates), "unique_saved_ids": [1, 2]}

    monkeypatch.setattr(svc, "DiscoveryPlanner", lambda: _FakePlanner())
    monkeypatch.setattr(svc, "get_registry", lambda: _FakeRegistry({
        "GOOGLE_MAPS": [{"business_name": "C Dental", "phone": "5553330000", "city": "Akron"}],
    }))
    monkeypatch.setattr(svc, "_scraper_cfg", lambda: _async({}))
    monkeypatch.setattr(svc, "merge_and_save", fake_merge)

    result = await svc.DiscoveryLeadSearch().search_leads("dental", "Akron", "OH", "USA", 50)
    assert result.new_leads == 1
    c = captured["candidates"][0]
    assert c["source"] == "AUTOMATION" and c["niche"] == "dental" and c["state"] == "OH"
