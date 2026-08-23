import logging

from fastapi import APIRouter, HTTPException

from .. import database as db
from ..intelligence.orchestrator import run_opportunity_analysis, run_pain_point_analysis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/leads", tags=["intelligence"])


async def _get_lead_or_404(lead_id: int):
    lead = await db.get_lead_by_id(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    return lead


async def _get_profile_or_404(lead_id: int):
    profile = await db.get_company_profile(lead_id)
    if not profile:
        raise HTTPException(404, "No research available for this lead yet")
    return profile


@router.get("/{lead_id}/research")
async def get_lead_research(lead_id: int):
    await _get_lead_or_404(lead_id)
    profile = await _get_profile_or_404(lead_id)
    evidence = await db.get_research_evidence(profile["id"])
    return {"profile": profile, "evidence": evidence}


@router.get("/{lead_id}/intelligence")
async def get_lead_intelligence(lead_id: int):
    """Combined business-intelligence view: profile + pain points + opportunities +
    solution recommendations + intelligence-fit score + evidence."""
    await _get_lead_or_404(lead_id)
    profile = await _get_profile_or_404(lead_id)

    pain_points   = await db.get_pain_points(profile["id"])
    opportunities = await db.get_business_opportunities(profile["id"])
    solutions     = await db.get_solution_recommendations(profile["id"])
    evidence      = await db.get_research_evidence(profile["id"])
    score         = await db.get_score(lead_id)
    return {
        "profile": profile,
        "pain_points": pain_points,
        "business_opportunities": opportunities,
        "solution_recommendations": solutions,
        "intelligence_score": (score or {}).get("intelligence_score"),
        "intelligence_category": (score or {}).get("intelligence_category"),
        "evidence": evidence,
    }


@router.get("/{lead_id}/pain-points")
async def get_lead_pain_points(lead_id: int):
    await _get_lead_or_404(lead_id)
    profile = await _get_profile_or_404(lead_id)
    return await db.get_pain_points(profile["id"])


@router.get("/{lead_id}/opportunities")
async def get_lead_opportunities(lead_id: int):
    await _get_lead_or_404(lead_id)
    profile = await _get_profile_or_404(lead_id)
    return await db.get_business_opportunities(profile["id"])


@router.get("/{lead_id}/evidence")
async def get_lead_evidence(lead_id: int):
    await _get_lead_or_404(lead_id)
    profile = await _get_profile_or_404(lead_id)
    return await db.get_research_evidence(profile["id"])


@router.post("/{lead_id}/pain-points/analyze")
async def analyze_lead_pain_points(lead_id: int):
    lead = await _get_lead_or_404(lead_id)
    profile = await db.get_company_profile(lead_id)
    if not profile or profile.get("status") != "DONE":
        raise HTTPException(400, "Company research must complete before pain-point analysis can run")

    result = await run_pain_point_analysis(lead)
    if result["status"] == "FAILED":
        raise HTTPException(500, result.get("error") or "Pain point analysis failed")
    return result


@router.get("/{lead_id}/solutions")
async def get_lead_solutions(lead_id: int):
    await _get_lead_or_404(lead_id)
    profile = await _get_profile_or_404(lead_id)
    return await db.get_solution_recommendations(profile["id"])


@router.post("/{lead_id}/opportunities/analyze")
async def analyze_lead_opportunities(lead_id: int):
    lead = await _get_lead_or_404(lead_id)
    profile = await db.get_company_profile(lead_id)
    if not profile:
        raise HTTPException(400, "Company research must complete before opportunity analysis can run")
    pain_points = await db.get_pain_points(profile["id"])
    if not pain_points:
        raise HTTPException(400, "Pain-point analysis must run before opportunity analysis can run")

    result = await run_opportunity_analysis(lead)
    if result["status"] == "FAILED":
        raise HTTPException(500, result.get("error") or "Opportunity analysis failed")
    return result
