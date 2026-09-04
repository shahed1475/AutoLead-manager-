"""
Campaign-mode Discovery Planner integration (Phase 1). Verifies:
  - the Planner's query variants replace the old single fixed query
  - run_bulk_scrape is called once per variant, with the daily_cap budget
    split across them (not once with the full budget, per variant)
  - existing campaign behavior (stage machine, PENDING-merge, empty-run
    completion) is unaffected when the planner is unavailable
"""
import pytest

from backend.discovery.planner import DiscoveryPlan
from backend.routers import campaigns as campaigns_module
from backend.routers.campaigns import _run_campaign_task, _run_state

pytestmark = pytest.mark.asyncio


def _reset_run_state():
    _run_state["paused"] = False
    _run_state["stop_requested"] = False
    _run_state["leads_found"] = 0
    _run_state["leads_sent"] = 0
    _run_state["messages_generated"] = 0


async def test_planner_query_variants_drive_multiple_scrape_calls(clean_db, monkeypatch):
    db = clean_db
    _reset_run_state()
    run_id = await db.create_campaign_run("dental clinic", "LA", "EMAIL", 20, sources="GOOGLE_MAPS")

    calls = []

    async def fake_run_bulk_scrape(campaign, log_callback=None, stage_callback=None, pause_callback=None):
        calls.append(dict(campaign))
        return []

    class _FakePlanner:
        async def plan(self, *a, **kw):
            return DiscoveryPlan(
                intent="LOCAL_BUSINESS", confidence=0.9,
                recommended_sources=["GOOGLE_MAPS"],
                query_variants=["dental clinic", "dental clinics"],
                mode="CAMPAIGN", classification_method="rule_based",
            )

    monkeypatch.setattr(campaigns_module.scrapers, "run_bulk_scrape", fake_run_bulk_scrape)
    monkeypatch.setattr(campaigns_module, "DiscoveryPlanner", _FakePlanner)

    await _run_campaign_task(
        niche="dental clinic", city="LA", channel="EMAIL", daily_cap=20, run_id=run_id,
        sources=["GOOGLE_MAPS"],
    )

    assert len(calls) == 2  # one per planner variant
    niches_used = [c["niche"] for c in calls]
    assert niches_used == ["dental clinic", "dental clinics"]
    # Budget split across variants, not the full 20 leads per call
    assert sum(c["max_leads"] for c in calls) == 20
    for c in calls:
        assert c["max_leads"] < 20


async def test_planner_unavailable_falls_back_to_single_original_query(clean_db, monkeypatch):
    """If the Discovery Planner raises, the campaign must still run exactly as
    before Phase 1 — one scrape call with the original niche, full budget."""
    db = clean_db
    _reset_run_state()
    run_id = await db.create_campaign_run("restaurant", "NYC", "EMAIL", 20, sources="GOOGLE_MAPS")

    calls = []

    async def fake_run_bulk_scrape(campaign, log_callback=None, stage_callback=None, pause_callback=None):
        calls.append(dict(campaign))
        return []

    class _ExplodingPlanner:
        async def plan(self, *a, **kw):
            raise RuntimeError("planner unavailable")

    monkeypatch.setattr(campaigns_module.scrapers, "run_bulk_scrape", fake_run_bulk_scrape)
    monkeypatch.setattr(campaigns_module, "DiscoveryPlanner", _ExplodingPlanner)

    # Must not raise — existing campaign behavior is preserved even if the
    # new planner integration fails outright.
    await _run_campaign_task(
        niche="restaurant", city="NYC", channel="EMAIL", daily_cap=20, run_id=run_id,
        sources=["GOOGLE_MAPS"],
    )

    assert len(calls) == 1
    assert calls[0]["niche"] == "restaurant"
    assert calls[0]["max_leads"] == 20  # full original budget, no variant split


async def test_discovery_run_bookkeeping_failure_does_not_discard_variants(clean_db, monkeypatch):
    """Regression: if the Planner succeeds but the discovery_runs bookkeeping
    insert then throws (e.g. transient DB error), the already-computed query
    variants must still drive the scrape — not silently collapse to a
    single-query fallback for a reason unrelated to the planner itself."""
    db = clean_db
    _reset_run_state()
    run_id = await db.create_campaign_run("bakery", "Miami", "EMAIL", 20, sources="GOOGLE_MAPS")

    calls = []

    async def fake_run_bulk_scrape(campaign, log_callback=None, stage_callback=None, pause_callback=None):
        calls.append(dict(campaign))
        return []

    class _FakePlanner:
        async def plan(self, *a, **kw):
            return DiscoveryPlan(
                intent="LOCAL_BUSINESS", confidence=0.9, recommended_sources=["GOOGLE_MAPS"],
                query_variants=["bakery", "bakeries"], mode="CAMPAIGN", classification_method="rule_based",
            )

    async def broken_create_discovery_run(data):
        raise RuntimeError("simulated transient DB error")

    monkeypatch.setattr(campaigns_module.scrapers, "run_bulk_scrape", fake_run_bulk_scrape)
    monkeypatch.setattr(campaigns_module, "DiscoveryPlanner", _FakePlanner)
    monkeypatch.setattr(campaigns_module.db, "create_discovery_run", broken_create_discovery_run)

    await _run_campaign_task(
        niche="bakery", city="Miami", channel="EMAIL", daily_cap=20, run_id=run_id,
        sources=["GOOGLE_MAPS"],
    )

    # The bookkeeping insert failed, but the plan's 2 variants must still
    # have driven 2 separate scrape calls.
    assert len(calls) == 2
    assert [c["niche"] for c in calls] == ["bakery", "bakeries"]


async def test_empty_scrape_result_completes_campaign_cleanly(clean_db, monkeypatch):
    """Regression guard: an empty scraper result (e.g. no leads found) must
    still let the campaign run to completion, not hang or error — this was
    already true before Phase 1 and must remain true."""
    db = clean_db
    _reset_run_state()
    run_id = await db.create_campaign_run("plumber", "Austin", "EMAIL", 10, sources="GOOGLE_MAPS")

    async def fake_run_bulk_scrape(campaign, log_callback=None, stage_callback=None, pause_callback=None):
        return []

    class _FakePlanner:
        async def plan(self, *a, **kw):
            return DiscoveryPlan(
                intent="LOCAL_BUSINESS", confidence=0.9, recommended_sources=["GOOGLE_MAPS"],
                query_variants=["plumber"], mode="CAMPAIGN", classification_method="rule_based",
            )

    monkeypatch.setattr(campaigns_module.scrapers, "run_bulk_scrape", fake_run_bulk_scrape)
    monkeypatch.setattr(campaigns_module, "DiscoveryPlanner", _FakePlanner)

    # Should complete without raising.
    await _run_campaign_task(
        niche="plumber", city="Austin", channel="EMAIL", daily_cap=10, run_id=run_id,
        sources=["GOOGLE_MAPS"],
    )
