import pytest

from backend.routers import campaigns as campaigns_router
from backend.routers import marketing as marketing_router
from backend import followup_engine

pytestmark = pytest.mark.asyncio


class _NeverCalled:
    def __getattr__(self, name):
        async def _fail(*args, **kwargs):
            raise AssertionError(f"sender.{name} must not be called for a DO_NOT_CONTACT lead")
        return _fail


async def test_send_one_blocks_do_not_contact_lead(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({
        "business_name": "Blocked Co", "email": "x@example.com", "status": "DO_NOT_CONTACT",
    })
    monkeypatch.setattr(campaigns_router, "email_sender", _NeverCalled())
    monkeypatch.setattr(campaigns_router, "whatsapp_sender", _NeverCalled())

    result = await campaigns_router._send_one(lead_id, "EMAIL")

    assert result["success"] is False
    assert "DO_NOT_CONTACT" in result["error"]


async def test_send_followup_endpoint_rejects_do_not_contact_lead(clean_db, monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from backend.main import app

    db = clean_db
    lead_id = await db.create_lead({
        "business_name": "Blocked Co 2", "email": "x@example.com", "status": "DO_NOT_CONTACT",
    })
    monkeypatch.setattr(campaigns_router, "email_sender", _NeverCalled())
    monkeypatch.setattr(campaigns_router, "whatsapp_sender", _NeverCalled())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/campaign/send-followup/{lead_id}")

    assert resp.status_code == 400


async def test_followup_engine_cancels_do_not_contact_lead_instead_of_sending(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({
        "business_name": "Blocked Co 3", "email": "x@example.com", "status": "DO_NOT_CONTACT",
    })
    msg_id = await db.create_message({
        "lead_id": lead_id, "sequence_step": 2, "message_type": "followup",
        "status": "PENDING", "body": "old body", "scheduled_for": "2000-01-01 00:00:00",
    })
    monkeypatch.setattr(followup_engine, "email_sender", _NeverCalled())
    monkeypatch.setattr(followup_engine, "whatsapp_sender", _NeverCalled())

    results = await followup_engine.process_followup_queue()

    assert results["cancelled"] == 1
    assert results["sent"] == 0
    msg = (await db.get_messages(lead_id))[0]
    assert msg["status"] == "CANCELLED"


async def test_marketing_router_blocks_do_not_contact_lead(clean_db):
    from fastapi import HTTPException
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Blocked Co 4", "status": "DO_NOT_CONTACT"})
    lead = await db.get_lead_by_id(lead_id)

    with pytest.raises(HTTPException):
        marketing_router._assert_not_opted_out(lead)
