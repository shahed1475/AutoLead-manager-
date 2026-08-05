import logging
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request
from .. import database as db
from .. import ai_brain
from ..models import AIGenerateRequest, BulkAIRequest, BusinessTextRequest, TestPromptRequest
from ..rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/status")
async def ollama_status():
    return await ai_brain.get_ollama_status()


@router.post("/generate")
@limiter.limit("30/minute")
async def generate_for_lead(request: Request, payload: AIGenerateRequest):
    lead = await db.get_lead_by_id(payload.lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")

    status = await ai_brain.get_ollama_status()
    if not status["connected"]:
        raise HTTPException(503, "Ollama is not running. Start it with: ollama serve")

    lead_id  = payload.lead_id
    msg_type = payload.message_type
    try:
        if msg_type == "all":
            from pathlib import Path
            from ..config import get_settings
            settings    = get_settings()
            stored      = await db.get_all_settings()
            dna_path    = stored.get("company_dna_path") or settings.company_dna_path
            company_dna = Path(dna_path).read_text(encoding="utf-8") if Path(dna_path).exists() else ""

            enriched = await db.get_enriched_data(lead_id) or {}
            scores   = await db.get_score(lead_id)         or {}

            # v2: single Ollama call with full enrichment context; handles own DB writes
            msgs = await ai_brain.generate_messages_v2(dict(lead), enriched, scores, company_dna)
            return {"lead_id": lead_id, **msgs,
                    # legacy keys for ViewMessagesModal backward compat
                    "ai_whatsapp_msg":  msgs["whatsapp_message"],
                    "ai_email_subject": msgs["email_subject"],
                    "ai_email_body":    msgs["email_body"],
                    "ai_followup_msg":  msgs["followup_day3_body"],
                    "ai_follow_up_1":   msgs["followup_day3_body"],
                    "ai_follow_up_2":   msgs["followup_day7_body"],
                    "ai_follow_up_3":   msgs["followup_day7_body"],
                    }

        elif msg_type == "whatsapp":
            msg = await ai_brain.generate_message(dict(lead), "whatsapp")
            await db.update_lead(lead_id, {"ai_whatsapp_msg": msg})
            return {"lead_id": lead_id, "ai_whatsapp_msg": msg}

        elif msg_type == "email":
            subject = await ai_brain.generate_message(dict(lead), "email_subject")
            body    = await ai_brain.generate_message(dict(lead), "email_body")
            await db.update_lead(lead_id, {"ai_email_subject": subject, "ai_email_body": body})
            return {"lead_id": lead_id, "ai_email_subject": subject, "ai_email_body": body}

        elif msg_type in ("followup", "followups"):
            msgs   = await ai_brain.generate_followup_sequence(dict(lead))
            update = {
                "ai_followup_msg": msgs["follow_up_1"],
                "ai_follow_up_1":  msgs["follow_up_1"],
                "ai_follow_up_2":  msgs["follow_up_2"],
                "ai_follow_up_3":  msgs["follow_up_3"],
            }
            await db.update_lead(lead_id, update)
            return {"lead_id": lead_id, **update}

        else:
            raise HTTPException(400, f"Unknown message_type: {msg_type}")

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("AI generation failed for lead %s (%s): %s", lead_id, msg_type, exc, exc_info=True)
        raise HTTPException(500, "AI generation failed — see server logs")


@router.post("/generate-bulk")
@limiter.limit("10/minute")
async def generate_bulk(request: Request, payload: BulkAIRequest, background_tasks: BackgroundTasks):
    if payload.lead_ids:
        lead_ids = payload.lead_ids
    else:
        result   = await db.get_leads(status="PENDING", page_size=200)
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
                    "ai_whatsapp_msg":  msgs["first_message"],
                    "ai_email_subject": msgs["email_subject"],
                    "ai_email_body":    msgs["email_body"],
                    "ai_followup_msg":  msgs["follow_up_1"],
                    "ai_follow_up_1":   msgs["follow_up_1"],
                    "ai_follow_up_2":   msgs["follow_up_2"],
                    "ai_follow_up_3":   msgs["follow_up_3"],
                })
            except Exception:
                pass

    background_tasks.add_task(_run)
    return {"queued": len(lead_ids), "message": "Bulk generation started in background"}


@router.post("/test-prompt")
async def test_prompt(payload: TestPromptRequest):
    if not payload.prompt:
        raise HTTPException(400, "prompt is required")
    try:
        cfg = await ai_brain._ollama_cfg()
        raw = await ai_brain._call_llm_raw(payload.prompt, cfg, num_predict=400)
        return {"response": raw}
    except Exception as exc:
        logger.error("test-prompt failed: %s", exc, exc_info=True)
        raise HTTPException(500, "Prompt test failed — see server logs")


@router.post("/test")
async def test_ai(payload: BusinessTextRequest):
    """Generate all message types from raw free-form business description text."""
    business_text = payload.business_text.strip()
    if not business_text:
        raise HTTPException(400, "business_text is required")

    status = await ai_brain.get_ollama_status()
    if not status["connected"]:
        raise HTTPException(503, "Ollama is not running. Start it with: ollama serve")

    try:
        msgs = await ai_brain.generate_messages_from_text(business_text)
        return {
            "ai_whatsapp_msg":  msgs.get("first_message") or msgs.get("whatsapp", ""),
            "ai_email_subject": msgs.get("email_subject", ""),
            "ai_email_body":    msgs.get("email_body", ""),
            "ai_followup_msg":  msgs.get("follow_up_1") or msgs.get("followup", ""),
            "ai_follow_up_1":   msgs.get("follow_up_1", ""),
            "ai_follow_up_2":   msgs.get("follow_up_2", ""),
            "ai_follow_up_3":   msgs.get("follow_up_3", ""),
        }
    except Exception as exc:
        logger.error("AI test generation failed: %s", exc, exc_info=True)
        raise HTTPException(500, "AI generation failed — see server logs")
