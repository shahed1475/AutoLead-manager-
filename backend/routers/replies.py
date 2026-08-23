"""
replies.py — Dedicated /api/replies/* endpoints.

Routes:
  GET   /api/replies              — paginated reply inbox (mirrors /api/inbox)
  GET   /api/replies/summary      — intent breakdown + reply rate
  POST  /api/replies/check        — trigger IMAP reply detection
  GET   /api/replies/drafts       — auto-reply drafts awaiting approval
  PUT   /api/replies/{id}/draft   — edit a draft before approving
  POST  /api/replies/{id}/approve — send the (possibly edited) draft, mark SENT
  POST  /api/replies/{id}/discard — discard a draft without sending
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from .. import database as db
from ..email_sender import send_reply_email
from ..reply_detector import check_replies, get_reply_summary

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/replies", tags=["replies"])


@router.get("")
async def list_replies(
    page:      int           = Query(1, ge=1),
    page_size: int           = Query(50, ge=1, le=200),
    intent:    Optional[str] = None,
    processed: Optional[bool]= None,
):
    """Paginated reply inbox — same data as /api/inbox, different URL prefix."""
    return await db.get_inbox(
        page=page, page_size=page_size,
        intent=intent, processed=processed,
    )


@router.get("/summary")
async def replies_summary():
    """Intent breakdown, reply rate, and meeting request counts."""
    return await get_reply_summary()


@router.post("/check")
async def trigger_reply_check(background_tasks: BackgroundTasks):
    """Manually trigger IMAP reply detection in the background."""
    background_tasks.add_task(check_replies)
    return {"queued": True, "message": "Reply check started"}


# ── Auto-reply draft approval flow ──────────────────────────────────────────────
# Positive-intent replies get an AI-drafted response (reply_detector.py) held here
# for human approval before anything is ever sent — see project_saas_transformation
# memory / this session's message-quality fix for why draft-first, not auto-send.

class DraftEditRequest(BaseModel):
    draft_subject: Optional[str] = None
    draft_body:    Optional[str] = None


@router.get("/drafts")
async def list_pending_drafts():
    """Auto-reply drafts awaiting human approval, with lead context."""
    return await db.get_pending_reply_drafts()


@router.put("/{reply_id}/draft")
async def edit_draft(reply_id: int, body: DraftEditRequest):
    """Edit a draft's subject/body before approving it."""
    reply = await db.get_reply_by_id(reply_id)
    if not reply:
        raise HTTPException(404, "Reply not found")
    if reply.get("draft_status") != "PENDING_APPROVAL":
        raise HTTPException(409, f"Draft is not pending approval (status: {reply.get('draft_status')})")

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")
    await db.update_reply_draft(reply_id, updates)
    return {"updated": True}


@router.post("/{reply_id}/approve")
async def approve_draft(reply_id: int):
    """Send the (possibly edited) draft and mark it SENT."""
    reply = await db.get_reply_by_id(reply_id)
    if not reply:
        raise HTTPException(404, "Reply not found")
    if reply.get("draft_status") != "PENDING_APPROVAL":
        raise HTTPException(409, f"Draft is not pending approval (status: {reply.get('draft_status')})")

    lead_id = reply.get("lead_id")
    lead = await db.get_lead_by_id(lead_id) if lead_id else None
    if not lead or not lead.get("email"):
        raise HTTPException(422, "Lead has no email address to send the reply to")
    if (lead.get("status") or "").upper() == "DO_NOT_CONTACT":
        raise HTTPException(400, "Lead is marked DO_NOT_CONTACT — cannot send reply")

    try:
        await send_reply_email(lead["email"], reply.get("draft_subject") or "", reply.get("draft_body") or "")
    except Exception as exc:
        logger.error("approve_draft: send failed for reply %d: %s", reply_id, exc)
        raise HTTPException(502, f"Failed to send reply: {exc}") from exc

    await db.update_reply_draft(reply_id, {"draft_status": "SENT", "draft_sent_at": datetime.now(timezone.utc)})
    return {"sent": True}


@router.post("/{reply_id}/discard")
async def discard_draft(reply_id: int):
    """Discard a draft without sending it."""
    reply = await db.get_reply_by_id(reply_id)
    if not reply:
        raise HTTPException(404, "Reply not found")
    if reply.get("draft_status") != "PENDING_APPROVAL":
        raise HTTPException(409, f"Draft is not pending approval (status: {reply.get('draft_status')})")

    await db.update_reply_draft(reply_id, {"draft_status": "DISCARDED"})
    return {"discarded": True}
