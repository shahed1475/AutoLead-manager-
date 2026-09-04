"""
scheduler_hooks.py — decides WHEN the automation runs.

automation_tick() is registered on APScheduler (IntervalTrigger, 2 min). It
never does search work itself — it only enqueues run_automation_slice on the
JobQueue when a daily run is due in the configured timezone.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import database as db
from ..queue_worker import get_queue
from .config import get_automation_settings
from .runner import run_automation_slice

logger = logging.getLogger(__name__)


def _now_local(tzname: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(tzname))
    except Exception:
        return datetime.now(ZoneInfo("UTC"))


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        h, m = str(value).split(":")
        return max(0, min(23, int(h))), max(0, min(59, int(m)))
    except (ValueError, AttributeError):
        return 7, 0


def _enqueue(queue) -> bool:
    if queue is None:
        return False
    return bool(queue.enqueue_nowait("AUTOMATION", {}, run_automation_slice))


async def _daily_reset_and_deadline(cfg: dict, today_local: str) -> None:
    duration_h = int(cfg["automation_duration_hours"])
    deadline = None
    if duration_h > 0:
        deadline = (datetime.now(timezone.utc) + timedelta(hours=duration_h)).replace(tzinfo=None).isoformat()
    await db.update_automation_state({
        "today_date": today_local, "today_count": 0, "status": "SCHEDULED",
        "duration_deadline": deadline,
    })


async def automation_tick() -> None:
    try:
        cfg = await get_automation_settings()
        if not cfg["automation_enabled"]:
            return
        state = await db.get_automation_state()
        if state["status"] in ("RUNNING", "STOPPED"):
            return
        if int(state.get("queue_total") or 0) == 0:
            return

        tzname = cfg["automation_timezone"]
        now_local = _now_local(tzname)
        today_local = now_local.date().isoformat()
        if state.get("today_date") == today_local:
            return  # already ran (or is running) today

        start_h, start_m = _parse_hhmm(cfg["automation_start_time"])
        if (now_local.hour, now_local.minute) < (start_h, start_m):
            return

        await _daily_reset_and_deadline(cfg, today_local)
        if _enqueue(get_queue()):
            await db.append_automation_log("INFO", f"Scheduled run started ({tzname} {cfg['automation_start_time']}).")
        else:
            await db.update_automation_state({"status": "SCHEDULED"})
            await db.append_automation_log("ERROR", "Could not enqueue scheduled run — queue unavailable.")
    except Exception as exc:
        logger.warning("automation_tick failed: %s", exc, exc_info=True)


async def resume_running_slice(queue) -> int:
    """Startup reconciler — a RUNNING state means the previous worker died
    (at startup there is never a live slice). Flip it back to SCHEDULED so the
    re-queued slice's own stale-guard doesn't mistake the pre-crash timestamp
    for a live owner, then re-enqueue. State + per-item statuses make the
    slice resume from current_position."""
    state = await db.get_automation_state()
    if state["status"] != "RUNNING":
        return 0
    await db.update_automation_state({"status": "SCHEDULED"})
    if _enqueue(queue):
        await db.append_automation_log("WARN", "Resumed an interrupted automation run after restart.")
        return 1
    return 0


async def kick_slice_now(queue) -> bool:
    """POST /start and /resume: reset the daily counter if it's a new day,
    then enqueue a slice immediately (still bounded by the daily limit)."""
    cfg = await get_automation_settings()
    state = await db.get_automation_state()
    today_local = _now_local(cfg["automation_timezone"]).date().isoformat()
    if state.get("today_date") != today_local:
        await _daily_reset_and_deadline(cfg, today_local)
    else:
        await db.update_automation_state({"status": "SCHEDULED"})
    return _enqueue(queue)
