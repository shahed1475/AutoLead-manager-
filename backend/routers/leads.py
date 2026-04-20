import csv
import io
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from fastapi.responses import Response
from .. import database as db
from ..models import Lead, LeadCreate, LeadUpdate, LeadListResponse, StatusUpdate

router = APIRouter(prefix="/api/leads", tags=["leads"])


@router.get("", response_model=LeadListResponse)
async def list_leads(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: Optional[str] = None,
    channel: Optional[str] = None,
    niche: Optional[str] = None,
    city: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = Query("created_at"),
    sort_dir: str = Query("desc"),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    date_field: str = Query("created_at"),
):
    return await db.get_leads(
        page=page, page_size=page_size,
        status=status, channel=channel,
        niche=niche, city=city, search=search,
        sort_by=sort_by, sort_dir=sort_dir,
        date_from=date_from, date_to=date_to, date_field=date_field,
    )


@router.post("", response_model=Lead, status_code=201)
async def create_lead(payload: LeadCreate):
    lead_id = await db.create_lead(payload.model_dump())
    return await db.get_lead_by_id(lead_id)


# ── Static sub-paths first — must come before /{lead_id} ──────────────────

@router.get("/export/csv")
async def export_csv(
    status: Optional[str] = None,
    channel: Optional[str] = None,
    niche: Optional[str] = None,
    city: Optional[str] = None,
):
    result = await db.get_leads(page_size=10_000, status=status, channel=channel, niche=niche, city=city)
    items = result["items"]
    output = io.StringIO()
    export_cols = [
        "id", "business_name", "phone", "email", "website", "niche", "city",
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
            errors.append(f"Row {i}: {exc}")
    return {"created": created, "errors": errors}


# ── Parameterised routes ───────────────────────────────────────────────────

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


@router.delete("", status_code=200)
async def delete_all_leads(status: Optional[str] = Query(None)):
    """Bulk-delete leads, optionally filtered by status."""
    from ..database import get_db
    async with get_db() as conn:
        valid = {"PENDING", "SENT", "REPLIED", "SKIPPED"}
        if status and status.upper() in valid:
            cursor = await conn.execute("DELETE FROM leads WHERE status = ?", (status.upper(),))
        else:
            cursor = await conn.execute("DELETE FROM leads")
        await conn.commit()
        return {"deleted": cursor.rowcount}


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
            msgs   = await ai_brain.generate_all_messages(dict(lead))
            update = {
                "ai_whatsapp_msg":  msgs["whatsapp"],
                "ai_email_subject": msgs["email_subject"],
                "ai_email_body":    msgs["email_body"],
                "ai_followup_msg":  msgs["followup"],
            }
        elif message_type == "whatsapp":
            msg    = await ai_brain.generate_message(dict(lead), "whatsapp")
            update = {"ai_whatsapp_msg": msg}
        elif message_type == "email":
            subject = await ai_brain.generate_message(dict(lead), "email_subject")
            body    = await ai_brain.generate_message(dict(lead), "email_body")
            update  = {"ai_email_subject": subject, "ai_email_body": body}
        elif message_type == "followup":
            msg    = await ai_brain.generate_message(dict(lead), "followup")
            update = {"ai_followup_msg": msg}
        else:
            raise HTTPException(400, f"Unknown message_type '{message_type}'. Use: all | whatsapp | email | followup")

        await db.update_lead(lead_id, update)
        return {"lead_id": lead_id, **update}

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc))


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
