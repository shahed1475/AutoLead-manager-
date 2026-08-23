"""
routers/pipeline.py — CRM deal-stage board query.

The two lead-scoped mutations (manual stage move, stage history) live in
routers/leads.py next to the existing PATCH /{lead_id}/status, since they're
lead-scoped under the /api/leads prefix. This router only holds the
board-wide GET, which doesn't fit that prefix.
"""
from fastapi import APIRouter

from .. import database as db

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


@router.get("/board")
async def get_board():
    return await db.get_board_leads()
