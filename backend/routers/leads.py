import json
import csv
import io
import logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel
from .. import database as db
from ..models import Lead, LeadCreate, LeadUpdate, LeadListResponse, StatusUpdate, StageUpdate, DealValueUpdate
from ..queue_worker import get_queue
from ..research_agent.handoff import handoff_leads

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/leads", tags=["leads"])


@router.get("", response_model=LeadListResponse)
async def list_leads(
    page:          int            = Query(1, ge=1),
    page_size:     int            = Query(50, ge=1, le=200),
    status:        Optional[str]  = None,
    channel:       Optional[str]  = None,
    niche:         Optional[str]  = None,
    city:          Optional[str]  = None,
    search:        Optional[str]  = None,
    sort_by:       str            = Query("created_at"),
    sort_dir:      str            = Query("desc"),
    date_from:     Optional[str]  = None,
    date_to:       Optional[str]  = None,
    date_field:      str            = Query("created_at"),
    score_label:     Optional[str]  = None,
    enriched_only:   bool           = False,
    source:          Optional[str]  = None,
    source_type:     Optional[str]  = None,
    research_status: Optional[str]  = None,
    run_id:          Optional[int]  = None,   # only the leads one Find-leads run saved
):
    lead_ids = None
    if run_id is not None:
        run = await db.get_lead_run(run_id)
        raw = (run or {}).get("lead_ids") or []
        try:
            lead_ids = [int(i) for i in (json.loads(raw) if isinstance(raw, str) else raw)]
        except (TypeError, ValueError):
            lead_ids = []
    return await db.get_leads(
        page=page, page_size=page_size, lead_ids=lead_ids,
        status=status, channel=channel,
        niche=niche, city=city, search=search,
        sort_by=sort_by, sort_dir=sort_dir,
        date_from=date_from, date_to=date_to, date_field=date_field,
        score_label=score_label, enriched_only=enriched_only,
        source=source, source_type=source_type, research_status=research_status,
    )


@router.post("", response_model=Lead, status_code=201)
async def create_lead(payload: LeadCreate):
    lead_id = await db.create_lead(payload.model_dump())
    return await db.get_lead_by_id(lead_id)


# ── Static sub-paths first — must come before /{lead_id} ──────────────────

@router.get("/export/csv")
async def export_csv(
    status:        Optional[str] = None,
    channel:       Optional[str] = None,
    niche:         Optional[str] = None,
    city:          Optional[str] = None,
    search:        Optional[str] = None,
    date_from:     Optional[str] = None,
    date_to:       Optional[str] = None,
    date_field:    str           = Query("created_at"),
    score_label:   Optional[str] = None,
    enriched_only: bool          = False,
):
    result = await db.get_leads(
        page_size=10_000, status=status, channel=channel, niche=niche, city=city,
        search=search, date_from=date_from, date_to=date_to, date_field=date_field,
        score_label=score_label, enriched_only=enriched_only,
    )
    items = result["items"]
    output = io.StringIO()
    export_cols = [
        "id", "business_name", "phone", "email", "website", "niche", "city", "country", "address",
        "source", "score", "score_label", "rating", "reviews_count",
        "status", "channel", "ai_whatsapp_msg", "ai_email_subject", "ai_email_body",
        "ai_followup_msg", "created_at", "sent_at", "followup_sent_at",
    ]
    writer = csv.DictWriter(output, fieldnames=export_cols, extrasaction="ignore")
    writer.writeheader()
    if items:
        writer.writerows(items)
    return Response(
        content=output.getvalue().encode("utf-8"),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads-export.csv"},
    )


@router.post("/import/csv", status_code=201)
async def import_csv(file: UploadFile = File(...)):
    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    created, errors = 0, []
    for i, row in enumerate(reader, start=1):
        row = {k.strip().lower().replace(" ", "_"): v.strip() for k, v in row.items()}
        if not row.get("business_name"):
            errors.append(f"Row {i}: missing business_name")
            continue
        try:
            await db.create_lead({
                "business_name": row["business_name"],
                "phone":   row.get("phone")   or None,
                "email":   row.get("email")   or None,
                "website": row.get("website") or None,
                "niche":   row.get("niche")   or None,
                "city":    row.get("city")    or None,
            })
            created += 1
        except Exception as exc:
            logger.warning("CSV import row %d failed: %s", i, exc)
            errors.append(f"Row {i}: {exc}")
    return {"created": created, "errors": errors}


# ── Research handoff ──────────────────────────────────────────────────────

