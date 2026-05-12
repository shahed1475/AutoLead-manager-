import asyncio
from fastapi import APIRouter, BackgroundTasks
from .. import database as db
from .. import scraper
from ..models import ScraperRequest
from ..scrapers.bing_search import scrape_bing_search

router = APIRouter(prefix="/api/scraper", tags=["scraper"])

_scraper_state: dict = {
    "running":   False,
    "progress":  0,
    "total":     0,
    "last_name": "",
    "last_log":  "",
}

# Separate state for Bing scraper
_bing_state: dict = {
    "running": False,
    "progress": 0,
    "total": 0,
    "last_log": "",
    "results": [],
}


@router.post("/search")
async def search_leads(payload: ScraperRequest, background_tasks: BackgroundTasks):
    if _scraper_state["running"]:
        return {"error": "Scraper is already running. Wait for it to finish."}

    async def _progress(done: int, total: int, name: str) -> None:
        _scraper_state.update({"progress": done, "total": total, "last_name": name})

    async def _run() -> None:
        loop = asyncio.get_running_loop()

        def _log_callback(msg: str) -> None:
            """
            Sync callback called from the Selenium thread.
            Schedules an async DB write on the event loop so SSE picks it up.
            """
            _scraper_state["last_log"] = msg
            coro = db.log_campaign_action(None, "SCRAPER", "PROGRESS", True, msg)
            asyncio.run_coroutine_threadsafe(coro, loop)

        _scraper_state.update({
            "running":   True,
            "progress":  0,
            "total":     payload.max_results,
            "last_name": "",
            "last_log":  "",
        })

        try:
            leads = await scraper.scrape_google_maps(
                niche=payload.niche,
                city=payload.city,
                max_results=payload.max_results,
                query=payload.query,          # kept for compat, ignored by scraper
                progress_callback=_progress,
                log_callback=_log_callback,
            )

            for lead in leads:
                lead_id, is_new = await db.create_lead_deduped(lead)
                if is_new:
                    # Logged as FOUND so SSE "✅ Found" line appears
                    await db.log_campaign_action(lead_id, "SCRAPE", "FOUND", True)
                else:
                    _log_callback(f"⏭️  Skipped duplicate: {lead.get('business_name')}")

        finally:
            _scraper_state["running"] = False

    background_tasks.add_task(_run)
    return {"queued": True, "max_results": payload.max_results}


@router.get("/status")
async def scraper_status():
    return _scraper_state
