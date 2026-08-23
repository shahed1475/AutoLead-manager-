"""
reply_detector.py — IMAP reply detection + AI intent classification engine.

HOW IT WORKS
────────────
1. Connect to Gmail (or any IMAP server) via imaplib over SSL.
2. Search INBOX for UNSEEN emails since the last N days.
3. For each email:
   a. Match to a lead by from_address email or business name in subject.
   b. Classify intent via a lightweight Ollama prompt.
   c. Persist to both `replies` table (v3 structured) and `reply_inbox` (legacy UI).
   d. Update lead.status based on intent:
        interested / meeting_request → REPLIED
        not_interested               → SKIPPED
        auto_reply / unknown         → no change
4. Return list of all new reply dicts.

Public API (all async)
──────────────────────
  check_for_replies(config, log_callback) → list[dict]
  check_replies(since_days)              → dict  (backward-compat wrapper)
  get_reply_summary()                    → dict
"""

import asyncio
import email
import imaplib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from typing import Any, Callable, Dict, List, Optional

import httpx

from . import database as db
from .config import get_settings
from .intelligence.reply_intelligence_agent import ReplyIntelligenceAgent

_reply_intelligence_agent = ReplyIntelligenceAgent()

logger   = logging.getLogger(__name__)
settings = get_settings()

# Intents that advance the lead to REPLIED
_POSITIVE_INTENTS    = frozenset({"interested", "meeting_request"})
# Intents that close the lead as not worth pursuing
_NEGATIVE_INTENTS    = frozenset({"not_interested"})
# Intents that leave lead status alone
_NEUTRAL_INTENTS     = frozenset({"auto_reply", "unknown"})
_ALL_VALID_INTENTS   = _POSITIVE_INTENTS | _NEGATIVE_INTENTS | _NEUTRAL_INTENTS

# Statuses that should never be reset by reply detection
_LOCKED_STATUSES     = frozenset({"REPLIED", "SKIPPED"})

# Maximum body characters to send to Ollama for intent classification
_INTENT_BODY_LIMIT   = 1_500
# Characters stored in replies.reply_text
_STORED_BODY_LIMIT   = 2_000


# ── Email parsing helpers ─────────────────────────────────────────────────────

