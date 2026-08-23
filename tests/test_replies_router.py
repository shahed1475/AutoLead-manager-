import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


class _RecordingReplySender:
    def __init__(self):
        self.calls = []

    async def __call__(self, to_email, subject, body):
        self.calls.append({"to_email": to_email, "subject": subject, "body": body})


async def _draft_reply(db, lead_overrides=None, draft_body="Draft reply body."):
    lead = {"business_name": "Draft Co", "email": "lead@company.com"}
    lead.update(lead_overrides or {})
    lead_id = await db.create_lead(lead)
    reply_id = await db.create_reply({
        "lead_id": lead_id, "reply_text": "Tell me more", "detected_intent": "interested",
        "rich_intent": "INTERESTED", "intent_confidence": 0.9, "recommended_action": "SCHEDULE_MEETING",
    })
    await db.set_reply_draft(reply_id, "Re: hello", draft_body)
    return lead_id, reply_id


async def test_drafts_list_includes_recommended_action(clean_db):
    from backend.main import app
    db = clean_db
    await _draft_reply(db)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/replies/drafts")

    assert resp.status_code == 200
    drafts = resp.json()
    assert len(drafts) == 1
    assert drafts[0]["recommended_action"] == "SCHEDULE_MEETING"


async def test_edit_draft_updates_body(clean_db):
    db = clean_db
    _, reply_id = await _draft_reply(db)
    from backend.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(f"/api/replies/{reply_id}/draft", json={"draft_body": "Edited body."})

    assert resp.status_code == 200
    reply = await db.get_reply_by_id(reply_id)
    assert reply["draft_body"] == "Edited body."


async def test_approve_sends_via_existing_sender_and_marks_sent(clean_db, monkeypatch):
    db = clean_db
    _, reply_id = await _draft_reply(db)

    import backend.routers.replies as replies_router
    sender = _RecordingReplySender()
    monkeypatch.setattr(replies_router, "send_reply_email", sender)

    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/replies/{reply_id}/approve")

    assert resp.status_code == 200
    assert len(sender.calls) == 1
    assert sender.calls[0]["to_email"] == "lead@company.com"
    reply = await db.get_reply_by_id(reply_id)
    assert reply["draft_status"] == "SENT"


async def test_approve_twice_returns_409(clean_db, monkeypatch):
    db = clean_db
    _, reply_id = await _draft_reply(db)

    import backend.routers.replies as replies_router
    monkeypatch.setattr(replies_router, "send_reply_email", _RecordingReplySender())

    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(f"/api/replies/{reply_id}/approve")
        second = await client.post(f"/api/replies/{reply_id}/approve")

    assert first.status_code == 200
    assert second.status_code == 409


async def test_discard_draft_without_sending(clean_db, monkeypatch):
    db = clean_db
    _, reply_id = await _draft_reply(db)

    import backend.routers.replies as replies_router
    sender = _RecordingReplySender()
    monkeypatch.setattr(replies_router, "send_reply_email", sender)

    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/replies/{reply_id}/discard")

    assert resp.status_code == 200
    assert sender.calls == []
    reply = await db.get_reply_by_id(reply_id)
    assert reply["draft_status"] == "DISCARDED"


async def test_approve_422_when_lead_has_no_email(clean_db, monkeypatch):
    db = clean_db
    _, reply_id = await _draft_reply(db, lead_overrides={"email": None})

    import backend.routers.replies as replies_router
    monkeypatch.setattr(replies_router, "send_reply_email", _RecordingReplySender())

    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/replies/{reply_id}/approve")

    assert resp.status_code == 422
