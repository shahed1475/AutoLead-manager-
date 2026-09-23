"""
runner.py — executes one Find leads run on the existing JobQueue.

    COLLECTING  -> a Quick-search discovery run (planner picks sources)
    RESEARCHING -> hand those exact leads to the Browser Research Agent
                   (optional) and wait for its session to finish
    WRITING     -> score each lead and draft messages with the existing
                   ai_brain generator (optional)

Outreach safety: this module NEVER sends. It only writes the ai_* message
fields onto not-yet-contacted leads and marks them MESSAGES_READY — the
drafts AI Lab lists for review — and the human sends them there through the
guarded campaigns._send_one path. A lead that is DO_NOT_CONTACT / REPLIED /
SKIPPED / SENT is re-read fresh and skipped.

Every stage is resumable: collected lead ids, the research session id and
per-lead drafts are persisted as they happen, so a restarted run continues
instead of starting over (see reconcile_interrupted_runs).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .. import ai_brain
from .. import database as db

logger = logging.getLogger(__name__)

STEP_COLLECT, STEP_RESEARCH, STEP_OUTREACH = "collect", "research", "outreach"
VALID_STEPS = (STEP_COLLECT, STEP_RESEARCH, STEP_OUTREACH)
TERMINAL_RUN = ("COMPLETED", "FAILED", "CANCELLED")
# Never draft for a lead that is opted out, finished, or already contacted.
TERMINAL_LEAD = ("DO_NOT_CONTACT", "REPLIED", "SKIPPED", "SENT")
# A lead with drafts waiting for review is MESSAGES_READY (same rule as
# ai_brain's generator) — that is what AI Lab lists for approval.
_READY_UPDATE = {"status": "MESSAGES_READY"}
MAX_RESUMES = 3
POLL_SECONDS = 5.0


class RunCancelled(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


async def _is_cancelled(run_id: int) -> bool:
    run = await db.get_lead_run(run_id)
    return not run or run["status"] in ("CANCEL_REQUESTED", "CANCELLED")


async def _check(run_id: int) -> None:
    if await _is_cancelled(run_id):
        raise RunCancelled()


# ── Stage 1: collect ─────────────────────────────────────────────────────────

async def collect_leads(run: Dict[str, Any]) -> List[int]:
    """Run a Quick-search discovery for this run (inline — we are already
    inside a JobQueue job) and return up to target_count lead ids."""
    from ..discovery.quick_search import run_quick_search

    disc_id = await db.create_discovery_run({
        "mode": "QUICK", "raw_query": run["niche"], "niche": run["niche"],
        "city": run["location"], "target_count": run["target_count"],
    })
    await db.update_lead_run(run["id"], {"discovery_run_id": disc_id})
    await run_quick_search({"run_id": disc_id})
    disc = await db.get_discovery_run(disc_id) or {}
    if disc.get("status") == "CANCELLED":
        raise RunCancelled()
    if disc.get("status") != "COMPLETED":
        raise RuntimeError(disc.get("error_message") or "The search did not finish")
    leads = await db.get_leads_for_discovery_run(disc_id)
    return [lead["id"] for lead in leads][: run["target_count"]]


# ── Stage 2: deep research ───────────────────────────────────────────────────

async def start_research(lead_ids: List[int], target_titles: Optional[List[str]]) -> Optional[int]:
    from ..queue_worker import get_queue
    from ..research_agent.handoff import handoff_leads

    result = await handoff_leads(get_queue(), lead_ids, submission_source="lead_run",
                                 target_titles=target_titles)
    return result.get("session_id")


async def wait_for_research(run_id: int, session_id: int) -> int:
    """Poll the research session until it ends; cancelling the run cancels
    the session too. Returns how many leads were researched."""
    while True:
        session = await db.get_research_session(session_id) or {}
        if session.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
            return int(session.get("leads_completed") or 0)
        if await _is_cancelled(run_id):
            await db.update_research_session(session_id, {"status": "CANCELLED"})
            raise RunCancelled()
        await db.update_lead_run(run_id, {
            "leads_researched": int(session.get("leads_completed") or 0),
            "current_item": session.get("current_business") or "",
        })
        await asyncio.sleep(POLL_SECONDS)


# ── Stage 3: draft outreach (never sends) ────────────────────────────────────

def _has_contact(lead: Dict[str, Any], channel: str) -> bool:
    if channel == "EMAIL":
        return bool(lead.get("email"))
    if channel == "WHATSAPP":
        return bool(lead.get("phone"))
    return bool(lead.get("email") or lead.get("phone"))


async def _enrich_and_score(lead: Dict[str, Any]) -> Dict[str, Any]:
    """Same enrich -> score sequence the daily scheduler uses."""
    from ..config import get_settings
    from ..enrichment.ai_enricher import enrich_lead_with_ai
    from ..enrichment.website_analyzer import analyze_website
    from ..scheduler import _load_company_dna
    from ..scoring.lead_scorer import score_lead

    settings = get_settings()
    if lead.get("website") and not lead.get("enriched_at"):
        try:
            stored = await db.get_all_settings()
            dna = _load_company_dna(stored.get("company_dna_path") or settings.company_dna_path)
            site = await analyze_website(lead["website"], timeout=settings.enrichment_timeout)
            await enrich_lead_with_ai(lead, site, dna)
        except Exception:
            logger.warning("[LEAD RUN] enrichment failed for lead %s", lead["id"], exc_info=True)
    if not lead.get("score"):
        try:
            await score_lead(dict(lead), enriched=await db.get_enriched_data(lead["id"]))
        except Exception:
            logger.warning("[LEAD RUN] scoring failed for lead %s", lead["id"], exc_info=True)
    return dict(await db.get_lead_by_id(lead["id"]) or lead)


async def draft_messages(run: Dict[str, Any], lead_ids: List[int]) -> Tuple[int, int]:
    """Draft messages for each eligible lead. Returns (drafted, skipped)."""
    channel = run.get("channel") or "EMAIL"
    drafted = skipped = 0
    for lead_id in lead_ids:
        await _check(run["id"])
        lead = await db.get_lead_by_id(lead_id)   # fresh read: opt-out may have just arrived
        if not lead or (lead.get("status") or "").upper() in TERMINAL_LEAD or not _has_contact(lead, channel):
            skipped += 1
        else:
            lead = dict(lead)
            await db.update_lead_run(run["id"], {"current_item": lead.get("business_name") or ""})
            lead = await _enrich_and_score(lead)
            if run.get("hot_warm_only") and (lead.get("score_label") or "COLD").upper() == "COLD":
                skipped += 1
            elif lead.get("ai_email_subject") or lead.get("ai_whatsapp_msg"):
                await db.update_lead(lead_id, {"channel": channel, **_READY_UPDATE})
                drafted += 1   # already has a draft waiting for review — keep it
            else:
                try:
                    msgs = await ai_brain.generate_all_messages(lead)
                    await db.update_lead(lead_id, {
                        "channel": channel, **_READY_UPDATE,
                        "ai_whatsapp_msg":  msgs.get("first_message", ""),
                        "ai_email_subject": msgs.get("email_subject", ""),
                        "ai_email_body":    msgs.get("email_body", ""),
                        "ai_followup_msg":  msgs.get("follow_up_1", ""),
                        "ai_follow_up_1":   msgs.get("follow_up_1", ""),
                        "ai_follow_up_2":   msgs.get("follow_up_2", ""),
                        "ai_follow_up_3":   msgs.get("follow_up_3", ""),
                    })
                    await db.log_campaign_action(lead_id, "AI", "GENERATE", True)
                    drafted += 1
                except Exception as exc:
                    logger.warning("[LEAD RUN] drafting failed for lead %s: %s", lead_id, exc)
                    await db.log_campaign_action(lead_id, "AI", "GENERATE", False, str(exc))
                    skipped += 1
        await db.update_lead_run(run["id"], {"drafts_written": drafted, "drafts_skipped": skipped})
    return drafted, skipped


# ── Orchestration ────────────────────────────────────────────────────────────

async def run_lead_run(payload: Dict[str, Any]) -> None:
    """JobQueue handler. Never raises; always leaves a terminal status."""
    run_id = payload.get("run_id")
    run = await db.get_lead_run(run_id) if run_id else None
    if not run or run["status"] in TERMINAL_RUN:
        return
    if run["status"] == "CANCEL_REQUESTED":
        await db.update_lead_run(run_id, {"status": "CANCELLED", "stage": "DONE", "finished_at": _now()})
        return
    await db.update_lead_run(run_id, {"status": "RUNNING", "started_at": run.get("started_at") or _now()})
    steps = run["steps"]
    try:
        lead_ids = list(run.get("lead_ids") or [])
        if not lead_ids:
            await db.update_lead_run(run_id, {"stage": "COLLECTING", "current_item": ""})
            lead_ids = await collect_leads(run)
            await db.update_lead_run(run_id, {"lead_ids": lead_ids, "leads_found": len(lead_ids)})
        await _check(run_id)

        if STEP_RESEARCH in steps and lead_ids:
            await db.update_lead_run(run_id, {"stage": "RESEARCHING", "current_item": ""})
            session_id = run.get("research_session_id")
            if not session_id:
                session_id = await start_research(lead_ids, run.get("target_titles"))
                if session_id:
                    await db.update_lead_run(run_id, {"research_session_id": session_id})
            if session_id:
                researched = await wait_for_research(run_id, session_id)
                await db.update_lead_run(run_id, {"leads_researched": researched})
            await _check(run_id)

        if STEP_OUTREACH in steps and lead_ids:
            await db.update_lead_run(run_id, {"stage": "WRITING", "current_item": ""})
            await draft_messages(run, lead_ids)

        await db.update_lead_run(run_id, {
            "status": "COMPLETED", "stage": "DONE", "current_item": "", "finished_at": _now(),
        })
    except RunCancelled:
        await db.update_lead_run(run_id, {
            "status": "CANCELLED", "stage": "DONE", "current_item": "", "finished_at": _now(),
        })
    except Exception as exc:
        logger.error("[LEAD RUN] run %s failed: %s", run_id, exc, exc_info=True)
        await db.update_lead_run(run_id, {
            "status": "FAILED", "stage": "DONE", "current_item": "",
            "error_message": str(exc)[:500], "finished_at": _now(),
        })


def enqueue_run(queue, run_id: int) -> bool:
    return bool(queue and queue.enqueue_nowait("LEAD_RUN", {"run_id": run_id}, run_lead_run))


async def reconcile_interrupted_runs(queue) -> int:
    """At startup: re-queue runs a restart interrupted (they resume from their
    saved progress); give up after MAX_RESUMES."""
    requeued = 0
    for run in await db.list_interrupted_lead_runs():
        if int(run.get("resume_count") or 0) >= MAX_RESUMES:
            await db.update_lead_run(run["id"], {
                "status": "FAILED", "stage": "DONE", "finished_at": _now(),
                "error_message": "The server restarted too many times during this run.",
            })
            continue
        await db.update_lead_run(run["id"], {"resume_count": int(run.get("resume_count") or 0) + 1})
        if enqueue_run(queue, run["id"]):
            requeued += 1
    return requeued
