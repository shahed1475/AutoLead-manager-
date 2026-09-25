"""
enrichment.py — the post-discovery enrichment + scoring pass, shared by Quick
Search (manual) and Lead Search Automation.

Mirrors the ENRICHING / SCORING phases of
routers/campaigns.py::_run_campaign_task — same reused components, no new
scraper / enricher / scorer:

  1. email_finder for leads with a website but no email   (scrapers.email_finder)
  2. website analysis + AI enrichment (the light path)     (enrichment.website_analyzer + ai_enricher)
  3. lead scoring                                          (scoring.lead_scorer)

Deliberately uses the *light* enrichment path (analyze_website + enrich_lead_with_ai)
regardless of the sales_intelligence_enabled flag: discovery volume is high and
the light path already gives the summary/gaps/score inputs that make a lead
usable for outreach. The deep intelligence pipeline stays campaign- and
per-lead-endpoint triggered.

Gated by app_setting `discovery_enrichment_enabled` (default true). A verified
email (verified_email = 1) or an already-present value is never overwritten.
Never raises out — callers treat a failure as "leads saved, not enriched".
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from .. import ai_brain
from .. import database as db
from ..config import get_settings
from ..enrichment.ai_enricher import enrich_lead_with_ai
from ..enrichment.website_analyzer import analyze_website
from ..queue_worker import get_queue
from ..scoring.lead_scorer import score_lead
from ..scrapers.email_finder import find_emails_from_website

logger = logging.getLogger(__name__)
_env = get_settings()

_EMAIL_CONCURRENCY = 4
_ENRICH_CONCURRENCY = 3
_ACTIVE_OR_DONE_RESEARCH = ("QUEUED", "RESEARCHING", "COMPLETED")


async def _handoff_leads(queue, lead_ids, **kw):
    """Thin indirection so tests can monkeypatch the handoff without importing
    the (browser-heavy) research_agent package at enrichment module load."""
    from ..research_agent.handoff import handoff_leads
    return await handoff_leads(queue, lead_ids, **kw)


async def _enrichment_enabled() -> bool:
    stored = await db.get_all_settings()
    raw = stored.get("discovery_enrichment_enabled")
    if raw is None:
        return bool(getattr(_env, "discovery_enrichment_enabled", True))
    return str(raw).strip().lower() != "false"


async def _find_email_for(lead: Dict[str, Any]) -> bool:
    """Fill email / phone from the lead's website when missing. Returns True if
    an email was found. Never overwrites a verified email or a present value."""
    if lead.get("email") or lead.get("verified_email") or not lead.get("website"):
        return False
    try:
        found = await find_emails_from_website(lead["website"])
    except Exception:
        logger.debug("email_finder failed for lead %s", lead.get("id"), exc_info=True)
        return False

    patch: Dict[str, Any] = {}
    got_email = bool(found.get("primary_email"))
    if got_email:
        patch["email"] = found["primary_email"]
        patch["email_status"] = "FOUND"
    else:
        patch["email_status"] = "NOT_FOUND"
    if found.get("phone_numbers") and not lead.get("phone"):
        patch["phone"] = found["phone_numbers"][0]
    try:
        await db.update_lead(lead["id"], patch)
    except Exception:
        logger.debug("could not persist email_finder result for lead %s", lead.get("id"), exc_info=True)
        return False
    return got_email


async def _run_website_ai(leads: List[Dict[str, Any]]) -> None:
    """Light enrichment leg: analyze_website + enrich_lead_with_ai for every
    lead that has a website. enrich_lead_with_ai persists its own row + degrades
    to structural-score-only on any LLM failure."""
    targets = [l for l in leads if l.get("website")]
    if not targets:
        return
    company_dna = ai_brain._load_company_dna()
    sem = asyncio.Semaphore(_ENRICH_CONCURRENCY)

    async def _one(lead: Dict[str, Any]) -> None:
        async with sem:
            try:
                website_data = await analyze_website(lead["website"])
                await enrich_lead_with_ai(dict(lead), website_data, company_dna)
            except Exception:
                logger.debug("website enrichment failed for lead %s", lead.get("id"), exc_info=True)

    await asyncio.gather(*[_one(l) for l in targets])


async def _handoff_settings() -> Dict[str, Any]:
    stored = await db.get_all_settings()

    def _int(key: str, default: int) -> int:
        try:
            return int(stored.get(key) if stored.get(key) is not None else getattr(_env, key, default))
        except (TypeError, ValueError):
            return default

    return {
        "mode": str(stored.get("research_handoff_mode")
                    or getattr(_env, "research_handoff_mode", "manual")).strip().lower(),
        "min_score": _int("research_handoff_min_score", 60),
        "max_per_batch": _int("research_handoff_max_per_batch", 25),
    }


async def maybe_auto_handoff(lead_ids: List[int], log_fn: Optional[Any] = None) -> Dict[str, Any]:
    """When research_handoff_mode == 'automatic', queue the eligible freshly-
    scored leads for the existing Research Agent. Eligible = score >= min_score,
    research_status NOT_STARTED, not excluded_from_research. Bounded by
    research_handoff_max_per_batch. Never raises. `log_fn(level, msg)` (async)
    receives one structured line for the automation activity log."""
    out: Dict[str, Any] = {"auto_queued": 0, "auto_session_id": None, "auto_skipped": 0}
    ids = [i for i in dict.fromkeys(lead_ids) if i]
    if not ids:
        return out

    async def _emit(level: str, msg: str) -> None:
        if log_fn:
            try:
                await log_fn(level, msg)
            except Exception:
                logger.debug("auto-handoff log_fn failed", exc_info=True)

    try:
        cfg = await _handoff_settings()
        if cfg["mode"] != "automatic":
            return out
        queue = get_queue()
        if queue is None:
            await _emit("WARN", "Research: queue unavailable — will retry next run")
            return out
        leads_map = await db.get_leads_by_ids(ids)
        eligible, already = [], 0
        for i in ids:
            l = leads_map.get(i)
            if l is None or l.get("excluded_from_research"):
                continue
            rs = l.get("research_status") or "NOT_STARTED"
            if rs in _ACTIVE_OR_DONE_RESEARCH:
                already += 1
                continue
            if int(l.get("score") or 0) >= cfg["min_score"]:
                eligible.append(i)
        eligible = eligible[: cfg["max_per_batch"]]
        out["auto_skipped"] = already
        if not eligible:
            if already:
                await _emit("INFO", f"Research: {already} lead(s) already queued/done — skipped")
            return out
        res = await _handoff_leads(queue, eligible, submission_source="lead_search_automation")
        out["auto_queued"] = res.get("queued", 0)
        out["auto_session_id"] = res.get("session_id")
        await _emit("INFO",
                    f"Research: queued {out['auto_queued']} lead(s) (session {out['auto_session_id']})"
                    + (f", {already} already queued" if already else ""))
    except Exception:
        logger.warning("auto research handoff failed", exc_info=True)
        await _emit("ERROR", "Research: auto-handoff failed — leads are safe, will retry next run")
    return out


def _queue_reports(ids: List[int]) -> None:
    try:
        from ..audit import lead_audit
        lead_audit.queue_audits(ids)
    except Exception:  # noqa: BLE001 — reports are a bonus; never break discovery
        logger.debug("queueing lead reports failed", exc_info=True)


async def enrich_and_score(
    lead_ids: List[int], *, campaign: Optional[Dict[str, Any]] = None,
    log_fn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Public entry — always safe to call. Returns
    {emails_found, enriched, scored, skipped}. No-op when disabled or empty."""
    ids = [i for i in dict.fromkeys(lead_ids) if i]
    stats: Dict[str, Any] = {"emails_found": 0, "enriched": 0, "scored": 0, "skipped": False}
    if not ids:
        stats["skipped"] = True
        return stats
    if not await _enrichment_enabled():
        stats["skipped"] = True
        _queue_reports(ids)
        return stats

    try:
        leads_map = await db.get_leads_by_ids(ids)
        leads = [dict(leads_map[i]) for i in ids if i in leads_map]

        # 1. email finder — website but no email
        email_targets = [
            l for l in leads
            if l.get("website") and not l.get("email") and not l.get("verified_email")
        ]
        if email_targets:
            sem = asyncio.Semaphore(_EMAIL_CONCURRENCY)

            async def _one(lead: Dict[str, Any]) -> bool:
                async with sem:
                    return await _find_email_for(lead)

            results = await asyncio.gather(*[_one(l) for l in email_targets])
            stats["emails_found"] = sum(1 for r in results if r)
            leads_map = await db.get_leads_by_ids(ids)
            leads = [dict(leads_map[i]) for i in ids if i in leads_map]

        # 2. website analysis + AI enrichment (light path)
        try:
            await _run_website_ai(leads)
            stats["enriched"] = sum(1 for l in leads if l.get("website"))
        except Exception:
            logger.warning("discovery enrichment leg failed for %d lead(s)", len(ids), exc_info=True)

        # 3. scoring
        fresh = await db.get_leads_by_ids(ids)
        for i in ids:
            lead = fresh.get(i)
            if not lead:
                continue
            try:
                enriched = await db.get_enriched_data(i)
                await score_lead(dict(lead), enriched=enriched)
                stats["scored"] += 1
            except Exception:
                logger.debug("scoring failed for lead %s", i, exc_info=True)

        # 4. persistent automatic research handoff (no-op unless enabled)
        handoff = await maybe_auto_handoff(ids, log_fn=log_fn)
        stats["auto_queued"] = handoff["auto_queued"]
    except Exception:
        logger.warning("discovery enrich_and_score failed", exc_info=True)
    # 5. a business report for every new lead (people appear in it once research finishes)
    _queue_reports(ids)

    return stats
