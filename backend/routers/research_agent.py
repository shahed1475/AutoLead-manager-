"""
routers/research_agent.py — Browser Research Agent API. Independent surface,
separate from Phase 1's routers/discovery.py — see
docs/superpowers/specs/2026-08-25-browser-research-agent-design.md.

Runs on the existing JobQueue (queue_worker.py), same pattern as Phase 1's
Quick Search — not a bespoke BackgroundTasks call. Jobs are page-independent:
the run keeps going after the browser navigates away, and the frontend
reconnects via GET /active.
"""
import csv
import io
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response

from .. import database as db
from ..models import ResearchAgentStartRequest
from ..queue_worker import get_queue
from ..rate_limit import limiter
from ..research_agent.models import (
    MAX_TARGET_TITLES,
    NICHE_MANAGEMENT_TITLES,
    management_titles_for_niche,
    sanitize_target_titles,
)
from ..research_agent.session import MAX_RESUMES, enqueue_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/research-agent", tags=["research-agent"])

_TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


def _compose_location(payload: ResearchAgentStartRequest) -> str:
    """City (if given) is definitely a specific place — combine with
    state/country for precision. Otherwise fall back to the free-text
    location, then country alone — never fabricate a comma-joined string
    that would make a broad region look like a specific city to the
    planner's heuristic (see design spec §9)."""
    if payload.city:
        return ", ".join(p for p in (payload.city, payload.state, payload.country) if p)
    if payload.location:
        return payload.location
    return payload.country or ""


@router.post("/start")
@limiter.limit("5/minute")
async def start_research(request: Request, payload: ResearchAgentStartRequest):
    location = _compose_location(payload)
    if not location.strip():
        raise HTTPException(status_code=422, detail="Provide at least one of location, city, or country")

    session_id = await db.create_research_session({
        "niche": payload.niche, "location": location,
        "country": payload.country, "target_count": payload.target_count,
        "target_titles": sanitize_target_titles(payload.target_titles) or None,
    })

    queue = get_queue()
    if queue is None:
        await db.update_research_session(session_id, {"status": "FAILED", "error_message": "Job queue unavailable"})
        raise HTTPException(status_code=503, detail="Research queue is not available")

    session_row = await db.get_research_session(session_id)
    if not enqueue_session(queue, session_row):
        await db.update_research_session(session_id, {"status": "FAILED", "error_message": "Job queue is full"})
        raise HTTPException(status_code=503, detail="Research queue is full — try again shortly")

    session = await db.get_research_session(session_id)
    return {"session_id": session_id, "status": session["status"]}


@router.get("/active")
async def get_active_research():
    """The session the frontend should reconnect to after a navigation/
    refresh: the most recent still-running session, else the most recent
    session overall, else null. Static path — before /{session_id}."""
    session = await db.get_latest_research_session(active_only=True)
    if session is None:
        session = await db.get_latest_research_session(active_only=False)
    return session


@router.get("/titles")
async def suggest_titles(niche: str = ""):
    """Default decision-maker titles for a niche (what the agent hunts for
    when no custom titles are given), plus every title from the built-in
    niche lists as suggestions. Static path — must precede /{session_id}."""
    defaults = list(management_titles_for_niche(niche))
    common = ["Owner", "Founder", "CEO", "President", "Managing Director", "General Manager",
              "Head of Marketing", "Marketing Director", "Head of Sales", "Operations Manager",
              "HR Director", "CFO", "CMO", "CTO", "COO", "Vice President"]
    niche_titles = sorted({t for titles in NICHE_MANAGEMENT_TITLES.values() for t in titles})
    # Most relevant first: this niche's defaults, then common leadership roles.
    suggestions = list(dict.fromkeys(defaults + common + niche_titles))
    return {
        "niche": niche,
        "default_titles": defaults,
        "suggestions": suggestions,
        "max_titles": MAX_TARGET_TITLES,
    }


