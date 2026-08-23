import pytest

pytestmark = pytest.mark.asyncio


def _message_item(**overrides):
    base = {
        "channel": "EMAIL", "variant": "PRIMARY",
        "strategy": "Lead with the observed booking gap.",
        "pain_point": "No visible online appointment/booking system",
        "evidence": "cta_buttons=[]",
        "business_impact": "Staff handle scheduling manually.",
        "solution": "An automated booking and WhatsApp reminder workflow.",
        "business_benefit": "Makes scheduling easier for customers and staff.",
        "service_name": "WhatsApp Automation",
        "subject": "Quick question about your booking process",
        "message": "I noticed your website doesn't appear to have online booking...",
        "cta": "Would you be open to a quick demo?",
        "confidence": 0.8,
    }
    base.update(overrides)
    return base


async def test_replace_and_get_generated_messages(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Alpha Dental"})

    ids = await db.replace_generated_messages(lead_id, [_message_item()])
    assert len(ids) == 1

    messages = await db.get_generated_messages(lead_id)
    assert len(messages) == 1
    assert messages[0]["channel"] == "EMAIL"
    assert messages[0]["approval_status"] == "READY_FOR_REVIEW"  # default


async def test_replace_generated_messages_prevents_duplicates_on_rerun(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Beta Clinic"})

    item = _message_item()
    await db.replace_generated_messages(lead_id, [item])
    await db.replace_generated_messages(lead_id, [item])

    messages = await db.get_generated_messages(lead_id)
    assert len(messages) == 1


async def test_get_generated_message_by_id(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Gamma Co"})
    [msg_id] = await db.replace_generated_messages(lead_id, [_message_item()])

    msg = await db.get_generated_message(msg_id)
    assert msg is not None
    assert msg["id"] == msg_id


async def test_get_generated_message_returns_none_when_absent(clean_db):
    db = clean_db
    assert await db.get_generated_message(999999) is None


async def test_update_generated_message_approval_status(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Delta Inc"})
    [msg_id] = await db.replace_generated_messages(lead_id, [_message_item()])

    updated = await db.update_generated_message(msg_id, {"approval_status": "APPROVED"})
    assert updated

    msg = await db.get_generated_message(msg_id)
    assert msg["approval_status"] == "APPROVED"


async def test_update_generated_message_edits_content(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Epsilon LLC"})
    [msg_id] = await db.replace_generated_messages(lead_id, [_message_item()])

    await db.update_generated_message(msg_id, {"message": "Edited by human reviewer."})
    msg = await db.get_generated_message(msg_id)
    assert msg["message"] == "Edited by human reviewer."


async def test_update_generated_message_rejection_with_reason(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Zeta Corp"})
    [msg_id] = await db.replace_generated_messages(lead_id, [_message_item()])

    await db.update_generated_message(msg_id, {
        "approval_status": "REJECTED", "rejection_reason": "Tone too casual",
    })
    msg = await db.get_generated_message(msg_id)
    assert msg["approval_status"] == "REJECTED"
    assert msg["rejection_reason"] == "Tone too casual"


async def test_multiple_channels_and_variants_coexist(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Eta Group"})
    items = [
        _message_item(channel="EMAIL", variant="PRIMARY"),
        _message_item(channel="WHATSAPP", variant="PRIMARY", subject=None, message="Short WA msg"),
        _message_item(channel="EMAIL", variant="ALTERNATIVE_1"),
    ]
    ids = await db.replace_generated_messages(lead_id, items)
    assert len(ids) == 3
    messages = await db.get_generated_messages(lead_id)
    assert len(messages) == 3


async def test_cascade_delete_removes_generated_messages(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Theta Ltd"})
    await db.replace_generated_messages(lead_id, [_message_item()])

    assert await db.delete_lead(lead_id)
    assert await db.get_generated_messages(lead_id) == []
