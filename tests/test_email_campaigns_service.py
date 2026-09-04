"""
test_email_campaigns_service.py — Checkpoint 3B: EmailCampaignService.

Covers: campaign creation (always DRAFT), lead import (CSV/XLSX, dedupe,
MISSING/INVALID/DUPLICATE, raw + verbatim body preservation), supplied-body
vs AI generation, the one optional attachment, the fail-closed TEST_MODE
safety gate, campaign- and lead-level idempotency, pause/resume, lifecycle
transitions, statistics, and activity/audit.

The CRITICAL regression test is `test_test_mode_never_sends_to_real_prospect`:
it proves `email_sender.send_email` is NEVER called with a real prospect's
address while the campaign is in TEST_MODE. It must fail if someone later
bypasses the safety gate.

No network: ai_brain and the SMTP layer are stubbed.
"""
import json
from dataclasses import dataclass, field
from typing import List

import pytest

pytestmark = pytest.mark.asyncio


# ── stubs ──────────────────────────────────────────────────────────────────

@dataclass
class _SendRecorder:
    """Stand-in for email_sender.send_email — records every call, sends nothing."""
    calls: List[dict] = field(default_factory=list)
    result: bool = True

    def __call__(self, to_email, subject, body, config, attachments=None, reply_to=None):
        self.calls.append({
            "to_email": to_email, "subject": subject, "body": body,
            "attachments": attachments,
        })
        return self.result

    @property
    def recipients(self):
        return [c["to_email"] for c in self.calls]


@pytest.fixture
def svc():
    from backend.email_campaigns.service import EmailCampaignService
    return EmailCampaignService()


@pytest.fixture
def stub_send(monkeypatch):
    """Replace the ONE sender + SMTP config lookup. Returns the recorder."""
    from backend import email_sender
    rec = _SendRecorder()
    monkeypatch.setattr(email_sender, "send_email", rec)

    async def _fake_cfg():
        return {"host": "smtp.test", "port": 587, "username": "u@test",
                "password": "pw", "from_name": "T", "from_email": "u@test"}
    monkeypatch.setattr(email_sender, "_smtp_cfg", _fake_cfg)
    return rec


@pytest.fixture
def stub_ai(monkeypatch):
    from backend import ai_brain

    async def _gen_messages(lead, dna):
        return {
            "email_subject": f"A quick idea for {lead.get('company') or 'you'}",
            "email_body": ("Hi there,\n\nI work with local businesses and had an idea "
                           "worth sharing. Open to a short chat?\n\nBest,\nT"),
        }

    async def _gen_message(lead, message_type):
        return "A quick idea for your business"

    monkeypatch.setattr(ai_brain, "generate_messages", _gen_messages)
    monkeypatch.setattr(ai_brain, "generate_message", _gen_message)
    monkeypatch.setattr(ai_brain, "_load_company_dna", lambda: "DNA")
    return monkeypatch


async def _enable(db):
    await db.upsert_setting("email_campaigns_enabled", "true")


CSV = (
    "First Name,Email,Company,Body\r\n"
    "Alice,alice@acmedental.com,Acme Dental,\r\n"
    "Bob,bob@brightsmile.com,Bright Smile,\"Hi Bob,\n\nWe spoke last year. Still keen?\n\n- S\"\r\n"
    "Carol,,Carol Corp,\r\n"                          # missing email
    "Dave,not-an-email,Dave LLC,\r\n"                 # invalid email
    "Alice2,alice@acmedental.com,Acme Dental,\r\n"    # duplicate email
)


# ── creation ───────────────────────────────────────────────────────────────

async def test_create_campaign_is_draft_and_test_mode(clean_db, svc):
    camp = await svc.create_campaign({"name": "Dental TX", "test_recipient": "owner@popupgenix.io"})
    assert camp["status"] == "DRAFT"
    assert camp["test_mode"] == 1
    assert camp["test_recipient"] == "owner@popupgenix.io"
    assert camp["started_at"] is None


