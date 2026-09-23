"""Campaigns pick their own scraping sources when the UI sends none
(the Find leads page no longer asks the user to choose scrapers)."""
import pytest
from httpx import ASGITransport, AsyncClient

from backend.routers import campaigns as campaigns_router


class _Plan:
    def __init__(self, sources):
        self.recommended_sources = sources


def _planner_returning(sources=None, raises=False):
    class FakePlanner:
        async def plan(self, query, niche, city, country="", mode="QUICK"):
            assert mode == "CAMPAIGN"
            if raises:
                raise RuntimeError("ollama down")
            return _Plan(sources)
    return FakePlanner


async def test_auto_sources_keeps_only_campaign_supported_sources(monkeypatch):
    monkeypatch.setattr(campaigns_router, "DiscoveryPlanner",
                        _planner_returning(["GOOGLE_MAPS", "DUCKDUCKGO", "YELLOW_PAGES", "GOOGLE_MAPS"]))
    assert await campaigns_router._auto_sources("dentist", "Dubai") == ["GOOGLE_MAPS", "YELLOW_PAGES"]


@pytest.mark.parametrize("planner", [_planner_returning(["DUCKDUCKGO"]), _planner_returning(raises=True)])
async def test_auto_sources_falls_back_to_defaults(monkeypatch, planner):
    monkeypatch.setattr(campaigns_router, "DiscoveryPlanner", planner)
    assert await campaigns_router._auto_sources("dentist", "Dubai") == ["GOOGLE_MAPS", "GOOGLE_SEARCH"]


@pytest.fixture
def no_real_campaign(monkeypatch):
    """Stub the scrape/send task and restore the shared run state."""
    calls = []

    async def fake_task(*args, **kwargs):
        calls.append(args)
    monkeypatch.setattr(campaigns_router, "_run_campaign_task", fake_task)
    saved = dict(campaigns_router._run_state)
    yield calls
    campaigns_router._run_state.clear()
    campaigns_router._run_state.update(saved)


async def _start(body):
    from backend.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/campaign/start", json=body)


async def test_start_without_sources_uses_planner(clean_db, monkeypatch, no_real_campaign):
    monkeypatch.setattr(campaigns_router, "DiscoveryPlanner", _planner_returning(["GOOGLE_SEARCH", "BING_SEARCH"]))
    resp = await _start({"niche": "saas startups", "city": "Austin", "channel": "EMAIL"})
    assert resp.status_code == 200
    assert campaigns_router._run_state["sources"] == ["GOOGLE_SEARCH", "BING_SEARCH"]
    assert no_real_campaign and no_real_campaign[0][5] == ["GOOGLE_SEARCH", "BING_SEARCH"]


async def test_start_with_explicit_sources_is_unchanged(clean_db, monkeypatch, no_real_campaign):
    monkeypatch.setattr(campaigns_router, "DiscoveryPlanner", _planner_returning(raises=True))
    resp = await _start({"niche": "dentist", "city": "Dubai", "channel": "EMAIL", "sources": ["YELP"]})
    assert resp.status_code == 200
    assert campaigns_router._run_state["sources"] == ["YELP"]