_VALID_SUBMISSION_SOURCES = {"manual", "lead_search_automation", "manual_from_automation"}


class BulkResearchRequest(BaseModel):
    lead_ids: List[int]
    submission_source: Optional[str] = "manual"
    priority: Optional[str] = None
    target_titles: Optional[List[str]] = None   # decision-maker titles to hunt for


class ResearchOneRequest(BaseModel):
    target_titles: Optional[List[str]] = None   # decision-maker titles to hunt for


class ResearchExcludeRequest(BaseModel):
    excluded: bool = True


async def _handoff(lead_ids: List[int], submission_source: Optional[str], priority: Optional[str],
                   target_titles: Optional[List[str]] = None) -> dict:
    src = submission_source if submission_source in _VALID_SUBMISSION_SOURCES else "manual"
    queue = get_queue()
    if queue is None:
        raise HTTPException(status_code=503, detail="Research queue is not available")
    result = await handoff_leads(queue, lead_ids, submission_source=src, priority=priority,
                                 target_titles=target_titles)
    if result["session_id"] is None and result["queued"] == 0 and not result["skipped"]:
        raise HTTPException(status_code=422, detail="No leads to send")
    return result


@router.post("/research")
async def bulk_research(payload: BulkResearchRequest):
    """Send the selected leads to the existing Browser Research Agent."""
    return await _handoff(payload.lead_ids, payload.submission_source, payload.priority,
                          payload.target_titles)


# ── Parameterised routes ───────────────────────────────────────────────────

@router.post("/{lead_id}/research")
async def research_one(lead_id: int, payload: Optional[ResearchOneRequest] = None):
    if not await db.get_lead_by_id(lead_id):
        raise HTTPException(404, "Lead not found")
    return await _handoff([lead_id], "manual", None, payload.target_titles if payload else None)


@router.post("/{lead_id}/research-exclude", response_model=Lead)
async def research_exclude(lead_id: int, payload: ResearchExcludeRequest):
    updated = await db.update_lead(lead_id, {"excluded_from_research": 1 if payload.excluded else 0})
    if not updated:
        raise HTTPException(404, "Lead not found")
    return await db.get_lead_by_id(lead_id)