async def test_create_campaign_forces_test_mode_even_if_disabled(clean_db, svc):
    camp = await svc.create_campaign({"name": "X", "test_mode": False})
    assert camp["test_mode"] == 1  # this build is TEST_MODE only


async def test_create_campaign_validates(clean_db, svc):
    from backend.email_campaigns.service import EmailCampaignError
    with pytest.raises(EmailCampaignError):
        await svc.create_campaign({"name": "   "})
    with pytest.raises(EmailCampaignError):
        await svc.create_campaign({"name": "ok", "test_recipient": "nope"})


# ── lead import ────────────────────────────────────────────────────────────

async def test_import_leads_csv_classifies_and_preserves(clean_db, svc):
    db = clean_db
    camp = await svc.create_campaign({"name": "C"})
    cid = camp["id"]
    stats = await svc.import_leads(cid, "leads.csv", CSV.encode("utf-8"))

    assert stats["total_rows"] == 5
    assert stats["valid"] == 2
    assert stats["missing_email"] == 1
    assert stats["invalid_email"] == 1
    assert stats["duplicates"] == 1
    # the in-file duplicate collapses onto the same campaign lead_key
    assert stats["inserted"] == 4

    bob = await db.get_email_campaign_lead(cid, f"{cid}::bob@brightsmile.com")
    assert bob["body_source"] == "provided"
    assert bob["provided_body"] == "Hi Bob,\n\nWe spoke last year. Still keen?\n\n- S"
    assert json.loads(bob["raw_json"])["Company"] == "Bright Smile"

    alice = await db.get_email_campaign_lead(cid, f"{cid}::alice@acmedental.com")
    assert alice["status"] == "VALIDATED"
    assert alice["body_source"] == "ai"


async def test_reimport_same_file_does_not_duplicate(clean_db, svc):
    db = clean_db
    camp = await svc.create_campaign({"name": "C"})
    cid = camp["id"]
    await svc.import_leads(cid, "leads.csv", CSV.encode("utf-8"))
    second = await svc.import_leads(cid, "leads.csv", CSV.encode("utf-8"))
    assert second["inserted"] == 0
    assert len(await db.get_email_campaign_leads(cid, limit=1000)) == 4


async def test_import_xls_binary_is_rejected(clean_db, svc):
    camp = await svc.create_campaign({"name": "C"})
    with pytest.raises(ValueError):
        await svc.import_leads(camp["id"], "old.xls", b"\xd0\xcf\x11\xe0rubbish")


async def test_import_blocked_once_running(clean_db, svc, stub_ai, stub_send):
    db = clean_db
    await _enable(db)
    from backend.email_campaigns.service import EmailCampaignError
    camp = await svc.create_campaign({"name": "C"})
    cid = camp["id"]
    await svc.import_leads(cid, "l.csv", b"email\nx@y.com\n")
    await svc.mark_ready(cid)
    await db.update_email_campaign(cid, {"status": "RUNNING"})
    with pytest.raises(EmailCampaignError):
        await svc.import_leads(cid, "l.csv", b"email\nz@y.com\n")


# ── preparation ────────────────────────────────────────────────────────────

async def test_prepare_provided_body_is_verbatim_ai_subject_only(clean_db, svc):
    db = clean_db
    camp = await svc.create_campaign({"name": "C", "ai_enabled": False})
    cid = camp["id"]
    body = "Hi Bob,\n\nWe spoke last year. Still keen?\n\n- S"
    await svc.import_leads(
        cid, "l.csv", f'email,body\r\nbob@brightsmile.com,"{body}"\r\n'.encode("utf-8"),
    )
    out = await svc.prepare_campaign(cid)
    assert out["generated"] == 1
    lead = await db.get_email_campaign_lead(cid, f"{cid}::bob@brightsmile.com")
    assert lead["status"] == "GENERATED"
    assert lead["ai_body"] == body           # byte-for-byte
    assert lead["ai_subject"]                # a deterministic fallback subject


