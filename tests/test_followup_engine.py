import pytest

from backend import followup_engine

pytestmark = pytest.mark.asyncio


class _RecordingSender:
    def __init__(self):
        self.calls = []

    async def send_followup_email(self, payload):
        self.calls.append(dict(payload))

    async def send_followup_whatsapp(self, payload):
        self.calls.append(dict(payload))


async def _lead_with_evidence(db, **overrides):
    base = {"business_name": "Acme Dental", "email": "a@acme.co", "status": "SENT", "channel": "EMAIL"}
    base.update(overrides)
    lead_id = await db.create_lead(base)
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    await db.replace_pain_points(profile_id, [{"title": "No booking system", "confidence": 0.8}])
    return lead_id


async def test_grounded_followup_used_when_evidence_chain_exists(clean_db, monkeypatch):
    from backend.intelligence import followup_agent as fa_module

    async def no_llm(*args, **kwargs):
        return None
    monkeypatch.setattr(fa_module, "_generate_fragments_llm", no_llm)

    db = clean_db
    lead_id = await _lead_with_evidence(db)
    await db.create_message({
        "lead_id": lead_id, "sequence_step": 2, "message_type": "followup",
        "status": "PENDING", "scheduled_for": "2000-01-01 00:00:00",
    })

    sender = _RecordingSender()
    monkeypatch.setattr(followup_engine, "email_sender", sender)

    results = await followup_engine.process_followup_queue()

    assert results["sent"] == 1
    assert len(sender.calls) == 1
    assert "Following up" in sender.calls[0]["ai_email_subject"]


async def test_step_2_and_step_3_never_send_identical_content(clean_db, monkeypatch):
    from backend.intelligence import followup_agent as fa_module

    async def no_llm(*args, **kwargs):
        return None
    monkeypatch.setattr(fa_module, "_generate_fragments_llm", no_llm)

    db = clean_db
    lead_id = await _lead_with_evidence(db)
    await db.create_message({
        "lead_id": lead_id, "sequence_step": 2, "message_type": "followup",
        "status": "PENDING", "scheduled_for": "2000-01-01 00:00:00",
    })

    sender = _RecordingSender()
    monkeypatch.setattr(followup_engine, "email_sender", sender)
    await followup_engine.process_followup_queue()
    step2_body = (await db.get_messages(lead_id))[0]["body"]

    await db.create_message({
        "lead_id": lead_id, "sequence_step": 3, "message_type": "followup",
        "status": "PENDING", "scheduled_for": "2000-01-01 00:00:00",
    })
    await followup_engine.process_followup_queue()
    step3_body = [m for m in await db.get_messages(lead_id) if m["sequence_step"] == 3][0]["body"]

    assert step2_body != step3_body


async def test_falls_back_to_legacy_body_when_no_evidence_chain(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({
        "business_name": "No Evidence Co", "email": "b@company.com", "status": "SENT", "channel": "EMAIL",
        "ai_follow_up_1": "Legacy pre-written follow-up body.",
    })
    await db.create_message({
        "lead_id": lead_id, "sequence_step": 2, "message_type": "followup",
        "status": "PENDING", "scheduled_for": "2000-01-01 00:00:00",
    })

    sender = _RecordingSender()
    monkeypatch.setattr(followup_engine, "email_sender", sender)

    results = await followup_engine.process_followup_queue()

    assert results["sent"] == 1
    assert sender.calls[0]["ai_email_body"] == "Legacy pre-written follow-up body."


async def test_replied_lead_still_cancelled_not_sent_regression(clean_db, monkeypatch):
    db = clean_db
    lead_id = await _lead_with_evidence(db, status="REPLIED")
    await db.create_message({
        "lead_id": lead_id, "sequence_step": 2, "message_type": "followup",
        "status": "PENDING", "scheduled_for": "2000-01-01 00:00:00",
    })
    sender = _RecordingSender()
    monkeypatch.setattr(followup_engine, "email_sender", sender)

    results = await followup_engine.process_followup_queue()

    assert results["cancelled"] == 1
    assert results["sent"] == 0
    assert sender.calls == []
