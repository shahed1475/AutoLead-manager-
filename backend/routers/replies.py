"""
replies.py — Dedicated /api/replies/* endpoints.

Routes:
  GET  /api/replies         — paginated reply inbox (mirrors /api/inbox)
  GET  /api/replies/summary — intent breakdown + reply rate
  POST /api/replies/check   — trigger IMAP reply detection
"""
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Query

from .. import database as db
from ..reply_detector import check_replies, get_reply_summary

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
