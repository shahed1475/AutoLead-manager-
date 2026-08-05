import logging

from fastapi import APIRouter, HTTPException

from .. import database as db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/leads", tags=["intelligence"])


@router.get("/{lead_id}/research")
async def get_lead_research(lead_id: int):
    lead = await db.get_lead_by_id(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")

    profile = await db.get_company_profile(lead_id)
    if not profile:
        raise HTTPException(404, "No research available for this lead yet")

    evidence = await db.get_research_evidence(profile["id"])
    return {"profile": profile, "evidence": evidence}
