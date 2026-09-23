"""
routers/lead_runs.py — Find leads runs API (/api/lead-runs).

A run chains the steps the user picked over one set of leads: collect
(always), deep research and draft outreach (optional). Runs on the existing
JobQueue; see backend/lead_runs/runner.py. Nothing here sends a message.
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from .. import database as db
from ..lead_runs.runner import STEP_COLLECT, TERMINAL_RUN, VALID_STEPS, enqueue_run
from ..queue_worker import get_queue
from ..rate_limit import limiter
from ..research_agent.models import sanitize_target_titles

router = APIRouter(prefix="/api/lead-runs", tags=["lead-runs"])

_RESULT_COLS = (
    "id", "business_name", "niche", "city", "country", "phone", "email", "website",
    "score", "score_label", "status", "channel", "research_status",
    "ai_email_subject", "ai_email_body", "ai_whatsapp_msg",
)


class LeadRunStart(BaseModel):
    niche: str
    location: str
    target_count: int = 20
    steps: List[str] = [STEP_COLLECT]
    channel: str = "EMAIL"
    target_titles: Optional[List[str]] = None
    hot_warm_only: bool = True

    @field_validator("niche", "location")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v.strip()

    @field_validator("target_count")
    @classmethod
    def cap_count(cls, v: int) -> int:
        return max(1, min(v, 100))

    @field_validator("steps")
    @classmethod
    def valid_steps(cls, v: List[str]) -> List[str]:
        steps = [s for s in dict.fromkeys(v or []) if s in VALID_STEPS]
        # Collecting is always the first step: the others work on its leads.
        return [STEP_COLLECT] + [s for s in steps if s != STEP_COLLECT]

    @field_validator("channel")
    @classmethod
    def valid_channel(cls, v: str) -> str:
        v = (v or "EMAIL").upper()
        if v not in ("EMAIL", "WHATSAPP", "BOTH"):
            raise ValueError("channel must be EMAIL, WHATSAPP or BOTH")
        return v


@router.post("")
@limiter.limit("10/minute")
async def start_run(request: Request, payload: LeadRunStart):
    run_id = await db.create_lead_run({
        **payload.model_dump(),
        "target_titles": sanitize_target_titles(payload.target_titles) or None,
    })
    if not enqueue_run(get_queue(), run_id):
        await db.update_lead_run(run_id, {"status": "FAILED", "stage": "DONE",
                                          "error_message": "The job queue is not available — try again shortly."})
        raise HTTPException(status_code=503, detail="The job queue is not available — try again shortly")
    return await db.get_lead_run(run_id)


@router.get("")
async def list_runs(limit: int = 20, offset: int = 0):
    limit = max(1, min(limit, 100))
    rows = await db.list_lead_runs(limit=limit + 1, offset=max(0, offset))
    return {"runs": rows[:limit], "has_more": len(rows) > limit}


async def _get_or_404(run_id: int):
    run = await db.get_lead_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/{run_id}")
async def get_run(run_id: int):
    return await _get_or_404(run_id)


@router.get("/{run_id}/results")
async def get_results(run_id: int):
    """The run's leads with their contact details, score, drafts and — when
    researched — decision makers (primary first)."""
    run = await _get_or_404(run_id)
    ids = run.get("lead_ids") or []
    leads_map = await db.get_leads_by_ids(ids) if ids else {}
    people = {}
    if run.get("research_session_id"):
        results = await db.list_research_results(run["research_session_id"])
        dms = await db.get_research_decision_makers_for_results([r["id"] for r in results])
        for r in results:
            if r.get("lead_id"):
                people[r["lead_id"]] = {
                    "decision_makers": dms.get(r["id"], []),
                    "research_status": r.get("research_status"),
                    "management_email": r.get("management_email"),
                }
    leads = []
    for lead_id in ids:
        lead = leads_map.get(lead_id)
        if not lead:
            continue
        row = {c: lead.get(c) for c in _RESULT_COLS}
        row["has_draft"] = bool(lead.get("ai_email_subject") or lead.get("ai_whatsapp_msg"))
        row.update(people.get(lead_id, {"decision_makers": []}))
        leads.append(row)
    return {"run": run, "leads": leads}


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: int):
    run = await _get_or_404(run_id)
    if run["status"] in TERMINAL_RUN:
        return run
    # Cooperative: the runner stops at its next check and keeps what it saved.
    await db.update_lead_run(run_id, {"status": "CANCEL_REQUESTED"})
    if run.get("discovery_run_id"):
        disc = await db.get_discovery_run(run["discovery_run_id"]) or {}
        if disc.get("status") in ("QUEUED", "RUNNING"):
            await db.update_discovery_run(run["discovery_run_id"], {"status": "CANCELLED"})
    return await db.get_lead_run(run_id)
