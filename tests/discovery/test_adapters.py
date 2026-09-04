import pytest

from backend.discovery import adapters as adapters_module
from backend.discovery.adapters import SourceRegistry, get_registry

# Note: no module-level `pytestmark = pytest.mark.asyncio` — this file mixes
# sync and async tests, and pytest.ini's asyncio_mode=auto already runs the
# async ones correctly without the marker.

ALL_SOURCES = {
    "GOOGLE_MAPS", "GOOGLE_SEARCH", "YELP", "YELLOW_PAGES", "BING_SEARCH",
    "DUCKDUCKGO", "HOTFROG", "FOURSQUARE", "TOP_LIST", "GENERIC_DIR",
}


def test_all_sources_registered():
    registry = get_registry()
    assert set(registry.all_names()) == ALL_SOURCES


def test_get_returns_adapter_by_name():
    registry = get_registry()
    adapter = registry.get("google_maps")  # case-insensitive
    assert adapter is not None
    assert adapter.name == "GOOGLE_MAPS"
    assert adapter.source_type == "BROWSER"


def test_get_unknown_source_returns_none():
    assert get_registry().get("NOT_A_SOURCE") is None


def test_list_enabled_returns_all_by_default():
    registry = get_registry()
    assert len(registry.list_enabled()) == len(ALL_SOURCES)


async def test_execute_delegates_to_dispatch_source_unchanged(monkeypatch):
    captured = {}

    async def fake_dispatch(source, niche, city, country, budget, cfg, log_fn):
        captured.update(source=source, niche=niche, city=city, country=country, budget=budget)
        return [{"business_name": "Acme", "source": source}]

    monkeypatch.setattr(adapters_module, "_dispatch_source", fake_dispatch)

    registry = SourceRegistry()
    result = await registry.execute("GOOGLE_MAPS", "dental", "LA", "US", 10, {}, None)

    assert captured == {"source": "GOOGLE_MAPS", "niche": "dental", "city": "LA", "country": "US", "budget": 10}
    assert result.source == "GOOGLE_MAPS"
    assert result.leads == [{"business_name": "Acme", "source": "GOOGLE_MAPS"}]
    assert result.error is None


async def test_adapter_metrics_and_latency_recorded(monkeypatch):
    async def fake_dispatch(source, niche, city, country, budget, cfg, log_fn):
        return [{"business_name": "Acme"}]

    monkeypatch.setattr(adapters_module, "_dispatch_source", fake_dispatch)
    registry = SourceRegistry()
    await registry.execute("YELP", "dental", "LA", "", 10, {}, None)

    adapter = registry.get("YELP")
    assert adapter.metrics["calls"] == 1
    assert adapter.metrics["exception_count"] == 0
    assert adapter.metrics["zero_result_count"] == 0
    assert adapter.health_state == "HEALTHY"


async def test_exception_from_dispatch_is_isolated_not_raised(monkeypatch):
    async def fake_dispatch(source, niche, city, country, budget, cfg, log_fn):
        raise RuntimeError("scraper exploded")

    monkeypatch.setattr(adapters_module, "_dispatch_source", fake_dispatch)
    registry = SourceRegistry()

    # Must not raise — the registry isolates the failure and returns an AdapterResult.
    result = await registry.execute("HOTFROG", "dental", "LA", "", 10, {}, None)
    assert result.leads == []
    assert "scraper exploded" in result.error


async def test_run_scoped_circuit_breaker_disables_after_two_failures(monkeypatch):
    async def fake_dispatch(source, niche, city, country, budget, cfg, log_fn):
        return []  # zero results counts as a failure toward the breaker

    monkeypatch.setattr(adapters_module, "_dispatch_source", fake_dispatch)
    registry = SourceRegistry()
    adapter = registry.get("FOURSQUARE")

    assert adapter.enabled is True
    await registry.execute("FOURSQUARE", "x", "y", "", 10, {}, None)
    assert adapter.enabled is True  # 1 failure — degraded, not tripped yet
    assert adapter.health_state == "DEGRADED"

    await registry.execute("FOURSQUARE", "x", "y", "", 10, {}, None)
    assert adapter.enabled is False  # 2 consecutive failures — circuit breaker trips
    assert adapter.health_state == "UNHEALTHY"
    assert adapter not in registry.list_enabled()


async def test_success_resets_consecutive_failure_count(monkeypatch):
    calls = {"n": 0}

    async def fake_dispatch(source, niche, city, country, budget, cfg, log_fn):
        calls["n"] += 1
        return [] if calls["n"] == 1 else [{"business_name": "Recovered Co"}]

    monkeypatch.setattr(adapters_module, "_dispatch_source", fake_dispatch)
    registry = SourceRegistry()
    adapter = registry.get("TOP_LIST")

    await registry.execute("TOP_LIST", "x", "y", "", 10, {}, None)
    assert adapter.health_state == "DEGRADED"
    await registry.execute("TOP_LIST", "x", "y", "", 10, {}, None)
    assert adapter.enabled is True  # recovered — did not trip the breaker
    assert adapter.health_state == "HEALTHY"


def test_registries_are_independent_per_instance(monkeypatch):
    """Run-scoped health must not leak across registry instances (i.e. across runs)."""
    r1 = SourceRegistry()
    r1.get("YELP").enabled = False
    r2 = SourceRegistry()
    assert r2.get("YELP").enabled is True