async def test_prepare_ai_body_uses_ai_brain(clean_db, svc, stub_ai):
    db = clean_db
    camp = await svc.create_campaign({"name": "C"})
    cid = camp["id"]
    await svc.import_leads(cid, "l.csv", b"email,company\nalice@acmedental.com,Acme\n")
    out = await svc.prepare_campaign(cid)
    assert out["generated"] == 1
    lead = await db.get_email_campaign_lead(cid, f"{cid}::alice@acmedental.com")
    assert "Acme" in lead["ai_subject"]
    assert len(lead["ai_body"]) > 40


async def test_ai_failure_marks_lead_and_is_skipped(clean_db, svc, stub_send, monkeypatch):
    db = clean_db
    await _enable(db)
    from backend import ai_brain

    async def _boom(lead, dna):
        raise RuntimeError("ollama down")
    monkeypatch.setattr(ai_brain, "generate_messages", _boom)
    monkeypatch.setattr(ai_brain, "_load_company_dna", lambda: "DNA")

    camp = await svc.create_campaign({"name": "C"})
    cid = camp["id"]
    await svc.import_leads(cid, "l.csv", b"email\nalice@acmedental.com\n")
    await svc.mark_ready(cid)
    await svc.start(cid, "run-1", background=False)

    lead = await db.get_email_campaign_lead(cid, f"{cid}::alice@acmedental.com")
    assert lead["status"] == "AI_GENERATION_FAILED"
    assert stub_send.calls == []


# ── attachment ─────────────────────────────────────────────────────────────

async def test_attachment_roundtrip_and_public_meta_has_no_path(clean_db, svc):
    db = clean_db
    camp = await svc.create_campaign({"name": "C"})
    cid = camp["id"]
    # directory components are stripped (basename) — traversal is impossible
    meta = await svc.set_attachment(cid, "../../etc/Proposal 2026.pdf", b"%PDF-1.4 fake")
    assert meta == {"filename": "Proposal 2026.pdf", "size": 13,
                    "mime": "application/pdf", "present": True}

    pub = await svc.get_campaign_public(cid)
    assert pub["attachment"] == {"filename": "Proposal 2026.pdf", "size": 13,
                                 "mime": "application/pdf", "present": True}
    assert "attachment_path" not in pub

    raw = await db.get_email_campaign(cid)
    assert raw["attachment_path"] and raw["attachment_path"].endswith(".pdf")


async def test_attachment_rejects_disallowed_type(clean_db, svc):
    from backend.email_campaigns.attachments import AttachmentError
    camp = await svc.create_campaign({"name": "C"})
    with pytest.raises(AttachmentError):
        await svc.set_attachment(camp["id"], "payload.exe", b"MZ...")


async def test_attachment_is_sent_with_the_email(clean_db, svc, stub_ai, stub_send):
    db = clean_db
    await _enable(db)
    camp = await svc.create_campaign({"name": "C"})
    cid = camp["id"]
    await svc.set_attachment(cid, "deck.pdf", b"%PDF-1.4 body")
    await svc.import_leads(cid, "l.csv", b"email,company\nalice@acmedental.com,Acme\n")
    await svc.mark_ready(cid)
    await svc.start(cid, "run-1", background=False)

    assert len(stub_send.calls) == 1
    atts = stub_send.calls[0]["attachments"]
    assert atts and atts[0]["filename"] == "deck.pdf" and atts[0]["content"] == b"%PDF-1.4 body"


# ── lifecycle ──────────────────────────────────────────────────────────────

async def test_mark_ready_requires_valid_leads(clean_db, svc):
    from backend.email_campaigns.service import EmailCampaignError
    camp = await svc.create_campaign({"name": "C"})
    with pytest.raises(EmailCampaignError):
        await svc.mark_ready(camp["id"])


async def test_start_rejected_from_draft(clean_db, svc, stub_ai, stub_send):
    db = clean_db
    await _enable(db)
    from backend.email_campaigns.service import EmailCampaignError
    camp = await svc.create_campaign({"name": "C"})
    await svc.import_leads(camp["id"], "l.csv", b"email\nx@y.com\n")
    with pytest.raises(EmailCampaignError):
        await svc.start(camp["id"], "k", background=False)  # still DRAFT


