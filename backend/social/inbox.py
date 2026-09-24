"""
inbox.py — Social inbox (Phase 2): comments + DMs from Facebook Pages and
Instagram, answered automatically by the local AI (the owner's choice, same as
WhatsApp), and turned into leads.

  sync      poll each Meta account (throttled; HOM's n8n tick drives it)
            - first sync only marks the backlog as seen — nothing old is answered
            - your own comments/messages are kept as history, never answered
  reply     send_reply() is THE one way a comment/DM answer goes out
  guards    opt-out words → never answered again (lead → Do not contact);
            Do-not-contact leads are skipped; per-person daily cap; messages
            older than MAX_AGE_H aren't answered; public comment replies are
            short, never mention prices and invite the person to message you
  leads     everyone who messages you, and commenters who show interest
            (price, "interested", a question …) become leads (source FACEBOOK
            / INSTAGRAM) so the conversation has a name and an opt-out status

Web page / message content is untrusted: it only ever goes into the AI prompt
as quoted conversation text.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .. import database as db
from ..secrets_crypto import decrypt
from . import meta
from .service import SocialError, _iso, _utc, log

logger = logging.getLogger(__name__)

DEFAULTS: Dict[str, Any] = {
    "social_auto_reply": True,        # answer comments + DMs automatically (owner's choice)
    "social_reply_comments": True,    # … also public comments (not only DMs)
    "social_reply_per_person": 6,     # automatic answers per person per day
}
SYNC_EVERY_S = 120                    # per account
MAX_AGE_H = 12                        # older messages are only shown, not answered
REPLY_AI_TIMEOUT_S = 240
MAX_COMMENT_REPLY = 300
MAX_DM_REPLY = 700
INBOX_PLATFORMS = ("facebook", "instagram")
_INTEREST = re.compile(
    r"\?|\b(price|pricing|cost|how much|interested|interest|info|details|dm|inbox|contact|quote|order|buy|"
    r"available|service|need|want|help|call|whatsapp|email)\b|দাম|কত|আগ্রহী|জানতে|ইনবক্স", re.I)
_lock = asyncio.Lock()
_tasks: set = set()


# ── Settings ─────────────────────────────────────────────────────────────────

async def get_settings() -> Dict[str, Any]:
    out = dict(DEFAULTS)
    for k, v in DEFAULTS.items():
        raw = await db.get_setting(k)
        if raw is None:
            continue
        if isinstance(v, bool):
            out[k] = raw == "true"
        else:
            try:
                out[k] = max(0, min(50, int(raw)))
            except ValueError:
                pass
    return out


async def save_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in (patch or {}).items():
        if k not in DEFAULTS:
            continue
        if isinstance(DEFAULTS[k], bool):
            await db.upsert_setting(k, "true" if v else "false")
        else:
            try:
                await db.upsert_setting(k, str(max(0, min(50, int(v)))))
            except (TypeError, ValueError):
                raise SocialError(f"{k} must be a number.")
    return await get_settings()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _when(raw: Optional[str]) -> str:
    """Meta's '2026-09-24T10:00:00+0000' → naive UTC ISO."""
    if raw:
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
            try:
                return _iso(datetime.strptime(raw, fmt).astimezone(timezone.utc).replace(tzinfo=None))
            except ValueError:
                continue
    return _iso(_utc())


def _extra(a: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return json.loads(a.get("extra") or "{}") or {}
    except ValueError:
        return {}


def _is_own(a: Dict[str, Any], m: Dict[str, Any]) -> bool:
    ex = _extra(a)
    ours = {a["external_id"], ex.get("page_id")}
    return bool(m["author_id"] and m["author_id"] in ours) or bool(
        ex.get("username") and m["author_name"] == f"@{ex['username']}")


def _today_start_utc() -> str:
    """Local midnight, as UTC (daily caps follow the owner's day)."""
    local = datetime.now().astimezone()
    return _iso(local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None))


