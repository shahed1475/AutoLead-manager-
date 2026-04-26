"""
inbox.py — Reply inbox and lead enrichment/scoring endpoints.

Routes:
  GET  /api/inbox              — paginated reply inbox
  POST /api/inbox/{id}/process — mark reply as processed + set intent
  POST /api/inbox/check        — trigger IMAP reply detection manually
  POST /api/leads/{id}/enrich  — enrich a single lead (website + AI)
  POST /api/leads/score        — score all un-scored leads (or a list)
  GET  /api/leads/score-dist   — HOT/WARM/COLD distribution
"""
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from .. import database as db
from ..config import get_settings
from ..models import ReplyInboxResponse, EnrichRequest, ScoreRequest
from ..scoring.lead_scorer import score_lead
from ..enrichment.website_analyzer import analyze_website
from ..enrichment.ai_enricher import enrich_lead
from ..reply_detector import check_replies

logger   = logging.getLogger(__name__)
settings = get_settings()
router   = APIRouter(prefix="/api", tags=["inbox"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Reply inbox ────────────────────────────────────────────────────────────────

@router.get("/inbox", response_model=ReplyInboxResponse)
async def list_inbox(
    page:       int           = Query(1, ge=1),
    page_size:  int           = Query(50, ge=1, le=200),
    intent:     Optional[str] = None,
    processed:  Optional[bool]= None,
):
    return await db.get_inbox(
        page=page, page_size=page_size,
        intent=intent, processed=processed,
    )


@router.get("/inbox/stats")
async def inbox_stats():
    data = await db.get_inbox(page=1, page_size=1)
    unread = await db.get_inbox(page=1, page_size=1, processed=False)
    return {"total": data["total"], "unread": unread["total"]}


@router.post("/inbox/{entry_id}/process")
async def process_reply(entry_id: int, intent: str = Query("NEUTRAL")):
    """Mark a reply as processed and set its intent classification."""
    valid_intents = {"POSITIVE", "NEGATIVE", "NEUTRAL", "INTERESTED", "SPAM"}
    if intent.upper() not in valid_intents:
        raise HTTPException(400, f"Invalid intent. Use one of: {valid_intents}")

    ok = await db.update_inbox_entry(entry_id, {"processed": 1, "intent": intent.upper()})
    if not ok:
        raise HTTPException(404, "Inbox entry not found")
    return {"entry_id": entry_id, "processed": True, "intent": intent.upper()}


@router.post("/inbox/check")
async def trigger_reply_check(background_tasks: BackgroundTasks):
    """Manually trigger IMAP reply detection (runs in background)."""
    background_tasks.add_task(check_replies)
    return {"queued": True, "message": "Reply check started in background"}


# ── Lead enrichment ────────────────────────────────────────────────────────────

async def _enrich_and_score(lead_id: int) -> dict:
    """Fetch website, run AI analysis, score, and persist to DB."""
    lead = await db.get_lead_by_id(lead_id)
    if not lead:
        return {"error": "Lead not found"}

    lead = dict(lead)
    update: dict = {}

    website = lead.get("website")
    if website:
        # 1. Fetch and analyze website
        site_data = await analyze_website(website, timeout=settings.enrichment_timeout)
        if not site_data.get("error"):
            # 2. AI enrichment from website text
            stored = await db.get_all_settings()
            from pathlib import Path
            dna_path    = stored.get("company_dna_path") or settings.company_dna_path
            company_dna = Path(dna_path).read_text(encoding="utf-8") if Path(dna_path).exists() else ""

            ai_data = await enrich_lead(lead, site_data.get("page_text", ""), company_dna)
            update.update(ai_data)
            update["has_social_links"] = 1 if site_data.get("has_social_links") else 0
            update["enriched_at"]      = _now_iso()

    # 3. Score the lead (with any new enrichment data merged)
    merged = {**lead, **update}
    scored = score_lead(merged)
    update.update(scored)

    if update:
        await db.update_lead(lead_id, update)

    return {"lead_id": lead_id, **scored}


@router.post("/leads/{lead_id}/enrich")
async def enrich_lead_endpoint(lead_id: int, background_tasks: BackgroundTasks):
    """
    Enrich a single lead: visit their website, run AI analysis, score.
    Runs in background; returns immediately.
    """
    lead = await db.get_lead_by_id(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")

    background_tasks.add_task(_enrich_and_score, lead_id)
    return {"queued": True, "lead_id": lead_id}


@router.post("/leads/{lead_id}/enrich-sync")
async def enrich_lead_sync(lead_id: int):
    """
    Enrich a single lead synchronously — waits for result.
    Use this from the UI when you want immediate feedback.
    """
    result = await _enrich_and_score(lead_id)
    if result.get("error"):
        raise HTTPException(404, result["error"])
    return result


@router.post("/leads/score-all")
async def score_all_leads(payload: ScoreRequest, background_tasks: BackgroundTasks):
    """
    (Re-)score leads. If lead_ids is None, scores all un-scored leads.
    Runs in background.
    """
    async def _run(lead_ids: Optional[List[int]]) -> None:
        if lead_ids:
            leads = [await db.get_lead_by_id(lid) for lid in lead_ids]
            leads = [dict(l) for l in leads if l]
        else:
            leads = await db.get_leads_without_score()

        for lead in leads:
            scored = score_lead(lead)
            await db.update_lead(lead["id"], scored)
        logger.info("Scored %d leads", len(leads))

    background_tasks.add_task(_run, payload.lead_ids)
    return {"queued": True}


@router.get("/leads/score-dist")
async def score_distribution():
    """Return HOT / WARM / COLD lead counts."""
    return await db.get_score_distribution()