async def test_start_requires_feature_flag(clean_db, svc, stub_ai, stub_send):
    from backend.email_campaigns.service import EmailCampaignError
    camp = await svc.create_campaign({"name": "C"})
    await svc.import_leads(camp["id"], "l.csv", b"email\nx@y.com\n")
    await svc.mark_ready(camp["id"])
    with pytest.raises(EmailCampaignError):
        await svc.start(camp["id"], "k", background=False)


async def test_full_run_completes(clean_db, svc, stub_ai, stub_send):
    db = clean_db
    await _enable(db)
    camp = await svc.create_campaign({"name": "C", "test_recipient": "owner@popupgenix.io"})
    cid = camp["id"]
    await svc.import_leads(cid, "l.csv", CSV.encode("utf-8"))
    await svc.mark_ready(cid)
    await svc.start(cid, "run-1", background=False)

    fresh = await db.get_email_campaign(cid)
    assert fresh["status"] == "COMPLETED"
    assert fresh["completed_at"] is not None
    # only the 2 VALIDATED leads were sent
    assert len(stub_send.calls) == 2
    assert set(stub_send.recipients) == {"owner@popupgenix.io"}


# ── idempotency ────────────────────────────────────────────────────────────

async def test_campaign_start_is_idempotent_on_key(clean_db, svc, stub_ai, stub_send):
    db = clean_db
    await _enable(db)
    camp = await svc.create_campaign({"name": "C"})
    cid = camp["id"]
    await svc.import_leads(cid, "l.csv", b"email,company\nalice@acmedental.com,Acme\n")
    await svc.mark_ready(cid)

    r1 = await svc.start(cid, "same-key", background=False)
    assert len(stub_send.calls) == 1
    assert len(await db.list_email_campaign_runs(cid)) == 1

    # a retry of the same start command while the campaign is still RUNNING must
    # return the same run and NOT launch a second send loop
    await db.update_email_campaign(cid, {"status": "RUNNING"})
    r2 = await svc.start(cid, "same-key", background=False)
    assert r2["id"] == r1["id"]
    assert len(stub_send.calls) == 1                       # not resent
    assert len(await db.list_email_campaign_runs(cid)) == 1  # no duplicate run


async def test_lead_level_idempotency_skips_already_sent(clean_db, svc, stub_ai, stub_send):
    db = clean_db
    await _enable(db)
    camp = await svc.create_campaign({"name": "C"})
    cid = camp["id"]
    await svc.import_leads(cid, "l.csv", b"email,company\nalice@acmedental.com,Acme\n")
    await svc.mark_ready(cid)
    run = await db.create_email_campaign_run(cid, "k")
    await db.update_email_campaign(cid, {"status": "RUNNING"})

    await svc.run_batch(cid, run["id"])
    await db.update_email_campaign(cid, {"status": "RUNNING"})
    await svc.run_batch(cid, run["id"])          # second pass
    assert len(stub_send.calls) == 1


# ── pause / resume ─────────────────────────────────────────────────────────

async def test_pause_stops_the_send_loop(clean_db, svc, stub_ai, stub_send):
    db = clean_db
    await _enable(db)
    camp = await svc.create_campaign({"name": "C"})
    cid = camp["id"]
    await svc.import_leads(cid, "l.csv", CSV.encode("utf-8"))
    await svc.mark_ready(cid)
    run = await db.create_email_campaign_run(cid, "k")
    await db.update_email_campaign(cid, {"status": "PAUSED"})

    await svc.run_batch(cid, run["id"])   # campaign not RUNNING -> no work
    assert stub_send.calls == []
    events = [a["event"] for a in await svc.get_activity(cid)]
    assert "batch_skipped" in events


