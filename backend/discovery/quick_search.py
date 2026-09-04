"""
quick_search.py — Quick Search orchestration: the JobQueue handler enqueued
by routers/discovery.py.

Flow (per design spec §9): Planner -> pick 1-2 recommended sources -> execute
via SourceRegistry, falling back to the next Planner-recommended source if
the first returns too few results -> normalize (validators, reused) ->
merge/dedup + provenance (merge_dedup.merge_and_save) -> update run
statistics -> COMPLETED (or FAILED with error_message, never left stuck
RUNNING). Deliberately does NOT run website research, AI qualification,
scoring, or messaging — later-phase scope.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from .. import database as db
from ..scraper import _scraper_cfg
from .adapters import get_registry
from .enrichment import enrich_and_score
from .merge_dedup import merge_and_save
from .planner import DiscoveryPlanner

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


async def _is_cancelled(run_id: int) -> bool:
    run = await db.get_discovery_run(run_id)
    return bool(run) and run.get("status") == "CANCELLED"


async def run_quick_search(payload: Dict[str, Any]) -> None:
    """JobQueue handler signature: async def handler(payload: dict) -> None."""
    run_id = payload.get("run_id")
    run = await db.get_discovery_run(run_id) if run_id else None
    if not run:
        logger.warning("run_quick_search: no discovery run found for run_id=%s", run_id)
        return
    if run.get("status") == "CANCELLED":
        return

    await db.update_discovery_run(run_id, {"status": "RUNNING", "started_at": _now_iso()})

    try:
        planner = DiscoveryPlanner()
        niche = run.get("niche") or run.get("raw_query") or ""
        city = run.get("city") or ""
        country = run.get("country") or ""
        target = int(run.get("target_count") or 50)

        plan = await planner.plan(query=run.get("raw_query"), niche=niche, city=city, country=country, mode="QUICK")
        await db.update_discovery_run(run_id, {
            "planner_intent": plan.intent,
            "planner_confidence": plan.confidence,
            "sources_planned": plan.recommended_sources,
        })

        if await _is_cancelled(run_id):
            return

        registry = get_registry()
        cfg = await _scraper_cfg()
        search_text = plan.query_variants[0] if plan.query_variants else niche
        min_acceptable = max(3, target // 4)

        raw_candidates = []
        for source_name in plan.recommended_sources:
            if await _is_cancelled(run_id):
                await db.update_discovery_run(run_id, {"finished_at": _now_iso()})
                return

            adapter = registry.get(source_name)
            if adapter is None or not adapter.enabled:
                continue

            result = await registry.execute(source_name, search_text, city, country, target, cfg, log_fn=None)
            raw_candidates.extend(result.leads)

            if len(result.leads) >= min_acceptable:
                break
            # Otherwise: insufficient results from this source — fall back to
            # the next Planner-recommended source (not necessarily Maps; the
            # Planner already picked sources appropriate to the query intent).

        await db.update_discovery_run(run_id, {"raw_candidates": len(raw_candidates)})

        if await _is_cancelled(run_id):
            await db.update_discovery_run(run_id, {"finished_at": _now_iso()})
            return

        save_stats = await merge_and_save(raw_candidates, run_id=run_id, default_source=plan.recommended_sources[0] if plan.recommended_sources else "UNKNOWN")
        unique_ids = save_stats["unique_saved_ids"]

        # Post-discovery enrichment + scoring (email_finder -> website AI -> lead_scorer).
        # Setting-gated, never raises — leads are already saved regardless.
        try:
            enr = await enrich_and_score(unique_ids, campaign={"niche": niche, "city": city})
            if not enr.get("skipped"):
                logger.info(
                    "quick_search run %s enrichment: %s email(s), %s scored",
                    run_id, enr.get("emails_found", 0), enr.get("scored", 0),
                )
        except Exception:
            logger.warning("quick_search run %s: enrichment failed", run_id, exc_info=True)

        await db.update_discovery_run(run_id, {
            # merged_count is the real "these collapsed into an existing lead"
            # figure — deriving it from raw-minus-unique would also count
            # basic_validate rejections (missing contact info) as if they
            # were duplicates, which they aren't.
            "deduplicated_count": save_stats["merged_count"],
            "results_count": len(unique_ids),
            "status": "COMPLETED",
            "finished_at": _now_iso(),
        })

    except Exception as exc:
        logger.error("run_quick_search failed for run_id=%s: %s", run_id, exc, exc_info=True)
        try:
            await db.update_discovery_run(run_id, {
                "status": "FAILED",
                "error_message": str(exc)[:500],
                "finished_at": _now_iso(),
            })
        except Exception:
            logger.error("run_quick_search: failed to persist FAILED status for run_id=%s", run_id, exc_info=True)
