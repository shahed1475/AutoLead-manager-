"""
test_campaign_send_from.py — Checkpoint 4: a campaign sends through its
selected sender profile.

  * SMTP profile  -> email_sender.send_email (mocked)
  * Gmail profile -> Gmail API (respx-mocked)
  * TEST_MODE still redirects EVERY send to shahedalfahad20@gmail.com,
    for both transports, and the fail-closed gate still runs first.
  * A named-but-unavailable profile BLOCKS the run — never a silent fallback.
  * (campaign_id, lead_key) idempotency is unchanged.
"""
from dataclasses import dataclass, field
from typing import List

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio

TEST_RECIPIENT = "shahedalfahad20@gmail.com"
GMAIL_SEND = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
TOKEN_URL  = "https://oauth2.googleapis.com/token"
PROSPECTS  = ["ceo@northwind-dental.com", "owner@summit-ortho.com", "hi@lakeside-dental.com"]


@dataclass
class _SmtpRecorder:
    calls: list = field(default_factory=list)
    forbidden: set = field(default_factory=set)

    def __call__(self, to_email, subject, body, config, attachments=None, reply_to=None):
        assert to_email not in self.forbidden, f"SMTP send to forbidden {to_email!r}"
        self.calls.append({"to": to_email, "subject": subject, "reply_to": reply_to,
                           "attachments": attachments})
        return True

    @property
    def recipients(self):
        return [c["to"] for c in self.calls]


@pytest.fixture
async def enabled(clean_db):
    await clean_db.upsert_setting("email_campaigns_enabled", "true")
    return clean_db


@pytest.fixture
def stub_ai(monkeypatch):
    from backend import ai_brain
    async def _gm(lead, dna):
        return {"email_subject": "Idea",
                "email_body": "Hi there,\n\nI had a genuine idea worth sharing for "
                              "your business. Open to a quick chat this week?\n\nBest,\nT"}
    monkeypatch.setattr(ai_brain, "generate_messages", _gm)
    monkeypatch.setattr(ai_brain, "_load_company_dna", lambda: "DNA")


@pytest.fixture
def stub_smtp(monkeypatch):
    from backend import email_sender
    rec = _SmtpRecorder()
    monkeypatch.setattr(email_sender, "send_email", rec)

    async def _cfg():
        return {"host": "h", "port": 587, "username": "u@x", "password": "pw",
                "from_name": "T", "from_email": "u@x"}
    monkeypatch.setattr(email_sender, "_smtp_cfg", _cfg)
    return rec


def _client():
    from backend.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _smtp_profile(db, connected=True):
    from backend.secrets_crypto import encrypt
    return await db.create_sender_profile({
        "name": "PopupGenix SMTP", "provider": "smtp", "transport": "smtp",
        "email_address": "hello@popupgenix.com",
        "status": "connected" if connected else "disconnected",
        "smtp_host": "mail.popupgenix.com", "smtp_port": 465, "smtp_security": "ssl",
        "smtp_username": "hello@popupgenix.com", "smtp_password_enc": encrypt("pw"),
    })


async def _gmail_profile(db):
    from backend.secrets_crypto import encrypt
    await db.upsert_setting("google_oauth_client_id", "cid.apps.googleusercontent.com")
    await db.upsert_setting("google_oauth_client_secret", "SECRET")
    return await db.create_sender_profile({
        "name": "Gmail", "provider": "gmail", "transport": "gmail_api",
        "email_address": "shahedalfahad20@gmail.com", "status": "connected",
        "oauth_refresh_token_enc": encrypt("RT"), "oauth_access_token_enc": encrypt("AT"),
        "oauth_expires_at": "2999-01-01T00:00:00",
    })


