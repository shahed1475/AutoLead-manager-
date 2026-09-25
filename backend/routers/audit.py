"""
routers/audit.py — run and read a lead's audit (audit/lead_audit.py).
Read-only intelligence: nothing here drafts or sends a message.
"""
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..audit import lead_audit

router = APIRouter(prefix="/api/leads", tags=["audit"])


class AuditBatch(BaseModel):
    lead_ids: List[int] = Field(min_length=1, max_length=lead_audit.MAX_BATCH)


@router.post("/audit-batch", status_code=202)
async def start_audit_batch(payload: AuditBatch):
    """Audit many leads in the background. Joins the running queue if there
    is one (new leads are audited automatically too); duplicates are skipped."""
    queued = lead_audit.queue_audits(payload.lead_ids)
    return {**lead_audit.batch_status(), "queued": queued}


@router.get("/audit-batch")
async def audit_batch_progress():
    return lead_audit.batch_status()


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
