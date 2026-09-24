"""Client portal: emailed-code sign-in, client isolation, delivery, and strict
separation from the owner's app."""
import pytest
from httpx import ASGITransport, AsyncClient

from backend import auth, database as db, email_sender
from backend.portal import service
from backend.routers import portal_admin

pytestmark = pytest.mark.asyncio


@pytest.fixture
def outbox(monkeypatch):
    """Capture sign-in emails instead of sending them."""
    sent = []

    async def ready():
        return True
    monkeypatch.setattr(email_sender, "system_email_ready", ready)

    async def fake_send(to, subject, body, reply_to=None):
        sent.append({"to": to, "subject": subject, "body": body, "reply_to": reply_to})
        return True
    monkeypatch.setattr(email_sender, "send_system_email", fake_send)
    return sent


def _code(mail):
    return mail["subject"].rsplit(" ", 1)[-1]


async def _client():
    from backend.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _sign_in(c, outbox, email="client@gmail.com"):
    r = await c.post("/api/portal/auth/request-code", json={"email": email})
    assert r.status_code == 200, r.text
    r = await c.post("/api/portal/auth/verify", json={"email": email, "code": _code(outbox[-1])})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["client"]


async def _age_codes(email):
    # Make the last code look older than the resend window.
    await db.portal_execute("UPDATE portal_login_codes SET created_at = datetime(created_at, '-2 minutes') WHERE email = ?", email)


# ── Sign-in ─────────────────────────────────────────────────────────────

async def test_code_sign_in_creates_account_instantly(clean_db, outbox):
    async with await _client() as c:
        headers, client = await _sign_in(c, outbox, "New.Client@Gmail.com")
        me = (await c.get("/api/portal/me", headers=headers)).json()
    assert outbox[0]["to"] == "new.client@gmail.com" and _code(outbox[0]).isdigit() and len(_code(outbox[0])) == 6
    assert client["email"] == "new.client@gmail.com" and client["needs_profile"] is True
    assert me["id"] == client["id"]
    row = await db.portal_fetchrow("SELECT code_hash FROM portal_login_codes")
    assert _code(outbox[0]) not in row["code_hash"]          # only a hash is stored


async def test_code_is_single_use_and_wrong_codes_lock_out(clean_db, outbox):
    async with await _client() as c:
        await c.post("/api/portal/auth/request-code", json={"email": "a@gmail.com"})
        code = _code(outbox[-1])
        wrong = "000000" if code != "000000" else "111111"
        for i in range(4):
            r = await c.post("/api/portal/auth/verify", json={"email": "a@gmail.com", "code": wrong})
            assert r.status_code == 400 and "left" in r.json()["detail"]
        r = await c.post("/api/portal/auth/verify", json={"email": "a@gmail.com", "code": wrong})
        assert r.status_code == 429
        r = await c.post("/api/portal/auth/verify", json={"email": "a@gmail.com", "code": code})
        assert r.status_code == 429                              # even the right code is locked now
        await _age_codes("a@gmail.com")
        await c.post("/api/portal/auth/request-code", json={"email": "a@gmail.com"})
        good = _code(outbox[-1])
        assert (await c.post("/api/portal/auth/verify", json={"email": "a@gmail.com", "code": good})).status_code == 200
        assert (await c.post("/api/portal/auth/verify", json={"email": "a@gmail.com", "code": good})).status_code == 400


async def test_expired_code_and_bad_input(clean_db, outbox):
    async with await _client() as c:
        assert (await c.post("/api/portal/auth/request-code", json={"email": "not-an-email"})).status_code == 400
        await c.post("/api/portal/auth/request-code", json={"email": "b@gmail.com"})
        await db.portal_execute("UPDATE portal_login_codes SET expires_at = datetime('now', '-1 minute')")
        r = await c.post("/api/portal/auth/verify", json={"email": "b@gmail.com", "code": _code(outbox[-1])})
    assert r.status_code == 400 and "expired" in r.json()["detail"]


async def test_resend_and_daily_limits(clean_db, outbox, monkeypatch):
    async with await _client() as c:
        assert (await c.post("/api/portal/auth/request-code", json={"email": "c@gmail.com"})).status_code == 200
        assert (await c.post("/api/portal/auth/request-code", json={"email": "c@gmail.com"})).status_code == 429
        for _ in range(4):
            await _age_codes("c@gmail.com")
            await c.post("/api/portal/auth/request-code", json={"email": "c@gmail.com"})
        await _age_codes("c@gmail.com")
        assert (await c.post("/api/portal/auth/request-code", json={"email": "c@gmail.com"})).status_code == 429
        monkeypatch.setattr(service, "CODES_PER_DAY", 5)
        r = await c.post("/api/portal/auth/request-code", json={"email": "someone-else@gmail.com"})
    assert r.status_code == 429 and len(outbox) == 5


