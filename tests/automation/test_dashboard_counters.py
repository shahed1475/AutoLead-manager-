"""Phase 10 — automation dashboard: real per-search pipeline counters."""
import pytest
from httpx import ASGITransport, AsyncClient

from backend import database as db

pytestmark = pytest.mark.asyncio


async def _client():
    from backend.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _run(**over):
    rid = await db.create_discovery_run({"mode": "AUTOMATION", "niche": "dental", "city": "Reno", "target_count": 20})
    await db.update_discovery_run(rid, {"status": "COMPLETED", **over})
    return rid


async def test_totals_sum_across_runs(clean_db):
    await _run(raw_candidates=20, results_count=15, emails_found=6, leads_scored=15, research_queued=3)
    await _run(raw_candidates=10, results_count=8, emails_found=2, leads_scored=8, research_queued=1)
    t = await db.get_discovery_run_totals("AUTOMATION")
    assert t["runs"] == 2
    assert t["results_count"] == 23
    assert t["emails_found"] == 8
    assert t["leads_scored"] == 23
    assert t["research_queued"] == 4


async def test_status_endpoint_exposes_pipeline_totals(clean_db):
    db_ = clean_db
    await db_.get_automation_state()
    await _run(raw_candidates=5, results_count=4, emails_found=1)
    async with await _client() as c:
        body = (await c.get("/api/automation/status")).json()
    assert "pipeline_totals" in body
    assert body["pipeline_totals"]["emails_found"] == 1


async def test_runs_endpoint_returns_funnel_rows(clean_db):
    await _run(raw_candidates=9, results_count=7, emails_found=3)
    async with await _client() as c:
        body = (await c.get("/api/automation/runs")).json()
    assert body["runs"] and body["runs"][0]["emails_found"] == 3
