"""
routers/marketing.py — Phase 3 AI Marketing Agent endpoints.

Generates and manages draft outreach messages behind a human-approval gate.
Approving a message only copies its content into the existing
leads.ai_email_subject / ai_email_body / ai_whatsapp_msg columns — the exact
fields email_sender.send_email_lead() and whatsapp_sender.send_whatsapp_lead()
already read. Actual delivery still goes through the existing, untouched
Send endpoints in routers/campaigns.py. Nothing here sends anything.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from .. import database as db
from ..intelligence.orchestrator import run_marketing_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/leads", tags=["marketing"])

# Same "never downgrade an already-progressed lead" convention used by
# lead_scorer.py's score_lead() — approving a draft must not resurrect a
# lead that already replied, was sent, or opted out.
_NO_STATUS_ADVANCE = frozenset({"SENT", "REPLIED", "SKIPPED", "DO_NOT_CONTACT"})


async def _get_lead_or_404(lead_id: int):
    lead = await db.get_lead_by_id(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    return lead


def _assert_not_opted_out(lead: dict) -> None:
    if (lead.get("status") or "").upper() in ("SKIPPED", "DO_NOT_CONTACT"):
        raise HTTPException(400, "Lead has opted out — cannot queue or approve marketing messages")


async def _get_generated_message_or_404(lead_id: int, message_id: int) -> dict:
    msg = await db.get_generated_message(message_id)
    if not msg or msg["lead_id"] != lead_id:
        raise HTTPException(404, "Generated message not found for this lead")
    return msg


@router.post("/{lead_id}/messages/generate")
async def generate_lead_messages(lead_id: int):
    lead = await _get_lead_or_404(lead_id)
    _assert_not_opted_out(lead)

    profile = await db.get_company_profile(lead_id)
    if not profile:
        raise HTTPException(400, "Company research must complete before message generation can run")
    pain_points = await db.get_pain_points(profile["id"])
    if not pain_points:
        raise HTTPException(400, "Pain-point analysis must run before message generation can run")

    result = await run_marketing_agent(lead)
    if result["status"] == "FAILED":
        raise HTTPException(500, result.get("error") or "Message generation failed")
    return result


@router.get("/{lead_id}/messages")
async def get_lead_messages(lead_id: int):
    await _get_lead_or_404(lead_id)
    return await db.get_generated_messages(lead_id)


@router.put("/{lead_id}/messages/{message_id}")
async def edit_lead_message(lead_id: int, message_id: int, payload: dict):
    await _get_lead_or_404(lead_id)
    await _get_generated_message_or_404(lead_id, message_id)

    update: dict = {}
    if "message" in payload:
        update["message"] = payload["message"]
    if "subject" in payload:
        update["subject"] = payload["subject"]
    if not update:
        raise HTTPException(400, "Nothing to update — provide 'message' and/or 'subject'")

    await db.update_generated_message(message_id, update)
    return await db.get_generated_message(message_id)


@router.post("/{lead_id}/messages/{message_id}/approve")
async def approve_lead_message(lead_id: int, message_id: int):
    lead = await _get_lead_or_404(lead_id)
    _assert_not_opted_out(lead)
    msg = await _get_generated_message_or_404(lead_id, message_id)

    now = datetime.now(timezone.utc).isoformat()
    await db.update_generated_message(message_id, {
        "approval_status": "APPROVED", "reviewed_at": now,
    })

    # Stage into the existing send path — never touch the other channel's fields.
    channel = (msg.get("channel") or "").upper()
    lead_update: dict = {}
    if channel == "EMAIL":
        lead_update["ai_email_subject"] = msg.get("subject") or ""
        lead_update["ai_email_body"] = msg.get("message") or ""
    elif channel == "WHATSAPP":
        lead_update["ai_whatsapp_msg"] = msg.get("message") or ""
    else:
        raise HTTPException(400, f"Unknown channel: {channel}")

    if (lead.get("status") or "").upper() not in _NO_STATUS_ADVANCE:
        lead_update["status"] = "MESSAGES_READY"

    await db.update_lead(lead_id, lead_update)
    return await db.get_generated_message(message_id)


@router.post("/{lead_id}/messages/{message_id}/reject")
async def reject_lead_message(lead_id: int, message_id: int, payload: dict = None):
    await _get_lead_or_404(lead_id)
    await _get_generated_message_or_404(lead_id, message_id)

    reason = (payload or {}).get("reason") if payload else None
    now = datetime.now(timezone.utc).isoformat()
    await db.update_generated_message(message_id, {
        "approval_status": "REJECTED", "rejection_reason": reason, "reviewed_at": now,
    })
    return await db.get_generated_message(message_id)
