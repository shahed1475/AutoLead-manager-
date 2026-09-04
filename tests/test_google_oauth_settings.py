"""
test_google_oauth_settings.py — Checkpoint 4A: the Google OAuth *application*
credentials (client id / client secret / redirect uri) must be configurable
from the dashboard through the EXISTING settings API, with the client secret
encrypted at rest, redacted on read, and never leaked.

No real Google network calls. The connect endpoint only builds an auth URL.
"""
import logging
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

_REPO = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.asyncio

MASK = "••••set••••"
REAL_SECRET = "GOCSPX-unit-test-only-secret"
CLIENT_ID = "8427xxxx.apps.googleusercontent.com"
REDIRECT = "http://localhost:8001/api/email-senders/gmail/callback"


def _client():
    from backend.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _enable(db):
    await db.upsert_setting("email_campaigns_enabled", "true")


async def _save_oauth_via_api(c, *, client_id=CLIENT_ID, client_secret=REAL_SECRET, redirect_uri=REDIRECT):
    return await c.put("/api/settings/bulk", json={
        "google_oauth_client_id": client_id,
        "google_oauth_client_secret": client_secret,
        "google_oauth_redirect_uri": redirect_uri,
    })


# ── persistence + encryption ────────────────────────────────────────────────

async def test_client_id_and_redirect_persist_plaintext(clean_db):
    db = clean_db
    async with _client() as c:
        assert (await _save_oauth_via_api(c)).status_code == 200
    assert await db.get_setting("google_oauth_client_id") == CLIENT_ID
    assert await db.get_setting("google_oauth_redirect_uri") == REDIRECT


async def test_client_secret_is_encrypted_at_rest(clean_db):
    db = clean_db
    async with _client() as c:
        await _save_oauth_via_api(c)
    # transparent decrypt for backend callers
    assert await db.get_setting("google_oauth_client_secret") == REAL_SECRET
    # raw stored bytes are ciphertext
    async with db.get_db() as conn:
        raw = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key='google_oauth_client_secret'"
        )
    assert raw.startswith("enc:v1:")
    assert REAL_SECRET not in raw


async def test_get_settings_redacts_client_secret(clean_db):
    async with _client() as c:
        await _save_oauth_via_api(c)
        body = (await c.get("/api/settings")).json()
    assert body["google_oauth_client_secret"] == MASK
    assert body["google_oauth_client_id"] == CLIENT_ID          # id is not a secret
    assert body["google_oauth_redirect_uri"] == REDIRECT
    assert REAL_SECRET not in str(body)


# ── mask / empty guards (must not regress the fixed corruption bug) ──────────

async def test_mask_does_not_overwrite_stored_client_secret(clean_db):
    db = clean_db
    await db.upsert_setting("google_oauth_client_secret", REAL_SECRET)
    async with _client() as c:
        r = await c.put("/api/settings/bulk", json={
            "google_oauth_client_id": CLIENT_ID,
            "google_oauth_client_secret": MASK,          # untouched field echoed back
            "google_oauth_redirect_uri": REDIRECT,
        })
        assert r.json()["skipped_unchanged_secrets"] == 1
    assert await db.get_setting("google_oauth_client_secret") == REAL_SECRET


async def test_empty_string_does_not_wipe_client_secret(clean_db):
    db = clean_db
    await db.upsert_setting("google_oauth_client_secret", REAL_SECRET)
    async with _client() as c:
        await c.put("/api/settings", json={"key": "google_oauth_client_secret", "value": ""})
    assert await db.get_setting("google_oauth_client_secret") == REAL_SECRET


async def test_a_genuinely_new_secret_is_saved(clean_db):
    db = clean_db
    await db.upsert_setting("google_oauth_client_secret", "old-secret")
    async with _client() as c:
        r = await c.put("/api/settings/bulk", json={"google_oauth_client_secret": "GOCSPX-rotated"})
        assert r.json()["skipped_unchanged_secrets"] == 0
    assert await db.get_setting("google_oauth_client_secret") == "GOCSPX-rotated"


# ── config-status: the UI's status source, must be secret-free ──────────────

async def test_config_status_reports_configured_without_leaking_secret(clean_db):
    db = clean_db
    await _enable(db)
    async with _client() as c:
        await _save_oauth_via_api(c)
        r = await c.get("/api/email-senders/gmail/config-status")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["client_secret_set"] is True
    assert body["client_id"] == CLIENT_ID
    assert body["redirect_uri"] == REDIRECT
    assert body["default_redirect_uri"] == REDIRECT
    # the secret value itself is never in the payload
    assert REAL_SECRET not in r.text
    assert "client_secret" not in {k for k in body if k == "client_secret"}


async def test_config_status_not_configured_when_secret_missing(clean_db):
    db = clean_db
    await _enable(db)
    await db.upsert_setting("google_oauth_client_id", CLIENT_ID)
    async with _client() as c:
        body = (await c.get("/api/email-senders/gmail/config-status")).json()
    assert body["configured"] is False
    assert body["client_secret_set"] is False
    assert body["client_id"] == CLIENT_ID
    # a sensible default is still offered for the UI to display
    assert body["default_redirect_uri"] == REDIRECT


async def test_config_status_falls_back_to_default_redirect(clean_db):
    db = clean_db
    await _enable(db)
    async with _client() as c:
        await _save_oauth_via_api(c, redirect_uri="")
        body = (await c.get("/api/email-senders/gmail/config-status")).json()
    assert body["redirect_uri"] == REDIRECT


# ── connect endpoint gating (backend fails closed regardless of UI) ─────────

async def test_connect_unavailable_until_configured_via_ui(clean_db):
    db = clean_db
    await _enable(db)
    async with _client() as c:
        assert (await c.get("/api/email-senders/gmail/connect")).status_code == 503
        await _save_oauth_via_api(c)
        r = await c.get("/api/email-senders/gmail/connect")
    assert r.status_code == 200
    assert r.json()["auth_url"].startswith("https://accounts.google.com/")
    assert CLIENT_ID in r.json()["auth_url"]


# ── the secret must never reach the logs ──────────────────────────────────

async def test_client_secret_never_appears_in_logs(clean_db, caplog):
    db = clean_db
    await _enable(db)
    with caplog.at_level(logging.DEBUG):
        async with _client() as c:
            await _save_oauth_via_api(c)
            await c.get("/api/settings")
            await c.get("/api/email-senders/gmail/config-status")
            await c.get("/api/email-senders/gmail/connect")
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert REAL_SECRET not in joined


# ── the frontend must not stash the secret client-side ────────────────────

async def test_frontend_never_persists_client_secret_client_side():
    src = (_REPO / "frontend" / "src" / "components" / "settings" / "EmailSendersSection.jsx").read_text(
        encoding="utf-8"
    )
    lowered = src.lower()
    # the OAuth client secret must never be put in browser storage or a URL
    for bad in ("localstorage", "sessionstorage"):
        assert bad not in lowered, f"{bad} must not appear in the Email Senders component"
    # no hard-coded Google client secret literal
    assert "gocspx-" not in lowered
    # the secret is written through the shared settings API, not a bespoke path
    assert "google_oauth_client_secret" in src
    assert "settingsApi.bulkUpdate" in src
