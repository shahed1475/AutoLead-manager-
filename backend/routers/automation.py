"""
routers/automation.py — Lead Search Automation API (spec §6). Discovery-only:
no outreach. All routes behind the existing single-user session dependency
(applied in main.py). Scheduling is backend-only — the frontend just polls
GET /status.
"""
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from .. import database as db
from ..automation.config import _INT_BOUNDS, get_automation_settings
from ..automation.file_import import parse_upload
from ..automation.lead_search_service import get_lead_search_service
from ..automation.queue_builder import build_queue
from ..automation.scheduler_hooks import kick_slice_now
from ..queue_worker import get_queue
from ..rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/automation", tags=["automation"])

_ACTIVE = {"RUNNING", "SCHEDULED"}
_DONE_ITEM_STATUSES = ("COMPLETED", "PARTIAL", "FAILED", "SKIPPED")
# statuses from which turning the "Enable Daily Automation" toggle ON should
# hand control back to the daily scheduler
_REVIVABLE = {"IDLE", "STOPPED", "COMPLETED", "LIMIT_REACHED"}


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", ""))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _compute_next_run(cfg: Dict[str, Any], state: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """The datetime the automation will actually fire next, or None + a plain
    reason it won't. The dashboard shows one or the other instead of echoing
    the raw start-time field."""
    if not cfg["automation_enabled"]:
        return None, "Disabled — turn on 'Enable Daily Automation'"
    status = state.get("status")
    if status == "PAUSED":
        return None, "Paused — press Resume"
    if status == "STOPPED":
        return None, "Stopped — press Start Now"
    if int(state.get("queue_total") or 0) == 0:
        return None, "No search list imported yet"
    if status == "COMPLETED":
        return None, "Search list finished — import more or Reset Progress"
    if status == "RUNNING":
        return None, "Running now"

    try:
        tz = ZoneInfo(str(cfg["automation_timezone"]))
    except Exception:
        tz = ZoneInfo("UTC")
    now = datetime.now(tz)
    try:
        hh, mm = (int(x) for x in str(cfg["automation_start_time"]).split(":"))
    except ValueError:
        hh, mm = 7, 0
    run_today = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    ran_today = state.get("today_date") == now.date().isoformat()

    if status == "LIMIT_REACHED":
        return (run_today + timedelta(days=1)).isoformat(), "Daily limit reached — resumes tomorrow"
    if ran_today:
        return (run_today + timedelta(days=1)).isoformat(), "Already ran today — next run tomorrow"
    if now < run_today:
        return run_today.isoformat(), ""
    return (now + timedelta(minutes=2)).isoformat(), "Due now — starting within ~2 min"


class ConfirmImport(BaseModel):
    locations: List[Dict[str, Optional[str]]]
    niches: List[str]
    filename: Optional[str] = None
    layout: str = "combined"


class SettingsUpdate(BaseModel):
    automation_enabled: Optional[bool] = None
    automation_daily_limit: Optional[int] = None
    automation_start_time: Optional[str] = None
    automation_timezone: Optional[str] = None
    automation_duration_hours: Optional[int] = None
    automation_per_item_target: Optional[int] = None
    automation_max_retries: Optional[int] = None


async def _status_body() -> Dict[str, Any]:
    state = await db.get_automation_state()
    cfg = await get_automation_settings()
    counts = await db.count_automation_queue_by_status()
    total = int(state.get("queue_total") or sum(counts.values()))
    done = sum(counts.get(s, 0) for s in _DONE_ITEM_STATUSES)
    limit = int(cfg["automation_daily_limit"]) or 1
    totals = await db.get_discovery_run_totals("AUTOMATION")
    rs = await db.get_research_status_counts("automation")
    next_run_at, next_run_reason = _compute_next_run(cfg, state)
    return {
        **state,
        "settings": cfg,
        "next_run_at": next_run_at,
        "next_run_reason": next_run_reason,
        "counts_by_status": counts,
        "progress_pct": round(min(100.0, 100.0 * int(state.get("today_count") or 0) / limit), 1),
        "searches_completed": done,
        "searches_remaining": max(0, total - done),
        "current_niche": state.get("last_niche"),
        "current_location": state.get("last_location"),
        "pipeline_totals": totals,   # raw / results / emails_found / leads_scored / research_queued / failed
        "research_queue": {
            "queued":      rs.get("QUEUED", 0),
            "researching": rs.get("RESEARCHING", 0),
            "completed":   rs.get("COMPLETED", 0),
            "failed":      rs.get("FAILED", 0),
        },
    }


@router.get("/runs")
async def get_runs(limit: int = 20):
    """Per-search funnel rows for this automation (raw → stored → emails → scored → research)."""
    return {"runs": await db.get_recent_discovery_runs("AUTOMATION", limit=min(limit, 100))}


@router.post("/import/preview")
@limiter.limit("10/minute")
async def import_preview(
    request: Request,
    file: UploadFile = File(...),
    file2: Optional[UploadFile] = File(None),
):
    data = await file.read()
    d2 = await file2.read() if file2 is not None else None
    try:
        parsed = parse_upload(file.filename, data, file2.filename if file2 else None, d2)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    cfg = await get_automation_settings()
    limit = int(cfg["automation_daily_limit"]) or 1
    return {
        "locations": parsed.locations,
        "niches": parsed.niches,
        "n_locations": len(parsed.locations),
        "n_niches": len(parsed.niches),
        "combinations": parsed.combinations,
        "layout": parsed.layout,
        "warnings": parsed.warnings,
        "estimated_days": max(1, math.ceil(parsed.combinations / limit)),
        "estimate_note": "Rough minimum — assumes at least one new lead per search; actual results vary.",
    }


@router.post("/import/confirm")
async def import_confirm(payload: ConfirmImport):
    if not payload.locations or not payload.niches:
        raise HTTPException(status_code=422, detail="Need at least one location and one niche.")
    items = build_queue(payload.locations, payload.niches)
    cfg = await get_automation_settings()
    import_id = await db.create_automation_import({
        "filename": payload.filename, "layout": payload.layout,
        "n_locations": len(payload.locations), "n_niches": len(payload.niches),
        "n_combinations": len(items),
    })
    async with db.transaction() as conn:
        await conn.execute("DELETE FROM automation_queue")
    await db.bulk_insert_automation_queue(items)
    await db.update_automation_state({
        "current_position": 0, "today_count": 0, "queue_total": len(items),
        "queue_completed": 0, "import_id": import_id,
        "status": "SCHEDULED" if cfg["automation_enabled"] else "IDLE",
    })
    await db.append_automation_log(
        "INFO",
        f"Imported {len(payload.locations)} locations x {len(payload.niches)} niches -> {len(items)} searches.",
    )
    return await _status_body()


@router.get("/status")
async def get_status():
    return await _status_body()


@router.get("/queue")
async def get_queue_items(status: Optional[str] = None, offset: int = 0, limit: int = 100):
    items = await db.get_automation_queue(status=status, offset=offset, limit=min(limit, 500))
    return {"items": items, "counts_by_status": await db.count_automation_queue_by_status()}


@router.get("/log")
async def get_log(limit: int = 100):
    return {"lines": await db.get_automation_log(limit=min(limit, 500))}


@router.put("/settings")
async def update_settings(payload: SettingsUpdate):
    data = payload.model_dump(exclude_none=True)
    if "automation_start_time" in data:
        try:
            h, m = str(data["automation_start_time"]).split(":")
            h, m = int(h), int(m)
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
        except (ValueError, AttributeError):
            raise HTTPException(status_code=422, detail="start_time must be HH:MM (00:00–23:59)")
        data["automation_start_time"] = f"{h:02d}:{m:02d}"
    if "automation_timezone" in data:
        try:
            ZoneInfo(data["automation_timezone"])
        except Exception:
            raise HTTPException(status_code=422, detail="Unknown timezone")
    for key, (lo, hi) in _INT_BOUNDS.items():
        if key in data:
            v = data[key]
            if isinstance(v, bool) or not isinstance(v, int) or not (lo <= v <= hi):
                raise HTTPException(
                    status_code=422,
                    detail=f"{key} must be an integer between {lo} and {hi}",
                )

    for k, v in data.items():
        await db.upsert_setting(k, "true" if v is True else "false" if v is False else str(v))

    await _reconcile_state_with_settings(data)
    return await _status_body()


async def _reconcile_state_with_settings(data: Dict[str, Any]) -> None:
    """Make the toggles the user just saved actually take effect on the run
    state, not just the stored config."""
    if "automation_enabled" in data:
        state = await db.get_automation_state()
        cur = state.get("status")
        if data["automation_enabled"]:
            if cur in _REVIVABLE:
                # hand back to the scheduler; clear today's marker so it may run today
                await db.update_automation_state({"status": "SCHEDULED", "today_date": ""})
                await db.append_automation_log("INFO", "Automation enabled — handed to the daily scheduler.")
        elif cur in _ACTIVE:
            await db.update_automation_state({"status": "STOPPED"})
            await db.append_automation_log("INFO", "Automation disabled in settings.")

    if "automation_duration_hours" in data:
        state = await db.get_automation_state()
        # a time window is only live if there's a deadline or a run in progress
        if state.get("duration_deadline") or state.get("status") == "RUNNING":
            hours = int(data["automation_duration_hours"])
            anchor = _parse_iso(state.get("last_run_started_at")) or datetime.now(timezone.utc)
            new_deadline = (
                "" if hours <= 0
                else (anchor + timedelta(hours=hours)).replace(tzinfo=None).isoformat()
            )
            await db.update_automation_state({"duration_deadline": new_deadline})


@router.post("/test-search")
@limiter.limit("3/minute")
async def test_search(request: Request):
    """Run ONE real search now with the current settings, on the next pending
    queue item, and return its funnel. Leads found are kept (merge-dedup means
    no duplicates); the daily counter and queue position are NOT advanced, so
    this doesn't disturb the schedule."""
    items = await db.get_automation_queue(status="PENDING", offset=0, limit=1)
    if not items:
        items = await db.get_automation_queue(offset=0, limit=1)
    if not items:
        raise HTTPException(status_code=503, detail="No search list imported yet.")
    item = items[0]
    cfg = await get_automation_settings()
    loc = ", ".join(p for p in (item.get("city"), item.get("state")) if p)
    try:
        result = await get_lead_search_service().search_leads(
            item["niche"], item["city"], item.get("state"), None,
            int(cfg["automation_per_item_target"]),
        )
    except Exception as exc:  # a test must never 500
        logger.warning("automation test-search failed", exc_info=True)
        return {"niche": item["niche"], "location": loc, "new_leads": 0, "total_found": 0,
                "merged": 0, "emails_found": 0, "sources_used": [], "error": str(exc)}
    await db.append_automation_log(
        "INFO", f"Test search: {item['niche']} / {loc} → {result.new_leads} new, "
        f"{result.total_found} raw" + (f" ({result.error})" if result.error else ""),
    )
    return {
        "niche": item["niche"], "location": loc,
        "new_leads": result.new_leads, "total_found": result.total_found,
        "merged": getattr(result, "merged_count", 0),
        "emails_found": getattr(result, "emails_found", 0),
        "sources_used": getattr(result, "sources_used", []) or [],
        "error": result.error,
    }


