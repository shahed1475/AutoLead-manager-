"""
reply_detector.py — IMAP inbox checker for auto-detecting lead replies.

Polls the configured IMAP inbox, matches incoming emails against known
lead emails, and marks matched leads as REPLIED. All new emails are saved
to the reply_inbox table for display in the Inbox UI.

Uses stdlib imaplib (no extra dependencies).
Threading model: IMAP is synchronous — runs in asyncio.to_thread.
"""
import asyncio
import email
import imaplib
import logging
import re
from datetime import datetime, timedelta
from email.header import decode_header
from typing import Any, Dict, List, Optional

from . import database as db
from .config import get_settings

logger   = logging.getLogger(__name__)
settings = get_settings()


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
    """Pull the first email address from a From/Reply-To header."""
    m = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", header or "")
    return m.group(0).lower().strip() if m else ""


def _body_snippet(msg: email.message.Message, max_chars: int = 250) -> str:
    """Extract plain-text body snippet (first readable part)."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                raw = part.get_payload(decode=True)
                if raw:
                    charset = part.get_content_charset() or "utf-8"
                    return raw.decode(charset, errors="replace")[:max_chars].strip()
    else:
        raw = msg.get_payload(decode=True)
        if raw:
            charset = msg.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")[:max_chars].strip()
    return ""


# ── Core IMAP check (synchronous) ─────────────────────────────────────────────

def _fetch_inbox_sync(
    host: str,
    port: int,
    username: str,
    password: str,
    use_ssl: bool,
    since_days: int,
) -> List[Dict[str, Any]]:
    """Connect to IMAP and return list of message dicts since `since_days` ago."""
    replies: List[Dict[str, Any]] = []

    try:
        conn = imaplib.IMAP4_SSL(host, port) if use_ssl else imaplib.IMAP4(host, port)
        conn.login(username, password)
        conn.select("INBOX")

        since_date = (datetime.now() - timedelta(days=since_days)).strftime("%d-%b-%Y")
        _, data = conn.search(None, f"(SINCE {since_date})")

        for num in (data[0] or b"").split():
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

                replies.append({
                    "from_email":   from_email,
                    "subject":      _decode_header_str(msg.get("Subject", "")),
                    "body_snippet": _body_snippet(msg),
                    "received_at":  msg.get("Date", ""),
                })
            except Exception as exc:
                logger.debug("IMAP: skipping message — %s", exc)
                continue

        conn.logout()

    except imaplib.IMAP4.error as exc:
        logger.error("IMAP login/search failed: %s", exc)
    except OSError as exc:
        logger.error("IMAP connection refused (%s:%s): %s", host, port, exc)
    except Exception as exc:
        logger.error("IMAP unexpected error: %s", exc, exc_info=True)

    return replies


# ── Public async API ───────────────────────────────────────────────────────────

async def check_replies(since_days: int = 7) -> Dict[str, Any]:
    """
    Check the configured IMAP inbox for replies from leads.

    Workflow:
    1. Fetch all messages newer than `since_days` days
    2. For each message, find matching lead by from_email
    3. Save new messages to reply_inbox table
    4. Auto-mark matched lead as REPLIED

    Returns
    -------
    dict: new_replies (int), matched (int), error (str|None)
    """
    stored = await db.get_all_settings()

    host     = stored.get("imap_host")     or settings.imap_host
    port_str = stored.get("imap_port")     or str(settings.imap_port)
    username = stored.get("imap_username") or settings.imap_username
    password = stored.get("imap_password") or settings.imap_password
    ssl_val  = stored.get("imap_ssl", "true")
    use_ssl  = str(ssl_val).lower() not in ("false", "0", "no")

    try:
        port = int(port_str)
    except (TypeError, ValueError):
        port = 993

    if not (host and username and password):
        return {"new_replies": 0, "matched": 0, "error": "IMAP not configured"}

    raw_messages = await asyncio.to_thread(
        _fetch_inbox_sync, host, port, username, password, use_ssl, since_days
    )

    new_replies = 0
    matched     = 0

    for msg in raw_messages:
        from_email = msg["from_email"]

        # Find matching lead by their email address
        lead = await db.find_lead_by_email(from_email)
        lead_id = lead["id"] if lead else None

        if lead_id:
            matched += 1
            current = await db.get_lead_by_id(lead_id)
            if current and current.get("status") not in ("REPLIED",):
                await db.update_lead(lead_id, {"status": "REPLIED"})

        # Skip if we already stored this message
        already = await db.find_inbox_entry(from_email, msg.get("subject", ""))
        if already:
            continue

        await db.save_reply({
            "lead_id":      lead_id,
            "from_email":   from_email,
            "subject":      msg.get("subject", ""),
            "body_snippet": msg.get("body_snippet", ""),
            "received_at":  msg.get("received_at", ""),
            "intent":       "NEUTRAL",
        })
        new_replies += 1

    logger.info(
        "Reply check complete — new=%d matched=%d total_checked=%d",
        new_replies, matched, len(raw_messages)
    )
    return {"new_replies": new_replies, "matched": matched, "error": None}
