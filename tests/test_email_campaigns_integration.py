"""
test_email_campaigns_integration.py — Checkpoint 3E: whole-stack integration.

Verifies the complete Email Campaign path end-to-end:

    Dashboard API  →  EmailCampaignService  →  DB  →  AI prep  →  SAFETY GATE
                   →  email_sender.send_email()   (mocked — no real transport)

n8n is mocked. email_sender.send_email is mocked. NO real email is sent, NO
real prospect is contacted, TEST_MODE stays on, the feature flag default stays
off (each test opts in per-DB via app_settings).

The single most important assertion, exercised many ways here:
    in TEST_MODE, email_sender.send_email is only ever called with
    shahedalfahad20@gmail.com — never a prospect address — and the fail-closed
    safety gate runs immediately before every send.
"""
import ast
import io
import json
import pathlib
from dataclasses import dataclass, field
from typing import List

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio

TEST_RECIPIENT = "shahedalfahad20@gmail.com"
PKG = pathlib.Path(__file__).resolve().parent.parent / "backend" / "email_campaigns"


# ── recorders / stubs ──────────────────────────────────────────────────────

@dataclass
class _Sender:
    """Mock for email_sender.send_email. Records calls, sends nothing.
    Fails the test loudly if ever handed a prospect address."""
    calls: list = field(default_factory=list)
    order: list = field(default_factory=list)      # shared call-order log
    result: bool = True
    forbidden: set = field(default_factory=set)

    def __call__(self, to_email, subject, body, config, attachments=None, reply_to=None):
        assert to_email not in self.forbidden, f"send_email called with forbidden address {to_email!r}"
        self.order.append(("send", to_email))
        self.calls.append({"to": to_email, "subject": subject, "body": body,
                           "attachments": attachments, "config": config})
        return self.result

    @property
    def recipients(self):
        return [c["to"] for c in self.calls]