async def test_resume_after_pause_sends_remaining_leads(clean_db, svc, stub_ai, stub_send):
    db = clean_db
    await _enable(db)
    camp = await svc.create_campaign({"name": "C", "test_recipient": "owner@popupgenix.io"})
    cid = camp["id"]
    await svc.import_leads(cid, "l.csv", b"email,company\na@acme.com,A\nb@brs.com,B\n")
    await svc.mark_ready(cid)

    # start, then pause before any batch runs
    await db.create_email_campaign_run(cid, "run-1")
    await svc._transition(cid, "RUNNING", detail="manual")
    paused = await svc.pause(cid)
    assert paused["status"] == "PAUSED"
    assert stub_send.calls == []

    # resume -> the loop sends both leads
    await svc.resume(cid, "run-2", background=False)
    assert set(stub_send.recipients) == {"owner@popupgenix.io"}
    assert len(stub_send.calls) == 2
    assert (await db.get_email_campaign(cid))["status"] == "COMPLETED"


# ── statistics / activity ──────────────────────────────────────────────────

async def test_stats_come_from_the_db(clean_db, svc, stub_ai, stub_send):
    db = clean_db
    await _enable(db)
    camp = await svc.create_campaign({"name": "C", "test_recipient": "owner@popupgenix.io"})
    cid = camp["id"]
    await svc.import_leads(cid, "l.csv", CSV.encode("utf-8"))
    await svc.mark_ready(cid)
    await svc.start(cid, "run-1", background=False)

    stats = await svc.get_stats(cid)
    assert stats["status"] == "COMPLETED"
    assert stats["test_mode"] is True
    assert stats["totals"]["sent"] == 2
    assert stats["by_lead_status"].get("MISSING_EMAIL") == 1
    assert stats["runs"] == 1


async def test_activity_is_logged_without_secrets(clean_db, svc, stub_ai, stub_send):
    db = clean_db
    await _enable(db)
    camp = await svc.create_campaign({"name": "C", "test_recipient": "owner@popupgenix.io"})
    cid = camp["id"]
    await svc.import_leads(cid, "l.csv", b"email,company\nalice@acmedental.com,Acme\n")
    await svc.mark_ready(cid)
    await svc.start(cid, "run-1", background=False)

    events = [a["event"] for a in await svc.get_activity(cid)]
    assert "campaign_created" in events
    assert "leads_imported" in events
    assert "email_sent" in events
    assert "campaign_completed" in events
    blob = json.dumps(await svc.get_activity(cid))
    assert "pw" not in blob and "password" not in blob.lower()


# ── DO_NOT_CONTACT ─────────────────────────────────────────────────────────

async def test_do_not_contact_global_lead_is_never_sent(clean_db, svc, stub_ai, stub_send):
    db = clean_db
    await _enable(db)
    gid = await db.create_lead({"business_name": "Opted Out", "email": "stop@optout.com",
                                "niche": "x", "city": "y"})
    await db.update_lead(gid, {"status": "DO_NOT_CONTACT"})

    camp = await svc.create_campaign({"name": "C", "test_recipient": "owner@popupgenix.io"})
    cid = camp["id"]
    await db.bulk_insert_email_campaign_leads(cid, [{
        "lead_key": f"{cid}::stop@optout.com", "lead_id": gid, "email": "stop@optout.com",
        "raw": {}, "status": "VALIDATED",
    }])
    await db.recount_email_campaign(cid)
    await svc.mark_ready(cid)
    await svc.start(cid, "run-1", background=False)

    lead = await db.get_email_campaign_lead(cid, f"{cid}::stop@optout.com")
    assert lead["status"] == "DO_NOT_CONTACT"
    assert stub_send.calls == []


# ── THE SAFETY GATE ────────────────────────────────────────────────────────

