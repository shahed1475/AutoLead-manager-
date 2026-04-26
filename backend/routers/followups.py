"""
followups.py — Follow-up sequence management endpoints.

Routes:
  GET  /api/followups/pending  — count of PENDING follow-up messages
  GET  /api/followups/history  — paginated sent follow-ups with lead info
  POST /api/followups/run      — manually trigger the follow-up engine
"""
import logging
from fastapi import APIRouter, BackgroundTasks, Query

from .. import followup_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/followups", tags=["followups"])


@router.get("/pending")
async def pending_followups():
    """Count of PENDING follow-up messages waiting to be sent."""
    count = await followup_engine.get_pending_count()
    return {"pending": count}


@router.get("/history")
async def followup_history(
    page:      int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """Paginated list of sent follow-ups with lead context."""
    return await followup_engine.get_history(page=page, page_size=page_size)


@router.post("/run")
async def run_followup_engine(background_tasks: BackgroundTasks):
    """Manually trigger the follow-up engine (runs in background)."""
    background_tasks.add_task(followup_engine.process_followup_queue)
    return {"queued": True, "message": "Follow-up engine started in background"}
