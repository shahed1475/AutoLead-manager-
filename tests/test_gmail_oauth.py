"""
test_gmail_oauth.py — Checkpoint 4B: Gmail OAuth2 for sender profiles.

Every Google call is mocked with respx. No real network, no real credentials,
no token or secret in any assertion output.
"""
import base64

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio

TOKEN_URL    = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
REVOKE_URL   = "https://oauth2.googleapis.com/revoke"
GMAIL_SEND   = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


@pytest.fixture
async def oauth_configured(clean_db):
    db = clean_db
    await db.upsert_setting("email_campaigns_enabled", "true")
    await db.upsert_setting("google_oauth_client_id", "test-client-id.apps.googleusercontent.com")
    await db.upsert_setting("google_oauth_client_secret", "TEST-CLIENT-SECRET")   # -> encrypted
    await db.upsert_setting("google_oauth_redirect_uri", "http://localhost:8001/api/email-senders/gmail/callback")
    await db.upsert_setting("frontend_base_url", "http://localhost:5173")
    return db


def _client():
    from backend.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── config ────────────────────────────────────────────────────────────────

async def test_config_fail_closed_when_unset(clean_db):
    from backend.email_campaigns import gmail_oauth
    with pytest.raises(gmail_oauth.GoogleOAuthNotConfigured):
        await gmail_oauth.get_config()
    assert await gmail_oauth.is_configured() is False


async def test_config_reads_encrypted_secret(oauth_configured):
    from backend.email_campaigns import gmail_oauth
    cfg = await gmail_oauth.get_config()
    assert cfg["client_id"] == "test-client-id.apps.googleusercontent.com"
    assert cfg["client_secret"] == "TEST-CLIENT-SECRET"          # decrypted for backend use
    # the raw stored value is encrypted
    async with oauth_configured.get_db() as conn:
        raw = await conn.fetchval("SELECT value FROM app_settings WHERE key='google_oauth_client_secret'")
    assert raw.startswith("enc:v1:") and "TEST-CLIENT-SECRET" not in raw


async def test_auth_url_has_minimal_scopes_and_state(oauth_configured):
    from backend.email_campaigns import gmail_oauth
    cfg = await gmail_oauth.get_config()
    url = gmail_oauth.build_auth_url("STATE123", cfg)
    assert "gmail.send" in url
    assert "state=STATE123" in url
    assert "access_type=offline" in url and "prompt=consent" in url
    for forbidden in ("gmail.readonly", "gmail.modify", "mail.google.com", "gmail.metadata"):
        assert forbidden not in url


# ── connect / callback ────────────────────────────────────────────────────

async def test_connect_returns_auth_url_and_stores_state(oauth_configured):
    async with _client() as c:
        r = await c.get("/api/email-senders/gmail/connect")
    assert r.status_code == 200
    assert r.json()["auth_url"].startswith("https://accounts.google.com/")
    async with oauth_configured.get_db() as conn:
        n = await conn.fetchval("SELECT COUNT(*) FROM oauth_states WHERE purpose='gmail_sender'")
    assert n == 1


async def test_connect_503_when_not_configured(clean_db):
    await clean_db.upsert_setting("email_campaigns_enabled", "true")
    async with _client() as c:
        assert (await c.get("/api/email-senders/gmail/connect")).status_code == 503


@respx.mock
async def test_callback_happy_path_creates_gmail_profile(oauth_configured):
    db = oauth_configured
    await db.create_oauth_state("good-state", "gmail_sender", 600)
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={
        "access_token": "AT-1", "refresh_token": "RT-1", "expires_in": 3600,
        "scope": "openid email https://www.googleapis.com/auth/gmail.send",
    }))
    respx.get(USERINFO_URL).mock(return_value=httpx.Response(200, json={
        "email": "shahedalfahad20@gmail.com", "email_verified": True, "name": "Fahad",
    }))
    async with _client() as c:
        r = await c.get("/api/email-senders/gmail/callback",
                        params={"state": "good-state", "code": "auth-code"},
                        follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "sender=connected" in r.headers["location"]

    profs = await db.list_sender_profiles()
    assert len(profs) == 1
    p = profs[0]
    assert p["provider"] == "gmail" and p["email_address"] == "shahedalfahad20@gmail.com"
    assert p["status"] == "connected"
    # tokens are encrypted, never plaintext
    assert p["oauth_refresh_token_enc"].startswith("enc:v1:")
    assert "RT-1" not in p["oauth_refresh_token_enc"]
    assert "AT-1" not in (p["oauth_access_token_enc"] or "")


async def test_callback_rejects_bad_state(oauth_configured):
    async with _client() as c:
        r = await c.get("/api/email-senders/gmail/callback",
                        params={"state": "never-created", "code": "x"}, follow_redirects=False)
    assert r.status_code in (302, 307) and "reason=bad_state" in r.headers["location"]
    assert await oauth_configured.list_sender_profiles() == []


async def test_callback_rejects_replayed_state(oauth_configured):
    db = oauth_configured
    await db.create_oauth_state("once", "gmail_sender", 600)
    assert await db.consume_oauth_state("once", "gmail_sender") is True
    async with _client() as c:
        r = await c.get("/api/email-senders/gmail/callback",
                        params={"state": "once", "code": "x"}, follow_redirects=False)
    assert "reason=bad_state" in r.headers["location"]


async def test_callback_rejects_expired_state(oauth_configured):
    db = oauth_configured
    await db.create_oauth_state("stale", "gmail_sender", ttl_seconds=-5)   # already expired
    async with _client() as c:
        r = await c.get("/api/email-senders/gmail/callback",
                        params={"state": "stale", "code": "x"}, follow_redirects=False)
    assert "reason=bad_state" in r.headers["location"]


async def test_callback_user_denied(oauth_configured):
    async with _client() as c:
        r = await c.get("/api/email-senders/gmail/callback",
                        params={"error": "access_denied"}, follow_redirects=False)
    assert "reason=denied" in r.headers["location"]


@respx.mock
async def test_callback_first_connect_without_refresh_token_is_rejected(oauth_configured):
    await oauth_configured.create_oauth_state("s2", "gmail_sender", 600)
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={
        "access_token": "AT", "expires_in": 3600,          # NO refresh_token
    }))
    respx.get(USERINFO_URL).mock(return_value=httpx.Response(200, json={
        "email": "x@gmail.com", "email_verified": True,
    }))
    async with _client() as c:
        r = await c.get("/api/email-senders/gmail/callback",
                        params={"state": "s2", "code": "c"}, follow_redirects=False)
    assert "reason=no_refresh_token" in r.headers["location"]
    assert await oauth_configured.list_sender_profiles() == []


