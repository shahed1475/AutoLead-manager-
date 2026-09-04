"""
routers/discovery.py — Phase 1 Universal Lead Discovery: Quick Search API.

Separate surface from the pre-existing routers/scraper_router.py
(legacy single-global-state, Google-Maps-only, still used by Dashboard.jsx —
untouched by this work). Quick Search runs are tracked per-run via
lead_discovery_runs, executed on the existing JobQueue (queue_worker.py),
not an ad-hoc BackgroundTasks call.
"""
import logging

from fastapi import APIRouter, HTTPException, Request

from .. import database as db
from ..discovery.quick_search import run_quick_search
from ..models import LeadSearchRequest
from ..queue_worker import get_queue
from ..rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/discovery", tags=["discovery"])

_TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


@router.post("/search")
@limiter.limit("10/minute")
async def create_search(request: Request, payload: LeadSearchRequest):
    run_id = await db.create_discovery_run({
        "mode":         "QUICK",
        "raw_query":    payload.query,
        "niche":        payload.niche or payload.query,
        "city":         payload.city,
        "country":      payload.country,
        "target_count": payload.target_count,
    })

    queue = get_queue()
    if queue is None:
        # JobQueue not started (should not happen outside of tests that skip
        # app lifespan) — fail the run explicitly rather than hang forever.
        await db.update_discovery_run(run_id, {"status": "FAILED", "error_message": "Job queue unavailable"})
        raise HTTPException(status_code=503, detail="Search queue is not available")

    enqueued = queue.enqueue_nowait("QUICK_SEARCH", {"run_id": run_id}, run_quick_search)
    if not enqueued:
        await db.update_discovery_run(run_id, {"status": "FAILED", "error_message": "Job queue is full"})
        raise HTTPException(status_code=503, detail="Search queue is full — try again shortly")

    run = await db.get_discovery_run(run_id)
    return {"run_id": run_id, "status": run["status"]}


@router.get("/search/active")
async def get_active_search():
    """The run the frontend should reconnect to after a navigation/refresh:
    the most recent still-running QUICK run, else the most recent QUICK run
    overall (so a just-completed run still shows its results), else null.
    Static path — must be declared before /search/{run_id}."""
    run = await db.get_latest_discovery_run(mode="QUICK", active_only=True)
    if run is None:
        run = await db.get_latest_discovery_run(mode="QUICK", active_only=False)
    return run  # may be null — the frontend treats that as "nothing to reconnect to"


@router.get("/search/{run_id}")
async def get_search_status(run_id: int):
    run = await db.get_discovery_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Discovery run not found")
    return run


@router.get("/search/{run_id}/results")
async def get_search_results(run_id: int):
    run = await db.get_discovery_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Discovery run not found")
    leads = await db.get_leads_for_discovery_run(run_id)
    return {"run_id": run_id, "status": run["status"], "results_count": len(leads), "leads": leads}


@router.post("/search/{run_id}/cancel")
async def cancel_search(run_id: int):
    run = await db.get_discovery_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Discovery run not found")
    if run["status"] in _TERMINAL_STATUSES:
        return run
    await db.update_discovery_run(run_id, {"status": "CANCELLED"})
    return await db.get_discovery_run(run_id)
