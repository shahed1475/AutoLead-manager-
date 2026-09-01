"""
session.py — persistence-aware orchestration: wires agent.run_research_session
to the DB (lead_research_sessions/results/evidence) and, optionally, the main
leads table via database.create_or_merge_lead (Phase 1's merge+dedup+
provenance work — reused, not duplicated). agent.py itself stays DB-agnostic
and independently unit-testable (see tests/research_agent/).
"""
from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .. import database as db
from .agent import run_research_session
from .config import get_research_config
from .models import RESEARCH_COMPLETE, RESEARCH_PARTIAL, ResearchLead

logger = logging.getLogger(__name__)

# A session that failed part-way can be re-queued this many times before it's
# considered permanently failed (guards against a crash-loop).
MAX_RESUMES = 3

# Statuses whose worker is gone after a server restart — reconciled at startup.
_INTERRUPTED_STATUSES = ("QUEUED", "RUNNING", "CANCEL_REQUESTED")


def _load_processed_keys(session: Dict[str, Any]) -> set:
    raw = session.get("processed_keys")
    if not raw:
        return set()
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        return set(parsed) if isinstance(parsed, list) else set()
    except (json.JSONDecodeError, TypeError):
        return set()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


# Sources used, in order, to seed research when the browser's own google_search
# discovery is blocked. Both are existing scrapers — no new scraper is added.
_FALLBACK_DISCOVERY_SOURCES = ("GOOGLE_MAPS", "YELLOW_PAGES")


async def _scraper_discovery_fallback(
    niche: str, location: str, country: str, limit: int,
) -> List[Dict[str, Any]]:
    """Seed candidates from the existing discovery scrapers (reused via the
    Phase-1 SourceRegistry — no new scraper, no new dispatch path) for when a
    Google consent/CAPTCHA wall makes the agent's own google_search discovery
    return nothing. Returns lightweight hint dicts the research loop then
    deep-researches exactly like google_search candidates."""
    from ..discovery.adapters import get_registry
    from ..scraper import _scraper_cfg

    registry = get_registry()
    scraper_cfg = await _scraper_cfg()
    hints: List[Dict[str, Any]] = []
    seen: set = set()
    for source in _FALLBACK_DISCOVERY_SOURCES:
        if len(hints) >= limit:
            break
        try:
            res = await registry.execute(source, niche, location, country or "", limit, scraper_cfg)
        except Exception:
            logger.warning("[RESEARCH] fallback discovery source %s failed", source, exc_info=True)
            continue
        for raw in res.leads or []:
            name = (raw.get("business_name") or "").strip()
            website = (raw.get("website") or "").strip()
            key = website.lower() or name.lower()
            if not name or key in seen:
                continue
            seen.add(key)
            hints.append({
                "business_name": name,
                "website": website or None,
                "phone": raw.get("phone"),
                "city": raw.get("city") or location,
                "state": raw.get("state"),
                "country": country or raw.get("country"),
            })
            if len(hints) >= limit:
                break
    return hints


def _evidence_dicts(lead: ResearchLead) -> List[Dict[str, Any]]:
    return [
        {
            "field_name": e.field_name, "source_type": e.source_type, "source_url": e.source_url,
            "snippet": e.snippet, "confidence": e.confidence, "status": e.status,
        }
        for e in lead.evidence
    ]


def enqueue_session(queue, session_row: Dict[str, Any]) -> bool:
    """Put a research session on the JobQueue. Shared by POST /start,
    POST /{id}/resume, and the startup reconciler so there is exactly one
    place that knows how to (re)launch a session's worker."""
    session_id = session_row["id"]

    async def _handler(_payload: Dict[str, Any]) -> None:
        await run_research_session_persisted(
            session_id=session_id,
            niche=session_row["niche"],
            location=session_row["location"],
            target_count=session_row["target_count"],
        )

    return bool(queue.enqueue_nowait("RESEARCH_AGENT", {"session_id": session_id}, _handler))


async def reconcile_interrupted_sessions(queue) -> int:
    """Called once at app startup. A session left QUEUED/RUNNING/
    CANCEL_REQUESTED means the previous process died mid-run. Re-queue it if
    it is under the resume cap (its already-saved results and processed_keys
    let it pick up where it stopped); otherwise mark it FAILED-resumable so a
    human can retry from the UI."""
    if queue is None:
        return 0
    requeued = 0
    for session in await db.list_interrupted_research_sessions():
        resume_count = int(session.get("resume_count") or 0)
        if resume_count >= MAX_RESUMES:
            await db.update_research_session(session["id"], {
                "status": "FAILED", "resumable": 1, "research_phase": "FAILED",
                "error_message": "Server restarted too many times during this run.",
                "finished_at": _now_iso(),
            })
            continue
        await db.update_research_session(session["id"], {
            "resume_count": resume_count + 1, "status": "QUEUED", "resumable": 0,
        })
        if enqueue_session(queue, session):
            requeued += 1
            logger.info("[RESEARCH] re-queued interrupted session %s (resume %d)", session["id"], resume_count + 1)
        else:
            await db.update_research_session(session["id"], {
                "status": "FAILED", "resumable": 1,
                "error_message": "Could not re-queue after restart — queue full.",
                "finished_at": _now_iso(),
            })
    return requeued


