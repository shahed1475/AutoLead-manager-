"""
routers/audit.py — run and read a lead's audit (audit/lead_audit.py).
Read-only intelligence: nothing here drafts or sends a message.
"""
from fastapi import APIRouter, HTTPException

from ..audit import lead_audit

router = APIRouter(prefix="/api/leads", tags=["audit"])


@router.post("/{lead_id}/audit")
async def run_audit(lead_id: int):
    audit = await lead_audit.build_audit(lead_id)
    if audit is None:
        raise HTTPException(404, "Lead not found")
    return audit


@router.get("/{lead_id}/audit")
async def get_audit(lead_id: int):
    audit = await lead_audit.latest_audit(lead_id)
    if audit is None:
        raise HTTPException(404, "No audit yet")
    return audit
