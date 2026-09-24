"""
test_sender_profiles.py — Checkpoint 4A: sender-profile persistence + API.

No real transport. Secrets are asserted to be encrypted at rest and absent
from every API response.
"""
import json

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio

MASK = "••••set••••"


@pytest.fixture
async def enabled(clean_db):
    await clean_db.upsert_setting("email_campaigns_enabled", "true")
    return clean_db


def _client():
    from backend.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


SMTP_BODY = {
    "name": "PopupGenix SMTP",
    "email_address": "hello@popupgenix.com",
    "smtp_host": "mail.popupgenix.com", "smtp_port": 465, "smtp_security": "ssl",
    "smtp_username": "hello@popupgenix.com", "smtp_password": "REAL-SMTP-PASSWORD",
}


# ── DB layer ──────────────────────────────────────────────────────────────

async def test_schema_and_crud(enabled):
    db = enabled
    from backend.secrets_crypto import encrypt, decrypt
    pid = await db.create_sender_profile({
        "name": "P", "provider": "smtp", "transport": "smtp",
        "email_address": "a@b.com", "status": "connected",
        "smtp_host": "h", "smtp_port": 465, "smtp_username": "a@b.com",
        "smtp_password_enc": encrypt("s3cret"),
    })
    p = await db.get_sender_profile(pid)
    assert p["provider"] == "smtp"
    assert p["smtp_password_enc"].startswith("enc:v1:") and "s3cret" not in p["smtp_password_enc"]
    assert decrypt(p["smtp_password_enc"]) == "s3cret"

    await db.update_sender_profile(pid, {"name": "P2", "status": "error", "last_error": "x"})
    assert (await db.get_sender_profile(pid))["name"] == "P2"


async def test_single_default_enforced(enabled):
    db = enabled
    a = await db.create_sender_profile({"name": "A", "provider": "smtp", "transport": "smtp",
                                        "email_address": "a@x.com"})
    b = await db.create_sender_profile({"name": "B", "provider": "smtp", "transport": "smtp",
                                        "email_address": "b@x.com"})
    await db.set_default_sender_profile(a)
    await db.set_default_sender_profile(b)
    defaults = [p["id"] for p in await db.list_sender_profiles() if p["is_default"]]
    assert defaults == [b]


async def test_delete_nulls_campaign_reference(enabled):
    db = enabled
    pid = await db.create_sender_profile({"name": "P", "provider": "smtp", "transport": "smtp",
                                          "email_address": "a@x.com"})
    cid = await db.create_email_campaign({"name": "C", "sender_profile_id": pid})
    assert (await db.get_email_campaign(cid))["sender_profile_id"] == pid
    await db.delete_sender_profile(pid)
    assert (await db.get_email_campaign(cid))["sender_profile_id"] is None   # SET NULL (code-enforced)


# ── API ───────────────────────────────────────────────────────────────────

async def test_create_and_list_never_expose_secret(enabled):
    async with _client() as c:
        r = await c.post("/api/email-senders", json=SMTP_BODY)
        assert r.status_code == 201, r.text
        body = r.json()
        blob = json.dumps(body)
        assert "REAL-SMTP-PASSWORD" not in blob
        assert "smtp_password" not in body and "smtp_password_enc" not in body
        assert body["status"] == "connected" and body["has_credentials"] is True

        lst = (await c.get("/api/email-senders")).json()["senders"]
        assert "REAL-SMTP-PASSWORD" not in json.dumps(lst)
        assert all("smtp_password_enc" not in s for s in lst)


async def test_feature_flag_and_auth(clean_db):
    async with _client() as c:
        assert (await c.get("/api/email-senders")).status_code == 503     # flag off
    await clean_db.upsert_setting("email_campaigns_enabled", "true")

    from backend import auth
    await auth.set_password("pw-integration-4")
    try:
        async with _client() as c:
            assert (await c.get("/api/email-senders")).status_code == 401
            tok = auth.issue_session()
            r = await c.get("/api/email-senders", headers={"Authorization": f"Bearer {tok}"})
            assert r.status_code == 200
    finally:
        await auth.clear_password()


async def test_gmail_smtp_identity_is_pinned_to_username(enabled):
    async with _client() as c:
        bad = dict(SMTP_BODY, smtp_host="smtp.gmail.com", smtp_username="me@gmail.com",
                   email_address="someone-else@gmail.com")
        assert (await c.post("/api/email-senders", json=bad)).status_code == 422
        ok = dict(SMTP_BODY, smtp_host="smtp.gmail.com", smtp_username="me@gmail.com",
                  email_address="me@gmail.com")
        assert (await c.post("/api/email-senders", json=ok)).status_code == 201