async def run_research_session_persisted(
    session_id: int,
    niche: str,
    location: str,
    target_count: int,
    seed_businesses: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """JobQueue handler body. Never raises out — any unhandled exception is
    caught and persisted as status=FAILED so the session row is always left
    in a terminal, queryable state (matches Phase 1's quick_search.py
    precedent for the same failure-visibility reason)."""
    cfg = await get_research_config()
    save_to_leads = cfg["research_agent_save_to_leads"]

    existing = await db.get_research_session(session_id) or {}
    already_processed = _load_processed_keys(existing)
    resume_count = int(existing.get("resume_count") or 0)
    if already_processed:
        logger.info("[RESEARCH] session %s resuming — %d businesses already done", session_id, len(already_processed))

    await db.update_research_session(session_id, {
        "status": "RUNNING", "started_at": existing.get("started_at") or _now_iso(),
        "resumable": 0, "error_message": "", "research_phase": "STARTING",
    })

    async def on_action(lead: ResearchLead, action, result) -> None:
        await db.update_research_session(session_id, {
            "current_action": action.action,
            "current_query": action.params.get("query") if action.action == "google_search" else "",
            "current_business": lead.business_name or "",
        })

    async def on_progress(updates: Dict[str, Any]) -> None:
        payload = dict(updates)
        if "processed_keys" in payload:
            payload["processed_keys"] = json.dumps(payload["processed_keys"])
        await db.update_research_session(session_id, payload)

    async def is_cancelled() -> bool:
        session = await db.get_research_session(session_id)
        return bool(session) and session.get("status") in ("CANCELLED", "CANCEL_REQUESTED")

    async def on_lead_complete(lead: ResearchLead) -> None:
        lead_id = None
        if save_to_leads and lead.business_name and lead.research_status in (RESEARCH_COMPLETE, RESEARCH_PARTIAL):
            try:
                candidate = {
                    "business_name": lead.business_name, "phone": lead.business_phone,
                    "email": lead.business_email, "website": lead.business_website,
                    "city": lead.city, "country": lead.country, "niche": niche,
                    "source": "BROWSER_RESEARCH_AGENT",
                }
                lead_id, _, _ = await db.create_or_merge_lead(
                    candidate, source="BROWSER_RESEARCH_AGENT",
                    source_identifier=lead.business_website, run_id=None,
                )
            except Exception:
                logger.warning("Failed to merge research result into leads table (non-fatal)", exc_info=True)

        await db.save_research_result(
            session_id, dataclasses.asdict(lead), _evidence_dicts(lead), lead_id=lead_id,
        )

        session = await db.get_research_session(session_id) or {}
        updates = {"leads_found": (session.get("leads_found") or 0) + 1}
        if lead.research_status in (RESEARCH_COMPLETE, RESEARCH_PARTIAL):
            updates["leads_completed"] = (session.get("leads_completed") or 0) + 1
        else:
            updates["leads_failed"] = (session.get("leads_failed") or 0) + 1
        await db.update_research_session(session_id, updates)

    try:
        result = await run_research_session(
            niche=niche, location=location, target_count=target_count,
            seed_businesses=seed_businesses, cfg=cfg,
            on_lead_complete=on_lead_complete, on_action=on_action,
            on_progress=on_progress, is_cancelled=is_cancelled,
            already_processed=already_processed,
            discovery_fallback=_scraper_discovery_fallback,
        )
        session = await db.get_research_session(session_id) or {}
        already_cancelled = session.get("status") in ("CANCELLED", "CANCEL_REQUESTED")
        await db.update_research_session(session_id, {
            "status": "CANCELLED" if already_cancelled else "COMPLETED",
            "leads_failed": (session.get("leads_failed") or 0) + result["failed_count"],
            "processed_keys": json.dumps(result.get("processed_keys") or []),
            "research_phase": "DONE",
            "finished_at": _now_iso(),
            "current_action": "", "current_business": "", "current_query": "", "current_source": "",
        })
    except Exception as exc:
        logger.error("Research session %s failed: %s", session_id, exc, exc_info=True)
        # A partially-completed run can be resumed (its saved results stay);
        # a crash-loop cannot.
        resumable = 1 if resume_count < MAX_RESUMES else 0
        try:
            await db.update_research_session(session_id, {
                "status": "FAILED", "error_message": str(exc)[:500],
                "resumable": resumable, "research_phase": "FAILED", "finished_at": _now_iso(),
            })
        except Exception:
            logger.error("Failed to persist FAILED status for session %s", session_id, exc_info=True)