class TestSafetyGate:
    async def test_blocks_missing_test_mode(self):
        from backend.email_campaigns.safety import assert_send_allowed, SafetyGateError
        with pytest.raises(SafetyGateError):
            assert_send_allowed({"test_recipient": "relay@popupgenix.io"}, "relay@popupgenix.io", "lead@acmedental.com")

    async def test_blocks_production_mode(self):
        from backend.email_campaigns.safety import assert_send_allowed, SafetyGateError
        with pytest.raises(SafetyGateError):
            assert_send_allowed(
                {"test_mode": 0, "test_recipient": "relay@popupgenix.io"}, "lead@acmedental.com", "lead@acmedental.com"
            )

    async def test_blocks_when_send_to_is_not_the_test_recipient(self):
        from backend.email_campaigns.safety import assert_send_allowed, SafetyGateError
        camp = {"test_mode": 1, "test_recipient": "owner@popupgenix.io"}
        with pytest.raises(SafetyGateError):
            assert_send_allowed(camp, "prospect@acmedental.com", "prospect@acmedental.com")

    async def test_blocks_when_send_to_equals_lead_email(self):
        from backend.email_campaigns.safety import assert_send_allowed, SafetyGateError
        camp = {"test_mode": 1, "test_recipient": "victim@acme.com"}
        with pytest.raises(SafetyGateError):
            assert_send_allowed(camp, "victim@acme.com", "victim@acme.com")

    async def test_allows_the_one_safe_case(self):
        from backend.email_campaigns.safety import assert_send_allowed
        camp = {"test_mode": 1, "test_recipient": "owner@popupgenix.io"}
        assert_send_allowed(camp, "owner@popupgenix.io", "prospect@acmedental.com")  # no raise


async def test_gate_blocks_a_lead_whose_email_is_the_test_recipient(clean_db, svc, stub_ai, stub_send):
    """If a real prospect's address happens to equal the test recipient, that
    lead must be SEND_BLOCKED — the redirect can't be proven to have applied."""
    db = clean_db
    await _enable(db)
    camp = await svc.create_campaign({"name": "C", "test_recipient": "victim@acmedental.com"})
    cid = camp["id"]
    await svc.import_leads(
        cid, "l.csv",
        b"email,company\nvictim@acmedental.com,Acme\nother@acmedental.com,Acme2\n",
    )
    await svc.mark_ready(cid)
    await svc.start(cid, "run-1", background=False)

    victim = await db.get_email_campaign_lead(cid, f"{cid}::victim@acmedental.com")
    other = await db.get_email_campaign_lead(cid, f"{cid}::other@acmedental.com")
    assert victim["status"] == "SEND_BLOCKED"
    assert other["status"] == "SENT"
    assert stub_send.recipients == ["victim@acmedental.com"]  # only the 'other' lead


async def test_test_mode_never_sends_to_real_prospect(clean_db, svc, stub_ai, stub_send):
    """CRITICAL REGRESSION TEST.

    A campaign full of real prospect addresses, in TEST_MODE, must result in
    `email_sender.send_email` being called ONLY with the configured test
    recipient — never with any prospect's real address. If someone later
    removes or bypasses the safety gate / redirect, this test fails.
    """
    db = clean_db
    await _enable(db)
    prospects = ["alice@acmedental.com", "bob@brightsmile.com", "carol@happyteeth.com"]
    test_recipient = "shahedalfahad20@gmail.com"

    camp = await svc.create_campaign({"name": "Real Prospects", "test_recipient": test_recipient})
    cid = camp["id"]
    csv = "email,company\n" + "\n".join(f"{p},Co{i}" for i, p in enumerate(prospects)) + "\n"
    await svc.import_leads(cid, "prospects.csv", csv.encode("utf-8"))
    await svc.mark_ready(cid)
    await svc.start(cid, "run-1", background=False)

    # the sender ran...
    assert len(stub_send.calls) == 3
    # ...and EVERY recipient was the test address, NEVER a prospect
    assert set(stub_send.recipients) == {test_recipient}
    for p in prospects:
        assert p not in stub_send.recipients

    # every lead is recorded SENT, with the real prospect email preserved on the row
    for p in prospects:
        lead = await db.get_email_campaign_lead(cid, f"{cid}::{p}")
        assert lead["status"] == "SENT"
        assert lead["email"] == p           # original address preserved, not overwritten

    # and the gate itself refuses a direct bypass attempt
    from backend.email_campaigns.safety import assert_send_allowed, SafetyGateError
    fresh = await db.get_email_campaign(cid)
    for p in prospects:
        with pytest.raises(SafetyGateError):
            assert_send_allowed(fresh, p, p)