@router.get("/{lead_id}", response_model=Lead)
async def get_lead(lead_id: int):
    lead = await db.get_lead_by_id(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    return lead


@router.put("/{lead_id}", response_model=Lead)
async def update_lead(lead_id: int, payload: LeadUpdate):
    updated = await db.update_lead(lead_id, payload.model_dump(exclude_none=True))
    if not updated:
        raise HTTPException(404, "Lead not found")
    return await db.get_lead_by_id(lead_id)


@router.patch("/{lead_id}/status", response_model=Lead)
async def patch_status(lead_id: int, payload: StatusUpdate):
    """Dedicated endpoint for status-only updates (REPLIED / SKIPPED / PENDING / SENT)."""
    updated = await db.update_lead(lead_id, {"status": payload.status})
    if not updated:
        raise HTTPException(404, "Lead not found")
    return await db.get_lead_by_id(lead_id)


# Manual moves may only target a real, single-valued board stage. NEW and
# CONTACTED are display-only aggregates (NEW groups PENDING/ENRICHED/SCORED/
# MESSAGES_READY) with no single underlying status to move a lead "back" to
# other than their canonical member — PENDING and SENT respectively. ENRICHED/
# SCORED/MESSAGES_READY/SKIPPED/DO_NOT_CONTACT are reachable only through their
# own dedicated flows (scoring, opt-out), never through this endpoint.
_MANUAL_STAGE_TARGETS = frozenset({
    "PENDING", "SENT", "REPLIED", "INTERESTED", "MEETING", "PROPOSAL", "WON", "LOST",
})


@router.post("/{lead_id}/stage")
async def move_lead_stage(lead_id: int, payload: StageUpdate):
    """Manual deal-stage move (drag-and-drop on the pipeline board, or a
    button on the lead detail drawer). Rejects a DO_NOT_CONTACT lead — an
    opted-out lead cannot be pulled back into an active pipeline stage
    through this endpoint."""
    lead = await db.get_lead_by_id(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    if (lead.get("status") or "").upper() == "DO_NOT_CONTACT":
        raise HTTPException(400, "Lead is marked DO_NOT_CONTACT — cannot move to a pipeline stage")
    if payload.to_status.value not in _MANUAL_STAGE_TARGETS:
        raise HTTPException(400, f"{payload.to_status.value} is not a valid manual stage target")
    if payload.deal_value is not None:
        await db.set_deal_value(lead_id, payload.deal_value)
    await db.set_lead_stage(lead_id, payload.to_status.value, "operator", payload.reason)
    return await db.get_lead_by_id(lead_id)


@router.put("/{lead_id}/deal", response_model=Lead)
async def set_deal_value(lead_id: int, payload: DealValueUpdate):
    """Record (or clear) what this deal is worth — the only source of revenue figures."""
    if not await db.get_lead_by_id(lead_id):
        raise HTTPException(404, "Lead not found")
    await db.set_deal_value(lead_id, payload.deal_value)
    return await db.get_lead_by_id(lead_id)


@router.get("/{lead_id}/stage-history")
async def lead_stage_history(lead_id: int):
    lead = await db.get_lead_by_id(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    return await db.get_stage_history(lead_id)


@router.delete("", status_code=200)
async def delete_all_leads(status: Optional[str] = Query(None)):
    """Bulk-delete leads, optionally filtered by status."""
    deleted = await db.delete_all_leads(status)
    return {"deleted": deleted}


@router.delete("/{lead_id}", status_code=204)
async def delete_lead(lead_id: int):
    deleted = await db.delete_lead(lead_id)
    if not deleted:
        raise HTTPException(404, "Lead not found")


@router.post("/{lead_id}/regenerate")
async def regenerate_messages(
    lead_id: int,
    message_type: str = Query("all", description="all | whatsapp | email | followup"),
):
    """Re-run Ollama for one lead and persist the result."""
    lead = await db.get_lead_by_id(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")

    from .. import ai_brain
    status = await ai_brain.get_ollama_status()
    if not status["connected"]:
        raise HTTPException(503, "Ollama is not running. Start it with: ollama serve")

    try:
        if message_type == "all":
            from pathlib import Path
            from ..config import get_settings
            settings    = get_settings()
            stored      = await db.get_all_settings()
            dna_path    = stored.get("company_dna_path") or settings.company_dna_path
            company_dna = Path(dna_path).read_text(encoding="utf-8") if Path(dna_path).exists() else ""

            # Load enrichment + scores from DB so v2 can use full context
            enriched = await db.get_enriched_data(lead_id) or {}
            scores   = await db.get_score(lead_id)         or {}

            msgs = await ai_brain.generate_messages_v2(dict(lead), enriched, scores, company_dna)
            # v2 already updated the lead in DB — return the result directly
            return {"lead_id": lead_id, **msgs}

        elif message_type == "whatsapp":
            msg = await ai_brain.generate_message(dict(lead), "whatsapp")
            await db.update_lead(lead_id, {"ai_whatsapp_msg": msg})
            return {"lead_id": lead_id, "ai_whatsapp_msg": msg}

        elif message_type == "email":
            subject = await ai_brain.generate_message(dict(lead), "email_subject")
            body    = await ai_brain.generate_message(dict(lead), "email_body")
            update  = {"ai_email_subject": subject, "ai_email_body": body}
            await db.update_lead(lead_id, update)
            return {"lead_id": lead_id, **update}

        elif message_type in ("followup", "followups"):
            msgs   = await ai_brain.generate_followup_sequence(dict(lead))
            update = {
                "ai_followup_msg": msgs.get("follow_up_1"),
                "ai_follow_up_1":  msgs.get("follow_up_1"),
                "ai_follow_up_2":  msgs.get("follow_up_2"),
                "ai_follow_up_3":  msgs.get("follow_up_3"),
            }
            await db.update_lead(lead_id, update)
            return {"lead_id": lead_id, **update}

        else:
            raise HTTPException(400, f"Unknown message_type '{message_type}'. Use: all | whatsapp | email | followup")

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Message regeneration failed for lead %s: %s", lead_id, exc, exc_info=True)
        raise HTTPException(500, "Message regeneration failed — see server logs")


@router.post("/{lead_id}/skip", response_model=Lead)
async def skip_lead(lead_id: int):
    updated = await db.update_lead(lead_id, {"status": "SKIPPED"})
    if not updated:
        raise HTTPException(404, "Lead not found")
    return await db.get_lead_by_id(lead_id)


@router.post("/{lead_id}/resend")
async def resend_lead(lead_id: int, channel: str = Query("EMAIL")):
    """Re-send outreach to an already-contacted lead."""
    lead = await db.get_lead_by_id(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    from .campaigns import _send_one
    return await _send_one(lead_id, channel.upper())
