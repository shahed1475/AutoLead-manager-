"""
test_settings_secret_guard.py — the Settings API must never persist the GET
redaction mask ('••••set••••') back over a real secret.

Repro of the data-corruption bug: GET /api/settings redacts secret values;
the Settings form loads the mask into its state; a later save (bulk or single)
sends the mask straight back. Without the guard, upsert_setting stores the
mask as the real value and the credential is lost.
"""
import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio

MASK = "••••set••••"


def _client():
    from backend.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_single_update_ignores_mask_for_secret_key(clean_db):
    db = clean_db
    await db.upsert_setting("smtp_password", "real-app-password-123")
    async with _client() as c:
        r = await c.put("/api/settings", json={"key": "smtp_password", "value": MASK})
        assert r.status_code == 200
    assert await db.get_setting("smtp_password") == "real-app-password-123"   # unchanged


async def test_bulk_update_ignores_mask_but_saves_real_change(clean_db):
    db = clean_db
    await db.upsert_setting("smtp_password", "real-app-password-123")
    async with _client() as c:
        r = await c.put("/api/settings/bulk", json={
            "smtp_host": "mail.example-host.com",
            "smtp_password": MASK,                 # untouched field — must be ignored
        })
        assert r.status_code == 200
        body = r.json()
        assert body["skipped_unchanged_secrets"] == 1
    assert await db.get_setting("smtp_password") == "real-app-password-123"
    assert await db.get_setting("smtp_host") == "mail.example-host.com"


async def test_bulk_update_persists_a_genuinely_new_secret(clean_db):
    db = clean_db
    await db.upsert_setting("smtp_password", "old-password")
    async with _client() as c:
        r = await c.put("/api/settings/bulk", json={"smtp_password": "brand-new-password"})
        assert r.status_code == 200
        assert r.json()["skipped_unchanged_secrets"] == 0
    assert await db.get_setting("smtp_password") == "brand-new-password"


async def test_empty_string_does_not_wipe_a_secret(clean_db):
    db = clean_db
    await db.upsert_setting("imap_password", "keep-me")
    async with _client() as c:
        await c.put("/api/settings", json={"key": "imap_password", "value": ""})
    assert await db.get_setting("imap_password") == "keep-me"


async def test_non_secret_key_still_accepts_empty_and_any_value(clean_db):
    db = clean_db
    async with _client() as c:
        await c.put("/api/settings", json={"key": "smtp_from_name", "value": ""})
    # non-secret: stored as given (empty is a valid clear here)
    assert await db.get_setting("smtp_from_name") == ""
