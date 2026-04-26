"""
followup_engine.py — Automated follow-up sequence engine (Upgrade 6).

Follow-up schedule (days from initial outreach sent_at):
  Step 2: +3  days  (Day-3  follow-up)
  Step 3: +7  days  (Day-7  follow-up)

The engine queries the messages table for PENDING follow-ups whose
scheduled_for has elapsed, handles dedup / intent-gating, and sends
via the lead's configured channel.  It also writes back legacy
follow_up_N_sent_at columns so the older scheduler.check_followups()
will not double-send the same lead.

Public API (all async)
──────────────────────
  schedule_followups_for_lead(lead_id, sent_at, lead) → None
  process_followup_queue(config, log_callback)         → dict
  get_pending_count()                                  → int
  get_history(page, page_size)                         → dict
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from . import ai_brain
from . import database as db
from . import email_sender, whatsapp_sender
from .config import get_settings

logger   = logging.getLogger(__name__)
settings = get_settings()

# Days from initial send_at to schedule each follow-up step
_STEP_DAYS:  Dict[int, int] = {2: 3, 3: 7}
_STEP_LABEL: Dict[int, str] = {2: "Day-3", 3: "Day-7"}

# Lead statuses that mean "stop the sequence"
_TERMINAL_STATUSES = frozenset({"REPLIED", "SKIPPED"})

# Recency window for duplicate detection (hours)
_RECENT_HOURS = 24


# ── Scheduling ────────────────────────────────────────────────────────────────


async def schedule_followups_for_lead(
    lead_id:  int,
    sent_at:  datetime,
    lead:     Optional[Dict[str, Any]] = None,
) -> None:
    """
    Create PENDING message records for follow-up steps 2 and 3.

    Idempotent — returns immediately if messages already exist for this lead
    (handles resend flows, manual sends, and concurrent requests safely).

    Parameters
    ----------
    lead_id  : the lead to schedule for
    sent_at  : the datetime the initial outreach was sent (UTC)
    lead     : optional lead dict — used to pre-populate body/subject so the
               engine can send without a round-trip AI call at delivery time
    """
    if await db.count_messages_for_lead(lead_id) > 0:
        return

    for step, days in _STEP_DAYS.items():
        scheduled_for = sent_at + timedelta(days=days)

        # Pre-populate body from lead columns if available
        body    = ""
        subject = ""
        if lead:
            # step 2 → ai_follow_up_1 | step 3 → ai_follow_up_2
            body    = lead.get(f"ai_follow_up_{step - 1}") or lead.get("ai_followup_msg") or ""
            subject = lead.get("ai_email_subject") or ""

        await db.create_message({
            "lead_id":       lead_id,
            "sequence_step": step,
            "message_type":  "followup",
            "subject":       subject or None,
            "body":          body    or None,
            "status":        "PENDING",
            "scheduled_for": scheduled_for,
        })

    logger.debug("Scheduled follow-up steps 2+3 for lead %d", lead_id)


# ── Core engine ───────────────────────────────────────────────────────────────


async def process_followup_queue(
    config:       Optional[Dict[str, Any]] = None,
    log_callback: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """
    Find and deliver all overdue follow-up messages.

    For each PENDING message whose scheduled_for ≤ NOW():
      1. Cancel if lead is REPLIED / SKIPPED.
      2. Check for duplicate send (24 h window + first-50-char body match).
         If duplicate → regenerate body via ai_brain before sending.
      3. Send via lead's channel (EMAIL / WHATSAPP / BOTH).
      4. Mark message SENT, update legacy follow_up_N_sent_at on lead,
         and log campaign action.

    Returns
    -------
    {processed, sent, cancelled, skipped, errors}
    """

    async def _log(msg: str) -> None:
        logger.info(msg)
        if log_callback:
            try:
                result = log_callback(msg)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass

    config  = config or {}
    results: Dict[str, Any] = {
        "processed": 0, "sent": 0,
        "cancelled": 0, "skipped": 0,
        "errors":    [],
    }

    due = await db.get_due_followup_messages(limit=100)
    if not due:
        logger.debug("Follow-up engine: no overdue messages")
        return results

    await _log(f"Follow-up engine: {len(due)} message(s) due for delivery")

    for msg in due:
        msg_id      = msg["id"]
        lead_id     = msg["lead_id"]
        step        = msg["sequence_step"]
        biz         = msg.get("business_name") or f"Lead {lead_id}"
        label       = _STEP_LABEL.get(step, f"Step-{step}")
        lead_status = (msg.get("lead_status") or "").upper()

        results["processed"] += 1

        # ── 1. Cancel if lead no longer needs follow-ups ──────────────────────
        if lead_status in _TERMINAL_STATUSES:
            await db.update_message(msg_id, {"status": "CANCELLED"})
            await _log(f"Follow-up engine: {biz} {label} → CANCELLED (lead is {lead_status})")
            results["cancelled"] += 1
            continue

        # ── 2. Resolve message body (stored → lead column → ai_brain) ─────────
        body    = (msg.get("msg_body")    or "").strip()
        subject = (msg.get("msg_subject") or msg.get("ai_email_subject") or "").strip()

        # Fallback: use lead column if messages.body wasn't pre-populated
        if not body:
            if step == 2:
                body = (msg.get("ai_follow_up_1") or msg.get("ai_followup_msg") or "").strip()
            elif step == 3:
                body = (msg.get("ai_follow_up_2") or "").strip()

        # ── 3. Duplicate detection (24 h + body similarity) ───────────────────
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
                    body   = (msgs.get(fu_key) or msgs.get("follow_up_1") or "").strip()
                    if body:
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

        # ── 4. Build send payload ─────────────────────────────────────────────
        channel = (msg.get("channel") or "EMAIL").upper()
        send_payload: Dict[str, Any] = {
            "id":               lead_id,
            "business_name":    biz,
            "email":            msg.get("email"),
            "phone":            msg.get("phone"),
            "niche":            msg.get("niche"),
            "city":             msg.get("city"),
            "channel":          channel,
            "ai_email_subject": subject,
            "ai_email_body":    body,
            "ai_followup_msg":  body,
        }

        # ── 5. Send ───────────────────────────────────────────────────────────
        sent_via: List[str] = []
        send_errors: List[str] = []

        if channel in ("EMAIL", "BOTH") and msg.get("email"):
            try:
                await email_sender.send_followup_email(send_payload)
                await db.log_campaign_action(lead_id, "EMAIL", f"FOLLOWUP_{step}", True)
                sent_via.append("EMAIL")
                await _log(f"Follow-up engine: 📧 {label} email sent → {biz}")
            except Exception as exc:
                err = f"Email failed for {biz}: {exc}"
                await db.log_campaign_action(lead_id, "EMAIL", f"FOLLOWUP_{step}", False, str(exc))
                send_errors.append(err)
                logger.error("Follow-up email error (lead %d): %s", lead_id, exc)

        if channel in ("WHATSAPP", "BOTH") and msg.get("phone"):
            try:
                await whatsapp_sender.send_followup_whatsapp(send_payload)
                await db.log_campaign_action(lead_id, "WHATSAPP", f"FOLLOWUP_{step}", True)
                sent_via.append("WHATSAPP")
                await _log(f"Follow-up engine: 💬 {label} WhatsApp sent → {biz}")
            except Exception as exc:
                err = f"WhatsApp failed for {biz}: {exc}"
                await db.log_campaign_action(lead_id, "WHATSAPP", f"FOLLOWUP_{step}", False, str(exc))
                send_errors.append(err)
                logger.error("Follow-up WA error (lead %d): %s", lead_id, exc)

        # ── 6. Persist outcome ────────────────────────────────────────────────
        if sent_via:
            now = datetime.now(timezone.utc)
            await db.update_message(msg_id, {"status": "SENT", "sent_at": now})

            # Write legacy follow_up_N_sent_at so check_followups() won't re-send
            _legacy_col = {2: "follow_up_1_sent_at", 3: "follow_up_2_sent_at"}
            if step in _legacy_col:
                await db.update_lead(lead_id, {
                    _legacy_col[step]:  now,
                    "followup_sent_at": now,
                })

            results["sent"] += 1
        else:
            results["errors"].extend(send_errors)
            results["skipped"] += 1

    await _log(
        f"Follow-up engine: complete — "
        f"processed={results['processed']} sent={results['sent']} "
        f"cancelled={results['cancelled']} skipped={results['skipped']} "
        f"errors={len(results['errors'])}"
    )
    return results


# ── Query helpers for API ─────────────────────────────────────────────────────


async def get_pending_count() -> int:
    """Count of PENDING follow-up messages waiting to be sent."""
    return await db.count_pending_followups()


async def get_history(
    page: int = 1,
    page_size: int = 50,
) -> Dict[str, Any]:
    """Paginated list of sent follow-up messages with lead context."""
    return await db.get_followup_history(page=page, page_size=page_size)