async def test_patch_mask_does_not_overwrite_password(enabled):
    db = enabled
    async with _client() as c:
        pid = (await c.post("/api/email-senders", json=SMTP_BODY)).json()["id"]
        before = (await db.get_sender_profile(pid))["smtp_password_enc"]
        # save with the redaction placeholder -> password must be untouched
        await c.patch(f"/api/email-senders/{pid}", json={"name": "Renamed", "smtp_password": MASK})
        after = (await db.get_sender_profile(pid))["smtp_password_enc"]
        assert after == before
        assert (await db.get_sender_profile(pid))["name"] == "Renamed"
        # a real new password IS saved
        await c.patch(f"/api/email-senders/{pid}", json={"smtp_password": "NEW-REAL-PW"})
        from backend.secrets_crypto import decrypt
        assert decrypt((await db.get_sender_profile(pid))["smtp_password_enc"]) == "NEW-REAL-PW"


async def test_patch_empty_password_does_not_wipe(enabled):
    db = enabled
    async with _client() as c:
        pid = (await c.post("/api/email-senders", json=SMTP_BODY)).json()["id"]
        before = (await db.get_sender_profile(pid))["smtp_password_enc"]
        await c.patch(f"/api/email-senders/{pid}", json={"smtp_password": ""})
        assert (await db.get_sender_profile(pid))["smtp_password_enc"] == before


async def test_disconnect_clears_credentials(enabled):
    db = enabled
    async with _client() as c:
        pid = (await c.post("/api/email-senders", json=SMTP_BODY)).json()["id"]
        r = await c.post(f"/api/email-senders/{pid}/disconnect")
        assert r.status_code == 200 and r.json()["status"] == "disconnected"
    p = await db.get_sender_profile(pid)
    assert p["smtp_password_enc"] is None and p["oauth_refresh_token_enc"] is None


async def test_test_endpoint_reports_failure_safely(enabled, monkeypatch):
    """A failing connection must report failure — never a raw error with a secret."""
    from backend.email_campaigns import senders

    async def _fail_verify(self):
        return senders.SendResult(ok=False, error="535 auth failed for hello@popupgenix.com")
    monkeypatch.setattr(senders.SmtpTransport, "verify", _fail_verify)

    async with _client() as c:
        pid = (await c.post("/api/email-senders", json=SMTP_BODY)).json()["id"]
        r = await c.post(f"/api/email-senders/{pid}/test")
        assert r.status_code == 200 and r.json()["ok"] is False
    assert (await enabled.get_sender_profile(pid))["status"] == "error"


async def test_single_operator_no_ownership_column(enabled):
    """AutoLead is single-operator (backend/auth.py). email_sender_profiles has
    no user/tenant/owner column — the boundary is the session token + flag."""
    async with enabled.get_db() as conn:
        cols = {r["name"] for r in await conn.fetch("PRAGMA table_info(email_sender_profiles)")}
    for owner in ("user_id", "tenant_id", "owner_id", "workspace_id", "account_id"):
        assert owner not in cols


async def test_no_credential_literals_in_sender_source():
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parent.parent / "backend"
    sus = re.compile(
        r"(GOCSPX-[A-Za-z0-9_\-]{6,}|AIza[0-9A-Za-z_\-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY|"
        r"(password|client_secret|refresh_token)\s*=\s*[\"'][^\"'\s]{8,}[\"'])",
        re.I,
    )
    for f in (root / "email_campaigns" / "senders.py", root / "email_campaigns" / "gmail_oauth.py",
              root / "routers" / "email_senders.py"):
        assert not sus.search(f.read_text(encoding="utf-8")), f"suspicious literal in {f.name}"


async def test_default_endpoint(enabled):
    async with _client() as c:
        a = (await c.post("/api/email-senders", json=dict(SMTP_BODY, name="A",
                                                         email_address="a@popupgenix.com",
                                                         smtp_username="a@popupgenix.com"))).json()["id"]
        b = (await c.post("/api/email-senders", json=dict(SMTP_BODY, name="B",
                                                         email_address="b@popupgenix.com",
                                                         smtp_username="b@popupgenix.com"))).json()["id"]
        await c.post(f"/api/email-senders/{b}/default")
        senders_ = (await c.get("/api/email-senders")).json()["senders"]
    assert [s["id"] for s in senders_ if s["is_default"]] == [b]


async def test_new_password_clears_an_error_so_it_can_be_retested(enabled):
    db = enabled
    async with _client() as c:
        pid = (await c.post("/api/email-senders", json=SMTP_BODY)).json()["id"]
        await db.update_sender_profile(pid, {"status": "error", "last_error": "bad password"})
        await c.patch(f"/api/email-senders/{pid}", json={"name": "Renamed"})
        assert (await db.get_sender_profile(pid))["status"] == "error"      # no new password, no change
        await c.patch(f"/api/email-senders/{pid}", json={"smtp_password": "NEW-APP-PW"})
        p = await db.get_sender_profile(pid)
    assert p["status"] == "connected" and p["last_error"] is None