async def _fetch(a: Dict[str, Any]) -> tuple:
    """(items, errors) — one missing permission only disables that part."""
    token = decrypt(a["token_enc"]) or ""
    ex = _extra(a)
    page = ex.get("page_id") or (a["external_id"] if a["platform"] == "facebook" else None)
    jobs = []
    if a["platform"] == "facebook":
        jobs = [("comments", meta.facebook_comments(a["external_id"], token)),
                ("messages", meta.conversations(page, token, "messenger"))]
    elif a["platform"] == "instagram":
        jobs = [("comments", meta.instagram_comments(a["external_id"], token))]
        if page:
            jobs.append(("messages", meta.conversations(page, token, "instagram")))
    items, errors = [], []
    for label, job in jobs:
        try:
            items += await job
        except meta.MetaError as exc:
            errors.append(f"{label}: {exc}")
    return items, errors


# ── Sync ─────────────────────────────────────────────────────────────────────

async def sync_account(a: Dict[str, Any]) -> Dict[str, int]:
    first = not a.get("inbox_synced_at")
    items, errors = await _fetch(a)
    cutoff = _iso(_utc() - timedelta(hours=MAX_AGE_H))
    new = 0
    for m in items:
        own = _is_own(a, m)
        when = _when(m["created_at"])
        status = "SENT" if own else ("SEEN" if first or when < cutoff or not m["text"].strip() else "NEW")
        rid = await db.portal_insert(
            """INSERT INTO social_messages (account_id, kind, external_id, thread_id, author_id, author_name, text,
                                            direction, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (account_id, external_id) DO NOTHING RETURNING id""",
            a["id"], m["kind"], m["external_id"], m["thread_id"], m["author_id"], m["author_name"],
            m["text"][:4000], "OUT" if own else "IN", status, when)
        if rid and status == "NEW":
            new += 1
    await db.portal_execute("UPDATE social_accounts SET inbox_synced_at = ?, inbox_error = ? WHERE id = ?",
                            _iso(_utc()), "; ".join(errors)[:500] or None, a["id"])
    if first and items:
        await log("info", f"Inbox connected for {a['name']} — {len(items)} earlier comment(s)/message(s) marked as seen.")
    return {"new": new, "errors": len(errors)}


async def sync_all(force: bool = False, send=None) -> Dict[str, Any]:
    """Poll the accounts that are due, then answer what's new."""
    async with _lock:
        due_before = _iso(_utc() - timedelta(seconds=SYNC_EVERY_S))
        accts = await db.portal_fetch(
            f"""SELECT * FROM social_accounts WHERE transport = 'api' AND status = 'connected'
                AND platform IN ({','.join('?' * len(INBOX_PLATFORMS))})
                {'' if force else 'AND (inbox_synced_at IS NULL OR inbox_synced_at <= ?)'}""",
            *INBOX_PLATFORMS, *([] if force else [due_before]))
        new = 0
        for a in accts:
            try:
                new += (await sync_account(a))["new"]
            except Exception as exc:  # noqa: BLE001 — one account failing must not stop the others
                logger.exception("social inbox sync failed: %s", exc)
        handled = await process_new(send=send)
        return {"accounts": len(accts), "new": new, "handled": handled}


def start_background_sync() -> bool:
    """Called from the n8n tick: answering takes the AI a while, so run it in
    the background (one at a time) and let the tick return at once."""
    if _lock.locked():
        return False
    task = asyncio.create_task(sync_all())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


# ── Leads ────────────────────────────────────────────────────────────────────

async def _lead_for(a: Dict[str, Any], m: Dict[str, Any]) -> Optional[int]:
    known = await db.portal_fetchrow(
        "SELECT lead_id FROM social_messages WHERE account_id = ? AND author_id = ? AND lead_id IS NOT NULL LIMIT 1",
        a["id"], m["author_id"])
    if known:
        return known["lead_id"]
    label = m["author_name"] or "someone"
    where = "message" if m["kind"] == "dm" else "comment"
    try:
        lid = await db.create_lead({"business_name": f"{a['platform'].title()} · {label}",
                                    "source": a["platform"].upper(),
                                    "niche": f"{a['platform'].title()} {where}"})
        await db.update_lead(lid, {"status": "REPLIED"})
    except Exception as exc:  # noqa: BLE001
        logger.info("social contact not saved as a lead: %s", exc)
        return None
    await log("lead", f"New lead from {a['platform'].title()}: {label} ({where}).")
    return lid


