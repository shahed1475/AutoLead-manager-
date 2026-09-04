"""
lead_search_service.py — the swappable search layer (spec §14).

Phase 1: DiscoveryLeadSearch runs one niche×location Quick Search through the
existing discovery pipeline (planner -> source registry -> merge/dedup). A
paid provider can be added later as another LeadSearchService subclass behind
a settings key without touching the runner.
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional

from .. import database as db
from ..discovery.adapters import get_registry
from ..discovery.enrichment import enrich_and_score
from ..discovery.merge_dedup import merge_and_save
from ..discovery.planner import DiscoveryPlanner
from ..scraper import _scraper_cfg

logger = logging.getLogger(__name__)

# Per-source time budget, proportional to how many businesses we asked for. A
# Selenium Google Maps scrape floors at ~3-8s per business, so a fixed cap
# (the old 150s) guaranteed a timeout on any non-trivial target. This still
# kills a genuinely hung browser session.
_BASE_TIMEOUT_SECONDS = 90.0
_PER_LEAD_SECONDS = 9.0

# The scraper budget the automation ever passes down, regardless of the
# configured per-item target. merge_and_save MERGES duplicates, so re-running
# the same niche×city on a later daily slice tops leads up instead of forcing
# one oversized, timing-out scrape.
_EFFECTIVE_TARGET_CAP = 40


def _source_timeout(effective_target: int) -> float:
    return _BASE_TIMEOUT_SECONDS + _PER_LEAD_SECONDS * effective_target


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


@dataclass
class SearchResult:
    new_leads: int = 0
    total_found: int = 0
    lead_ids: List[int] = field(default_factory=list)
    sources_used: List[str] = field(default_factory=list)
    error: Optional[str] = None
    # Per-run visibility (audit finding L7).
    run_id: Optional[int] = None
    raw_count: int = 0
    merged_count: int = 0
    rejected_count: int = 0
    emails_found: int = 0
    timed_out_sources: List[str] = field(default_factory=list)


class LeadSearchService(ABC):
    @abstractmethod
    async def search_leads(
        self, niche: str, city: str, state: Optional[str],
        country: Optional[str], target: int,
    ) -> SearchResult:
        ...


async def _open_discovery_run(
    niche: str, city: str, country: Optional[str], target: int, plan: Any,
) -> Optional[int]:
    """Bookkeeping only — a failure here must not fail the search."""
    try:
        return await db.create_discovery_run({
            "mode": "AUTOMATION",
            "raw_query": niche, "niche": niche,
            "city": city, "country": country or "",
            "target_count": target,
            "planner_intent": getattr(plan, "intent", None),
            "planner_confidence": getattr(plan, "confidence", None),
            "sources_planned": list(getattr(plan, "recommended_sources", []) or []),
        })
    except Exception:
        logger.warning("automation: could not open discovery run row", exc_info=True)
        return None


async def _close_discovery_run(
    run_id: Optional[int], status: str, *,
    raw: Optional[int] = None, merged: Optional[int] = None,
    results: Optional[int] = None, error: Optional[str] = None,
    emails_found: Optional[int] = None, leads_scored: Optional[int] = None,
    research_queued: Optional[int] = None,
) -> None:
    if not run_id:
        return
    patch: dict = {"status": status, "finished_at": _now_iso()}
    if raw is not None:
        patch["raw_candidates"] = raw
    if merged is not None:
        patch["deduplicated_count"] = merged
    if results is not None:
        patch["results_count"] = results
    if error is not None:
        patch["error_message"] = error
    if emails_found is not None:
        patch["emails_found"] = emails_found
    if leads_scored is not None:
        patch["leads_scored"] = leads_scored
    if research_queued is not None:
        patch["research_queued"] = research_queued
    try:
        await db.update_discovery_run(run_id, patch)
    except Exception:
        logger.warning("automation: could not close discovery run row %s", run_id, exc_info=True)


class DiscoveryLeadSearch(LeadSearchService):
    """Runs the built-in discovery pipeline for a single niche x location."""

    async def search_leads(
        self, niche: str, city: str, state: Optional[str],
        country: Optional[str], target: int,
    ) -> SearchResult:
        effective_target = max(1, min(int(target or 1), _EFFECTIVE_TARGET_CAP))
        run_id: Optional[int] = None
        try:
            planner = DiscoveryPlanner()
            plan = await planner.plan(query=niche, niche=niche, city=city,
                                      country=country or "", mode="QUICK")
            search_text = plan.query_variants[0] if plan.query_variants else niche

            run_id = await _open_discovery_run(niche, city, country, effective_target, plan)

            registry = get_registry()
            cfg = await _scraper_cfg()
            per_source_timeout = _source_timeout(effective_target)

            candidates: list = []
            used: List[str] = []
            timed_out: List[str] = []
            for source_name in plan.recommended_sources:
                if registry.get(source_name) is None:
                    continue
                try:
                    res = await asyncio.wait_for(
                        registry.execute(
                            source_name, search_text, city, country or "",
                            effective_target, cfg, log_fn=None,
                        ),
                        timeout=per_source_timeout,
                    )
                except asyncio.TimeoutError:
                    timed_out.append(source_name)
                    logger.warning(
                        "LeadSearchService: %s timed out (%.0fs) for %s / %s",
                        source_name, per_source_timeout, niche, city,
                    )
                    continue
                used.append(source_name)
                candidates.extend(res.leads or [])
                if len(candidates) >= effective_target:
                    break

            if not used and timed_out:
                msg = f"all {len(timed_out)} source(s) timed out"
                await _close_discovery_run(run_id, "FAILED", error=msg)
                return SearchResult(run_id=run_id, error=msg, timed_out_sources=timed_out)

            for c in candidates:
                c.setdefault("source", "AUTOMATION")
                c.setdefault("niche", niche)
                if state and not c.get("state"):
                    c["state"] = state

            save = await merge_and_save(candidates, run_id=run_id, default_source="AUTOMATION")
            new_count = save.get("new_count", 0)
            merged_count = save.get("merged_count", 0)
            rejected_count = save.get("rejected_count", 0)
            unique_ids = save.get("unique_saved_ids", [])

            # Post-discovery enrichment + scoring — setting-gated, never raises.
            enr: Dict[str, Any] = {}
            try:
                enr = await enrich_and_score(
                    unique_ids, campaign={"niche": niche, "city": city},
                    log_fn=db.append_automation_log,
                )
            except Exception:
                logger.warning("automation enrichment failed for %s / %s", niche, city, exc_info=True)
            emails_found = enr.get("emails_found", 0)

            await _close_discovery_run(
                run_id, "COMPLETED",
                raw=len(candidates), merged=merged_count, results=len(unique_ids),
                emails_found=emails_found, leads_scored=enr.get("scored", 0),
                research_queued=enr.get("auto_queued", 0),
            )
            return SearchResult(
                run_id=run_id,
                new_leads=new_count,
                total_found=len(candidates),
                raw_count=len(candidates),
                merged_count=merged_count,
                rejected_count=rejected_count,
                emails_found=emails_found,
                lead_ids=unique_ids,
                sources_used=used,
                timed_out_sources=timed_out,
            )
        except Exception as exc:  # never raises — the runner treats .error as a soft failure
            logger.warning("LeadSearchService failed for %s / %s: %s", niche, city, exc, exc_info=True)
            await _close_discovery_run(run_id, "FAILED", error=str(exc)[:300])
            return SearchResult(run_id=run_id, error=str(exc)[:300])


def get_lead_search_service() -> LeadSearchService:
    return DiscoveryLeadSearch()
