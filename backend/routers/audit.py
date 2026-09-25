"""
routers/audit.py — run and read a lead's audit (audit/lead_audit.py).
Read-only intelligence: nothing here drafts or sends a message.
"""
import asyncio
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..audit import lead_audit

router = APIRouter(prefix="/api/leads", tags=["audit"])


class AuditBatch(BaseModel):
    lead_ids: List[int] = Field(min_length=1, max_length=lead_audit.MAX_BATCH)


@router.post("/audit-batch", status_code=202)
async def start_audit_batch(payload: AuditBatch):
    """Audit many leads in the background; one batch at a time."""
    if lead_audit.batch_status()["running"]:
        raise HTTPException(409, "An audit batch is already running")
    asyncio.create_task(lead_audit.run_batch(payload.lead_ids))
    await asyncio.sleep(0)                      # let it register as running
    return {**lead_audit.batch_status(), "total": len(set(payload.lead_ids))}


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