def wants_lead(m: Dict[str, Any]) -> bool:
    return m["kind"] == "dm" or bool(_INTEREST.search(m["text"] or ""))


# ── Replies ──────────────────────────────────────────────────────────────────

async def _history(m: Dict[str, Any]) -> List[Dict[str, str]]:
    if m["kind"] == "dm":
        rows = await db.portal_fetch(
            "SELECT direction, text FROM social_messages WHERE account_id = ? AND thread_id = ? ORDER BY created_at DESC, id DESC LIMIT 14",
            m["account_id"], m["thread_id"])
        return [{"direction": r["direction"], "body": r["text"]} for r in reversed(rows)]
    return [{"direction": "IN", "body": m["text"]}]


def comment_prompt(platform: str, author: str, text: str, post_text: str, dna: str) -> str:
    return f"""You answer a PUBLIC comment on the {platform.title()} page of the business described below, as a real person from it.

COMPANY PROFILE:
{dna}

THE COMMENT (from {author or 'someone'}): "{text}"

HOW TO REPLY:
- 1–2 short, friendly sentences, in the language of the comment. At most one emoji. No hashtags, no links.
- Thank them or answer briefly. If they ask about price, details or ordering, say you'd be happy to help and
  invite them to send a direct message (DM) — never state or guess a price in public.
- Never invent facts, numbers, results or promises. Only use the company profile.
- If the comment is just praise or an emoji, answer with a short thank-you.

Write ONLY the reply text."""


async def draft(message_id: int) -> Optional[str]:
    """The AI's answer to one incoming comment/DM (grounded in the Company DNA)."""
    from .. import ai_brain
    from ..whatsapp.service import build_reply_prompt, clean_reply, offer_digest
    m = await _message(message_id)
    a = await db.portal_fetchrow("SELECT * FROM social_accounts WHERE id = ?", m["account_id"])
    dna = ai_brain._load_company_dna().strip()[:3500]
    cfg = await ai_brain._ollama_cfg()
    platform = a["platform"].title()
    if m["kind"] == "comment":
        prompt = comment_prompt(a["platform"], m["author_name"], m["text"], "", dna)
        limit = MAX_COMMENT_REPLY
    else:
        lead = (await db.portal_fetchrow("SELECT * FROM leads WHERE id = ?", m["lead_id"])) if m.get("lead_id") else None
        prompt = build_reply_prompt(lead or {"business_name": m["author_name"]}, await _history(m), dna, offer_digest())
        prompt = prompt.replace("WhatsApp messages", f"{platform} direct messages")
        limit = MAX_DM_REPLY
    raw = await asyncio.wait_for(ai_brain._call_llm_raw(prompt, cfg, temperature=0.55, num_predict=300),
                                 timeout=REPLY_AI_TIMEOUT_S)
    text = clean_reply(raw)
    return text[:limit] if text else None