async def test_email_failure_is_reported_and_does_not_block_retry(clean_db, monkeypatch):
    async def ready():
        return True

    async def fail(*a, **k):
        return False
    monkeypatch.setattr(email_sender, "system_email_ready", ready)
    monkeypatch.setattr(email_sender, "send_system_email", fail)
    async with await _client() as c:
        for _ in range(3):   # a failed send must not trigger "wait a minute"
            r = await c.post("/api/portal/auth/request-code", json={"email": "d@gmail.com"})
            assert r.status_code == 503, r.text
    assert await db.portal_fetchrow("SELECT count(*) AS n FROM portal_login_codes") == {"n": 0}


async def test_sign_in_closed_until_email_is_set_up(clean_db, monkeypatch):
    async def not_ready():
        return False
    monkeypatch.setattr(email_sender, "system_email_ready", not_ready)
    async with await _client() as c:
        assert (await c.get("/api/portal/status")).json()["sign_in_ready"] is False
        r = await c.post("/api/portal/auth/request-code", json={"email": "e@gmail.com"})
    assert r.status_code == 503 and "setting it up" in r.json()["detail"]


# ── Separation from the owner's app ─────────────────────────────────────

async def test_portal_and_owner_sessions_never_mix(clean_db, outbox):
    await auth.set_password("owner-password-123")
    owner_token = auth.issue_session()
    async with await _client() as c:
        portal_headers, _ = await _sign_in(c, outbox)
        # a client token opens nothing of the owner's
        for path in ("/api/leads", "/api/settings", "/api/clients", "/api/lead-runs", "/api/clients/release"):
            assert (await c.get(path, headers=portal_headers)).status_code == 401, path
        # the owner's token is not a portal session
        assert (await c.get("/api/portal/me", headers={"Authorization": f"Bearer {owner_token}"})).status_code == 401
        assert (await c.get("/api/portal/me")).status_code == 401
        assert (await c.get("/api/clients", headers={"Authorization": f"Bearer {owner_token}"})).status_code == 200



async def test_blocking_a_client_signs_them_out(clean_db, outbox):
    async with await _client() as c:
        h, client = await _sign_in(c, outbox)
        assert (await c.patch(f"/api/clients/{client['id']}", json={"status": "BLOCKED"})).status_code == 200
        assert (await c.get("/api/portal/me", headers=h)).status_code == 401
        await _age_codes("client@gmail.com")
        r = await c.post("/api/portal/auth/request-code", json={"email": "client@gmail.com"})
    assert r.status_code == 403 and len(outbox) == 1        # no code is even sent


async def test_profile_update(clean_db, outbox):
    async with await _client() as c:
        h, _ = await _sign_in(c, outbox)
        assert (await c.put("/api/portal/me", headers=h, json={"name": " "})).status_code == 400
        me = (await c.put("/api/portal/me", headers=h, json={"name": "Sara", "company": "Bright Dental"})).json()
    assert me["name"] == "Sara" and me["needs_profile"] is False


async def test_setup_reports_whether_sign_in_email_works(clean_db, monkeypatch):
    async def ready():
        return False
    monkeypatch.setattr(email_sender, "system_email_ready", ready)
    async with await _client() as c:
        assert (await c.get("/api/clients/setup")).json()["email_ready"] is False


# ── Portal settings (owner) ─────────────────────────────────────────────

