---
name: autolead-outreach-safety
description: Use when touching anything that sends, drafts, approves, or schedules an outreach message in AutoLead — campaigns, follow-ups, reply drafts, marketing/message generation, opt-out handling, or any new lead-generation feature that will eventually produce a message a human sends.
---

# AutoLead Outreach Safety (Phase 4 Protection)

## Purpose

AutoLead has exactly one real-world consequence more dangerous than a bad lead score: sending an unwanted message to a real business, or sending after they've asked to stop. This skill protects that boundary. It exists because the product has already been built, broken, and fixed here once — see `docs/superpowers/plans/2026-08-23-phase4-intelligent-outreach.md` for the incident this codified.

## When to Use

- Adding or modifying anything under `backend/outreach_domain.py`, `backend/followup_engine.py`, `backend/email_sender.py`, `backend/whatsapp_sender.py`, `backend/reply_detector.py`, `backend/routers/marketing.py`, `backend/routers/replies.py`, `backend/routers/campaigns.py`'s send path, or `backend/scheduler.py`'s send path.
- Building any new lead-generation feature (discovery, research, scoring, personalization) that will eventually hand a message to a human for approval.
- Reviewing a PR that touches lead `status`, `DO_NOT_CONTACT`, opt-out, or follow-up scheduling.

## When NOT to Use

- Read-only work (dashboards, reporting, lead search/discovery that never touches messaging).
- Pure scraping/research code that has no path to `send_email`/`send_whatsapp`.

## Project Context

**The only two functions that ever actually deliver a message:**
- Email: `backend/email_sender.py::send_email(to_email, subject, body, config) -> bool` (wraps private `_send_smtp`). High-level callers: `send_email_lead`, `send_followup_email`, `send_reply_email`.
- WhatsApp: `backend/whatsapp_sender.py::send_whatsapp(phone_number, message, config) -> bool` (wraps private `_send_whatsapp_desktop`, pyautogui desktop automation, serialized behind a process-wide `asyncio.Lock` since it drives the real desktop). High-level callers: `send_whatsapp_lead`, `send_followup_whatsapp`.

**Every caller of these funnels through 6 independent DO_NOT_CONTACT re-check points** (not one shared decorator — each was added separately and each must keep checking):
1. `routers/campaigns.py::_send_one`
2. `followup_engine.py::process_followup_queue` — checks terminal statuses at loop start AND again immediately before the actual send (guards against opt-out arriving mid-batch)
3. `routers/marketing.py::_assert_not_opted_out`
4. `routers/replies.py::approve_draft`
5. `reply_intelligence_agent.py`'s opt-out phrase scan (deterministic, runs before any LLM call) → sets `DO_NOT_CONTACT`, cancels pending follow-ups, discards pending drafts
6. `scheduler.py::_dispatch_send`

**Terminal statuses:** `REPLIED`, `SKIPPED`, `DO_NOT_CONTACT` block further sends. `DO_NOT_CONTACT` overrides even a locked `REPLIED`/`SKIPPED` status — opt-out always wins over everything else.

**Approval is staging, not sending.** `POST /api/leads/{id}/messages/{message_id}/approve` copies drafted content into `leads.ai_email_subject/ai_email_body`/`ai_whatsapp_msg` — the fields the existing send path already reads. It never calls a sender directly. Same for reply-draft approval in `routers/replies.py`.

**Message content structure is enforced by `MarketingAgent`/`FollowUpAgent`, not by prompt-asking alone:** fixed fragment order (pain point → evidence → business impact → solution → CTA), a forbidden-claim regex (`$`, `%`, "guarantee", "ROI"), and a check that only ONE matched service is ever mentioned (never the brand name, never other services).

## Rules