@pytest.fixture
def client_ctx():
    from backend.main import app
    return lambda: AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def enabled(clean_db):
    await clean_db.upsert_setting("email_campaigns_enabled", "true")
    return clean_db


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Belt-and-braces: nothing in this integration file may make a real
    outbound HTTP call. n8n config stays real (fail-closed tests need it);
    only the health probe is stubbed. AI + the email sender are stubbed
    per-test by the `wire` / `stub_ai` fixtures."""
    from backend.email_campaigns import n8n_client

    async def _fake_health():
        cfg = await n8n_client.get_n8n_config()
        return {"ok": False, "status_code": None, "base_url": cfg["base_url"], "error": "stubbed"}

    monkeypatch.setattr(n8n_client, "health_check", _fake_health)


@pytest.fixture
def wire(monkeypatch):
    """Wire mock sender + mock SMTP cfg + instrument the safety gate for
    call-order verification. Returns the sender recorder."""
    from backend import email_sender
    from backend.email_campaigns import service as service_mod

    sender = _Sender()
    monkeypatch.setattr(email_sender, "send_email", sender)

    async def _cfg():
        return {"host": "smtp.mock", "port": 587, "username": "mock@mock",
                "password": "MOCK-NOT-A-REAL-SECRET", "from_name": "T", "from_email": "mock@mock"}
    monkeypatch.setattr(email_sender, "_smtp_cfg", _cfg)

    real_gate = service_mod.assert_send_allowed

    def _instrumented_gate(campaign, send_to, lead_email):
        sender.order.append(("gate", send_to))
        return real_gate(campaign, send_to, lead_email)
    monkeypatch.setattr(service_mod, "assert_send_allowed", _instrumented_gate)

    return sender


@pytest.fixture
def stub_ai(monkeypatch):
    from backend import ai_brain

    async def _gen_messages(lead, dna):
        return {"email_subject": f"A note for {lead.get('company') or 'you'}",
                "email_body": "Hi there,\n\nA short, real message with an idea. Open to a quick chat?\n\nBest,\nT"}

    async def _gen_message(lead, mtype):
        return f"A note for {lead.get('company') or 'you'}"

    monkeypatch.setattr(ai_brain, "generate_messages", _gen_messages)
    monkeypatch.setattr(ai_brain, "generate_message", _gen_message)
    monkeypatch.setattr(ai_brain, "_load_company_dna", lambda: "DNA")
    return monkeypatch


# ── helpers ────────────────────────────────────────────────────────────────

async def _create(c, name="C", **extra):
    r = await c.post("/api/email-campaigns", json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _import_csv(c, cid, text, filename="leads.csv"):
    return await c.post(f"/api/email-campaigns/{cid}/leads/import",
                        files={"file": (filename, text.encode("utf-8"), "text/csv")})


def _xlsx_bytes(rows):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def _run_to_completion(c, cid, key="run-1"):
    assert (await c.post(f"/api/email-campaigns/{cid}/ready")).status_code == 200
    r = await c.post(f"/api/email-campaigns/{cid}/start", json={"idempotency_key": key})
    assert r.status_code == 200, r.text
    return r.json()


# ══ GROUP 1 — lifecycle ═══════════════════════════════════════════════════

async def test_full_lifecycle_draft_to_completed(enabled, wire, stub_ai, client_ctx):
    async with client_ctx() as c:
        cid = await _create(c, "Lifecycle")
        assert (await c.get(f"/api/email-campaigns/{cid}")).json()["status"] == "DRAFT"
        await _import_csv(c, cid, "email,company\na@realbiz-alpha.com,Alpha\nb@realbiz-beta.com,Beta\n")
        assert (await c.post(f"/api/email-campaigns/{cid}/ready")).json()["status"] == "READY"
        run = await c.post(f"/api/email-campaigns/{cid}/start", json={"idempotency_key": "k"})
        assert run.status_code == 200
        assert (await c.get(f"/api/email-campaigns/{cid}")).json()["status"] == "COMPLETED"
    assert set(wire.recipients) == {TEST_RECIPIENT}


async def test_invalid_transitions_rejected(enabled, wire, stub_ai, client_ctx):
    async with client_ctx() as c:
        cid = await _create(c)
        await _import_csv(c, cid, "email\na@realbiz-alpha.com\n")
        # DRAFT -> start (skips READY) rejected
        assert (await c.post(f"/api/email-campaigns/{cid}/start", json={"idempotency_key": "k"})).status_code == 409
        # DRAFT -> pause rejected
        assert (await c.post(f"/api/email-campaigns/{cid}/pause")).status_code == 409
        await c.post(f"/api/email-campaigns/{cid}/ready")
        # READY -> resume rejected (not paused)
        assert (await c.post(f"/api/email-campaigns/{cid}/resume", json={"idempotency_key": "k"})).status_code == 409


# ══ GROUP 2 — lead import ═════════════════════════════════════════════════

CSV_MIX = (
    "First Name,Email,Company,Body\r\n"
    "I. ,unicodé@réalbiz-gамма.com,Gamma,\r\n"          # unicode local/domain
    "Ana,ana@realbiz-alpha.com,Alpha,\"Hola Ana,\n\n<b>Keep this exact</b>.  \n-- S \"\r\n"  # HTML + trailing spaces
    "\r\n"                                                # blank row
    "Bob,bob@realbiz-beta.com,Beta,\r\n"
    "Cara,,No Email Co,\r\n"                              # missing email
    "Dan,not-an-email,Bad Co,\r\n"                        # malformed
    "Bob2,bob@realbiz-beta.com,Beta,\r\n"                 # dup email -> dup lead_key
)


async def test_import_classification_and_preservation(enabled, client_ctx):
    db = enabled
    async with client_ctx() as c:
        cid = await _create(c)
        r = await _import_csv(c, cid, CSV_MIX)
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["missing_email"] == 1
        assert b["invalid_email"] >= 1        # 'not-an-email' (+ maybe the unicode one, validator-dependent)
        assert b["duplicates"] == 1

        ana = await db.get_email_campaign_lead(cid, f"{cid}::ana@realbiz-alpha.com")
        assert ana["body_source"] == "provided"
        assert ana["provided_body"] == "Hola Ana,\n\n<b>Keep this exact</b>.  \n-- S"  # verbatim (strip only)
        raw = json.loads(ana["raw_json"])
        assert raw["Company"] == "Alpha" and raw["First Name"] == "Ana"


async def test_import_xlsx_via_api(enabled, client_ctx):
    db = enabled
    async with client_ctx() as c:
        cid = await _create(c)
        data = _xlsx_bytes([
            ["email", "company", "body"],
            ["x@realbiz-alpha.com", "Alpha", ""],
            ["y@realbiz-beta.com", "Beta", "supplied body ünïcode"],
        ])
        r = await c.post(f"/api/email-campaigns/{cid}/leads/import",
                         files={"file": ("leads.xlsx", data,
                                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        assert r.status_code == 200, r.text
        assert r.json()["valid"] == 2
        y = await db.get_email_campaign_lead(cid, f"{cid}::y@realbiz-beta.com")
        assert y["provided_body"] == "supplied body ünïcode"


async def test_repeated_import_is_idempotent_on_lead_key(enabled, client_ctx):
    db = enabled
    async with client_ctx() as c:
        cid = await _create(c)
        txt = "email,company\na@realbiz-alpha.com,Alpha\nb@realbiz-beta.com,Beta\n"
        await _import_csv(c, cid, txt)
        second = await _import_csv(c, cid, txt)
        assert second.json()["inserted"] == 0
        assert len(await db.get_email_campaign_leads(cid, limit=1000)) == 2


async def test_invalid_tld_addresses_are_rejected_at_import(enabled, client_ctx):
    """`prospectN@example.invalid` never becomes sendable — the validator
    rejects it, so it cannot even reach the send path."""
    db = enabled
    async with client_ctx() as c:
        cid = await _create(c)
        r = await _import_csv(
            c, cid,
            "email\nprospect1@example.invalid\nprospect2@example.invalid\nprospect3@example.invalid\n",
        )
        assert r.json()["valid"] == 0
        assert r.json()["invalid_email"] == 3
        for l in await db.get_email_campaign_leads(cid, limit=100):
            assert l["status"] == "INVALID_EMAIL"


# ══ GROUP 3 — AI generation ══════════════════════════════════════════════

async def test_supplied_body_is_never_replaced_by_ai(enabled, wire, stub_ai, client_ctx):
    db = enabled
    body = "EXACT SUPPLIED BODY — do not touch.\n\nRegards."
    async with client_ctx() as c:
        cid = await _create(c)
        await _import_csv(c, cid, f'email,company,body\nz@realbiz-alpha.com,Alpha,"{body}"\n')
        await _run_to_completion(c, cid)
        lead = await db.get_email_campaign_lead(cid, f"{cid}::z@realbiz-alpha.com")
    assert lead["ai_body"] == body                      # AI did NOT rewrite it
    assert lead["body_source"] == "provided"
    assert lead["ai_subject"]                           # AI (or fallback) produced a subject
    # the sent body contains the supplied text verbatim (plus the TEST banner)
    assert body in wire.calls[0]["body"]


async def test_ai_failure_marks_lead_and_campaign_still_completes(enabled, wire, client_ctx, monkeypatch):
    from backend import ai_brain
    async def _boom(lead, dna):
        raise RuntimeError("ollama unreachable")
    monkeypatch.setattr(ai_brain, "generate_messages", _boom)
    monkeypatch.setattr(ai_brain, "_load_company_dna", lambda: "DNA")

    db = enabled
    async with client_ctx() as c:
        cid = await _create(c)
        await _import_csv(c, cid, "email,company\nok@realbiz-alpha.com,Alpha\n")
        await _run_to_completion(c, cid)
        lead = await db.get_email_campaign_lead(cid, f"{cid}::ok@realbiz-alpha.com")
        assert lead["status"] == "AI_GENERATION_FAILED"
        assert (await c.get(f"/api/email-campaigns/{cid}")).json()["status"] == "COMPLETED"
    assert wire.calls == []                             # nothing sent


# ══ GROUP 4 — TEST_MODE SAFETY (the critical group) ══════════════════════

PROSPECTS = ["ceo@northwind-dental.com", "owner@summit-orthodontics.com", "info@lakeside-familydental.com"]


async def test_test_mode_never_sends_to_real_prospect_end_to_end(enabled, wire, stub_ai, client_ctx):
    wire.forbidden = set(PROSPECTS)
    async with client_ctx() as c:
        cid = await _create(c, "Prospects")
        csv = "email,company\n" + "\n".join(f"{p},Co{i}" for i, p in enumerate(PROSPECTS)) + "\n"
        await _import_csv(c, cid, csv)
        await _run_to_completion(c, cid)
        leads = (await c.get(f"/api/email-campaigns/{cid}/leads")).json()["leads"]

    assert len(wire.calls) == 3
    assert set(wire.recipients) == {TEST_RECIPIENT}
    for p in PROSPECTS:
        assert p not in wire.recipients
    # original prospect addresses preserved on the rows, all SENT
    assert {l["email"] for l in leads} == set(PROSPECTS)
    assert all(l["status"] == "SENT" for l in leads)


async def test_safety_gate_runs_immediately_before_every_send(enabled, wire, stub_ai, client_ctx):
    async with client_ctx() as c:
        cid = await _create(c)
        await _import_csv(c, cid, "email,company\na@realbiz-alpha.com,A\nb@realbiz-beta.com,B\nd@realbiz-delta.com,D\n")
        await _run_to_completion(c, cid)
    # order log is [gate, send, gate, send, ...] — every 'send' preceded by a 'gate'
    kinds = [k for k, _ in wire.order]
    assert kinds == ["gate", "send"] * 3
    for i, (k, _) in enumerate(wire.order):
        if k == "send":
            assert wire.order[i - 1][0] == "gate"


async def test_safety_gate_blocks_direct_bypass(enabled):
    from backend.email_campaigns.safety import assert_send_allowed, SafetyGateError
    camp = {"test_mode": 1, "test_recipient": TEST_RECIPIENT}
    for p in PROSPECTS:
        with pytest.raises(SafetyGateError):
            assert_send_allowed(camp, p, p)                 # send_to == prospect
    with pytest.raises(SafetyGateError):
        assert_send_allowed({"test_mode": 0, "test_recipient": TEST_RECIPIENT}, TEST_RECIPIENT, "x@y.com")
    with pytest.raises(SafetyGateError):
        assert_send_allowed({"test_mode": 1, "test_recipient": ""}, "", "x@y.com")
    with pytest.raises(SafetyGateError):
        assert_send_allowed({"test_mode": 1, "test_recipient": "not an email"}, "x@y.com", "x@y.com")
    # the one allowed shape
    assert_send_allowed(camp, TEST_RECIPIENT, "prospect@realbiz.com")


async def test_redirect_still_protects_even_if_gate_is_disabled(enabled, wire, stub_ai, client_ctx, monkeypatch):
    """Defense in depth: the TEST_MODE redirect and the safety gate are
    independent. Neutralise the gate entirely — the redirect alone still
    guarantees no prospect is contacted."""
    from backend.email_campaigns import service as service_mod
    monkeypatch.setattr(service_mod, "assert_send_allowed", lambda *a, **k: None)   # gate fully off
    wire.forbidden = set(PROSPECTS)
    async with client_ctx() as c:
        cid = await _create(c)
        csv = "email,company\n" + "\n".join(f"{p},Co{i}" for i, p in enumerate(PROSPECTS)) + "\n"
        await _import_csv(c, cid, csv)
        await _run_to_completion(c, cid)
    assert len(wire.calls) == 3
    assert set(wire.recipients) == {TEST_RECIPIENT}


# ══ GROUP 5 — idempotency ════════════════════════════════════════════════

async def test_duplicate_start_same_key_one_run_no_double_send(enabled, wire, stub_ai, client_ctx):
    db = enabled
    async with client_ctx() as c:
        cid = await _create(c)
        await _import_csv(c, cid, "email,company\na@realbiz-alpha.com,A\n")
        await c.post(f"/api/email-campaigns/{cid}/ready")
        await c.post(f"/api/email-campaigns/{cid}/start", json={"idempotency_key": "same"})
        assert len(wire.calls) == 1
        await db.update_email_campaign(cid, {"status": "RUNNING"})
        r2 = await c.post(f"/api/email-campaigns/{cid}/start", json={"idempotency_key": "same"})
        assert r2.status_code == 200
    assert len(wire.calls) == 1
    assert len(await db.list_email_campaign_runs(cid)) == 1


async def test_already_sent_lead_is_not_resent(enabled, wire, stub_ai, client_ctx):
    db = enabled
    from backend.email_campaigns.service import get_email_campaign_service
    svc = get_email_campaign_service()
    async with client_ctx() as c:
        cid = await _create(c)
        await _import_csv(c, cid, "email,company\na@realbiz-alpha.com,A\n")
        await c.post(f"/api/email-campaigns/{cid}/ready")
    run = await db.create_email_campaign_run(cid, "k")
    await db.update_email_campaign(cid, {"status": "RUNNING"})
    await svc.run_batch(cid, run["id"])
    await db.update_email_campaign(cid, {"status": "RUNNING"})
    await svc.run_batch(cid, run["id"])          # second pass over the same SENT lead
    assert len(wire.calls) == 1


# ══ GROUP 6 — DO_NOT_CONTACT ════════════════════════════════════════════

async def test_do_not_contact_global_lead_is_never_sent(enabled, wire, stub_ai, client_ctx):
    db = enabled
    wire.forbidden = {"optout@realbiz-omega.com"}
    gid = await db.create_lead({"business_name": "Omega", "email": "optout@realbiz-omega.com",
                                "niche": "x", "city": "y"})
    await db.update_lead(gid, {"status": "DO_NOT_CONTACT"})
    async with client_ctx() as c:
        cid = await _create(c)
        await db.bulk_insert_email_campaign_leads(cid, [{
            "lead_key": f"{cid}::optout@realbiz-omega.com", "lead_id": gid,
            "email": "optout@realbiz-omega.com", "raw": {}, "status": "VALIDATED",
        }])
        await db.recount_email_campaign(cid)
        await _run_to_completion(c, cid)
        lead = await db.get_email_campaign_lead(cid, f"{cid}::optout@realbiz-omega.com")
    assert lead["status"] == "DO_NOT_CONTACT"
    assert wire.calls == []


# ══ GROUP 7 — pause / resume ════════════════════════════════════════════

async def test_pause_stops_batch_then_resume_finishes_without_resending(enabled, wire, stub_ai, client_ctx, monkeypatch):
    db = enabled
    from backend import database as real_db
    from backend.email_campaigns.service import get_email_campaign_service
    svc = get_email_campaign_service()

    async with client_ctx() as c:
        cid = await _create(c, test_recipient=TEST_RECIPIENT)
        await _import_csv(c, cid, "email,company\na@realbiz-alpha.com,A\nb@realbiz-beta.com,B\nd@realbiz-delta.com,D\n")
        await c.post(f"/api/email-campaigns/{cid}/ready")

    run = await db.create_email_campaign_run(cid, "k1")
    await db.update_email_campaign(cid, {"status": "RUNNING"})

    real_get = real_db.get_email_campaign
    state = {"n": 0}
    async def _get_flipping(campaign_id):
        row = await real_get(campaign_id)
        state["n"] += 1
        if row and campaign_id == cid and state["n"] >= 3 and row["status"] == "RUNNING":
            await real_db.update_email_campaign(cid, {"status": "PAUSED"})
            row = await real_get(campaign_id)
        return row

    # scoped patch — auto-undone here, WITHOUT disturbing the wire / stub_ai patches
    with monkeypatch.context() as m:
        m.setattr(real_db, "get_email_campaign", _get_flipping)
        await svc.run_batch(cid, run["id"])

    assert len(wire.calls) == 1                          # stopped after the first lead
    assert (await db.get_email_campaign(cid))["status"] == "PAUSED"
    remaining = [l for l in await db.get_email_campaign_leads(cid, limit=100) if l["status"] == "VALIDATED"]
    assert len(remaining) == 2

    # resume via the API -> the rest go out, first lead not re-sent
    async with client_ctx() as c:
        r = await c.post(f"/api/email-campaigns/{cid}/resume", json={"idempotency_key": "k2"})
        assert r.status_code == 200
    assert len(wire.calls) == 3
    assert set(wire.recipients) == {TEST_RECIPIENT}
    sent = [l for l in await db.get_email_campaign_leads(cid, limit=100) if l["status"] == "SENT"]
    assert len(sent) == 3


# ══ GROUP 8 — attachment ═══════════════════════════════════════════════

async def test_attachment_reaches_sender_and_never_leaks_path(enabled, wire, stub_ai, client_ctx):
    db = enabled
    blob = b"%PDF-1.4 synthetic test attachment bytes"
    async with client_ctx() as c:
        cid = await _create(c)
        up = await c.post(f"/api/email-campaigns/{cid}/attachment",
                          files={"file": ("../../secret/Proposal.pdf", blob, "application/pdf")})
        assert up.status_code == 200
        meta = up.json()
        assert meta["filename"] == "Proposal.pdf"        # directory components stripped
        assert "path" not in meta and "attachment_path" not in meta

        got = await c.get(f"/api/email-campaigns/{cid}")
        assert "attachment_path" not in got.json()
        assert got.json()["attachment"]["present"] is True

        await _import_csv(c, cid, "email,company\na@realbiz-alpha.com,Alpha\n")
        await _run_to_completion(c, cid)

    atts = wire.calls[0]["attachments"]
    assert atts and len(atts) == 1
    assert atts[0]["filename"] == "Proposal.pdf"
    assert atts[0]["content"] == blob
    assert atts[0]["mime"] == "application/pdf"
    # the stored path stays inside the backend upload area
    raw = await db.get_email_campaign(cid)
    assert "email_campaign_uploads" in raw["attachment_path"].replace("\\", "/")


async def test_attachment_type_rejected(enabled, client_ctx):
    async with client_ctx() as c:
        cid = await _create(c)
        r = await c.post(f"/api/email-campaigns/{cid}/attachment",
                         files={"file": ("evil.exe", b"MZ\x90\x00", "application/octet-stream")})
        assert r.status_code == 422


# ══ GROUP 9 — API integration + response hygiene ════════════════════════

async def test_every_endpoint_and_no_secret_in_any_response(enabled, wire, stub_ai, client_ctx):
    # put n8n secrets in the DB so we can prove they never surface
    await enabled.upsert_setting("n8n_base_url", "http://localhost:5678")
    await enabled.upsert_setting("n8n_api_key", "MOCK-N8N-API-KEY-XYZ")
    await enabled.upsert_setting("n8n_callback_secret", "MOCK-N8N-CALLBACK-SECRET-ABC")
    await enabled.upsert_setting("smtp_password", "MOCK-SMTP-PASSWORD-123")

    SECRETS = ("MOCK-N8N-API-KEY-XYZ", "MOCK-N8N-CALLBACK-SECRET-ABC", "MOCK-SMTP-PASSWORD-123",
               "MOCK-NOT-A-REAL-SECRET")
    bodies = []

    async with client_ctx() as c:
        cid = await _create(c, "API sweep")
        bodies.append((await c.get("/api/email-campaigns")).text)
        bodies.append((await c.get(f"/api/email-campaigns/{cid}")).text)
        bodies.append((await c.patch(f"/api/email-campaigns/{cid}", json={"description": "d"})).text)
        bodies.append((await _import_csv(c, cid, "email,company\na@realbiz-alpha.com,A\n")).text)
        bodies.append((await c.post(f"/api/email-campaigns/{cid}/attachment",
                                    files={"file": ("d.pdf", b"%PDF x", "application/pdf")})).text)
        bodies.append((await c.post(f"/api/email-campaigns/{cid}/prepare")).text)
        bodies.append((await c.post(f"/api/email-campaigns/{cid}/ready")).text)
        bodies.append((await c.get(f"/api/email-campaigns/{cid}/leads")).text)
        bodies.append((await c.post(f"/api/email-campaigns/{cid}/start", json={"idempotency_key": "k"})).text)
        bodies.append((await c.get(f"/api/email-campaigns/{cid}/stats")).text)
        bodies.append((await c.get(f"/api/email-campaigns/{cid}/activity")).text)
        bodies.append((await c.get("/api/email-campaigns/n8n/status")).text)
        bodies.append((await c.delete(f"/api/email-campaigns/{cid}/attachment")).text)

    blob = "\n".join(bodies)
    for s in SECRETS:
        assert s not in blob, f"secret leaked in an API response: {s[:10]}…"
    for marker in ("attachment_path", "email_campaign_uploads", "/home/",
                   "n8n_trigger_ref", "execution_id", "idempotency_key"):
        assert marker not in blob, f"internal field leaked in an API response: {marker}"


async def test_unauthenticated_rejected_when_password_set(enabled, client_ctx):
    from backend import auth
    await auth.set_password("integration-pw-xyz")
    try:
        async with client_ctx() as c:
            assert (await c.get("/api/email-campaigns")).status_code == 401
            tok = auth.issue_session()
            r = await c.get("/api/email-campaigns", headers={"Authorization": f"Bearer {tok}"})
            assert r.status_code == 200
    finally:
        await auth.clear_password()


async def test_feature_flag_default_off_in_source():
    """Source/config default must stay off; tests opt in per-DB only."""
    from backend.config import Settings
    assert Settings().email_campaigns_enabled is False
    cfg_src = (pathlib.Path(__file__).resolve().parent.parent / "backend" / "config.py").read_text(encoding="utf-8")
    assert "email_campaigns_enabled: bool = False" in cfg_src


async def test_disabled_feature_blocks_every_operation(clean_db, client_ctx):
    async with client_ctx() as c:
        cid_attempts = [
            ("post", "/api/email-campaigns", {"name": "x"}),
            ("get", "/api/email-campaigns", None),
            ("get", "/api/email-campaigns/1", None),
            ("get", "/api/email-campaigns/1/stats", None),
            ("get", "/api/email-campaigns/1/activity", None),
            ("post", "/api/email-campaigns/1/start", {"idempotency_key": "k"}),
            ("get", "/api/email-campaigns/n8n/status", None),
        ]
        for method, path, body in cid_attempts:
            kw = {"json": body} if body is not None else {}
            r = await getattr(c, method)(path, **kw)
            assert r.status_code == 503, (path, r.status_code)


# ══ GROUP 11 — multi-tenancy / isolation ═══════════════════════════════

async def test_no_tenant_model_exists_single_operator(enabled):
    """AutoLead is single-operator by design (backend/auth.py). The Email
    Campaign tables carry no user_id / tenant_id / owner_id — there is no
    cross-user data to isolate. The security boundary is the session token
    (tested above) + the feature flag + the safety gate."""
    async with enabled.get_db() as conn:
        cols = {r["name"] for r in await conn.fetch("PRAGMA table_info(email_campaigns)")}
    for owner_col in ("user_id", "tenant_id", "owner_id", "workspace_id", "account_id"):
        assert owner_col not in cols
    auth_src = (pathlib.Path(__file__).resolve().parent.parent / "backend" / "auth.py").read_text(encoding="utf-8")
    assert "single-user" in auth_src.lower() or "single operator" in auth_src.lower() \
        or "not a multi-tenant" in auth_src.lower()


# ══ GROUP 12 — n8n boundary ════════════════════════════════════════════

async def test_send_path_has_zero_n8n_dependency(enabled, wire, stub_ai, client_ctx, monkeypatch):
    """Break every n8n entry point; a full campaign still prepares + sends."""
    from backend.email_campaigns import n8n_client

    async def _explode(*a, **k):
        raise n8n_client.N8nNotConfigured("n8n is down for this test")
    monkeypatch.setattr(n8n_client, "get_n8n_config", _explode)
    monkeypatch.setattr(n8n_client, "health_check", _explode)

    async with client_ctx() as c:
        cid = await _create(c)
        await _import_csv(c, cid, "email,company\na@realbiz-alpha.com,A\nb@realbiz-beta.com,B\n")
        await _run_to_completion(c, cid)
        assert (await c.get(f"/api/email-campaigns/{cid}")).json()["status"] == "COMPLETED"
    assert len(wire.calls) == 2
    assert set(wire.recipients) == {TEST_RECIPIENT}


async def test_n8n_callback_cannot_send_or_mark_sent(enabled, wire, client_ctx):
    db = enabled
    await db.upsert_setting("n8n_base_url", "http://localhost:5678")
    await db.upsert_setting("n8n_callback_secret", "MOCK-CB-SECRET")
    async with client_ctx() as c:
        cid = await _create(c)
        await _import_csv(c, cid, "email,company\na@realbiz-alpha.com,Alpha\n")
        run = await db.create_email_campaign_run(cid, "r")
        # wrong secret -> 401
        assert (await c.post(f"/api/email-campaigns/{cid}/n8n-callback",
                             headers={"X-N8n-Callback-Secret": "wrong"},
                             json={"run_id": run["id"], "event": "preparation_complete", "leads": []})).status_code == 401
        # right secret: ingest prepared copy
        r = await c.post(f"/api/email-campaigns/{cid}/n8n-callback",
                         headers={"X-N8n-Callback-Secret": "MOCK-CB-SECRET"},
                         json={"run_id": run["id"], "event": "preparation_complete",
                               "leads": [{"lead_key": f"{cid}::a@realbiz-alpha.com",
                                          "ai_subject": "from n8n", "ai_body": "prepared by n8n, at least forty chars long."}]})
        assert r.status_code == 200 and r.json()["applied"] == 1
        lead = await db.get_email_campaign_lead(cid, f"{cid}::a@realbiz-alpha.com")
        assert lead["status"] == "GENERATED"            # NOT SENT
    assert wire.calls == []                             # the callback sent nothing


async def test_no_second_sender_in_module():
    """`send_email` is referenced only by the sanctioned transport layer
    (service.py resolves + drives it; senders.SmtpTransport adapts it).
    No email_campaigns module imports smtplib directly; n8n_client is inert."""
    _SENDER_LAYER = {"service.py", "senders.py"}
    ref_files = set()
    for pyf in PKG.glob("*.py"):
        tree = ast.parse(pyf.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Attribute, ast.Name)):
                nm = getattr(node, "attr", None) or getattr(node, "id", None)
                if nm == "send_email":
                    ref_files.add(pyf.name)
            if isinstance(node, ast.Import) and any(a.name == "smtplib" for a in node.names):
                assert False, f"{pyf.name} imports smtplib directly"
            if isinstance(node, ast.ImportFrom) and (node.module or "") == "smtplib":
                assert False, f"{pyf.name} imports smtplib"
    assert ref_files <= _SENDER_LAYER, f"send_email referenced outside the sender layer: {ref_files}"
    nc = (PKG / "n8n_client.py").read_text(encoding="utf-8")
    assert "smtplib" not in ast.dump(ast.parse(nc))


# ══ GROUP 14 — secret-leak scan of new source + a full run's outputs ════

async def test_no_secret_patterns_in_new_source_or_run_output(enabled, wire, stub_ai, client_ctx):
    import re
    # 1. static: new module + router source must not embed credential literals
    suspicious = re.compile(
        r"(AIza[0-9A-Za-z_\-]{20,}|xox[baprs]-[0-9A-Za-z\-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY|"
        r"password\s*=\s*[\"'][^\"'\s]{6,}[\"']|api_key\s*=\s*[\"'][^\"'\s]{8,}[\"'])",
        re.I,
    )
    for f in list(PKG.glob("*.py")) + [pathlib.Path(__file__).resolve().parent.parent / "backend" / "routers" / "email_campaigns.py"]:
        src = f.read_text(encoding="utf-8")
        m = suspicious.search(src)
        assert not m, f"suspicious literal in {f.name}"

    # 2. runtime: a full campaign run's activity/stats/leads output carries no secret
    await enabled.upsert_setting("smtp_password", "RUNTIME-LEAK-CHECK-PW")
    async with client_ctx() as c:
        cid = await _create(c)
        await _import_csv(c, cid, "email,company\na@realbiz-alpha.com,A\n")
        await _run_to_completion(c, cid)
        dump = "\n".join([
            (await c.get(f"/api/email-campaigns/{cid}/activity")).text,
            (await c.get(f"/api/email-campaigns/{cid}/stats")).text,
            (await c.get(f"/api/email-campaigns/{cid}/leads")).text,
        ])
    assert "RUNTIME-LEAK-CHECK-PW" not in dump
    assert "MOCK-NOT-A-REAL-SECRET" not in dump          # the mock SMTP password
