"""
test_email_campaigns_router.py — Checkpoint 3C: the Email Campaign REST API.

Exercises the router through the real ASGI app: auth, feature flag, CRUD,
import, attachment, lifecycle, stats, activity, idempotency, the fail-closed
TEST_MODE safety gate at the API level, and the optional n8n boundary
(config fail-closed, callback auth + idempotency, n8n cannot bypass the
native sender / cannot cause a Gmail send).
"""
import json
from dataclasses import dataclass, field
from typing import List

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


# ── helpers ────────────────────────────────────────────────────────────────

@dataclass
class _SendRecorder:
    calls: List[dict] = field(default_factory=list)

    def __call__(self, to_email, subject, body, config, attachments=None, reply_to=None):
        self.calls.append({"to_email": to_email, "subject": subject,
                           "attachments": attachments})
        return True

    @property
    def recipients(self):
        return [c["to_email"] for c in self.calls]


@pytest.fixture
def client_ctx():
    from backend.main import app
    return lambda: AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def enabled(clean_db):
    await clean_db.upsert_setting("email_campaigns_enabled", "true")
    return clean_db


@pytest.fixture
def stub_send(monkeypatch):
    from backend import email_sender
    rec = _SendRecorder()
    monkeypatch.setattr(email_sender, "send_email", rec)

    async def _cfg():
        return {"host": "h", "port": 587, "username": "u@x", "password": "pw",
                "from_name": "T", "from_email": "u@x"}
    monkeypatch.setattr(email_sender, "_smtp_cfg", _cfg)
    return rec


@pytest.fixture
def stub_ai(monkeypatch):
    from backend import ai_brain

    async def _gen_messages(lead, dna):
        return {"email_subject": f"Idea for {lead.get('company') or 'you'}",
                "email_body": "Hi there,\n\nShort note with a real idea. Open to a chat?\n\nBest,\nT"}

    monkeypatch.setattr(ai_brain, "generate_messages", _gen_messages)
    monkeypatch.setattr(ai_brain, "_load_company_dna", lambda: "DNA")
    return monkeypatch


PROSPECT_CSV = (
    "email,company\n"
    "alice@acmedental.com,Acme Dental\n"
    "bob@brightsmile.com,Bright Smile\n"
    "carol@happyteeth.com,Happy Teeth\n"
)


