import pytest

from backend import reply_detector

pytestmark = pytest.mark.asyncio


def _imap_message(from_email="lead@company.com", subject="Re: hello", body="STOP"):
    return {
        "from_email": from_email, "from_header": from_email, "subject": subject,
        "body_text": body, "received_at": "Mon, 1 Jan 2026 00:00:00 +0000",
    }


def _patch_imap(monkeypatch, messages):
    async def fake_to_thread(func, *args, **kwargs):
        return messages
    monkeypatch.setattr(reply_detector.asyncio, "to_thread", fake_to_thread)


def _patch_legacy_intent(monkeypatch, intent="unknown"):
    async def fake_classify_intent(*args, **kwargs):
        return intent
    monkeypatch.setattr(reply_detector, "_classify_intent", fake_classify_intent)


def _patch_rich_intent(monkeypatch, intent, action, confidence=0.9, draft=None):
    async def fake_run(self, reply_text, lead, original_message=None, previous_replies=None, campaign=None):
        from backend.intelligence.base import AgentResult
        return AgentResult(
            status="ok",
            data={"intent": intent, "confidence": confidence, "recommended_action": action, "draft_response": draft},
            evidence=[], confidence=confidence,
        )
    monkeypatch.setattr(reply_detector.ReplyIntelligenceAgent, "run", fake_run)


_CONFIG = {
    "imap_host": "imap.example.com", "imap_username": "u", "imap_password": "p",
    "imap_port": "993", "imap_ssl": "true", "imap_since_days": 7,
}


async def test_opt_out_cancels_pending_followups(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Opt Out Co", "email": "lead@company.com", "status": "SENT"})
    await db.create_message({"lead_id": lead_id, "sequence_step": 2, "message_type": "followup", "status": "PENDING"})
    await db.create_message({"lead_id": lead_id, "sequence_step": 3, "message_type": "followup", "status": "PENDING"})

    _patch_imap(monkeypatch, [_imap_message(body="STOP")])
    _patch_legacy_intent(monkeypatch, "unknown")
    _patch_rich_intent(monkeypatch, "OPT_OUT", "SUPPRESS_OUTREACH", confidence=1.0)

    await reply_detector.check_for_replies(_CONFIG)

    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] == "DO_NOT_CONTACT"
    pending = await db.count_pending_followups()
    assert pending == 0


async def test_wrong_contact_stops_campaign_and_advances_status(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Wrong Contact Co", "email": "lead@company.com", "status": "SENT"})
    await db.create_message({"lead_id": lead_id, "sequence_step": 2, "message_type": "followup", "status": "PENDING"})

    _patch_imap(monkeypatch, [_imap_message(body="Wrong person, I no longer work here")])
    _patch_legacy_intent(monkeypatch, "unknown")
    _patch_rich_intent(monkeypatch, "WRONG_CONTACT", "STOP_CAMPAIGN")

    await reply_detector.check_for_replies(_CONFIG)

    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] == "SKIPPED"
    assert await db.count_pending_followups() == 0


async def test_not_interested_stop_campaign_does_not_downgrade_replied(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Already Replied Co", "email": "lead@company.com", "status": "REPLIED"})
    await db.create_message({"lead_id": lead_id, "sequence_step": 2, "message_type": "followup", "status": "PENDING"})

    _patch_imap(monkeypatch, [_imap_message(body="Not interested, thanks")])
    _patch_legacy_intent(monkeypatch, "not_interested")
    _patch_rich_intent(monkeypatch, "NOT_INTERESTED", "STOP_CAMPAIGN")

    await reply_detector.check_for_replies(_CONFIG)

    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] == "REPLIED"  # locked status not downgraded to SKIPPED
    assert await db.count_pending_followups() == 0  # but follow-ups are still cancelled


async def test_interested_reply_does_not_cancel_followups(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Interested Co", "email": "lead@company.com", "status": "SENT"})
    await db.create_message({"lead_id": lead_id, "sequence_step": 2, "message_type": "followup", "status": "PENDING"})

    _patch_imap(monkeypatch, [_imap_message(body="Sounds interesting, tell me more")])
    _patch_legacy_intent(monkeypatch, "interested")
    _patch_rich_intent(monkeypatch, "INTERESTED", "SCHEDULE_MEETING", draft="Happy to share more.")

    await reply_detector.check_for_replies(_CONFIG)

    assert await db.count_pending_followups() == 1
    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] == "REPLIED"
