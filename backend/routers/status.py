from datetime import datetime, timezone
from fastapi import APIRouter, BackgroundTasks
from ..scheduler import get_scheduler_status, run_campaign_now
from .scraper_router import _scraper_state
from .campaigns import get_campaign_state

router = APIRouter(prefix="/api/engine", tags=["engine"])


def _live_campaign_metrics(camp: dict) -> dict:
    """Leads/min + ETA — derived from started_at + counts, not separately tracked."""
    leads_per_min = None
    eta_seconds   = None
    started_at    = camp.get("started_at")

    if camp.get("running") and started_at:
        try:
            started = datetime.fromisoformat(started_at)
            elapsed_min = max((datetime.now(timezone.utc) - started).total_seconds() / 60, 1 / 60)
            sent = camp.get("leads_sent", 0)
            if sent > 0:
                leads_per_min = round(sent / elapsed_min, 2)
                remaining = max(camp.get("leads_found", 0) - sent, 0)
                if leads_per_min > 0:
                    eta_seconds = round((remaining / leads_per_min) * 60)
        except (ValueError, TypeError):
            pass

    return {"campaign_leads_per_min": leads_per_min, "campaign_eta_seconds": eta_seconds}


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
        "campaign_running":      camp["running"],
        "campaign_paused":       camp.get("paused", False),
        "campaign_stage":        camp.get("stage", "QUEUED"),
        "campaign_niche":        camp["niche"],
        "campaign_city":         camp["city"],
        "campaign_country":      camp.get("country"),
        "campaign_channel":      camp["channel"],
        "campaign_daily_cap":    camp["daily_cap"],
        "campaign_sources":      camp.get("sources", []),
        "campaign_hot_warm_only": camp.get("hot_warm_only", True),
        "campaign_leads_found":  camp["leads_found"],
        "campaign_leads_sent":   camp["leads_sent"],
        "campaign_current_lead": camp.get("current_lead"),
        "campaign_messages_generated": camp.get("messages_generated", 0),
        "campaign_started_at":   camp.get("started_at"),
        **_live_campaign_metrics(camp),
    }


@router.post("/run-now")
async def trigger_campaign_now(background_tasks: BackgroundTasks):
    """Manually fire the daily campaign job — AI generate + send all pending leads."""
    background_tasks.add_task(run_campaign_now)
    return {"queued": True, "message": "Daily campaign job triggered manually"}