async def send_reply(message_id: int, text: str, source: str = "manual") -> Dict[str, Any]:
    """THE one way an answer to a comment or DM goes out."""
    text = (text or "").strip()
    if not text:
        raise SocialError("Write the reply.")
    m = await _message(message_id)
    if m["direction"] != "IN":
        raise SocialError("You can only answer incoming comments and messages.")
    if await _opted_out(m):
        raise SocialError("This person asked not to be contacted.")
    if m.get("lead_id"):
        lead = await db.portal_fetchrow("SELECT status FROM leads WHERE id = ?", m["lead_id"])
        if lead and lead["status"] == "DO_NOT_CONTACT":
            raise SocialError("This lead is marked Do not contact.")
    a = await db.portal_fetchrow("SELECT * FROM social_accounts WHERE id = ?", m["account_id"])
    token = decrypt(a["token_enc"]) or ""
    if m["kind"] == "comment":
        ext = await meta.reply_comment(a["platform"], m["external_id"], token, text[:MAX_COMMENT_REPLY])
    else:
        page = _extra(a).get("page_id") or a["external_id"]
        ext = await meta.send_dm(page, token, m["author_id"], text[:MAX_DM_REPLY])
    await db.portal_execute(
        """INSERT INTO social_messages (account_id, kind, external_id, thread_id, author_id, author_name, text,
                                        direction, status, lead_id, created_at)
           VALUES (?, ?, ?, ?, ?, 'You', ?, 'OUT', ?, ?, ?) ON CONFLICT (account_id, external_id) DO NOTHING""",
        a["id"], m["kind"], ext or f"out-{secrets.token_hex(8)}", m["thread_id"], m["author_id"], text,
        "AUTO" if source == "auto" else "SENT", m.get("lead_id"), _iso(_utc()))
    await db.portal_execute("UPDATE social_messages SET status = 'REPLIED', handled_at = ? WHERE id = ?",
                            _iso(_utc()), message_id)
    where = "comment" if m["kind"] == "comment" else "message"
    await log("auto_reply" if source == "auto" else "reply",
              f"Answered {m['author_name'] or 'someone'}'s {where} on {a['platform'].title()}: “{text[:120]}”")
    return await _message(message_id)


async def _opted_out(m: Dict[str, Any]) -> bool:
    return bool(await db.portal_fetchrow(
        "SELECT 1 AS x FROM social_messages WHERE account_id = ? AND author_id = ? AND status = 'OPTOUT' LIMIT 1",
        m["account_id"], m["author_id"]))


async def _set(message_id: int, status: str, lead_id: Optional[int] = None) -> None:
    await db.portal_execute(
        "UPDATE social_messages SET status = ?, lead_id = COALESCE(?, lead_id), handled_at = ? WHERE id = ?",
        status, lead_id, _iso(_utc()), message_id)


async def process_new(send=None, limit: int = 10) -> int:
    """Handle NEW incoming comments/DMs: opt-outs, leads, automatic answers."""
    from ..intelligence.reply_intelligence_agent import is_opt_out_phrase
    s = await get_settings()
    rows = await db.portal_fetch(
        """SELECT m.*, a.platform FROM social_messages m JOIN social_accounts a ON a.id = m.account_id
           WHERE m.status = 'NEW' AND m.direction = 'IN' ORDER BY m.created_at, m.id LIMIT ?""", limit)
    handled = 0
    for m in rows:
        handled += 1
        a = await db.portal_fetchrow("SELECT * FROM social_accounts WHERE id = ?", m["account_id"])
        lead_id = m.get("lead_id")
        if lead_id is None and wants_lead(m):
            lead_id = await _lead_for(a, m)
        if is_opt_out_phrase(m["text"]):
            await _set(m["id"], "OPTOUT", lead_id)
            if lead_id:
                await db.update_lead(lead_id, {"status": "DO_NOT_CONTACT"})
            await log("optout", f"{m['author_name'] or 'Someone'} asked not to be contacted on {a['platform'].title()} — no more replies.")
            continue
        if await _opted_out(m):
            await _set(m["id"], "SKIPPED", lead_id)
            continue
        if lead_id:
            lead = await db.portal_fetchrow("SELECT status FROM leads WHERE id = ?", lead_id)
            if lead and lead["status"] == "DO_NOT_CONTACT":
                await _set(m["id"], "SKIPPED", lead_id)
                continue
        await db.portal_execute("UPDATE social_messages SET lead_id = COALESCE(?, lead_id) WHERE id = ?", lead_id, m["id"])
        if not s["social_auto_reply"] or (m["kind"] == "comment" and not s["social_reply_comments"]):
            await _set(m["id"], "SEEN", lead_id)
            continue
        if m["kind"] == "dm":
            newer = await db.portal_fetchrow(
                """SELECT 1 AS x FROM social_messages WHERE account_id = ? AND thread_id = ? AND direction = 'IN'
                   AND status = 'NEW' AND id != ? AND created_at >= ? LIMIT 1""",
                m["account_id"], m["thread_id"], m["id"], m["created_at"])
            if newer:
                await _set(m["id"], "SEEN", lead_id)       # the newest message in the chat gets the answer
                continue
        today = await db.portal_fetchrow(
            "SELECT count(*) AS n FROM social_messages WHERE account_id = ? AND author_id = ? AND status = 'AUTO' AND created_at >= ?",
            m["account_id"], m["author_id"], _today_start_utc())
        if today and today["n"] >= s["social_reply_per_person"]:
            await _set(m["id"], "SEEN", lead_id)
            await log("info", f"Daily automatic-reply limit reached for {m['author_name'] or 'this person'} — over to you.")
            continue
        try:
            text = await draft(m["id"])
        except asyncio.TimeoutError:
            text = None
        except Exception as exc:  # noqa: BLE001
            logger.exception("social draft failed: %s", exc)
            text = None
        if not text:
            await _set(m["id"], "SEEN", lead_id)
            await log("error", f"The AI couldn't answer {m['author_name'] or 'a'} {m['kind']} on {a['platform'].title()} — please reply yourself.")
            continue
        try:
            fresh = await _message(m["id"])
            if fresh["status"] != "NEW":
                continue                                   # you handled it meanwhile
            await (send or send_reply)(m["id"], text, "auto")
        except (SocialError, meta.MetaError) as exc:
            await _set(m["id"], "FAILED", lead_id)
            await log("error", f"Couldn't answer on {a['platform'].title()}: {str(exc)[:160]}")
    return handled


