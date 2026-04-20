from fastapi import APIRouter, HTTPException, BackgroundTasks
from .. import database as db
from .. import ai_brain
from ..models import AIGenerateRequest, BulkAIRequest

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/status")
async def ollama_status():
    return await ai_brain.get_ollama_status()


@router.post("/generate")
async def generate_for_lead(payload: AIGenerateRequest):
    lead = await db.get_lead_by_id(payload.lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")

    status = await ai_brain.get_ollama_status()
    if not status["connected"]:
        raise HTTPException(503, "Ollama is not running. Start it with: ollama serve")

    msg_type = payload.message_type
    try:
        if msg_type == "all":
            msgs = await ai_brain.generate_all_messages(dict(lead))
            update = {
                "ai_whatsapp_msg": msgs["whatsapp"],
                "ai_email_subject": msgs["email_subject"],
                "ai_email_body": msgs["email_body"],
                "ai_followup_msg": msgs["followup"],
            }
            await db.update_lead(payload.lead_id, update)
            return {"lead_id": payload.lead_id, **update}

        elif msg_type == "whatsapp":
            msg = await ai_brain.generate_message(dict(lead), "whatsapp")
            await db.update_lead(payload.lead_id, {"ai_whatsapp_msg": msg})
            return {"lead_id": payload.lead_id, "ai_whatsapp_msg": msg}

        elif msg_type == "email":
            subject = await ai_brain.generate_message(dict(lead), "email_subject")
            body = await ai_brain.generate_message(dict(lead), "email_body")
            await db.update_lead(payload.lead_id, {"ai_email_subject": subject, "ai_email_body": body})
            return {"lead_id": payload.lead_id, "ai_email_subject": subject, "ai_email_body": body}

        elif msg_type == "followup":
            msg = await ai_brain.generate_message(dict(lead), "followup")
            await db.update_lead(payload.lead_id, {"ai_followup_msg": msg})
            return {"lead_id": payload.lead_id, "ai_followup_msg": msg}

        else:
            raise HTTPException(400, f"Unknown message_type: {msg_type}")

    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/generate-bulk")
async def generate_bulk(payload: BulkAIRequest, background_tasks: BackgroundTasks):
    if payload.lead_ids:
        lead_ids = payload.lead_ids
    else:
        result = await db.get_leads(status="PENDING", page_size=200)
        lead_ids = [r["id"] for r in result["items"]]

    if not lead_ids:
        return {"message": "No leads to process", "count": 0}

    async def _run():
        for lead_id in lead_ids:
            lead = await db.get_lead_by_id(lead_id)
            if not lead:
                continue
            try:
                msgs = await ai_brain.generate_all_messages(dict(lead))
                await db.update_lead(lead_id, {
                    "ai_whatsapp_msg": msgs["whatsapp"],
                    "ai_email_subject": msgs["email_subject"],
                    "ai_email_body": msgs["email_body"],
                    "ai_followup_msg": msgs["followup"],
                })
            except Exception:
                pass

    background_tasks.add_task(_run)
    return {"queued": len(lead_ids), "message": "Bulk generation started in background"}


@router.post("/test-prompt")
async def test_prompt(payload: dict):
    prompt = payload.get("prompt", "")
    if not prompt:
        raise HTTPException(400, "prompt is required")

    from .. import ai_brain
    raw = await ai_brain._call_ollama_raw(prompt, await ai_brain._ollama_cfg(), num_predict=400)
    return {"response": raw}


@router.post("/test")
async def test_ai(payload: dict):
    """Generate all message types from raw free-form business description text."""
    business_text = (payload.get("business_text") or "").strip()
    if not business_text:
        raise HTTPException(400, "business_text is required")

    status = await ai_brain.get_ollama_status()
    if not status["connected"]:
        raise HTTPException(503, "Ollama is not running. Start it with: ollama serve")

    try:
        msgs = await ai_brain.generate_messages_from_text(business_text)
        return {
            "ai_whatsapp_msg":  msgs["whatsapp"],
            "ai_email_subject": msgs["email_subject"],
            "ai_email_body":    msgs["email_body"],
            "ai_followup_msg":  msgs["followup"],
        }
    except Exception as exc:
        raise HTTPException(500, str(exc))
