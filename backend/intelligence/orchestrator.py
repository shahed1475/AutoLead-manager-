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
from ..scoring.lead_scorer import score_opportunity_fit
from .base import AgentResult
from .company_research_agent import CompanyResearchAgent
from .opportunity_agent import OpportunityAgent
from .pain_point_agent import PainPointAgent
from .qualification_agent import QualificationAgent
from .solution_matcher import match_solution

logger = logging.getLogger(__name__)

_qualification_agent = QualificationAgent()
_company_research_agent = CompanyResearchAgent()
_pain_point_agent = PainPointAgent()
_opportunity_agent = OpportunityAgent()


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
    lead_id = None
    try:
        lead_id = lead["id"]
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
        if lead_id is not None:
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


# ─────────────────────────────────────────────────────────────────────────────
# Pain point analysis — deliberately a separate entry point from
# run_research_pipeline/run_pending_research above (not a third pipeline
# stage). It requires a completed ("DONE") company_profiles row, so it can
# only ever run after qualification + company research have finished for a
# lead, and is invoked on demand (its own API endpoint) rather than being
# auto-chained — keeps the existing, already-tested research pipeline
# untouched while still making pain-point analysis fully reachable.
# ─────────────────────────────────────────────────────────────────────────────

async def run_pain_point_analysis(lead: Dict[str, Any]) -> Dict[str, Any]:
    """Run the Pain Point Agent for a lead with completed research. Never raises."""
    lead_id = None
    try:
        lead_id = lead["id"]
        profile = await db.get_company_profile(lead_id)
        if not profile or profile.get("status") != "DONE":
            return {"lead_id": lead_id, "status": "SKIPPED", "reason": "no completed research"}

        evidence = await db.get_research_evidence(profile["id"])
        result: AgentResult = await _pain_point_agent.run(lead, profile, evidence)

        if result.status != "ok":
            logger.warning("Pain point analysis failed for lead %s: %s", lead_id, result.reason)
            return {"lead_id": lead_id, "status": "FAILED", "error": result.reason}

        pain_points = result.data.get("pain_points", [])
        opportunities = result.data.get("business_opportunities", [])

        pain_point_ids = await db.replace_pain_points(profile["id"], pain_points)

        # Resolve each opportunity's placeholder pain-point index into its real,
        # now-persisted pain_point_id before storing.
        for opp in opportunities:
            idx = opp.pop("_pain_point_index", None)
            if idx is not None and 0 <= idx < len(pain_point_ids):
                opp["pain_point_id"] = pain_point_ids[idx]
        await db.replace_business_opportunities(profile["id"], opportunities)

        if result.evidence:
            await db.add_research_evidence(profile["id"], [
                {"agent_name": "pain_point", "field_name": e.field_name, "source_type": e.source_type,
                 "source_url": e.source_url, "snippet": e.snippet}
                for e in result.evidence
            ])

        return {
            "lead_id": lead_id,
            "status": "DONE",
            "pain_points_found": len(pain_points),
            "opportunities_found": len(opportunities),
        }

    except Exception as exc:
        logger.error("Pain point analysis crashed for lead %s: %s", lead_id, exc, exc_info=True)
        return {"lead_id": lead_id, "status": "FAILED", "error": str(exc)}


# ─────────────────────────────────────────────────────────────────────────────
# Opportunity + solution analysis (Phase 2) — another separate, on-demand
# entry point (same pattern as run_pain_point_analysis above). Requires pain
# points to already exist, so the causal order BUSINESS -> PAIN POINT ->
# IMPACT -> OPPORTUNITY -> SOLUTION is enforced by construction: this can
# never run before pain points do, and solution matching here always runs
# against freshly-computed opportunities in the same call, never stale ones.
# ─────────────────────────────────────────────────────────────────────────────

async def run_opportunity_analysis(lead: Dict[str, Any]) -> Dict[str, Any]:
    """Run the Opportunity Agent then the Solution Matcher for a lead that
    already has pain points. Never raises."""
    lead_id = None
    try:
        lead_id = lead["id"]
        profile = await db.get_company_profile(lead_id)
        if not profile:
            return {"lead_id": lead_id, "status": "SKIPPED", "reason": "no completed research"}

        pain_points = await db.get_pain_points(profile["id"])
        if not pain_points:
            return {"lead_id": lead_id, "status": "SKIPPED", "reason": "no pain points identified yet"}

        evidence = await db.get_research_evidence(profile["id"])
        result: AgentResult = await _opportunity_agent.run(lead, profile, pain_points, evidence)

        if result.status != "ok":
            logger.warning("Opportunity analysis failed for lead %s: %s", lead_id, result.reason)
            return {"lead_id": lead_id, "status": "FAILED", "error": result.reason}

        opportunities = result.data.get("opportunities", [])

        # Solution-match each opportunity BEFORE persisting opportunities, so
        # the source pain points (still carrying real DB ids/evidence links)
        # are available to the matcher exactly as computed by the agent.
        matches: List[Optional[Dict[str, Any]]] = []
        for opp in opportunities:
            source_pain_points = opp.get("_source_pain_points", [])
            matches.append(match_solution(opp, source_pain_points, profile, evidence))

        # Map the agent's output contract (opportunity/why_it_matters/business_ease/
        # priority/confidence — see opportunity_agent.py) onto business_opportunities'
        # columns: "opportunity" -> "title" (the table's existing NOT NULL name
        # column, unchanged since Phase 1), and drop the non-persisted scratch key.
        persistable_opportunities = [
            {
                **{k: v for k, v in opp.items() if k not in ("opportunity", "_source_pain_points")},
                "title": opp.get("opportunity") or opp.get("title") or "Untitled opportunity",
            }
            for opp in opportunities
        ]
        opportunity_ids = await db.replace_business_opportunities(profile["id"], persistable_opportunities)

        solution_items = []
        for opp_id, match in zip(opportunity_ids, matches):
            if match is None:
                continue  # no confident service match — never force one
            # match_solution() returns "service" per its documented output contract;
            # the DB column is service_name (matches _SOLUTION_RECOMMENDATION_WRITABLE).
            solution_items.append({
                **{k: v for k, v in match.items() if k != "service"},
                "service_name": match["service"],
                "business_opportunity_id": opp_id,
            })
        await db.replace_solution_recommendations(profile["id"], solution_items)

        if result.evidence:
            await db.add_research_evidence(profile["id"], [
                {"agent_name": "opportunity", "field_name": e.field_name, "source_type": e.source_type,
                 "source_url": e.source_url, "snippet": e.snippet}
                for e in result.evidence
            ])

        try:
            await score_opportunity_fit(lead_id)
        except Exception as exc:
            logger.warning("score_opportunity_fit failed for lead %s: %s", lead_id, exc)

        return {
            "lead_id": lead_id,
            "status": "DONE",
            "opportunities_found": len(opportunities),
            "solutions_recommended": len(solution_items),
        }

    except Exception as exc:
        logger.error("Opportunity analysis crashed for lead %s: %s", lead_id, exc, exc_info=True)
        return {"lead_id": lead_id, "status": "FAILED", "error": str(exc)}
