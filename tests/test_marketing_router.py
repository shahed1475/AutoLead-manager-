import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


def _generated_message_item(**overrides):
    base = {
        "channel": "EMAIL", "variant": "PRIMARY",
        "subject": "Quick thought on Acme Dental",
        "message": "I noticed something worth mentioning...",
        "pain_point": "No booking", "confidence": 0.8,
    }
    base.update(overrides)
    return base


async def test_generate_404_when_lead_missing(clean_db):
    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/leads/999999/messages/generate")
    assert resp.status_code == 404


async def test_generate_400_when_no_profile(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "No Profile Co"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/leads/{lead_id}/messages/generate")
    assert resp.status_code == 400


async def test_generate_400_when_no_pain_points(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "No Pain Points Co"})
    await db.upsert_company_profile(lead_id, {"status": "DONE"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/leads/{lead_id}/messages/generate")
    assert resp.status_code == 400


async def test_generate_400_when_lead_opted_out(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Opted Out Co", "status": "SKIPPED"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    await db.replace_pain_points(profile_id, [{"title": "X", "confidence": 0.5}])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/leads/{lead_id}/messages/generate")
    assert resp.status_code == 400


async def test_generate_success(clean_db, monkeypatch):
    import backend.routers.marketing as marketing_router
    from backend.main import app

    db = clean_db
    lead_id = await db.create_lead({"business_name": "Ready Co"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    await db.replace_pain_points(profile_id, [{"title": "No booking", "confidence": 0.8}])

    async def fake_run_marketing_agent(lead):
        return {"lead_id": lead_id, "status": "DONE", "messages_generated": 2}

    monkeypatch.setattr(marketing_router, "run_marketing_agent", fake_run_marketing_agent)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/leads/{lead_id}/messages/generate")

    assert resp.status_code == 200
    assert resp.json()["messages_generated"] == 2


async def test_get_messages_endpoint(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "List Co"})
    await db.replace_generated_messages(lead_id, [_generated_message_item()])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/leads/{lead_id}/messages")

    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["approval_status"] == "READY_FOR_REVIEW"


async def test_edit_message_updates_content(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Edit Co"})
    [msg_id] = await db.replace_generated_messages(lead_id, [_generated_message_item()])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(f"/api/leads/{lead_id}/messages/{msg_id}", json={"message": "Edited text"})

    assert resp.status_code == 200
    assert resp.json()["message"] == "Edited text"


async def test_edit_message_404_for_wrong_lead(clean_db):
    from backend.main import app
    db = clean_db
    lead_id_a = await db.create_lead({"business_name": "Lead A"})
    lead_id_b = await db.create_lead({"business_name": "Lead B"})
    [msg_id] = await db.replace_generated_messages(lead_id_a, [_generated_message_item()])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(f"/api/leads/{lead_id_b}/messages/{msg_id}", json={"message": "x"})
    assert resp.status_code == 404


async def test_approve_email_message_stages_into_lead_columns(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Approve Co", "status": "PENDING"})
    [msg_id] = await db.replace_generated_messages(lead_id, [_generated_message_item(
        channel="EMAIL", subject="Subject line", message="Email body text",
    )])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/leads/{lead_id}/messages/{msg_id}/approve")

    assert resp.status_code == 200
    assert resp.json()["approval_status"] == "APPROVED"

    lead = await db.get_lead_by_id(lead_id)
    assert lead["ai_email_subject"] == "Subject line"
    assert lead["ai_email_body"] == "Email body text"
    assert lead["status"] == "MESSAGES_READY"
    assert not lead.get("ai_whatsapp_msg")  # other channel's field untouched


async def test_approve_whatsapp_message_only_touches_whatsapp_field(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "WA Co", "status": "PENDING"})
    [msg_id] = await db.replace_generated_messages(lead_id, [_generated_message_item(
        channel="WHATSAPP", subject=None, message="Short WA body",
    )])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/leads/{lead_id}/messages/{msg_id}/approve")

    assert resp.status_code == 200
    lead = await db.get_lead_by_id(lead_id)
    assert lead["ai_whatsapp_msg"] == "Short WA body"
    assert not lead.get("ai_email_body")


async def test_approve_never_sets_status_to_sent(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Never Sent Co", "status": "PENDING"})
    [msg_id] = await db.replace_generated_messages(lead_id, [_generated_message_item()])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/leads/{lead_id}/messages/{msg_id}/approve")

    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] != "SENT"
    assert lead["status"] == "MESSAGES_READY"


async def test_approve_does_not_downgrade_already_sent_lead(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Already Sent Co", "status": "SENT"})
    [msg_id] = await db.replace_generated_messages(lead_id, [_generated_message_item()])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/leads/{lead_id}/messages/{msg_id}/approve")

    assert resp.status_code == 200
    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] == "SENT"  # not downgraded to MESSAGES_READY


async def test_approve_400_when_lead_opted_out(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Opted Out Co", "status": "SKIPPED"})
    [msg_id] = await db.replace_generated_messages(lead_id, [_generated_message_item()])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/leads/{lead_id}/messages/{msg_id}/approve")
    assert resp.status_code == 400


async def test_reject_message_with_reason(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Reject Co"})
    [msg_id] = await db.replace_generated_messages(lead_id, [_generated_message_item()])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/leads/{lead_id}/messages/{msg_id}/reject", json={"reason": "Too generic"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_status"] == "REJECTED"
    assert body["rejection_reason"] == "Too generic"


async def test_approve_404_for_nonexistent_message(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "No Msg Co"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/leads/{lead_id}/messages/999999/approve")
    assert resp.status_code == 404