1. **Never call `_send_smtp`/`_send_whatsapp_desktop` directly, and never write a second `send_email`/`send_whatsapp`-shaped function.** Every new feature that sends goes through the existing public wrappers, exactly as every current caller does.
2. **Never remove or weaken a DO_NOT_CONTACT check.** If you're refactoring one of the 6 checkpoints above, the refactor must still re-check status immediately before sending, using a fresh DB read — not a value cached earlier in the request.
3. **Never let generated content skip human approval.** A new lead-gen system may prepare research and draft messages; it must land in `generated_messages` (or the equivalent staging table) with `approval_status = READY_FOR_REVIEW`, not be auto-sent.
4. **Never open a message with "We are PopupGenix and we provide..."** — pain-point-first, evidence-supported, one CTA. This is a product rule as much as a safety rule: generic openers are explicitly banned by the user's own spec.
5. **Any new discovery/research feature only ever produces intelligence for the existing outreach system to consume — it must never gain its own send path**, even for a "quick preview send" or "test message" feature. If a preview is needed, render it read-only in the UI; do not wire it to a sender.

## Architecture Guidance

New lead-gen work (Discovery Planner, contact verification, buying signals, sales brief) should write into new evidence/research tables and stop at generating a draft. Handoff to outreach is exactly the existing marketing-agent → `generated_messages` → approve → staged-into-`leads` → existing send path. Don't add a parallel handoff.

## Implementation Guidance

When adding a new send-adjacent feature: trace the exact call chain from your new code to `send_email`/`send_whatsapp` before writing anything, and confirm a DO_NOT_CONTACT re-check sits on that chain. If it doesn't, add one at the point closest to the actual send call, re-fetching lead status fresh from the DB.

## Testing Requirements

- Reuse the `_NeverCalled` mock pattern from `tests/test_send_guards.py` — assert your new code path never calls `send_email`/`send_whatsapp` for a `DO_NOT_CONTACT` lead.
- If you add a new checkpoint, add a test for it in the same style (mid-batch opt-out arriving between fetch and send).
- Run the full existing suite (`pytest -q` from repo root, venv activated) and confirm all previously-passing outreach/reply/follow-up tests (`tests/test_send_guards.py`, `tests/test_followup_engine.py`, `tests/test_replies_router.py`, `tests/test_reply_detector.py`, `tests/test_marketing_router.py`) still pass — a regression here is not acceptable to ship.

## Security Considerations

WhatsApp sending controls the real desktop (pyautogui) — a bug here can send real messages with no undo. Treat any change touching `whatsapp_sender.py` or its callers as higher-risk than average; prefer adding a guard over removing one when in doubt.

## Performance Considerations

Not a performance-sensitive area — correctness and the opt-out guarantee matter more than throughput. Do not "optimize" by removing a redundant-looking re-check; the redundancy is deliberate (mid-batch opt-out race protection).

## Failure Modes

| Mistake | Why it happens | Fix |
|---|---|---|
| New feature calls `smtplib`/`pyautogui` directly "just for this one case" | Feels faster than wiring through the existing wrapper | Always go through `send_email`/`send_whatsapp` |
| DO_NOT_CONTACT checked once at request start, not re-checked before send | Looks redundant with an earlier check | Keep both — mid-batch opt-out is a real race the existing tests cover |
| Message generation auto-approves because "the AI seemed confident" | Confuses AI confidence with human sign-off | `approval_status` starts at `READY_FOR_REVIEW`, never auto-advances |
| New research feature adds a "send test message" button wired to a sender | Feels like a natural preview feature | Render previews read-only; never give a preview path a real sender |

## Verification Checklist

Before claiming any outreach-adjacent change complete:
- [ ] Traced the full call chain to `send_email`/`send_whatsapp` (or confirmed the change never reaches one)
- [ ] A DO_NOT_CONTACT/opt-out re-check exists immediately before any send, using a fresh DB read
- [ ] No new function duplicates `_send_smtp`/`_send_whatsapp_desktop`
- [ ] Message content (if any) starts with an observation, not a company pitch
- [ ] `pytest -q` passes, including `test_send_guards.py`, `test_followup_engine.py`, `test_replies_router.py`, `test_reply_detector.py`, `test_marketing_router.py`

## Related Skills

`autolead-lead-generation-architecture`, `autolead-lead-intelligence-and-scoring`, `autolead-verification-before-completion`, `autolead-reliability-and-background-jobs`

---
Version: 1.0
Scope: AutoLead-manager
Last reviewed: 2026-08-25