# ── Inbox views ──────────────────────────────────────────────────────────────

async def _message(message_id: int) -> Dict[str, Any]:
    m = await db.portal_fetchrow("SELECT * FROM social_messages WHERE id = ?", message_id)
    if not m:
        raise SocialError("Message not found.")
    return m


async def threads(limit: int = 60) -> List[Dict[str, Any]]:
    """One row per conversation (DM) or per comment, newest first."""
    rows = await db.portal_fetch(
        """SELECT m.*, a.platform, a.name AS account_name, l.status AS lead_status
           FROM social_messages m JOIN social_accounts a ON a.id = m.account_id
           LEFT JOIN leads l ON l.id = m.lead_id
           WHERE m.direction = 'IN' ORDER BY m.created_at DESC, m.id DESC LIMIT 400""")
    out, seen = [], set()
    for r in rows:
        key = (r["account_id"], r["thread_id"]) if r["kind"] == "dm" else ("c", r["id"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= limit:
            break
    return out


async def thread(message_id: int) -> List[Dict[str, Any]]:
    """The conversation a message belongs to (a DM chat, or a comment and your answers to it)."""
    m = await _message(message_id)
    if m["kind"] == "dm":
        return await db.portal_fetch(
            "SELECT * FROM social_messages WHERE account_id = ? AND thread_id = ? ORDER BY created_at, id",
            m["account_id"], m["thread_id"])
    return [m] + await db.portal_fetch(
        """SELECT * FROM social_messages WHERE account_id = ? AND thread_id = ? AND direction = 'OUT'
           AND kind = 'comment' AND author_id = ? AND created_at >= ? ORDER BY created_at, id""",
        m["account_id"], m["thread_id"], m["author_id"], m["created_at"])


async def mark(message_id: int, status: str) -> Dict[str, Any]:
    if status not in ("SEEN", "SKIPPED"):
        raise SocialError("Unknown status.")
    await _set(message_id, status)
    return await _message(message_id)


async def summary() -> Dict[str, Any]:
    row = await db.portal_fetchrow(
        """SELECT sum(CASE WHEN status = 'NEW' THEN 1 ELSE 0 END) AS new,
                  sum(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS failed,
                  sum(CASE WHEN status = 'AUTO' AND created_at >= ? THEN 1 ELSE 0 END) AS auto_today
           FROM social_messages""", _today_start_utc())
    return {k: int((row or {}).get(k) or 0) for k in ("new", "failed", "auto_today")}