def _decode_header_str(value: str) -> str:
    parts, result = decode_header(value or ""), []
    for chunk, enc in parts:
        if isinstance(chunk, bytes):
            result.append(chunk.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(str(chunk))
    return " ".join(result).strip()


def _extract_addr(header: str) -> str:
    """Extract the first bare email address from a From / Reply-To header."""
    m = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", header or "")
    return m.group(0).lower().strip() if m else ""


def _extract_body(msg: email.message.Message, max_chars: int = _STORED_BODY_LIMIT) -> str:
    """Return the full plain-text body, stripping quoted reply chains."""
    text = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                raw = part.get_payload(decode=True)
                if raw:
                    charset = part.get_content_charset() or "utf-8"
                    text    = raw.decode(charset, errors="replace")
                    break
    else:
        raw = msg.get_payload(decode=True)
        if raw:
            charset = msg.get_content_charset() or "utf-8"
            text    = raw.decode(charset, errors="replace")

    # Strip quoted reply blocks (lines starting with ">")
    clean_lines = [ln for ln in text.splitlines() if not ln.strip().startswith(">")]
    return " ".join(" ".join(clean_lines).split())[:max_chars].strip()


# ── IMAP fetcher (synchronous — runs in thread) ───────────────────────────────

def _fetch_unseen_sync(
    host:       str,
    port:       int,
    username:   str,
    password:   str,
    use_ssl:    bool,
    since_days: int,
) -> List[Dict[str, Any]]:
    """
    Connect to IMAP and return dicts for each UNSEEN message since `since_days` ago.
    Uses RFC822 fetch (which marks messages as SEEN — intentional: prevents re-fetch
    on the next poll).
    """
    messages: List[Dict[str, Any]] = []

    try:
        conn = imaplib.IMAP4_SSL(host, port) if use_ssl else imaplib.IMAP4(host, port)
        conn.login(username, password)
        conn.select("INBOX")

        since_date = (datetime.now() - timedelta(days=since_days)).strftime("%d-%b-%Y")
        _, data    = conn.search(None, f"(UNSEEN SINCE {since_date})")

        msg_nums = (data[0] or b"").split()
        logger.info("IMAP: found %d UNSEEN message(s) since %s", len(msg_nums), since_date)

        for num in msg_nums:
            try:
                _, msg_data = conn.fetch(num, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                if not isinstance(raw, bytes):
                    continue

                msg         = email.message_from_bytes(raw)
                from_header = msg.get("From", "")
                from_email  = _extract_addr(from_header)
                if not from_email:
                    continue

                messages.append({
                    "from_email":  from_email,
                    "from_header": from_header,
                    "subject":     _decode_header_str(msg.get("Subject", ""))[:500],
                    "body_text":   _extract_body(msg),
                    "received_at": msg.get("Date", ""),
                })
            except Exception as exc:
                logger.debug("IMAP: skipping message — %s", exc)

        conn.logout()

    except imaplib.IMAP4.error as exc:
        logger.error("IMAP auth/search failed: %s", exc)
    except OSError as exc:
        logger.error("IMAP connection refused (%s:%s): %s", host, port, exc)
    except Exception as exc:
        logger.error("IMAP unexpected error: %s", exc, exc_info=True)

    return messages


# ── Ollama intent classifier ───────────────────────────────────────────────────

async def _classify_intent(
    body_text:   str,
    ollama_url:  str,
    model:       str,
    timeout:     int = 30,
) -> str:
    """
    Send the reply body to Ollama and get back a single intent category.

    Returns one of: interested | meeting_request | not_interested |
                    auto_reply | unknown
    Defaults to 'unknown' on any error.
    """
    if not body_text.strip():
        return "unknown"

    prompt = f"""Classify this email reply into EXACTLY ONE of these categories:
- interested: they want to know more, asked questions, positive tone
- meeting_request: they asked for a call, demo, or meeting
- not_interested: they said no, unsubscribe, remove me, not now
- auto_reply: vacation message, out-of-office, or automated response
- unknown: cannot classify clearly

Email reply:
{body_text[:_INTENT_BODY_LIMIT]}

Reply ONLY with the category word, nothing else. One word."""

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                f"{ollama_url}/api/generate",
                json={
                    "model":   model,
                    "prompt":  prompt,
                    "stream":  False,
                    "options": {"temperature": 0.05, "num_predict": 8},
                },
            )
            r.raise_for_status()
            raw  = r.json().get("response", "").strip().lower()
            word = re.split(r"[\s\.,\!\:\;]+", raw)[0].strip()
            return word if word in _ALL_VALID_INTENTS else "unknown"

    except (httpx.ConnectError, httpx.TimeoutException):
        logger.warning("Intent classification: Ollama unreachable — defaulting to 'unknown'")
    except Exception as exc:
        logger.warning("Intent classification failed: %s", exc)

    return "unknown"


# ── Lead matching ─────────────────────────────────────────────────────────────

async def _find_matching_lead(
    from_email: str,
    subject:    str,
) -> Optional[Dict[str, Any]]:
    """
    Try to match an inbound email to a lead we previously contacted.

    Strategy (ordered by confidence):
    1. Exact email address match (from_email == lead.email)
    2. Business name appears in subject (case-insensitive substring)
    """
    # --- primary: email match ---
    lead = await db.find_lead_by_email(from_email)
    if lead:
        return dict(lead)

    # --- secondary: business name in subject ---
    if subject.strip():
        lead = await db.find_lead_by_business_name_in_subject(subject)
        if lead:
            return dict(lead)

    return None


# ── Core engine ───────────────────────────────────────────────────────────────

async def check_for_replies(
    config:       Dict[str, Any],
    log_callback: Optional[Callable[[str], Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Poll the IMAP inbox, match replies to leads, classify intent, persist.

    Parameters
    ----------
    config        : merged dict of DB settings + env settings
                    Required keys: imap_host, imap_username, imap_password
                    Optional: imap_port (993), imap_ssl (True), imap_since_days (7),
                              ollama_base_url, ollama_model
    log_callback  : optional async/sync callable(str) for real-time log streaming

    Returns
    -------
    List of dicts for every NEW reply saved this run.
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

    # ── IMAP credentials ───────────────────────────────────────────────────────
    host     = config.get("imap_host")     or ""
    port_str = config.get("imap_port")     or "993"
    username = config.get("imap_username") or ""
    password = config.get("imap_password") or ""
    ssl_val  = config.get("imap_ssl", "true")
    use_ssl  = str(ssl_val).lower() not in ("false", "0", "no")
    since    = int(config.get("imap_since_days") or 7)

    try:
        port = int(port_str)
    except (TypeError, ValueError):
        port = 993

    if not (host and username and password):
        await _log("Reply detector: IMAP not configured — skipping")
        return []

    # ── Ollama config ──────────────────────────────────────────────────────────
    ollama_url = config.get("ollama_base_url") or settings.ollama_base_url
    model      = config.get("ollama_model")    or settings.ollama_model

    await _log(f"Reply detector: checking {username} via {host}:{port} (last {since} days UNSEEN)")

    # ── Fetch from IMAP (blocking → thread) ───────────────────────────────────
    raw_messages = await asyncio.to_thread(
        _fetch_unseen_sync, host, port, username, password, use_ssl, since
    )
    await _log(f"Reply detector: {len(raw_messages)} new email(s) found")

    new_replies: List[Dict[str, Any]] = []

    for msg in raw_messages:
        from_email = msg["from_email"]
        subject    = msg["subject"]
        body_text  = msg["body_text"]
        received   = msg["received_at"]

        # ── Dedup: skip if already stored in reply_inbox ───────────────────────
        already = await db.find_inbox_entry(from_email, subject)
        if already:
            logger.debug("Reply detector: skipping already-stored message from %s", from_email)
            continue

        # ── Match to a lead ────────────────────────────────────────────────────
        lead    = await _find_matching_lead(from_email, subject)
        lead_id = lead["id"] if lead else None
        biz     = (lead or {}).get("business_name", from_email)

        # ── Conversation context for the Reply Intelligence Agent (fetched before
        # this reply is inserted, so it's genuinely "previous") ────────────────
        previous_replies: List[Dict[str, Any]] = []
        original_message: Optional[Dict[str, Any]] = None
        if lead_id:
            try:
                previous_replies = await db.get_replies(lead_id)
            except Exception as exc:
                logger.debug("Reply detector: could not load reply history for lead %d: %s", lead_id, exc)
            try:
                approved_email_msgs = [
                    m for m in await db.get_generated_messages(lead_id)
                    if m.get("channel") == "EMAIL" and m.get("approval_status") == "APPROVED"
                ]
                if approved_email_msgs:
                    original_message = approved_email_msgs[-1]
            except Exception as exc:
                logger.debug("Reply detector: could not load generated messages for lead %d: %s", lead_id, exc)

        # ── AI intent classification ───────────────────────────────────────────
        intent = await _classify_intent(body_text, ollama_url, model)
        await _log(
            f"Reply detector: {biz} → intent={intent} "
            f"{'[MATCHED]' if lead_id else '[UNMATCHED]'}"
        )

        # ── Map intent → inbox display intent (legacy table uses UPPERCASE) ────
        legacy_intent = {
            "interested":      "POSITIVE",
            "meeting_request": "POSITIVE",
            "not_interested":  "NEGATIVE",
            "auto_reply":      "NEUTRAL",
            "unknown":         "NEUTRAL",
        }.get(intent, "NEUTRAL")

        # ── Persist to reply_inbox (legacy UI table) ───────────────────────────
        try:
            await db.save_reply({
                "lead_id":      lead_id,
                "from_email":   from_email,
                "subject":      subject,
                "body_snippet": body_text[:250],
                "received_at":  received,
                "intent":       legacy_intent,
            })
        except Exception as exc:
            logger.error("Reply detector: reply_inbox insert failed: %s", exc)

        # ── Persist to replies table (v3 structured) ───────────────────────────
        reply_row: Dict[str, Any] = {
            "reply_text":      body_text[:_STORED_BODY_LIMIT],
            "detected_intent": intent,
            "raw_email_data":  json.dumps({
                "from_email":  from_email,
                "from_header": msg.get("from_header", ""),
                "subject":     subject,
                "received_at": received,
            }),
        }
        if lead_id:
            reply_row["lead_id"] = lead_id

        try:
            reply_id = await db.create_reply(reply_row)
            reply_row["id"] = reply_id
        except Exception as exc:
            logger.error("Reply detector: replies insert failed: %s", exc)
            reply_id = None

        # ── Update lead status based on intent (existing 5-category classifier) ─
        if lead_id:
            current_status = (lead.get("status") or "").upper()
            if current_status not in _LOCKED_STATUSES:
                if intent in _POSITIVE_INTENTS:
                    await db.update_lead(lead_id, {"status": "REPLIED"})
                    await _log(f"Reply detector: {biz} → marked REPLIED (positive intent)")
                elif intent in _NEGATIVE_INTENTS:
                    await db.update_lead(lead_id, {"status": "SKIPPED"})
                    await _log(f"Reply detector: {biz} → marked SKIPPED (not interested)")

        # ── Reply Intelligence Agent (Phase 4) — richer 12-category intent +
        # recommended action + grounded draft, layered on top of the classification
        # above (which still drives REPLIED/SKIPPED and get_reply_summary()). ────
        if reply_id and lead:
            try:
                ria_result = await _reply_intelligence_agent.run(
                    body_text, lead, original_message, previous_replies,
                )
                rich = ria_result.data
                await db.update_reply_intelligence(reply_id, {
                    "rich_intent":        rich["intent"],
                    "intent_confidence":  rich["confidence"],
                    "recommended_action": rich["recommended_action"],
                })
                await _log(f"Reply detector: {biz} → rich_intent={rich['intent']} action={rich['recommended_action']}")

                # OPT_OUT is a hard safety override — always honored, even over a
                # locked REPLIED/SKIPPED status, and suppresses all future outreach.
                action = rich["recommended_action"]
                if rich["intent"] == "OPT_OUT":
                    await db.update_lead(lead_id, {"status": "DO_NOT_CONTACT"})
                    cancelled = await db.cancel_pending_followups(lead_id)
                    discarded = await db.discard_pending_drafts_for_lead(lead_id)
                    await _log(
                        f"Reply detector: {biz} → marked DO_NOT_CONTACT (opt-out detected), "
                        f"{cancelled} pending follow-up(s) cancelled, {discarded} pending draft(s) discarded"
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
            except Exception as exc:
                logger.error("Reply detector: reply intelligence failed for reply %s: %s", reply_id, exc, exc_info=True)

        new_replies.append({
            "lead_id":         lead_id,
            "business_name":   biz,
            "from_email":      from_email,
            "subject":         subject,
            "intent":          intent,
            "matched":         lead_id is not None,
            "received_at":     received,
        })

    await _log(
        f"Reply detector: complete — {len(new_replies)} new, "
        f"{sum(1 for r in new_replies if r['matched'])} matched"
    )
    return new_replies


# ── Backward-compat wrapper ────────────────────────────────────────────────────

async def check_replies(since_days: int = 7) -> Dict[str, Any]:
    """
    Check the configured IMAP inbox for replies.

    Loads IMAP + Ollama config from DB / env, then delegates to
    check_for_replies(). Returns the legacy {new_replies, matched, error} dict
    so existing callers (scheduler, trigger endpoint) need no changes.
    """
    try:
        stored = await db.get_all_settings()
        config = {
            "imap_host":       stored.get("imap_host")     or settings.imap_host,
            "imap_port":       stored.get("imap_port")      or str(settings.imap_port),
            "imap_username":   stored.get("imap_username")  or settings.imap_username,
            "imap_password":   stored.get("imap_password")  or settings.imap_password,
            "imap_ssl":        stored.get("imap_ssl", "true"),
            "imap_since_days": since_days,
            "ollama_base_url": stored.get("ollama_base_url") or settings.ollama_base_url,
            "ollama_model":    stored.get("ollama_model")    or settings.ollama_model,
        }

        replies = await check_for_replies(config)
        return {
            "new_replies": len(replies),
            "matched":     sum(1 for r in replies if r["matched"]),
            "error":       None,
        }

    except Exception as exc:
        logger.error("check_replies: unexpected error: %s", exc, exc_info=True)
        return {"new_replies": 0, "matched": 0, "error": str(exc)}


# ── Summary stats ─────────────────────────────────────────────────────────────

async def get_reply_summary() -> Dict[str, Any]:
    """
    Aggregate reply statistics across all detected replies.

    Returns
    -------
    {
      total_replies      : int,
      interested_count   : int,
      meeting_requests   : int,
      not_interested     : int,
      auto_replies       : int,
      unknown_count      : int,
      reply_rate_percent : float,
      leads_to_follow_up : list[dict]   — REPLIED leads with positive intent
    }
    """
    stats = await db.get_reply_stats()

    counts = {row["detected_intent"]: row["cnt"] for row in stats.get("by_intent", [])}
    total  = stats.get("total_replies", 0)

    # Reply rate = REPLIED leads / all leads that were SENT (including REPLIED)
    sent_count    = stats.get("total_sent", 0)
    replied_count = stats.get("total_replied", 0)
    reply_rate    = round((replied_count / sent_count * 100), 1) if sent_count > 0 else 0.0

    return {
        "total_replies":      total,
        "interested_count":   counts.get("interested", 0),
        "meeting_requests":   counts.get("meeting_request", 0),
        "not_interested":     counts.get("not_interested", 0),
        "auto_replies":       counts.get("auto_reply", 0),
        "unknown_count":      counts.get("unknown", 0),
        "reply_rate_percent": reply_rate,
        "leads_to_follow_up": stats.get("leads_to_follow_up", []),
    }
