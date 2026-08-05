"""
orchestrator.py — runs Qualification then (if qualified) Company Research for a
lead, persisting progressively so a crash mid-pipeline never loses the
qualification verdict. run_pending_research is the resumable, bounded-
concurrency entry point used by the campaign pipeline (Task 9) and available
for standalone backlog catch-up.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .. import database as db
from .base import AgentResult
from .company_research_agent import CompanyResearchAgent
from .qualification_agent import QualificationAgent

logger = logging.getLogger(__name__)

_qualification_agent = QualificationAgent()
_company_research_agent = CompanyResearchAgent()


async def _persist(lead_id: int, result: AgentResult, agent_name: str, extra: Dict[str, Any]) -> int:
    profile_id = await db.upsert_company_profile(lead_id, {**result.data, **extra})
    if result.evidence:
        await db.add_research_evidence(profile_id, [
            {"agent_name": agent_name, "field_name": e.field_name, "source_type": e.source_type,
             "source_url": e.source_url, "snippet": e.snippet}
            for e in result.evidence
        ])
    return profile_id


async def run_research_pipeline(lead: Dict[str, Any], campaign: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run Qualification then (if qualified) Company Research for one lead.
    Persists after each agent so a crash never loses earlier progress. Never raises."""
    lead_id = lead["id"]

    try:
        await db.upsert_company_profile(lead_id, {"status": "QUALIFYING"})

        qual_result = await _qualification_agent.run(lead, campaign)
        rejected = qual_result.status == "rejected"
        await _persist(lead_id, qual_result, "qualification", {
            "status": "REJECTED" if rejected else "RESEARCHING",
            "qualification_reason": qual_result.reason,
        })

        if rejected:
            logger.info("Lead %s rejected by qualification: %s", lead_id, qual_result.reason)
            return {"lead_id": lead_id, "status": "REJECTED", "reason": qual_result.reason}

        research_result = await _company_research_agent.run(lead, campaign)
        final_status = "DONE" if research_result.status == "ok" else "FAILED"
        await _persist(lead_id, research_result, "company_research", {
            "status": final_status,
            "research_confidence": research_result.confidence,
            "researched_at": datetime.now(timezone.utc).isoformat(),
        })
        return {"lead_id": lead_id, "status": final_status}

    except Exception as exc:
        logger.error("Research pipeline failed for lead %s: %s", lead_id, exc, exc_info=True)
        try:
            await db.upsert_company_profile(lead_id, {"status": "FAILED"})
        except Exception:
            pass
        return {"lead_id": lead_id, "status": "FAILED", "error": str(exc)}


async def run_pending_research(
    lead_ids: Optional[List[int]] = None,
    limit: int = 50,
    max_concurrent: int = 3,
    campaign: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Process research for a set of leads with bounded concurrency.

    lead_ids given: scoped strictly to those leads (used by the campaign pipeline — prevents
    a different campaign's backlog from being evaluated against this campaign's niche/city).
    lead_ids=None: scans the global PENDING backlog (used for standalone resume/catch-up).
    """
    if lead_ids is not None:
        leads_map = await db.get_leads_by_ids(lead_ids)
        for lid in lead_ids:
            if lid in leads_map and await db.get_company_profile(lid) is None:
                await db.upsert_company_profile(lid, {"status": "PENDING"})
        pending_lead_ids = []
        for lid in lead_ids:
            profile = await db.get_company_profile(lid)
            if profile and profile["status"] == "PENDING":
                pending_lead_ids.append(lid)
    else:
        new_leads = await db.get_leads_without_company_profile(limit=limit)
        for lead in new_leads:
            await db.upsert_company_profile(lead["id"], {"status": "PENDING"})
        pending_profiles = await db.get_pending_company_profiles(limit=limit)
        pending_lead_ids = [p["lead_id"] for p in pending_profiles]
        leads_map = await db.get_leads_by_ids(pending_lead_ids)

    if not pending_lead_ids:
        return {"processed": 0, "results": []}

    sem = asyncio.Semaphore(max_concurrent)
    results: List[Dict[str, Any]] = []

    async def _bounded(lead: Dict[str, Any]) -> None:
        async with sem:
            results.append(await run_research_pipeline(lead, campaign))

    await asyncio.gather(*[_bounded(leads_map[lid]) for lid in pending_lead_ids if lid in leads_map])
    return {"processed": len(results), "results": results}
