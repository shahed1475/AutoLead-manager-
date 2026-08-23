# Phase 4 — Intelligent Outreach + Reply + Follow-Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the already-built Marketing Agent + Reply Intelligence Agent to the existing send/reply/follow-up infrastructure so that (a) an opted-out or stopped lead can never be contacted again by any send path, and (b) automated follow-ups are grounded in the same pain-point evidence chain as the initial message and never repeat themselves.

**Architecture:** No new systems. `backend/intelligence/marketing_agent.py` (outbound drafts) and `backend/intelligence/reply_intelligence_agent.py` (reply classification + next-best-action) already exist and are unit-tested but are only partially wired into `reply_detector.py`, `followup_engine.py`, and `routers/campaigns.py`. This plan (1) closes the safety gap where `DO_NOT_CONTACT`/`STOP_CAMPAIGN` set a status but nothing actually stops a send, and (2) adds a new `FollowUpAgent` (same structural-guarantee pattern as `MarketingAgent`) that generates follow-up content grounded in the evidence chain, wired into the existing `followup_engine.py` send loop in place of its evidence-blind fallback path.

**Tech Stack:** Python 3 / FastAPI / aiosqlite (existing), pytest + pytest-asyncio (existing), no new dependencies.

## Global Constraints

- Do not rebuild Gmail sending (`email_sender.py`), WhatsApp automation (`whatsapp_sender.py`), reply detection (`reply_detector.py`'s IMAP/classification core), the follow-up engine's scheduling primitives, or campaign scheduling (`scheduler.py`) — only add guards/wiring around them.
- Every new agent module follows this codebase's existing convention (see `marketing_agent.py`, `reply_intelligence_agent.py`): heuristic (non-LLM) path is always computed and used whenever the LLM is unavailable, empty, or fails a safety check; never raises out of `.run()`; imports shared helpers by name (not module attribute access) so tests can monkeypatch them.
- No automated test may perform a real network send (SMTP/IMAP/WhatsApp automation/HTTP to Ollama) — every sender/LLM call in a test is monkeypatched.
- Never downgrade or resurrect a lead whose status is `SENT`, `REPLIED`, `SKIPPED`, or `DO_NOT_CONTACT` except where a task explicitly says otherwise (opt-out is the one status transition allowed to override a locked status).
- `DO_NOT_CONTACT` is sticky and must block every existing send path (initial send, manual resend, manual follow-up endpoint, automated follow-up engine) — checked at send time using freshly-fetched lead status, not a possibly-stale in-memory copy.

---

### Task 1: Opt-out & STOP_CAMPAIGN follow-up cancellation wiring

**Files:**
- Modify: `backend/reply_detector.py:398-435`
- Test: `tests/test_reply_detector.py` (new)

**Interfaces:**
- Consumes: `db.cancel_pending_followups(lead_id) -> int` (already exists, `backend/database.py:2026`), `ReplyIntelligenceAgent.run(...) -> AgentResult` with `data["intent"]`, `data["recommended_action"]` (already exists).
- Produces: no new public functions — this task only changes what `check_for_replies()` does with an already-computed `recommended_action`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_reply_detector.py
import pytest

from backend import reply_detector

pytestmark = pytest.mark.asyncio


def _imap_message(from_email="lead@example.com", subject="Re: hello", body="STOP"):
    return {
        "from_email": from_email, "from_header": from_email, "subject": subject,
        "body_text": body, "received_at": "Mon, 1 Jan 2026 00:00:00 +0000",
    }


def _patch_imap(monkeypatch, messages):
    async def fake_to_thread(func, *args, **kwargs):
        return messages
    monkeypatch.setattr(reply_detector.asyncio, "to_thread", fake_to_thread)


def _patch_legacy_intent(monkeypatch, intent="unknown"):
    async def fake_classify_intent(*args, **kwargs):
        return intent
    monkeypatch.setattr(reply_detector, "_classify_intent", fake_classify_intent)


def _patch_rich_intent(monkeypatch, intent, action, confidence=0.9, draft=None):
    async def fake_run(self, reply_text, lead, original_message=None, previous_replies=None, campaign=None):
        from backend.intelligence.base import AgentResult
        return AgentResult(
            status="ok",
            data={"intent": intent, "confidence": confidence, "recommended_action": action, "draft_response": draft},
            evidence=[], confidence=confidence,
        )
    monkeypatch.setattr(reply_detector.ReplyIntelligenceAgent, "run", fake_run)


_CONFIG = {
    "imap_host": "imap.example.com", "imap_username": "u", "imap_password": "p",
    "imap_port": "993", "imap_ssl": "true", "imap_since_days": 7,
}


async def test_opt_out_cancels_pending_followups(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Opt Out Co", "email": "lead@example.com", "status": "SENT"})
    await db.create_message({"lead_id": lead_id, "sequence_step": 2, "message_type": "followup", "status": "PENDING"})
    await db.create_message({"lead_id": lead_id, "sequence_step": 3, "message_type": "followup", "status": "PENDING"})

    _patch_imap(monkeypatch, [_imap_message(body="STOP")])
    _patch_legacy_intent(monkeypatch, "unknown")
    _patch_rich_intent(monkeypatch, "OPT_OUT", "SUPPRESS_OUTREACH", confidence=1.0)

    await reply_detector.check_for_replies(_CONFIG)

    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] == "DO_NOT_CONTACT"
    pending = await db.count_pending_followups()
    assert pending == 0


async def test_wrong_contact_stops_campaign_and_advances_status(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Wrong Contact Co", "email": "lead@example.com", "status": "SENT"})
    await db.create_message({"lead_id": lead_id, "sequence_step": 2, "message_type": "followup", "status": "PENDING"})

    _patch_imap(monkeypatch, [_imap_message(body="Wrong person, I no longer work here")])
    _patch_legacy_intent(monkeypatch, "unknown")
    _patch_rich_intent(monkeypatch, "WRONG_CONTACT", "STOP_CAMPAIGN")

    await reply_detector.check_for_replies(_CONFIG)

    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] == "SKIPPED"
    assert await db.count_pending_followups() == 0


async def test_not_interested_stop_campaign_does_not_downgrade_replied(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Already Replied Co", "email": "lead@example.com", "status": "REPLIED"})
    await db.create_message({"lead_id": lead_id, "sequence_step": 2, "message_type": "followup", "status": "PENDING"})

    _patch_imap(monkeypatch, [_imap_message(body="Not interested, thanks")])
    _patch_legacy_intent(monkeypatch, "not_interested")
    _patch_rich_intent(monkeypatch, "NOT_INTERESTED", "STOP_CAMPAIGN")

    await reply_detector.check_for_replies(_CONFIG)

    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] == "REPLIED"  # locked status not downgraded to SKIPPED
    assert await db.count_pending_followups() == 0  # but follow-ups are still cancelled


async def test_interested_reply_does_not_cancel_followups(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Interested Co", "email": "lead@example.com", "status": "SENT"})
    await db.create_message({"lead_id": lead_id, "sequence_step": 2, "message_type": "followup", "status": "PENDING"})

    _patch_imap(monkeypatch, [_imap_message(body="Sounds interesting, tell me more")])
    _patch_legacy_intent(monkeypatch, "interested")
    _patch_rich_intent(monkeypatch, "INTERESTED", "SCHEDULE_MEETING", draft="Happy to share more.")

    await reply_detector.check_for_replies(_CONFIG)

    assert await db.count_pending_followups() == 1
    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] == "REPLIED"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_reply_detector.py -v`
Expected: `test_opt_out_cancels_pending_followups` and the `WRONG_CONTACT`/`NOT_INTERESTED` tests FAIL — pending follow-ups remain 1/2 instead of 0, because `check_for_replies` never calls `cancel_pending_followups` today.

- [ ] **Step 3: Wire cancellation into `check_for_replies`**

In `backend/reply_detector.py`, replace the block at lines 424-435 (starting at the comment `# OPT_OUT is a hard safety override`):

```python
                # OPT_OUT is a hard safety override — always honored, even over a
                # locked REPLIED/SKIPPED status, and suppresses all future outreach.
                action = rich["recommended_action"]
                if rich["intent"] == "OPT_OUT":
                    await db.update_lead(lead_id, {"status": "DO_NOT_CONTACT"})
                    cancelled = await db.cancel_pending_followups(lead_id)
                    await _log(
                        f"Reply detector: {biz} → marked DO_NOT_CONTACT (opt-out detected), "
                        f"{cancelled} pending follow-up(s) cancelled"
                    )
                elif action == "STOP_CAMPAIGN":
                    cancelled = await db.cancel_pending_followups(lead_id)
                    current_status = (lead.get("status") or "").upper()
                    if current_status not in _LOCKED_STATUSES and current_status != "DO_NOT_CONTACT":
                        await db.update_lead(lead_id, {"status": "SKIPPED"})
                    await _log(
                        f"Reply detector: {biz} → campaign stopped ({rich['intent']}), "
                        f"{cancelled} pending follow-up(s) cancelled"
                    )
                elif rich["draft_response"]:
                    draft_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}" if subject else "Re: your message"
                    await db.set_reply_draft(reply_id, draft_subject, rich["draft_response"])
                    await _log(f"Reply detector: {biz} → reply draft queued for approval")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_reply_detector.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/reply_detector.py tests/test_reply_detector.py
git commit -m "feat: cancel pending follow-ups on opt-out and stop-campaign replies"
```

---

### Task 2: Send-time DO_NOT_CONTACT guards

**Files:**
- Modify: `backend/routers/campaigns.py:63-76` (`_send_one`), `backend/routers/campaigns.py:511-521` (`send_followup`)
- Modify: `backend/followup_engine.py:40` (`_TERMINAL_STATUSES`)
- Modify: `backend/routers/marketing.py:26,36-38` (`_NO_STATUS_ADVANCE`, `_assert_not_opted_out`)
- Test: `tests/test_send_guards.py` (new)

**Interfaces:**
- Consumes: `db.get_lead_by_id`, `db.log_campaign_action` (existing).
- Produces: no new public functions — pure guard additions.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_send_guards.py
import pytest

from backend.routers import campaigns as campaigns_router
from backend.routers import marketing as marketing_router
from backend import followup_engine

pytestmark = pytest.mark.asyncio


class _NeverCalled:
    def __getattr__(self, name):
        async def _fail(*args, **kwargs):
            raise AssertionError(f"sender.{name} must not be called for a DO_NOT_CONTACT lead")
        return _fail


async def test_send_one_blocks_do_not_contact_lead(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({
        "business_name": "Blocked Co", "email": "x@example.com", "status": "DO_NOT_CONTACT",
    })
    monkeypatch.setattr(campaigns_router, "email_sender", _NeverCalled())
    monkeypatch.setattr(campaigns_router, "whatsapp_sender", _NeverCalled())

    result = await campaigns_router._send_one(lead_id, "EMAIL")

    assert result["success"] is False
    assert "DO_NOT_CONTACT" in result["error"]


async def test_send_followup_endpoint_rejects_do_not_contact_lead(clean_db, monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from backend.main import app

    db = clean_db
    lead_id = await db.create_lead({
        "business_name": "Blocked Co 2", "email": "x@example.com", "status": "DO_NOT_CONTACT",
    })
    monkeypatch.setattr(campaigns_router, "email_sender", _NeverCalled())
    monkeypatch.setattr(campaigns_router, "whatsapp_sender", _NeverCalled())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/campaign/send-followup/{lead_id}")

    assert resp.status_code == 400


async def test_followup_engine_cancels_do_not_contact_lead_instead_of_sending(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({
        "business_name": "Blocked Co 3", "email": "x@example.com", "status": "DO_NOT_CONTACT",
    })
    msg_id = await db.create_message({
        "lead_id": lead_id, "sequence_step": 2, "message_type": "followup",
        "status": "PENDING", "body": "old body", "scheduled_for": "2000-01-01 00:00:00",
    })
    monkeypatch.setattr(followup_engine, "email_sender", _NeverCalled())
    monkeypatch.setattr(followup_engine, "whatsapp_sender", _NeverCalled())

    results = await followup_engine.process_followup_queue()

    assert results["cancelled"] == 1
    assert results["sent"] == 0
    msg = (await db.get_messages(lead_id))[0]
    assert msg["status"] == "CANCELLED"


async def test_marketing_router_blocks_do_not_contact_lead(clean_db):
    from fastapi import HTTPException
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Blocked Co 4", "status": "DO_NOT_CONTACT"})
    lead = await db.get_lead_by_id(lead_id)

    with pytest.raises(HTTPException):
        marketing_router._assert_not_opted_out(lead)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_send_guards.py -v`
Expected: all 4 tests FAIL — `_send_one` currently calls `email_sender.send_email_lead` (raising `AssertionError` from `_NeverCalled`), the follow-up endpoint returns 200 instead of 400, `process_followup_queue` sends instead of cancelling, and `_assert_not_opted_out` does not raise for `DO_NOT_CONTACT`.

- [ ] **Step 3: Add the guards**

In `backend/routers/campaigns.py`, in `_send_one` (currently lines 69-76), insert the guard right after `channel = channel.upper()`:

```python
    lead = await db.get_lead_by_id(lead_id)
    if not lead:
        return {"lead_id": lead_id, "success": False, "error": "Lead not found"}

    channel = channel.upper()

    if (lead.get("status") or "").upper() == "DO_NOT_CONTACT":
        msg = f"Lead {lead_id} is marked DO_NOT_CONTACT — send blocked"
        await db.log_campaign_action(lead_id, channel, "SEND", False, msg)
        return {"lead_id": lead_id, "success": False, "error": msg}

    has_email  = bool(lead.get("email"))
```

In `send_followup` (currently starting line 511), insert right after the lead-not-found check:

```python
async def send_followup(request: Request, lead_id: int, channel: str = Query("EMAIL")):
    lead = await db.get_lead_by_id(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    if (lead.get("status") or "").upper() == "DO_NOT_CONTACT":
        raise HTTPException(400, "Lead is marked DO_NOT_CONTACT — cannot send follow-up")

    channel  = channel.upper()
```

In `backend/followup_engine.py`, line 40:

```python
_TERMINAL_STATUSES = frozenset({"REPLIED", "SKIPPED", "DO_NOT_CONTACT"})
```

In `backend/routers/marketing.py`, line 26 and lines 36-38:

```python
_NO_STATUS_ADVANCE = frozenset({"SENT", "REPLIED", "SKIPPED", "DO_NOT_CONTACT"})
```

```python
def _assert_not_opted_out(lead: dict) -> None:
    if (lead.get("status") or "").upper() in ("SKIPPED", "DO_NOT_CONTACT"):
        raise HTTPException(400, "Lead has opted out — cannot queue or approve marketing messages")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_send_guards.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Run the full existing test suite for regressions**

Run: `python -m pytest tests/ -v`
Expected: all previously-passing tests still PASS (in particular `tests/test_marketing_router.py::test_approve_400_when_lead_opted_out` and everything in `tests/intelligence/`).

- [ ] **Step 6: Commit**

```bash
git add backend/routers/campaigns.py backend/routers/marketing.py backend/followup_engine.py tests/test_send_guards.py
git commit -m "fix: block every send path for DO_NOT_CONTACT leads"
```

---

### Task 3: Follow-Up Intelligence Agent

**Files:**
- Create: `backend/intelligence/followup_agent.py`
- Test: `tests/intelligence/test_followup_agent.py` (new)

**Interfaces:**
- Consumes: `marketing_agent._heuristic_fragments`, `marketing_agent._generate_fragments_llm`, `marketing_agent._assemble_email`, `marketing_agent._assemble_whatsapp`, `marketing_agent._build_business_impact`, `marketing_agent._business_benefit_text`, `marketing_agent._solution_description`, `marketing_agent._pick_opportunity_for`, `marketing_agent._pick_solution_for`, `marketing_agent._strip_period` (all already exist, module-level, `backend/intelligence/marketing_agent.py`); `opportunity_agent._strongest`; `service_knowledge_base.get_service`.
- Produces: `FollowUpAgent.run(lead, pain_points, opportunities, solutions, step, previous_bodies=None, latest_reply_intent=None) -> AgentResult` where `result.data` is either `{"generated": False}` or `{"generated": True, "step": int, "subject": str, "email_body": str, "whatsapp_body": str, "service_name": Optional[str], "pain_point": str}`. Consumed by Task 4's `orchestrator.run_followup_agent`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/intelligence/test_followup_agent.py
import pytest

from backend.intelligence import followup_agent as fa_module
from backend.intelligence.followup_agent import FollowUpAgent

pytestmark = pytest.mark.asyncio


def _pain_point(**overrides):
    base = {
        "id": 1, "title": "No visible online appointment/booking system",
        "evidence_snippet": "cta_buttons=[]", "source_url": "https://acmedental.co",
        "confidence": 0.85, "operational_impact": "Staff handle scheduling manually.",
        "customer_impact": "Customers must call to book.",
    }
    base.update(overrides)
    return base


def _opportunity(**overrides):
    base = {"id": 10, "pain_point_id": 1, "confidence": 0.85}
    base.update(overrides)
    return base


def _solution(**overrides):
    base = {"id": 20, "business_opportunity_id": 10, "service_name": "WhatsApp Automation", "confidence": 0.8}
    base.update(overrides)
    return base


async def _no_llm(*args, **kwargs):
    return None


def _patch_no_llm(monkeypatch):
    monkeypatch.setattr(fa_module, "_generate_fragments_llm", _no_llm)


async def test_no_pain_points_returns_not_generated():
    agent = FollowUpAgent()
    result = await agent.run({"id": 1, "business_name": "No Data Co"}, [], [], [], step=2)
    assert result.data == {"generated": False}


async def test_step_2_and_step_3_use_different_openings(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = FollowUpAgent()
    lead = {"id": 1, "business_name": "Acme Dental"}

    r2 = await agent.run(lead, [_pain_point()], [_opportunity()], [_solution()], step=2)
    r3 = await agent.run(lead, [_pain_point()], [_opportunity()], [_solution()], step=3)

    assert r2.data["generated"] and r3.data["generated"]
    opening2 = r2.data["email_body"].split("\n")[0]
    opening3 = r3.data["email_body"].split("\n")[0]
    assert opening2 != opening3


async def test_never_repeats_a_previous_body_heuristic_path(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = FollowUpAgent()
    lead = {"id": 1, "business_name": "Acme Dental"}

    first = await agent.run(lead, [_pain_point()], [_opportunity()], [_solution()], step=2)
    second = await agent.run(
        lead, [_pain_point()], [_opportunity()], [_solution()], step=3,
        previous_bodies=[first.data["email_body"]],
    )
    assert second.data["email_body"] != first.data["email_body"]


async def test_llm_output_colliding_with_previous_body_falls_back_to_heuristic(monkeypatch):
    async def fake_llm(*args, **kwargs):
        return {"opening": "Repeat opening.", "solution_benefit": "Same benefit.", "cta": "Same CTA?"}
    monkeypatch.setattr(fa_module, "_generate_fragments_llm", fake_llm)

    agent = FollowUpAgent()
    lead = {"id": 1, "business_name": "Acme Dental"}
    previous_body = "Repeat opening.\n\nSame benefit.\n\nSame CTA?"

    result = await agent.run(
        lead, [_pain_point()], [_opportunity()], [_solution()], step=2,
        previous_bodies=[previous_body],
    )
    assert result.data["email_body"] != previous_body


async def test_maybe_later_intent_uses_gentler_angle(monkeypatch):
    captured = {}

    async def fake_llm(pain_point, evidence_snippet, business_impact, solution_desc, benefit_text, business_name, angle_instruction, service_name):
        captured["angle"] = angle_instruction
        return None

    monkeypatch.setattr(fa_module, "_generate_fragments_llm", fake_llm)

    agent = FollowUpAgent()
    lead = {"id": 1, "business_name": "Acme Dental"}
    await agent.run(
        lead, [_pain_point()], [_opportunity()], [_solution()], step=2,
        latest_reply_intent="MAYBE_LATER",
    )
    assert "later" in captured["angle"].lower()


async def test_whatsapp_body_present_and_shorter_than_email(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = FollowUpAgent()
    lead = {"id": 1, "business_name": "Acme Dental"}

    result = await agent.run(lead, [_pain_point()], [_opportunity()], [_solution()], step=2)
    assert result.data["whatsapp_body"]
    assert len(result.data["whatsapp_body"]) < len(result.data["email_body"])


async def test_no_solution_match_never_names_a_service(monkeypatch):
    _patch_no_llm(monkeypatch)
    agent = FollowUpAgent()
    lead = {"id": 1, "business_name": "Vague Co"}

    result = await agent.run(lead, [_pain_point()], [], [], step=2)
    assert result.data["service_name"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/intelligence/test_followup_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.intelligence.followup_agent'`.

- [ ] **Step 3: Implement `FollowUpAgent`**

```python
# backend/intelligence/followup_agent.py
"""
followup_agent.py — Follow-up Intelligence Agent (Phase 4).

Generates the body for scheduled follow-up steps (2 = Day-3, 3 = Day-7) from
the same evidence chain marketing_agent.py uses (pain point -> impact ->
solution), never a second identical copy of the initial message and never a
generic ai_brain.generate_all_messages() rewrite (that's the older,
evidence-blind path — see marketing_agent.py's own docstring for why this
codebase treats it as legacy fallback only, not something to build on).

Two guarantees are structural, not LLM-hoped, mirroring every other agent in
this codebase:

1. Step 2 and step 3 always use a different opening angle (a fixed
   step -> angle mapping), so two follow-ups for the same lead never open
   the same way by construction, regardless of what an LLM does with word
   choice.
2. The assembled body is checked against every previous message body
   (initial send + any earlier follow-up) by a normalized-prefix match; if
   it collides, the heuristic (always-available, non-LLM) fragments are
   used instead, which are lexically distinct per step by construction —
   never re-sends the literal previous text.

Reuses marketing_agent.py's fragment builder, renderer, and safety filters
directly rather than re-implementing them, per this codebase's "don't
rebuild, extend" convention for the sales-intelligence agents.
"""
import logging
from typing import Any, Dict, List, Optional

from .base import AgentResult, EvidenceItem
from .marketing_agent import (
    _assemble_email,
    _assemble_whatsapp,
    _build_business_impact,
    _business_benefit_text,
    _generate_fragments_llm,
    _heuristic_fragments,
    _pick_opportunity_for,
    _pick_solution_for,
    _solution_description,
    _strip_period,
)
from .opportunity_agent import _strongest
from .service_knowledge_base import get_service

logger = logging.getLogger(__name__)

# Step -> follow-up angle instruction for the LLM path. Deliberately
# different per step so two follow-ups for the same lead never open the
# same way (structural guarantee #1 above).
_STEP_ANGLE_INSTRUCTION: Dict[int, str] = {
    2: (
        "Open by referencing that you're following up on your earlier note, "
        "then add ONE new, specific observation about the pain point that "
        "was NOT in the first message — a different detail or angle, not a "
        "restatement."
    ),
    3: (
        "Open with a brief, low-pressure final check-in. Reference the "
        "customer-facing impact (not the operational one) of the pain "
        "point, and keep it short — this is the last note in the sequence."
    ),
}

_MAYBE_LATER_ANGLE_INSTRUCTION = (
    "The lead previously said they might be interested later. Open by "
    "acknowledging that gently (no pressure), then add ONE new, specific "
    "observation about the pain point they haven't heard from you before."
)

# Heuristic (non-LLM) opening templates — lexically distinct per step by
# construction, and distinct from marketing_agent's initial-outreach opening
# ("I noticed something worth mentioning about ...").
_STEP_HEURISTIC_OPENING: Dict[int, str] = {
    2: "Following up on my note about {biz} — one more thing I noticed: {pain_point}.",
    3: "Last note from me — {pain_point} still stood out when I checked back on {biz}.",
}
_MAYBE_LATER_HEURISTIC_OPENING = "No rush at all — one more thought on {biz}: {pain_point}."


def _normalize_prefix(text: str, length: int = 60) -> str:
    return "".join((text or "").lower().split())[:length]


def _collides_with_previous(candidate_body: str, previous_bodies: List[str]) -> bool:
    candidate_key = _normalize_prefix(candidate_body)
    if not candidate_key:
        return False
    return any(candidate_key == _normalize_prefix(prev) for prev in previous_bodies if prev)


class FollowUpAgent:
    name = "followup"

    async def run(
        self,
        lead: Dict[str, Any],
        pain_points: List[Dict[str, Any]],
        opportunities: List[Dict[str, Any]],
        solutions: List[Dict[str, Any]],
        step: int,
        previous_bodies: Optional[List[str]] = None,
        latest_reply_intent: Optional[str] = None,
    ) -> AgentResult:
        if not pain_points:
            return AgentResult(status="ok", data={"generated": False}, evidence=[], confidence=0.0)

        previous_bodies = previous_bodies or []
        strongest_pp = _strongest(pain_points)
        opportunity = _pick_opportunity_for(strongest_pp, opportunities or [])
        solution_rec = _pick_solution_for(opportunity, solutions or [])
        service = get_service(solution_rec["service_name"]) if solution_rec else None

        business_name = lead.get("business_name")
        pain_point_text = strongest_pp.get("title") or ""
        evidence_snippet = strongest_pp.get("evidence_snippet") or ""
        business_impact = _build_business_impact(strongest_pp)
        solution_desc = _solution_description(service)
        benefit_text = _business_benefit_text(service)
        service_name = service.name if service else None

        confidence = max(0.0, min(1.0, float(strongest_pp.get("confidence") or 0.0)))

        is_maybe_later = latest_reply_intent == "MAYBE_LATER"
        angle_instruction = _MAYBE_LATER_ANGLE_INSTRUCTION if is_maybe_later else _STEP_ANGLE_INSTRUCTION.get(step, _STEP_ANGLE_INSTRUCTION[2])

        fragments = await _generate_fragments_llm(
            pain_point_text, evidence_snippet, business_impact, solution_desc, benefit_text,
            business_name, angle_instruction, service_name,
        )
        source = "ai_inference"
        email_body: Optional[str] = None

        if fragments is not None:
            candidate = _assemble_email(fragments["opening"], fragments["solution_benefit"], fragments["cta"])
            if _collides_with_previous(candidate, previous_bodies):
                fragments = None
            else:
                email_body = candidate

        if fragments is None:
            fragments = _heuristic_fragments(pain_point_text, business_impact, solution_desc, benefit_text, business_name)
            biz = business_name or "your business"
            if is_maybe_later:
                opening = _MAYBE_LATER_HEURISTIC_OPENING.format(biz=biz, pain_point=_strip_period(pain_point_text))
            else:
                opening_template = _STEP_HEURISTIC_OPENING.get(step, _STEP_HEURISTIC_OPENING[2])
                opening = opening_template.format(biz=biz, pain_point=_strip_period(pain_point_text))
            fragments = {**fragments, "opening": opening}
            email_body = _assemble_email(fragments["opening"], fragments["solution_benefit"], fragments["cta"])
            source = "heuristic"

        whatsapp_body = _assemble_whatsapp(fragments["opening"], fragments["solution_benefit"], fragments["cta"], email_body)
        subject = f"Following up — {business_name}" if business_name else "Following up"

        evidence = [EvidenceItem(
            field_name=f"followup:step{step}",
            source_type=source,
            source_url=strongest_pp.get("source_url"),
            snippet=fragments["opening"][:200],
        )]

        return AgentResult(
            status="ok",
            data={
                "generated": True, "step": step, "subject": subject,
                "email_body": email_body, "whatsapp_body": whatsapp_body,
                "service_name": service_name, "pain_point": pain_point_text,
            },
            evidence=evidence, confidence=confidence,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/intelligence/test_followup_agent.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/intelligence/followup_agent.py tests/intelligence/test_followup_agent.py
git commit -m "feat: add Follow-up Intelligence Agent"
```

---

### Task 4: Orchestrator wrapper for the Follow-up Agent

**Files:**
- Modify: `backend/intelligence/orchestrator.py`
- Test: `tests/intelligence/test_orchestrator.py` (extend)

**Interfaces:**
- Consumes: `FollowUpAgent` (Task 3), `db.get_company_profile`, `db.get_pain_points`, `db.get_business_opportunities`, `db.get_solution_recommendations` (all already exist and are already used the same way by `run_marketing_agent`).
- Produces: `run_followup_agent(lead: dict, step: int, previous_bodies: Optional[List[str]] = None, latest_reply_intent: Optional[str] = None) -> Dict[str, Any]`, returning `{"lead_id": int, "generated": False, "reason": str}` or `{"lead_id": int, "generated": True, "step": int, "subject": str, "email_body": str, "whatsapp_body": str, "service_name": Optional[str], "pain_point": str}`. Consumed by Task 5's `followup_engine.py`.

- [ ] **Step 1: Write the failing test**

Append to `tests/intelligence/test_orchestrator.py`:

```python
async def test_run_followup_agent_no_profile_returns_not_generated(clean_db):
    from backend.intelligence.orchestrator import run_followup_agent
    db = clean_db
    lead_id = await db.create_lead({"business_name": "No Profile Co"})
    lead = await db.get_lead_by_id(lead_id)

    result = await run_followup_agent(dict(lead), step=2)
    assert result["generated"] is False
    assert result["lead_id"] == lead_id


async def test_run_followup_agent_generates_grounded_content(clean_db, monkeypatch):
    from backend.intelligence import followup_agent as fa_module
    from backend.intelligence.orchestrator import run_followup_agent

    async def no_llm(*args, **kwargs):
        return None
    monkeypatch.setattr(fa_module, "_generate_fragments_llm", no_llm)

    db = clean_db
    lead_id = await db.create_lead({"business_name": "Grounded Co"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    await db.replace_pain_points(profile_id, [{"title": "No booking system", "confidence": 0.8}])
    lead = await db.get_lead_by_id(lead_id)

    result = await run_followup_agent(dict(lead), step=2)
    assert result["generated"] is True
    assert result["email_body"]
    assert result["whatsapp_body"]


async def test_run_followup_agent_never_raises_on_bad_lead():
    from backend.intelligence.orchestrator import run_followup_agent
    result = await run_followup_agent({"id": 999999}, step=2)
    assert result["generated"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/intelligence/test_orchestrator.py -k followup_agent -v`
Expected: FAIL — `run_followup_agent` does not exist yet (`ImportError`).

- [ ] **Step 3: Implement `run_followup_agent`**

In `backend/intelligence/orchestrator.py`, add the import alongside the existing agent imports (near line 17):

```python
from .followup_agent import FollowUpAgent
from .marketing_agent import MarketingAgent
```

Add the instance alongside the existing module-level instances (near line 27):

```python
_marketing_agent = MarketingAgent()
_followup_agent = FollowUpAgent()
```

Append at the end of the file:

```python
# ─────────────────────────────────────────────────────────────────────────────
# Follow-up message generation (Phase 4) — used by followup_engine.py at
# send time instead of its pre-Phase-4 evidence-blind fallback. Returns
# {"generated": False} (never raises, never a FAILED status) when the lead
# has no completed pain-point research — callers fall back to the legacy
# pre-written column/ai_brain path in that case, same "degrade, don't error"
# convention as every other agent entry point above.
# ─────────────────────────────────────────────────────────────────────────────

async def run_followup_agent(
    lead: Dict[str, Any],
    step: int,
    previous_bodies: Optional[List[str]] = None,
    latest_reply_intent: Optional[str] = None,
) -> Dict[str, Any]:
    lead_id = None
    try:
        lead_id = lead["id"]
        profile = await db.get_company_profile(lead_id)
        if not profile:
            return {"lead_id": lead_id, "generated": False, "reason": "no completed research"}

        pain_points = await db.get_pain_points(profile["id"])
        if not pain_points:
            return {"lead_id": lead_id, "generated": False, "reason": "no pain points identified yet"}

        opportunities = await db.get_business_opportunities(profile["id"])
        solutions = await db.get_solution_recommendations(profile["id"])

        result: AgentResult = await _followup_agent.run(
            lead, pain_points, opportunities, solutions, step,
            previous_bodies=previous_bodies, latest_reply_intent=latest_reply_intent,
        )
        if result.status != "ok" or not result.data.get("generated"):
            return {"lead_id": lead_id, "generated": False, "reason": result.reason or "no pain points"}

        return {"lead_id": lead_id, **result.data}

    except Exception as exc:
        logger.error("Follow-up agent crashed for lead %s (step %s): %s", lead_id, step, exc, exc_info=True)
        return {"lead_id": lead_id, "generated": False, "reason": str(exc)}
```

`orchestrator.py` already imports `Optional` and `List` from `typing` (used by other functions in the file) and already imports `database as db` — no new imports beyond the two lines above.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/intelligence/test_orchestrator.py -v`
Expected: all tests PASS, including the 3 new ones.

- [ ] **Step 5: Commit**

```bash
git add backend/intelligence/orchestrator.py tests/intelligence/test_orchestrator.py
git commit -m "feat: wire Follow-up Intelligence Agent into the orchestrator"
```

---

### Task 5: Wire grounded follow-up generation into `followup_engine.py`

**Files:**
- Modify: `backend/followup_engine.py:141-216` (the `for msg in due:` loop body)
- Test: `tests/test_followup_engine.py` (new)

**Interfaces:**
- Consumes: `orchestrator.run_followup_agent` (Task 4), `db.get_messages`, `db.get_generated_messages`, `db.get_replies`, `db.get_recent_send_info` (all already exist).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_followup_engine.py
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
        "business_name": "No Evidence Co", "email": "b@example.co", "status": "SENT", "channel": "EMAIL",
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_followup_engine.py -v`
Expected: `test_grounded_followup_used_when_evidence_chain_exists` and `test_step_2_and_step_3_never_send_identical_content` FAIL (subject/body still come from the empty legacy columns, not the grounded agent); the other two should already pass (regression baseline) — confirming they still pass after Step 3 is what matters.

- [ ] **Step 3: Wire grounded generation into the send loop**

In `backend/followup_engine.py`, add the import near the top (alongside the existing `from . import ai_brain` etc.):

```python
from .intelligence.orchestrator import run_followup_agent
```

Replace the loop body from `for msg in due:` through the line `channel = (msg.get("channel") or "EMAIL").upper()` (currently lines 141-216) with:

```python
    for msg in due:
        msg_id      = msg["id"]
        lead_id     = msg["lead_id"]
        step        = msg["sequence_step"]
        biz         = msg.get("business_name") or f"Lead {lead_id}"
        label       = _STEP_LABEL.get(step, f"Step-{step}")
        lead_status = (msg.get("lead_status") or "").upper()
        channel     = (msg.get("channel") or "EMAIL").upper()

        results["processed"] += 1

        # ── 1. Cancel if lead no longer needs follow-ups (includes DO_NOT_CONTACT
        # — this must run before anything is sent) ─────────────────────────────
        if lead_status in _TERMINAL_STATUSES:
            await db.update_message(msg_id, {"status": "CANCELLED"})
            await _log(f"Follow-up engine: {biz} {label} → CANCELLED (lead is {lead_status})")
            results["cancelled"] += 1
            continue

        # ── 2. Resolve message body — grounded Follow-up Intelligence Agent
        # first (considers pain point, prior message, prior follow-up, and the
        # lead's latest reply intent; structurally never repeats a prior body).
        # Falls back to the legacy pre-written column when the lead has no
        # completed sales-intelligence research (no evidence chain to ground on).
        subject     = (msg.get("msg_subject") or msg.get("ai_email_subject") or "").strip()
        legacy_body = (msg.get("msg_body") or "").strip()
        if not legacy_body:
            if step == 2:
                legacy_body = (msg.get("ai_follow_up_1") or msg.get("ai_followup_msg") or "").strip()
            elif step == 3:
                legacy_body = (msg.get("ai_follow_up_2") or "").strip()

        body = legacy_body
        grounded = False
        try:
            prior_sent = await db.get_messages(lead_id)
            previous_bodies = [
                (m.get("body") or "").strip() for m in prior_sent
                if m.get("status") == "SENT" and m.get("sequence_step") != step
            ]
            initial_msgs = await db.get_generated_messages(lead_id)
            previous_bodies += [
                (m.get("message") or "").strip() for m in initial_msgs
                if m.get("channel") == "EMAIL" and m.get("approval_status") == "APPROVED"
            ]
            latest_reply_intent = None
            recent_replies = await db.get_replies(lead_id)
            if recent_replies:
                latest_reply_intent = recent_replies[0].get("rich_intent")

            lead_row = await db.get_lead_by_id(lead_id)
            fu_result = (
                await run_followup_agent(
                    dict(lead_row), step, previous_bodies=previous_bodies,
                    latest_reply_intent=latest_reply_intent,
                )
                if lead_row else {"generated": False}
            )

            if fu_result.get("generated"):
                body = fu_result["whatsapp_body"] if channel == "WHATSAPP" else fu_result["email_body"]
                subject = fu_result.get("subject") or subject
                grounded = True
                await db.update_message(msg_id, {"body": body, "subject": subject})
        except Exception as exc:
            logger.warning("Follow-up intelligence failed for lead %d, step %d: %s", lead_id, step, exc)

        # ── 3. Duplicate/recency guard — only for the legacy (ungrounded) path,
        # since grounded content is structurally guaranteed not to repeat. ─────
        if not grounded:
            recent_info  = await db.get_recent_send_info(lead_id, hours=_RECENT_HOURS)
            last_body    = (recent_info.get("last_body") or "").strip()
            is_duplicate = (
                recent_info["sent_recently"] or
                (body and last_body and body[:50] == last_body[:50])
            )
            if is_duplicate:
                await _log(
                    f"Follow-up engine: {biz} {label} → regenerating "
                    f"({'sent recently' if recent_info['sent_recently'] else 'body duplicate'})"
                )
                try:
                    lead_row = await db.get_lead_by_id(lead_id)
                    if lead_row:
                        msgs = await ai_brain.generate_all_messages(dict(lead_row))
                        fu_key = f"follow_up_{step - 1}"   # step 2 → follow_up_1, etc.
                        regen  = (msgs.get(fu_key) or msgs.get("follow_up_1") or "").strip()
                        if regen:
                            body = regen
                            await db.update_message(msg_id, {"body": body})
                except Exception as exc:
                    logger.warning("Follow-up regen failed for lead %d: %s", lead_id, exc)

        if not body:
            msg_err = f"{biz} {label}: empty body after all fallbacks — skipping"
            await _log(f"Follow-up engine: ⚠️  {msg_err}")
            results["errors"].append(msg_err)
            results["skipped"] += 1
            continue

        if not subject:
            subject = f"Following up — {biz}"
```

Leave everything from `# ── 4. Build send payload ─────` onward unchanged (it already reads `body`, `subject`, and `channel` — all still defined the same way).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_followup_engine.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Run the full suite for regressions**

Run: `python -m pytest tests/ -v`
Expected: all tests PASS, including `tests/test_send_guards.py` (Task 2) and everything under `tests/intelligence/`.

- [ ] **Step 6: Commit**

```bash
git add backend/followup_engine.py tests/test_followup_engine.py
git commit -m "feat: ground automated follow-ups in the pain-point evidence chain"
```

---

### Task 6: Reply-draft approval workflow test coverage

**Files:**
- Test: `tests/test_replies_router.py` (new — no production code changes; `routers/replies.py` is pre-existing and reused as-is per the Phase 4 spec)

**Interfaces:**
- Consumes: `routers/replies.py`'s existing endpoints (`GET /api/replies/drafts`, `PUT /api/replies/{id}/draft`, `POST /api/replies/{id}/approve`, `POST /api/replies/{id}/discard`) and `db.set_reply_draft`/`db.get_reply_by_id` (all already exist, unchanged).

- [ ] **Step 1: Write the tests**

```python
# tests/test_replies_router.py
import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


class _RecordingReplySender:
    def __init__(self):
        self.calls = []

    async def __call__(self, to_email, subject, body):
        self.calls.append({"to_email": to_email, "subject": subject, "body": body})


async def _draft_reply(db, lead_overrides=None, draft_body="Draft reply body."):
    lead = {"business_name": "Draft Co", "email": "lead@example.com"}
    lead.update(lead_overrides or {})
    lead_id = await db.create_lead(lead)
    reply_id = await db.create_reply({
        "lead_id": lead_id, "reply_text": "Tell me more", "detected_intent": "interested",
        "rich_intent": "INTERESTED", "intent_confidence": 0.9, "recommended_action": "SCHEDULE_MEETING",
    })
    await db.set_reply_draft(reply_id, "Re: hello", draft_body)
    return lead_id, reply_id


async def test_drafts_list_includes_recommended_action(clean_db):
    from backend.main import app
    db = clean_db
    await _draft_reply(db)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/replies/drafts")

    assert resp.status_code == 200
    drafts = resp.json()
    assert len(drafts) == 1
    assert drafts[0]["recommended_action"] == "SCHEDULE_MEETING"


async def test_edit_draft_updates_body(clean_db):
    db = clean_db
    _, reply_id = await _draft_reply(db)
    from backend.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(f"/api/replies/{reply_id}/draft", json={"draft_body": "Edited body."})

    assert resp.status_code == 200
    reply = await db.get_reply_by_id(reply_id)
    assert reply["draft_body"] == "Edited body."


async def test_approve_sends_via_existing_sender_and_marks_sent(clean_db, monkeypatch):
    db = clean_db
    _, reply_id = await _draft_reply(db)

    import backend.routers.replies as replies_router
    sender = _RecordingReplySender()
    monkeypatch.setattr(replies_router, "send_reply_email", sender)

    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/replies/{reply_id}/approve")

    assert resp.status_code == 200
    assert len(sender.calls) == 1
    assert sender.calls[0]["to_email"] == "lead@example.com"
    reply = await db.get_reply_by_id(reply_id)
    assert reply["draft_status"] == "SENT"


async def test_approve_twice_returns_409(clean_db, monkeypatch):
    db = clean_db
    _, reply_id = await _draft_reply(db)

    import backend.routers.replies as replies_router
    monkeypatch.setattr(replies_router, "send_reply_email", _RecordingReplySender())

    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(f"/api/replies/{reply_id}/approve")
        second = await client.post(f"/api/replies/{reply_id}/approve")

    assert first.status_code == 200
    assert second.status_code == 409


async def test_discard_draft_without_sending(clean_db, monkeypatch):
    db = clean_db
    _, reply_id = await _draft_reply(db)

    import backend.routers.replies as replies_router
    sender = _RecordingReplySender()
    monkeypatch.setattr(replies_router, "send_reply_email", sender)

    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/replies/{reply_id}/discard")

    assert resp.status_code == 200
    assert sender.calls == []
    reply = await db.get_reply_by_id(reply_id)
    assert reply["draft_status"] == "DISCARDED"


async def test_approve_422_when_lead_has_no_email(clean_db, monkeypatch):
    db = clean_db
    _, reply_id = await _draft_reply(db, lead_overrides={"email": None})

    import backend.routers.replies as replies_router
    monkeypatch.setattr(replies_router, "send_reply_email", _RecordingReplySender())

    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/replies/{reply_id}/approve")

    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/test_replies_router.py -v`
Expected: all 6 tests PASS against the existing, unmodified `routers/replies.py` — this task adds coverage, it does not change behavior. If any test fails, the failure is in the test (fix the test), not in `routers/replies.py` (do not modify pre-existing, working send/approval code to satisfy this task).

- [ ] **Step 3: Commit**

```bash
git add tests/test_replies_router.py
git commit -m "test: add coverage for the existing reply-draft approval workflow"
```

---

### Task 7: Surface DO_NOT_CONTACT in the frontend lead list

**Files:**
- Modify: `frontend/src/lib/badges.js`
- Modify: `frontend/src/pages/Leads.jsx:16,26-43`

**Interfaces:**
- None — pure UI, no new props/exports beyond one new map entry each.

- [ ] **Step 1: Add the badge color**

In `frontend/src/lib/badges.js`, in `STATUS_BADGE`:

```javascript
export const STATUS_BADGE = {
  PENDING:        'badge-pending',
  ENRICHED:       'badge-enriched',
  SCORED:         'badge-scored',
  MESSAGES_READY: 'badge-messages-ready',
  SENT:           'badge-sent',
  REPLIED:        'badge-replied',
  SKIPPED:        'badge-skipped',
  DO_NOT_CONTACT: 'badge bg-rose-600/20 text-rose-400 border border-rose-600/30',
  FAILED:         'badge-failed',
}
```

- [ ] **Step 2: Add it to the Leads page filter and status-button styling**

In `frontend/src/pages/Leads.jsx`, line 16:

```javascript
const STATUSES = ['', 'PENDING', 'SENT', 'REPLIED', 'SKIPPED', 'DO_NOT_CONTACT']
```

In the `STATUS_BTN` map (lines 26-43), add after the `SKIPPED` entry:

```javascript
  SKIPPED: {
    base: 'border-slate-700/50 text-slate-500',
    active: 'border-red-500/50 bg-red-500/15 text-red-300',
  },
  DO_NOT_CONTACT: {
    base: 'border-slate-700/50 text-slate-500',
    active: 'border-rose-600/50 bg-rose-600/15 text-rose-300',
  },
```

- [ ] **Step 3: Manual verification**

Run: `cd frontend && npm run dev` (or the project's existing dev-server skill), open the Leads page, filter by `DO_NOT_CONTACT` and confirm a lead marked that way (e.g. via `sqlite3 backend/data/*.db "UPDATE leads SET status='DO_NOT_CONTACT' WHERE id=<some id>"` on a scratch/test lead, or by running the opt-out flow end to end) shows a distinct rose-colored badge and is filterable. Revert any manual DB edit made for this check.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/badges.js frontend/src/pages/Leads.jsx
git commit -m "feat: surface DO_NOT_CONTACT status in the leads UI"
```

---

## Explicitly out of scope for this plan (see final report's Phase 5 notes)

- A distinct CRM deal-stage taxonomy (`MEETING`/`PROPOSAL`/`WON`/`LOST` as new `LeadStatus` values). The spec labels this "suggested" and requires "do not break existing statuses"; `recommended_action` is already surfaced to the human reviewer via `GET /api/replies/drafts` (Task 6 adds test coverage for this), which satisfies "next best action" without a pipeline-status redesign.
- Auto-booking a meeting or auto-answering a QUESTION/PRICING reply. Every draft-worthy intent already stages a `RESPONSE_DRAFT` behind the existing human-approval endpoints (Task 6); nothing in this plan auto-sends a reply.
- Changing `email_sender.py` / `whatsapp_sender.py` / `reply_detector.py`'s IMAP or classification core, or `scheduler.py`'s cron wiring — all reused unmodified per the spec's explicit instruction.