@router.post("/start")
async def start_now(request: Request):
    state = await db.get_automation_state()
    if int(state.get("queue_total") or 0) == 0:
        raise HTTPException(status_code=503, detail="No search queue imported yet.")
    cfg = await get_automation_settings()
    if int(state.get("today_count") or 0) >= int(cfg["automation_daily_limit"]):
        raise HTTPException(status_code=409, detail="Daily lead limit already reached — try again tomorrow.")
    if not await kick_slice_now(get_queue()):
        raise HTTPException(status_code=503, detail="Job queue unavailable.")
    return await _status_body()


@router.post("/pause")
async def pause():
    state = await db.get_automation_state()
    if state["status"] not in _ACTIVE:
        raise HTTPException(status_code=409, detail=f"Automation is {state['status']}, cannot pause.")
    await db.update_automation_state({"status": "PAUSED", "paused_at": db._now_naive_iso()})
    await db.append_automation_log("INFO", "Paused by user.")
    return await db.get_automation_state()


@router.post("/resume")
async def resume():
    await db.update_automation_state({"status": "SCHEDULED"})
    await kick_slice_now(get_queue())
    await db.append_automation_log("INFO", "Resumed by user.")
    return await db.get_automation_state()


@router.post("/stop")
async def stop():
    await db.update_automation_state({"status": "STOPPED"})
    await db.append_automation_log("INFO", "Stopped by user.")
    return await db.get_automation_state()


@router.post("/reset")
async def reset(confirm: bool = False):
    if not confirm:
        raise HTTPException(status_code=400, detail="Pass ?confirm=true — this resets the search position.")
    await db.reset_automation_queue()
    await db.update_automation_state({
        "current_position": 0, "today_count": 0, "queue_completed": 0, "status": "IDLE",
    })
    await db.append_automation_log("WARN", "Progress reset by user (total leads preserved).")
    return await _status_body()
