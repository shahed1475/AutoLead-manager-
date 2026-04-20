from fastapi import APIRouter, BackgroundTasks
from ..scheduler import get_scheduler_status, run_campaign_now
from .scraper_router import _scraper_state
from .campaigns import get_campaign_state

router = APIRouter(prefix="/api/engine", tags=["engine"])


@router.get("/status")
async def engine_status():
    sched = get_scheduler_status()
    camp  = get_campaign_state()
    return {
        "scheduler_running":   sched["running"],
        "next_run":            sched["next_run"],
        "scraper_running":     _scraper_state["running"],
        "scraper_progress":    _scraper_state["progress"],
        "scraper_total":       _scraper_state["total"],
        "scraper_last_name":   _scraper_state["last_name"],
        "campaign_running":    camp["running"],
        "campaign_niche":      camp["niche"],
        "campaign_city":       camp["city"],
        "campaign_channel":    camp["channel"],
        "campaign_daily_cap":  camp["daily_cap"],
        "campaign_leads_found":camp["leads_found"],
        "campaign_leads_sent": camp["leads_sent"],
    }


@router.post("/run-now")
async def trigger_campaign_now(background_tasks: BackgroundTasks):
    """Manually fire the daily campaign job — AI generate + send all pending leads."""
    background_tasks.add_task(run_campaign_now)
    return {"queued": True, "message": "Daily campaign job triggered manually"}
