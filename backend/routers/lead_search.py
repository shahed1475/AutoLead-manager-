"""
routers/lead_search.py — cross-cutting Lead Search surface (spec §31).
Currently: the search-provider catalog + status. Execution stays on
/api/discovery (Quick Search) and /api/automation.
"""
from fastapi import APIRouter, HTTPException

from .. import edition
from ..discovery.provider_catalog import get_provider_catalog

router = APIRouter(prefix="/api/lead-search", tags=["lead-search"])


@router.get("/providers")
async def list_providers():
    # Which search services/keys the machine has is the owner's business.
    if edition.is_client():
        raise HTTPException(404, "Not found")
    catalog = await get_provider_catalog()
    by_status: dict = {}
    for p in catalog:
        by_status[p["status"]] = by_status.get(p["status"], 0) + 1
    return {"providers": catalog, "counts_by_status": by_status}