@respx.mock
async def test_callback_token_exchange_failure(oauth_configured):
    await oauth_configured.create_oauth_state("s3", "gmail_sender", 600)
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    async with _client() as c:
        r = await c.get("/api/email-senders/gmail/callback",
                        params={"state": "s3", "code": "bad"}, follow_redirects=False)
    assert "reason=exchange_failed" in r.headers["location"]


# ── transport: token refresh + send ──────────────────────────────────────

@respx.mock
async def test_gmail_transport_refreshes_expired_token_and_sends(oauth_configured):
    from backend import database as db
    from backend.secrets_crypto import encrypt
    from backend.email_campaigns import senders

    pid = await db.create_sender_profile({
        "name": "Gmail", "provider": "gmail", "transport": "gmail_api",
        "email_address": "shahedalfahad20@gmail.com", "status": "connected",
        "oauth_client_id": "cid", "oauth_refresh_token_enc": encrypt("RT-live"),
        "oauth_access_token_enc": encrypt("AT-old"),
        "oauth_expires_at": "2000-01-01T00:00:00",       # long expired -> must refresh
    })
    refresh_route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={
        "access_token": "AT-new", "expires_in": 3600,
    }))
    send_route = respx.post(GMAIL_SEND).mock(return_value=httpx.Response(200, json={
        "id": "msg-123", "threadId": "t-1",
    }))

    profile = await db.get_sender_profile(pid)
    transport = await senders.resolve_transport_for_profile(profile)   # refreshes here
    assert refresh_route.called
    res = await transport.send("shahedalfahad20@gmail.com", "Sub", "Body")
    assert res.ok and res.message_id == "msg-123"
    sent = send_route.calls.last.request
    assert "Bearer AT-new" in sent.headers["authorization"]
    # persisted new access token is encrypted
    fresh = await db.get_sender_profile(pid)
    assert "AT-new" not in (fresh["oauth_access_token_enc"] or "")
    assert fresh["oauth_access_token_enc"].startswith("enc:v1:")


@respx.mock
async def test_gmail_transport_blocks_when_refresh_fails(oauth_configured):
    from backend import database as db
    from backend.secrets_crypto import encrypt
    from backend.email_campaigns import senders

    pid = await db.create_sender_profile({
        "name": "Gmail", "provider": "gmail", "transport": "gmail_api",
        "email_address": "x@gmail.com", "status": "connected",
        "oauth_refresh_token_enc": encrypt("RT-revoked"),
        "oauth_expires_at": "2000-01-01T00:00:00",
    })
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))

    profile = await db.get_sender_profile(pid)
    with pytest.raises(senders.SenderUnavailable):
        await senders.resolve_transport_for_profile(profile)
    assert (await db.get_sender_profile(pid))["status"] == "error"


@respx.mock
async def test_gmail_message_is_base64url_and_mime_reused(oauth_configured):
    from backend import database as db
    from backend.secrets_crypto import encrypt
    from backend.email_campaigns import senders

    pid = await db.create_sender_profile({
        "name": "Gmail", "provider": "gmail", "transport": "gmail_api",
        "email_address": "shahedalfahad20@gmail.com", "status": "connected",
        "oauth_refresh_token_enc": encrypt("RT"), "oauth_access_token_enc": encrypt("AT"),
        "oauth_expires_at": "2999-01-01T00:00:00",       # not expired -> no refresh
    })
    route = respx.post(GMAIL_SEND).mock(return_value=httpx.Response(200, json={"id": "m1"}))
    t = await senders.resolve_transport_for_profile(await db.get_sender_profile(pid))
    await t.send("shahedalfahad20@gmail.com", "Hello — ünïcode", "Body line",
                 attachments=[{"filename": "a.txt", "content": b"hi", "mime": "text/plain"}])
    raw = route.calls.last.request.read()
    import json as J
    payload = J.loads(raw)["raw"]
    mime = base64.urlsafe_b64decode(payload).decode("utf-8", "replace")
    assert "multipart/mixed" in mime          # attachment path -> mixed (shared MIME builder)
    assert 'filename="a.txt"' in mime