async def _run(db, sender_profile_id, reply_to=None):
    from backend.email_campaigns.service import get_email_campaign_service
    svc = get_email_campaign_service()
    camp = await svc.create_campaign({"name": "C", "sender_profile_id": sender_profile_id,
                                      "reply_to": reply_to})
    cid = camp["id"]
    csv = "email,company\n" + "\n".join(f"{p},Co{i}" for i, p in enumerate(PROSPECTS)) + "\n"
    await svc.import_leads(cid, "p.csv", csv.encode())
    await svc.mark_ready(cid)
    await svc.start(cid, "run-1", background=False)
    return cid, svc


# ── SMTP profile ──────────────────────────────────────────────────────────

async def test_campaign_uses_selected_smtp_profile_testmode_redirects(enabled, stub_ai, stub_smtp):
    stub_smtp.forbidden = set(PROSPECTS)
    pid = await _smtp_profile(enabled)
    cid, svc = await _run(enabled, pid, reply_to="hello@popupgenix.com")

    assert len(stub_smtp.calls) == 3
    assert set(stub_smtp.recipients) == {TEST_RECIPIENT}     # never a prospect
    assert all(c["reply_to"] == "hello@popupgenix.com" for c in stub_smtp.calls)
    assert (await enabled.get_email_campaign(cid))["status"] == "COMPLETED"

    events = [a["event"] for a in await svc.get_activity(cid)]
    assert "sender_selected" in events
    blob = " ".join(a["detail"] for a in await svc.get_activity(cid))
    assert "hello@popupgenix.com" in blob and "pw" not in blob.split()


# ── Gmail profile ─────────────────────────────────────────────────────────

@respx.mock
async def test_campaign_uses_gmail_profile_testmode_redirects(enabled, stub_ai, stub_smtp):
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "AT2", "expires_in": 3600}))
    send_route = respx.post(GMAIL_SEND).mock(return_value=httpx.Response(200, json={"id": "m1"}))
    pid = await _gmail_profile(enabled)

    cid, svc = await _run(enabled, pid)

    assert send_route.call_count == 3
    import base64, json as J
    for call in send_route.calls:
        mime = base64.urlsafe_b64decode(J.loads(call.request.read())["raw"]).decode("utf-8", "replace")
        assert f"To: {TEST_RECIPIENT}" in mime
        for p in PROSPECTS:
            assert f"To: {p}" not in mime
        assert "From: shahedalfahad20@gmail.com" in mime
    assert stub_smtp.calls == []                             # SMTP path NOT used
    assert (await enabled.get_email_campaign(cid))["status"] == "COMPLETED"


@respx.mock
async def test_gmail_gate_runs_before_transport(enabled, stub_ai, monkeypatch):
    from backend.email_campaigns import service as svc_mod
    order = []
    real_gate = svc_mod.assert_send_allowed
    monkeypatch.setattr(svc_mod, "assert_send_allowed",
                        lambda c, s, l: (order.append("gate"), real_gate(c, s, l))[1])

    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "A", "expires_in": 3600}))

    def _send(request):
        order.append("send")
        return httpx.Response(200, json={"id": "m"})
    respx.post(GMAIL_SEND).mock(side_effect=_send)

    pid = await _gmail_profile(enabled)
    await _run(enabled, pid)
    assert order == ["gate", "send"] * 3


# ── no silent fallback ────────────────────────────────────────────────────

async def test_disconnected_profile_blocks_start_no_fallback(enabled, stub_ai, stub_smtp):
    from backend.email_campaigns.service import get_email_campaign_service, EmailCampaignError
    svc = get_email_campaign_service()
    pid = await _smtp_profile(enabled, connected=False)
    camp = await svc.create_campaign({"name": "C", "sender_profile_id": pid})
    cid = camp["id"]
    await svc.import_leads(cid, "p.csv", b"email\na@northwind-dental.com\n")
    await svc.mark_ready(cid)

    with pytest.raises(EmailCampaignError):
        await svc.start(cid, "k", background=False)
    assert stub_smtp.calls == []                             # NO fallback to global SMTP
    assert (await enabled.get_email_campaign(cid))["status"] == "READY"


