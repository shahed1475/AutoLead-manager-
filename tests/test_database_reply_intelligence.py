import pytest

pytestmark = pytest.mark.asyncio


async def test_create_reply_with_rich_intent_fields(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Alpha Dental"})

    reply_id = await db.create_reply({
        "lead_id": lead_id, "reply_text": "How much does this cost?",
        "detected_intent": "interested",
        "rich_intent": "PRICING", "intent_confidence": 0.85,
        "recommended_action": "REQUEST_REQUIREMENTS",
    })

    reply = await db.get_reply_by_id(reply_id)
    assert reply["rich_intent"] == "PRICING"
    assert reply["intent_confidence"] == 0.85
    assert reply["recommended_action"] == "REQUEST_REQUIREMENTS"


async def test_update_reply_intelligence_sets_fields(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Beta Co"})
    reply_id = await db.create_reply({"lead_id": lead_id, "reply_text": "x", "detected_intent": "unknown"})

    await db.update_reply_intelligence(reply_id, {
        "rich_intent": "OPT_OUT", "intent_confidence": 1.0, "recommended_action": "SUPPRESS_OUTREACH",
    })

    reply = await db.get_reply_by_id(reply_id)
    assert reply["rich_intent"] == "OPT_OUT"
    assert reply["intent_confidence"] == 1.0
    assert reply["recommended_action"] == "SUPPRESS_OUTREACH"


async def test_update_reply_intelligence_noop_on_empty_data(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Gamma Co"})
    reply_id = await db.create_reply({"lead_id": lead_id, "reply_text": "x", "detected_intent": "unknown"})

    await db.update_reply_intelligence(reply_id, {})  # must not raise
    reply = await db.get_reply_by_id(reply_id)
    assert reply["rich_intent"] is None


async def test_get_pending_reply_drafts_includes_rich_intent_fields(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Delta Inc", "email": "d@delta.co"})
    reply_id = await db.create_reply({"lead_id": lead_id, "reply_text": "Tell me more", "detected_intent": "interested"})
    await db.update_reply_intelligence(reply_id, {
        "rich_intent": "INTERESTED", "intent_confidence": 0.9, "recommended_action": "SCHEDULE_MEETING",
    })
    await db.set_reply_draft(reply_id, "Re: hello", "Draft body")

    drafts = await db.get_pending_reply_drafts()
    assert len(drafts) == 1
    assert drafts[0]["rich_intent"] == "INTERESTED"
    assert drafts[0]["recommended_action"] == "SCHEDULE_MEETING"


async def test_get_replies_returns_conversation_history_ordered(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Epsilon LLC"})
    await db.create_reply({"lead_id": lead_id, "reply_text": "First reply", "detected_intent": "unknown"})
    await db.create_reply({"lead_id": lead_id, "reply_text": "Second reply", "detected_intent": "interested"})

    history = await db.get_replies(lead_id)
    assert len(history) == 2


async def test_do_not_contact_status_survives_restart_migration(clean_db):
    """Safety-critical: DO_NOT_CONTACT must be in the migration's status
    allow-list, or a restart would silently revert the opt-out to PENDING."""
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Zeta Opt-Out Co"})
    await db.update_lead(lead_id, {"status": "DO_NOT_CONTACT"})

    await db.init_db()  # simulates a process restart re-running migrations

    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] == "DO_NOT_CONTACT"