async def _make_campaign(client, name="C"):
    r = await client.post("/api/email-campaigns", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _import(client, cid, csv=PROSPECT_CSV, filename="leads.csv"):
    return await client.post(
        f"/api/email-campaigns/{cid}/leads/import",
        files={"file": (filename, csv.encode("utf-8"), "text/csv")},
    )


# ── auth ───────────────────────────────────────────────────────────────────

async def test_unauthenticated_rejected_when_password_set(enabled, client_ctx):
    from backend import auth
    await auth.set_password("secret-pw-123")
    try:
        async with client_ctx() as c:
            r = await c.post("/api/email-campaigns", json={"name": "X"})
            assert r.status_code == 401
            token = auth.issue_session()
            r2 = await c.post("/api/email-campaigns", json={"name": "X"},
                              headers={"Authorization": f"Bearer {token}"})
            assert r2.status_code == 201
    finally:
        await auth.clear_password()


# ── feature flag ───────────────────────────────────────────────────────────

async def test_disabled_returns_503(clean_db, client_ctx):
    async with client_ctx() as c:
        assert (await c.post("/api/email-campaigns", json={"name": "X"})).status_code == 503
        assert (await c.get("/api/email-campaigns")).status_code == 503
        assert (await c.get("/api/email-campaigns/1/stats")).status_code == 503
        assert (await c.get("/api/email-campaigns/n8n/status")).status_code == 503


async def test_enabled_allows_create(enabled, client_ctx):
    async with client_ctx() as c:
        r = await c.post("/api/email-campaigns", json={"name": "Dental TX"})
        assert r.status_code == 201


# ── CRUD ───────────────────────────────────────────────────────────────────

async def test_create_forces_test_mode_and_hides_secrets(enabled, client_ctx):
    async with client_ctx() as c:
        # even if the client tries to smuggle test_mode=false, the model drops it
        r = await c.post("/api/email-campaigns",
                         json={"name": "C", "test_mode": False, "test_recipient": "x@y.com"})
        assert r.status_code == 201
        body = r.json()
        assert body["test_mode"] is True
        assert body["status"] == "DRAFT"
        for banned in ("attachment_path", "config_json"):
            assert banned not in body
        blob = json.dumps(body).lower()
        for secret in ("password", "api_key", "secret", "token"):
            assert secret not in blob


async def test_get_list_patch(enabled, client_ctx):
    async with client_ctx() as c:
        cid = await _make_campaign(c, "Original")
        r = await c.patch(f"/api/email-campaigns/{cid}", json={"name": "Renamed",
                                                              "ai_enabled": False})
        assert r.status_code == 200 and r.json()["name"] == "Renamed"
        assert r.json()["ai_enabled"] is False

        r = await c.get(f"/api/email-campaigns/{cid}")
        assert r.status_code == 200 and r.json()["name"] == "Renamed"

        r = await c.get("/api/email-campaigns")
        assert any(x["id"] == cid for x in r.json()["campaigns"])


async def test_get_missing_campaign_404(enabled, client_ctx):
    async with client_ctx() as c:
        r = await c.get("/api/email-campaigns/99999")
        assert r.status_code == 404


async def test_patch_rejected_once_running(enabled, stub_ai, stub_send, client_ctx):
    async with client_ctx() as c:
        cid = await _make_campaign(c)
        await _import(c, cid)
        await c.post(f"/api/email-campaigns/{cid}/ready")
        # start completes inline in tests (no queue)
        await c.post(f"/api/email-campaigns/{cid}/start", json={"idempotency_key": "k1"})
        # campaign is COMPLETED now; PATCH must be rejected
        r = await c.patch(f"/api/email-campaigns/{cid}", json={"name": "nope"})
        assert r.status_code == 409


# ── import ─────────────────────────────────────────────────────────────────

async def test_import_csv_counts(enabled, client_ctx):
    async with client_ctx() as c:
        cid = await _make_campaign(c)
        r = await _import(c, cid, "email,company\na@x.com,A\n,B\nnope,C\na@x.com,A\n")
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["valid"] == 1
        assert b["missing_email"] == 1
        assert b["invalid_email"] == 1
        assert b["duplicates"] == 1


async def test_import_rejects_bad_extension(enabled, client_ctx):
    async with client_ctx() as c:
        cid = await _make_campaign(c)
        r = await c.post(f"/api/email-campaigns/{cid}/leads/import",
                         files={"file": ("data.txt", b"email\na@x.com\n", "text/plain")})
        assert r.status_code == 422


async def test_import_rejects_empty_file(enabled, client_ctx):
    async with client_ctx() as c:
        cid = await _make_campaign(c)
        r = await c.post(f"/api/email-campaigns/{cid}/leads/import",
                         files={"file": ("data.csv", b"", "text/csv")})
        assert r.status_code == 422


async def test_leads_listing_hides_raw_json(enabled, client_ctx):
    async with client_ctx() as c:
        cid = await _make_campaign(c)
        await _import(c, cid)
        r = await c.get(f"/api/email-campaigns/{cid}/leads")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 3
        for lead in data["leads"]:
            assert "raw_json" not in lead
            assert "lead_key" in lead and "status" in lead


# ── attachment ─────────────────────────────────────────────────────────────

async def test_attachment_upload_and_meta_only(enabled, client_ctx):
    async with client_ctx() as c:
        cid = await _make_campaign(c)
        blob = b"%PDF-1.4 fake pdf bytes"
        r = await c.post(f"/api/email-campaigns/{cid}/attachment",
                         files={"file": ("deck.pdf", blob, "application/pdf")})
        assert r.status_code == 200
        meta = r.json()
        assert meta == {"filename": "deck.pdf", "size": len(blob),
                        "mime": "application/pdf", "present": True}
        assert "path" not in meta

        got = await c.get(f"/api/email-campaigns/{cid}")
        assert got.json()["attachment"]["present"] is True
        assert "attachment_path" not in got.json()

        d = await c.delete(f"/api/email-campaigns/{cid}/attachment")
        assert d.status_code == 204


async def test_attachment_rejects_bad_type(enabled, client_ctx):
    async with client_ctx() as c:
        cid = await _make_campaign(c)
        r = await c.post(f"/api/email-campaigns/{cid}/attachment",
                         files={"file": ("x.exe", b"MZ\x90\x00", "application/octet-stream")})
        assert r.status_code == 422


async def test_attachment_unsafe_filename_is_sanitised(enabled, client_ctx):
    async with client_ctx() as c:
        cid = await _make_campaign(c)
        r = await c.post(f"/api/email-campaigns/{cid}/attachment",
                         files={"file": ("../../../etc/passwd.pdf", b"%PDF fake", "application/pdf")})
        assert r.status_code == 200
        assert "/" not in r.json()["filename"] and "\\" not in r.json()["filename"]


# ── lifecycle ──────────────────────────────────────────────────────────────

async def test_lifecycle_transitions(enabled, stub_ai, stub_send, client_ctx):
    async with client_ctx() as c:
        cid = await _make_campaign(c)
        await _import(c, cid)

        # DRAFT -> RUNNING directly is rejected
        bad = await c.post(f"/api/email-campaigns/{cid}/start", json={"idempotency_key": "k"})
        assert bad.status_code == 409

        r = await c.post(f"/api/email-campaigns/{cid}/ready")
        assert r.status_code == 200 and r.json()["status"] == "READY"

        # pause from READY is invalid
        assert (await c.post(f"/api/email-campaigns/{cid}/pause")).status_code == 409


async def test_stats_are_db_backed(enabled, stub_ai, stub_send, client_ctx):
    async with client_ctx() as c:
        cid = await _make_campaign(c)
        await _import(c, cid)
        await c.post(f"/api/email-campaigns/{cid}/ready")
        await c.post(f"/api/email-campaigns/{cid}/start", json={"idempotency_key": "k1"})

        s = await c.get(f"/api/email-campaigns/{cid}/stats")
        assert s.status_code == 200
        body = s.json()
        assert body["totals"]["sent"] == 3
        assert body["test_mode"] is True
        assert body["status"] == "COMPLETED"


async def test_activity_endpoint_is_secret_free(enabled, stub_ai, stub_send, client_ctx):
    async with client_ctx() as c:
        cid = await _make_campaign(c)
        await _import(c, cid)
        await c.post(f"/api/email-campaigns/{cid}/ready")
        await c.post(f"/api/email-campaigns/{cid}/start", json={"idempotency_key": "k1"})
        r = await c.get(f"/api/email-campaigns/{cid}/activity")
        assert r.status_code == 200
        events = [a["event"] for a in r.json()["activity"]]
        assert "campaign_created" in events and "email_sent" in events
        blob = json.dumps(r.json()).lower()
        for s in ("password", "api_key", "callback_secret", "bearer "):
            assert s not in blob


# ── idempotency ────────────────────────────────────────────────────────────

async def test_duplicate_start_same_key_no_double_send(enabled, stub_ai, stub_send, client_ctx):
    async with client_ctx() as c:
        cid = await _make_campaign(c)
        await _import(c, cid)
        await c.post(f"/api/email-campaigns/{cid}/ready")
        r1 = await c.post(f"/api/email-campaigns/{cid}/start", json={"idempotency_key": "dup"})
        assert r1.status_code == 200
        n_after_first = len(stub_send.calls)
        assert n_after_first == 3

        # campaign COMPLETED; a repeated start with the same key must not resend
        r2 = await c.post(f"/api/email-campaigns/{cid}/start", json={"idempotency_key": "dup"})
        assert r2.status_code == 409  # cannot start from COMPLETED
        assert len(stub_send.calls) == 3


# ── THE API-LEVEL SAFETY REGRESSION ────────────────────────────────────────

async def test_api_start_in_test_mode_never_sends_to_prospect(enabled, stub_ai, stub_send, client_ctx):
    """POST /start + TEST_MODE + real prospect leads -> the sender is only ever
    called with the test recipient (shahedalfahad20@gmail.com), never a
    prospect address."""
    async with client_ctx() as c:
        cid = await _make_campaign(c, "Real Prospects")
        await _import(c, cid)
        await c.post(f"/api/email-campaigns/{cid}/ready")
        r = await c.post(f"/api/email-campaigns/{cid}/start", json={"idempotency_key": "k1"})
        assert r.status_code == 200

    assert len(stub_send.calls) == 3
    assert set(stub_send.recipients) == {"shahedalfahad20@gmail.com"}
    for p in ("alice@acmedental.com", "bob@brightsmile.com", "carol@happyteeth.com"):
        assert p not in stub_send.recipients


# ── optional n8n boundary ─────────────────────────────────────────────────

async def test_n8n_status_unconfigured_is_safe(enabled, client_ctx):
    async with client_ctx() as c:
        r = await c.get("/api/email-campaigns/n8n/status")
        assert r.status_code == 200
        b = r.json()
        assert b["configured"] is False
        assert b["sends_email"] is False
        assert b["authoritative_sender"] == "email_sender.send_email"


async def test_n8n_health_failure_handled(enabled, clean_db, client_ctx):
    # a configured but dead n8n endpoint must be reported, never raised
    await clean_db.upsert_setting("n8n_base_url", "http://127.0.0.1:59999")
    async with client_ctx() as c:
        r = await c.get("/api/email-campaigns/n8n/status")
    assert r.status_code == 200
    assert r.json()["configured"] is True
    assert r.json()["health"]["ok"] is False


async def test_n8n_callback_requires_secret(enabled, clean_db, client_ctx):
    await clean_db.upsert_setting("n8n_base_url", "http://localhost:5678")
    await clean_db.upsert_setting("n8n_callback_secret", "s3cr3t-value")
    async with client_ctx() as c:
        cid = await _make_campaign(c)
        await _import(c, cid)
        run = await clean_db.create_email_campaign_run(cid, "run-x")
        # no secret header
        r = await c.post(f"/api/email-campaigns/{cid}/n8n-callback",
                         json={"run_id": run["id"], "event": "preparation_complete", "leads": []})
        assert r.status_code == 401
        # wrong secret
        r = await c.post(f"/api/email-campaigns/{cid}/n8n-callback",
                         headers={"X-N8n-Callback-Secret": "wrong"},
                         json={"run_id": run["id"], "event": "preparation_complete", "leads": []})
        assert r.status_code == 401


async def test_n8n_callback_prepares_but_cannot_send(enabled, clean_db, stub_send, client_ctx):
    await clean_db.upsert_setting("n8n_base_url", "http://localhost:5678")
    await clean_db.upsert_setting("n8n_callback_secret", "s3cr3t-value")
    async with client_ctx() as c:
        cid = await _make_campaign(c)
        await _import(c, cid)
        run = await clean_db.create_email_campaign_run(cid, "run-x")

        payload = {
            "run_id": run["id"], "event": "preparation_complete",
            "leads": [
                {"lead_key": f"{cid}::alice@acmedental.com",
                 "ai_subject": "Prepared by n8n", "ai_body": "A fully prepared body from n8n."},
            ],
        }
        r = await c.post(f"/api/email-campaigns/{cid}/n8n-callback",
                         headers={"X-N8n-Callback-Secret": "s3cr3t-value"}, json=payload)
        assert r.status_code == 200
        assert r.json()["applied"] == 1

        lead = await clean_db.get_email_campaign_lead(cid, f"{cid}::alice@acmedental.com")
        assert lead["status"] == "GENERATED"
        assert lead["ai_subject"] == "Prepared by n8n"

    # the callback did NOT send anything — no Gmail path exists
    assert stub_send.calls == []
    # re-applying the same callback is idempotent (lead already GENERATED)
    async with client_ctx() as c:
        r2 = await c.post(f"/api/email-campaigns/{cid}/n8n-callback",
                          headers={"X-N8n-Callback-Secret": "s3cr3t-value"}, json=payload)
        assert r2.status_code == 200


async def test_n8n_callback_run_must_match_campaign(enabled, clean_db, client_ctx):
    await clean_db.upsert_setting("n8n_base_url", "http://localhost:5678")
    await clean_db.upsert_setting("n8n_callback_secret", "s3cr3t-value")
    async with client_ctx() as c:
        cid_a = await _make_campaign(c, "A")
        cid_b = await _make_campaign(c, "B")
        run_b = await clean_db.create_email_campaign_run(cid_b, "run-b")
        r = await c.post(f"/api/email-campaigns/{cid_a}/n8n-callback",
                         headers={"X-N8n-Callback-Secret": "s3cr3t-value"},
                         json={"run_id": run_b["id"], "event": "preparation_complete", "leads": []})
        assert r.status_code in (409, 422)


async def test_no_n8n_gmail_send_path_exists():
    """Guard: n8n_client is inert (no SMTP / no sender), and the only code that
    references `send_email` is the sanctioned transport layer
    (service.py + senders.py)."""
    import ast
    import pathlib

    pkg = pathlib.Path(__file__).resolve().parent.parent / "backend" / "email_campaigns"

    def _imports(src):
        got = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                got.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                got.add(node.module or "")
        return got

    nc_imports = _imports((pkg / "n8n_client.py").read_text(encoding="utf-8"))
    assert "smtplib" not in nc_imports
    assert not any("email_sender" in m for m in nc_imports)
    assert not any("gmail" in m.lower() for m in nc_imports)

    _SENDER_LAYER = {"service.py", "senders.py"}
    ref_files = set()
    for pyf in pkg.glob("*.py"):
        for node in ast.walk(ast.parse(pyf.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr == "send_email":
                ref_files.add(pyf.name)
            if isinstance(node, ast.Name) and node.id == "send_email":
                ref_files.add(pyf.name)
    assert ref_files <= _SENDER_LAYER, f"send_email referenced outside the sender layer: {ref_files}"