@respx.mock
async def test_gmail_refresh_failure_blocks_run_no_fallback(enabled, stub_ai, stub_smtp):
    from backend.email_campaigns.service import get_email_campaign_service, EmailCampaignError
    from backend.secrets_crypto import encrypt
    svc = get_email_campaign_service()
    await enabled.upsert_setting("google_oauth_client_id", "cid.apps.googleusercontent.com")
    await enabled.upsert_setting("google_oauth_client_secret", "SECRET")
    pid = await enabled.create_sender_profile({
        "name": "Gmail", "provider": "gmail", "transport": "gmail_api",
        "email_address": "x@gmail.com", "status": "connected",
        "oauth_refresh_token_enc": encrypt("RT-dead"), "oauth_expires_at": "2000-01-01T00:00:00",
    })
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))

    camp = await svc.create_campaign({"name": "C", "sender_profile_id": pid})
    cid = camp["id"]
    await svc.import_leads(cid, "p.csv", b"email\na@northwind-dental.com\n")
    await svc.mark_ready(cid)
    with pytest.raises(EmailCampaignError):
        await svc.start(cid, "k", background=False)
    assert stub_smtp.calls == []
    assert (await enabled.get_sender_profile(pid))["status"] == "error"


async def test_deleted_profile_mid_flight_blocks_run(enabled, stub_ai, stub_smtp):
    """Profile selected, campaign RUNNING, profile deleted -> run FAILS closed."""
    from backend.email_campaigns.service import get_email_campaign_service
    svc = get_email_campaign_service()
    pid = await _smtp_profile(enabled)
    camp = await svc.create_campaign({"name": "C", "sender_profile_id": pid})
    cid = camp["id"]
    await svc.import_leads(cid, "p.csv", b"email\na@northwind-dental.com\n")
    await svc.mark_ready(cid)

    run = await enabled.create_email_campaign_run(cid, "k")
    # simulate: campaign RUNNING but the profile row is gone AND still referenced
    await enabled.update_email_campaign(cid, {"status": "RUNNING"})
    async with enabled.get_db() as conn:
        await conn.execute("DELETE FROM email_sender_profiles WHERE id = ?", pid)

    await svc.run_batch(cid, run["id"])
    assert stub_smtp.calls == []
    assert (await enabled.get_email_campaign(cid))["status"] == "FAILED"


# ── idempotency preserved ─────────────────────────────────────────────────

async def test_idempotency_unchanged_with_sender_profile(enabled, stub_ai, stub_smtp):
    from backend.email_campaigns.service import get_email_campaign_service
    svc = get_email_campaign_service()
    pid = await _smtp_profile(enabled)
    camp = await svc.create_campaign({"name": "C", "sender_profile_id": pid})
    cid = camp["id"]
    await svc.import_leads(cid, "p.csv", b"email\na@northwind-dental.com\n")
    await svc.mark_ready(cid)

    run = await enabled.create_email_campaign_run(cid, "k")
    await enabled.update_email_campaign(cid, {"status": "RUNNING"})
    await svc.run_batch(cid, run["id"])
    await enabled.update_email_campaign(cid, {"status": "RUNNING"})
    await svc.run_batch(cid, run["id"])            # second pass over the SENT lead
    assert len(stub_smtp.calls) == 1


# ── campaign API exposes the sender safely ────────────────────────────────

async def test_campaign_api_shows_sender_without_secrets(enabled, stub_smtp):
    import json as J
    pid = await _smtp_profile(enabled)
    async with _client() as c:
        r = await c.post("/api/email-campaigns", json={"name": "C", "sender_profile_id": pid})
        assert r.status_code == 201
        body = r.json()
        assert body["sender"]["email_address"] == "hello@popupgenix.com"
        assert body["sender"]["provider"] == "smtp"
        assert "smtp_password" not in J.dumps(body) and "pw" not in J.dumps(body["sender"]).split()
        # PATCH to unset -> back to global
        cid = body["id"]
        r = await c.patch(f"/api/email-campaigns/{cid}", json={"sender_profile_id": None})
        assert r.json()["sender"] is None
