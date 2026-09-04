"""
runner.py — run_automation_slice: the JobQueue handler that walks
automation_queue from current_position, one niche x location at a time,
checkpointing progress atomically after each item and stopping when the daily
limit or time window is hit, or the user pauses/stops. Never raises out.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .. import database as db
from .config import get_automation_settings
from .lead_search_service import get_lead_search_service

logger = logging.getLogger(__name__)

DURATION_UNLIMITED = 0
_STALE_SLICE_SECONDS = 1200
_RETRY_BACKOFF_SECONDS = 5

_STOP_STATUSES = {"PAUSED", "STOPPED"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().replace(tzinfo=None).isoformat()


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", ""))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


async def _log(level: str, msg: str) -> None:
    try:
        await db.append_automation_log(level, msg)
    except Exception:
        logger.debug("automation_log write failed", exc_info=True)
    logger.info("[AUTOMATION] %s", msg)


async def run_automation_slice(payload: Optional[Dict[str, Any]] = None) -> None:
    try:
        await _run_slice()
    except Exception as exc:  # last-resort — a slice must never crash the worker
        logger.error("run_automation_slice crashed: %s", exc, exc_info=True)
        try:
            await db.update_automation_state({"status": "SCHEDULED", "last_run_finished_at": _now_iso()})
            await _log("ERROR", f"Automation run stopped unexpectedly: {exc}")
        except Exception:
            logger.error("could not persist automation failure state", exc_info=True)


async def _run_slice() -> None:
    cfg = await get_automation_settings()
    state = await db.get_automation_state()

    # ── Guards ───────────────────────────────────────────────────────────
    if not cfg["automation_enabled"] and state["status"] != "SCHEDULED":
        return
    if state["status"] in _STOP_STATUSES:
        return
    if state["status"] == "RUNNING":
        started = _parse_ts(state.get("last_run_started_at"))
        updated = _parse_ts(state.get("updated_at"))
        fresh = max([t for t in (started, updated) if t], default=None)
        if fresh and (_now() - fresh).total_seconds() < _STALE_SLICE_SECONDS:
            return  # another slice genuinely owns this run
        await _log("WARN", "Previous run looked stalled — taking over.")

    limit = int(cfg["automation_daily_limit"])
    if int(state["today_count"] or 0) >= limit:
        await db.update_automation_state({"status": "LIMIT_REACHED"})
        return

    deadline = _parse_ts(state.get("duration_deadline"))
    duration_h = int(cfg["automation_duration_hours"])
    if duration_h != DURATION_UNLIMITED and deadline and _now() >= deadline:
        await db.update_automation_state({"status": "SCHEDULED"})
        return

    queue = await db.get_automation_queue(offset=0, limit=100000)
    pos = int(state["current_position"] or 0)
    queue = [q for q in queue if q["position"] >= pos]
    if not queue:
        await db.update_automation_state({"status": "COMPLETED"})
        return

    # ── Run ──────────────────────────────────────────────────────────────
    service = get_lead_search_service()
    target = int(cfg["automation_per_item_target"])
    max_retries = int(cfg["automation_max_retries"])
    await db.update_automation_state({"status": "RUNNING", "last_run_started_at": _now_iso()})
    await _log("INFO", f"Automation run started at position {pos}")

    today_count = int(state["today_count"] or 0)
    total_count = int(state["total_count"] or 0)
    completed = int(state["queue_completed"] or 0)

    for item in queue:
        live = await db.get_automation_state()
        if live["status"] in _STOP_STATUSES:
            await _log("INFO", f"Run {live['status'].lower()} at position {item['position']}")
            return
        if today_count >= limit:
            await db.update_automation_state({"status": "LIMIT_REACHED"})
            await _log("INFO", f"Daily limit {limit} reached — {today_count} leads today.")
            return
        if duration_h != DURATION_UNLIMITED and deadline and _now() >= deadline:
            await db.update_automation_state({"status": "SCHEDULED"})
            await _log("INFO", "Daily time window ended — resuming at the next scheduled run.")
            return

        loc = ", ".join(p for p in (item["city"], item["state"]) if p)
        await db.update_automation_queue_item(item["id"], {"status": "SEARCHING", "started_at": _now_iso()})
        await _log("INFO", f"Searching {item['niche']} / {loc}")

        result = None
        attempts = 0
        attempt_target = target
        for attempt in range(max_retries + 1):
            attempts = attempt + 1
            result = await service.search_leads(
                item["niche"], item["city"], item.get("state"), None, attempt_target,
            )
            if not result.error:
                break
            if attempt < max_retries:
                # Retry with a smaller target — re-issuing the identical call is
                # what made a timeout retry into another timeout.
                attempt_target = max(5, attempt_target // 2)
                await _log(
                    "WARN",
                    f"{item['niche']} / {loc}: {result.error} — "
                    f"retry {attempt + 1}/{max_retries} (target {attempt_target})",
                )
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS)

        if result.error and not result.new_leads:
            item_status = "FAILED"
        elif result.error:
            item_status = "PARTIAL"
        else:
            item_status = "COMPLETED"

        today_count += result.new_leads
        total_count += result.new_leads
        completed += 1

        await db.checkpoint_automation_progress(
            item["id"],
            {
                "status": item_status, "leads_found": result.total_found,
                "new_leads": result.new_leads, "attempts": attempts,
                "error_message": result.error, "finished_at": _now_iso(),
            },
            {
                "current_position": item["position"] + 1,
                "today_count": today_count, "total_count": total_count,
                "queue_completed": completed,
                "last_niche": item["niche"], "last_location": loc,
                "last_query": f"{item['niche']} {loc}".strip(),
                "last_success_at": _now_iso() if result.new_leads else None,
            },
        )
        await _log(
            "INFO" if item_status != "FAILED" else "ERROR",
            f"{item['niche']} / {loc}: {result.total_found} raw, "
            f"{result.new_leads} new, {getattr(result, 'merged_count', 0)} merged, "
            f"{getattr(result, 'emails_found', 0)} email(s)"
            + (f" ({result.error})" if result.error else ""),
        )

    await db.update_automation_state({"status": "COMPLETED", "last_run_finished_at": _now_iso()})
    await _log("INFO", "Queue complete.")