async def test_invite_only_and_closed_modes(clean_db, outbox):
    async with await _client() as c:
        await c.put("/api/clients/portal-settings", json={"signup_mode": "invite", "name": "Acme Leads"})
        r = await c.post("/api/portal/auth/request-code", json={"email": "stranger@gmail.com"})
        assert r.status_code == 403 and "Acme Leads" in r.json()["detail"]
        added = (await c.post("/api/clients", json={"email": "Friend@Gmail.com", "name": "Omar", "invite": False})).json()
        assert added["client"]["email"] == "friend@gmail.com" and added["invited"] is False
        assert (await c.post("/api/clients", json={"email": "friend@gmail.com"})).status_code == 409
        headers, client = await _sign_in(c, outbox, "friend@gmail.com")
        assert client["name"] == "Omar" and "Acme Leads" in outbox[-1]["subject"]

        await c.put("/api/clients/portal-settings", json={"signup_mode": "closed"})
        assert (await c.get("/api/portal/status")).json()["sign_in_ready"] is False
        await _age_codes("friend@gmail.com")
        assert (await c.post("/api/portal/auth/request-code", json={"email": "friend@gmail.com"})).status_code == 403
        assert (await c.get("/api/portal/me", headers=headers)).status_code == 200   # signed-in clients keep access


async def test_add_client_sends_invitation_with_link(clean_db, outbox, monkeypatch, tmp_path):
    from backend.portal import config
    link = tmp_path / "portal-link.txt"
    monkeypatch.setattr(config, "LINK_FILE", link)
    async with await _client() as c:
        r = (await c.post("/api/clients", json={"email": "a@gmail.com"})).json()
        assert r["invited"] is False and "No client link" in r["invite_problem"]
        link.write_text("https://clients-example.trycloudflare.com\n")
        await c.put("/api/clients/portal-settings", json={"contact_email": "Owner@Example.com"})
        r = (await c.post("/api/clients", json={"email": "b@gmail.com", "name": "Sara Khan"})).json()
        view = (await c.get("/api/clients/portal-settings")).json()
    assert r["invited"] is True
    mail = outbox[-1]
    assert mail["to"] == "b@gmail.com" and "https://clients-example.trycloudflare.com" in mail["body"]
    assert mail["body"].startswith("Hi Sara,") and mail["reply_to"] == "owner@example.com"
    assert view["client_link"] == "https://clients-example.trycloudflare.com"


async def test_settings_validation(clean_db, outbox):
    async with await _client() as c:
        for bad in ({"signup_mode": "everyone"}, {"max_workspaces": -1}, {"max_workspaces": 500},
                    {"sender": "999"}, {"name": "  "}, {"contact_email": "nope"}):
            assert (await c.put("/api/clients/portal-settings", json=bad)).status_code == 422, bad
        await c.put("/api/clients/portal-settings", json={"max_workspaces": 3, "welcome": "Hello!"})
        status = (await c.get("/api/portal/status")).json()
        assert status["welcome"] == "Hello!"
        assert (await c.get("/api/clients/portal-settings")).json()["settings"]["max_workspaces"] == 3


async def test_sign_in_email_from_a_sender_profile(clean_db, monkeypatch):
    """With a sender profile chosen, codes go out through that account's transport."""
    from backend.email_campaigns import senders
    from backend.secrets_crypto import encrypt

    async def smtp_not_ready():
        return False
    monkeypatch.setattr(email_sender, "system_email_ready", smtp_not_ready)
    sent = []

    class FakeTransport:
        async def send(self, to, subject, body, **kw):
            sent.append((to, subject, kw.get("reply_to")))
            return senders.SendResult(ok=True)

    async def fake_resolve(profile):
        return FakeTransport()
    monkeypatch.setattr(senders, "resolve_transport_for_profile", fake_resolve)
    async with await _client() as c:
        assert (await c.get("/api/portal/status")).json()["sign_in_ready"] is False   # nothing set up
        pid = await db.create_sender_profile({
            "name": "My Gmail", "provider": "smtp", "transport": "smtp", "email_address": "me@gmail.com",
            "status": "connected", "smtp_host": "smtp.gmail.com", "smtp_port": 465, "smtp_security": "ssl",
            "smtp_username": "me@gmail.com", "smtp_password_enc": encrypt("app-password")})
        # "auto" picks the connected sender when the main email isn't set up
        view = (await c.get("/api/clients/portal-settings")).json()
        assert view["sender"]["ready"] and view["sender"]["profile_id"] == pid
        assert "smtp_password_enc" not in str(view)
        assert (await c.post("/api/portal/auth/request-code", json={"email": "x@gmail.com"})).status_code == 200
        assert (await c.post("/api/clients/portal-settings/test-email", json={"to": "me@gmail.com"})).status_code == 200
        await c.put("/api/clients/portal-settings", json={"sender": "smtp"})
        assert (await c.get("/api/portal/status")).json()["sign_in_ready"] is False
    assert [t for t, _, _ in sent] == ["x@gmail.com", "me@gmail.com"]