@router.get("/sessions")
async def list_sessions(
    limit: int = 20, offset: int = 0, mode: Optional[str] = None,
):
    """All research sessions, newest first. `mode` filters
    'discovery' | 'handoff'. Static path — must precede /{session_id}."""
    if mode is not None and mode not in ("discovery", "handoff"):
        raise HTTPException(status_code=422, detail="mode must be 'discovery' or 'handoff'")
    limit = max(1, min(limit, 100))
    rows = await db.list_research_sessions(limit=limit + 1, offset=max(0, offset), mode=mode)
    return {"sessions": rows[:limit], "has_more": len(rows) > limit}


@router.get("/{session_id}")
async def get_research_status(session_id: int):
    session = await db.get_research_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Research session not found")
    return session


@router.get("/{session_id}/results")
async def get_research_results(session_id: int):
    session = await db.get_research_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Research session not found")
    results = await db.list_research_results(session_id)
    ids = [r["id"] for r in results]
    evidence_by_result = await db.get_research_evidence_for_results(ids)
    dms_by_result = await db.get_research_decision_makers_for_results(ids)
    for r in results:
        r["evidence"] = evidence_by_result.get(r["id"], [])
        r["decision_makers"] = dms_by_result.get(r["id"], [])
    return {"session_id": session_id, "status": session["status"], "results_count": len(results), "results": results}


_CSV_COLUMNS = [
    "city", "state", "country", "business_name", "business_phone", "business_email",
    "business_website", "business_email_status",
    "management_contact_name", "management_title", "management_phone", "management_phone_type",
    "management_email", "management_email_status",
    "decision_makers", "confidence", "research_status", "research_notes", "lead_id", "evidence_urls",
]


@router.get("/{session_id}/results.csv")
async def export_research_results_csv(session_id: int):
    session = await db.get_research_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Research session not found")
    results = await db.list_research_results(session_id)
    ids = [r["id"] for r in results]
    evidence_by_result = await db.get_research_evidence_for_results(ids)
    dms_by_result = await db.get_research_decision_makers_for_results(ids)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for r in results:
        row = dict(r)
        ev = evidence_by_result.get(r["id"], [])
        row["evidence_urls"] = "; ".join(sorted({e["source_url"] for e in ev if e.get("source_url")}))
        row["decision_makers"] = "; ".join(
            f"{d['name']} ({d['title']})" if d.get("title") else d["name"]
            for d in dms_by_result.get(r["id"], [])
        )
        writer.writerow(row)

    return Response(
        content=output.getvalue().encode("utf-8"),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=research-{session_id}.csv"},
    )


@router.post("/{session_id}/cancel")
async def cancel_research(session_id: int):
    session = await db.get_research_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Research session not found")
    if session["status"] in _TERMINAL_STATUSES:
        return session
    # Cooperative: the worker polls this between businesses and winds down,
    # keeping every lead already saved.
    await db.update_research_session(session_id, {"status": "CANCELLED"})
    return await db.get_research_session(session_id)


@router.post("/{session_id}/resume")
async def resume_research(session_id: int):
    session = await db.get_research_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Research session not found")
    if session["status"] not in ("FAILED", "CANCELLED"):
        raise HTTPException(status_code=409, detail=f"Session is {session['status']}, not resumable")
    if not session.get("resumable"):
        raise HTTPException(status_code=409, detail="Session is not marked resumable")
    resume_count = int(session.get("resume_count") or 0)
    if resume_count >= MAX_RESUMES:
        raise HTTPException(status_code=409, detail="Session has hit the resume limit")

    queue = get_queue()
    if queue is None:
        raise HTTPException(status_code=503, detail="Research queue is not available")

    await db.update_research_session(session_id, {
        "resume_count": resume_count + 1, "status": "QUEUED", "resumable": 0, "error_message": "",
    })
    session_row = await db.get_research_session(session_id)
    if not enqueue_session(queue, session_row):
        await db.update_research_session(session_id, {"status": "FAILED", "resumable": 1, "error_message": "Job queue is full"})
        raise HTTPException(status_code=503, detail="Research queue is full — try again shortly")
    return await db.get_research_session(session_id)
