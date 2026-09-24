"""
service.py — WhatsApp Campaigns: paced campaign sending, incoming-message
handling, automatic AI replies, and the activity log the monitor shows.

Flow
  n8n "HOM · WhatsApp campaign pacer" → POST /api/whatsapp/hooks/tick (every minute)
      → tick(): sends at most ONE due campaign message, within the owner's
        limits (working hours, daily cap, a pause of min–max seconds between
        messages).
  WAHA → n8n "HOM · WhatsApp inbound" → POST /api/whatsapp/hooks/event
      → handle_event(): saves the message, marks the lead REPLIED, and —
        opt-outs aside — lets the AI answer automatically (owner's choice),
        with guardrails: only leads HOM messaged, never groups or unknown
        contacts, a per-chat daily cap, and a Do-Not-Contact check right
        before every send.

Every message goes out through whatsapp_sender.send_whatsapp — the one
WhatsApp send path.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .. import database as db
from ..intelligence.reply_intelligence_agent import is_opt_out_phrase

logger = logging.getLogger(__name__)

# ── Settings (app_settings "wa_*"; the owner edits them on the page) ────────

DEFAULTS: Dict[str, Any] = {
    "wa_daily_limit": 30,          # campaign messages per day
    "wa_min_gap": 90,              # seconds between campaign messages …
    "wa_max_gap": 240,             # … picked at random in this range
    "wa_hours_start": 9,           # send only between these local hours
    "wa_hours_end": 19,
    "wa_auto_reply": True,         # answer messages automatically (owner's choice)
    "wa_reply_scope": "everyone",  # everyone (1:1 chats) | leads (only leads HOM messaged)
    "wa_engine": "web",            # web (free WhatsApp Web, QR) | meta (official Cloud API, your keys)
    "wa_auto_reply_per_chat": 20,  # automatic answers per chat per day (stops bot-to-bot loops)
    "wa_reply_delay_min": 20,      # seconds before answering (time to read)
    "wa_reply_delay_max": 60,
}
_BOUNDS = {"wa_daily_limit": (1, 200), "wa_min_gap": (30, 3600), "wa_max_gap": (30, 7200),
           "wa_hours_start": (0, 23), "wa_hours_end": (1, 24), "wa_auto_reply_per_chat": (0, 50),
           "wa_reply_delay_min": (0, 600), "wa_reply_delay_max": (0, 900)}
CAMPAIGN_OK_STATUSES = ("PENDING", "MESSAGES_READY", "ENRICHED", "SCORED", "SENT")
BLOCKED = ("DO_NOT_CONTACT",)
MAX_REPLY_CHARS = 700
MAX_MESSAGE_AGE_S = 30 * 60        # older messages (e.g. delivered after a reconnect) aren't answered
REPLY_SCOPES = ("everyone", "leads")
ENGINES = ("web", "meta")
_CHOICES = {"wa_reply_scope": REPLY_SCOPES, "wa_engine": ENGINES}
REPLY_AI_TIMEOUT_S = 240           # the local AI must answer within this, or the chat is handed to you
_reply_tasks: set = set()          # keep background replies referenced until they finish


class WhatsAppError(ValueError):
    pass


def _now() -> datetime:
    """Local time (sending hours follow the owner's clock; TZ is set by start.sh)."""
    return datetime.now()


def _utc() -> datetime:
    """UTC, the way SQLite's CURRENT_TIMESTAMP stores created_at."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _today_start_utc() -> str:
    """Local midnight, expressed in UTC — for "today" counts over created_at."""
    midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return _iso(midnight.astimezone(timezone.utc).replace(tzinfo=None))


def _iso(dt: datetime) -> str:
    return dt.isoformat(sep=" ", timespec="seconds")


async def current_engine() -> str:
    """'meta' (official Cloud API) or 'web' (WhatsApp Web engine). Client
    workspaces always use Meta — the Web engine is the owner's own number."""
    from . import engine
    if not engine.available():
        return "meta"
    return (await get_settings())["wa_engine"]


async def get_settings() -> Dict[str, Any]:
    stored = await db.get_all_settings()
    out = dict(DEFAULTS)
    for k, default in DEFAULTS.items():
        raw = stored.get(k)
        if raw in (None, ""):
            continue
        if isinstance(default, bool):
            out[k] = str(raw).lower() in ("1", "true", "yes", "on")
        elif isinstance(default, str):
            out[k] = raw if raw in _CHOICES[k] else default
        else:
            try:
                out[k] = int(raw)
            except ValueError:
                pass
    return out


async def save_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in patch.items():
        if k not in DEFAULTS or v is None:
            continue
        if isinstance(DEFAULTS[k], bool):
            await db.upsert_setting(k, "true" if v else "false")
            continue
        if isinstance(DEFAULTS[k], str):
            if v not in _CHOICES[k]:
                raise WhatsAppError("Choose one of the options.")
            from . import engine
            if k == "wa_engine" and v == "web" and not engine.available():
                raise WhatsAppError("This workspace has no WhatsApp Web engine — connect with your Meta API.")
            await db.upsert_setting(k, v)
            continue
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise WhatsAppError(f"{k} must be a number")
        lo, hi = _BOUNDS[k]
        if not lo <= n <= hi:
            raise WhatsAppError(f"{k} must be between {lo} and {hi}")
        await db.upsert_setting(k, str(n))
    s = await get_settings()
    from . import engine
    if not engine.available():
        s["wa_engine"] = "meta"       # no WhatsApp Web engine here: only the Meta API
    if s["wa_max_gap"] < s["wa_min_gap"] or s["wa_reply_delay_max"] < s["wa_reply_delay_min"]:
        raise WhatsAppError("The longest pause can't be shorter than the shortest.")
    if s["wa_hours_end"] <= s["wa_hours_start"]:
        raise WhatsAppError("Sending hours must end after they start.")
    return s


# ── Activity log (the monitor) ───────────────────────────────────────────────

async def log(kind: str, text: str, lead_id: Optional[int] = None) -> None:
    await db.portal_execute("INSERT INTO whatsapp_activity (kind, lead_id, text) VALUES (?, ?, ?)",
                            kind, lead_id, text[:500])
    await db.portal_execute(
        "DELETE FROM whatsapp_activity WHERE id <= (SELECT max(id) - 2000 FROM whatsapp_activity)")


async def activity(limit: int = 80) -> List[Dict[str, Any]]:
    return await db.portal_fetch(
        """SELECT a.*, l.business_name FROM whatsapp_activity a LEFT JOIN leads l ON l.id = a.lead_id
           ORDER BY a.id DESC LIMIT ?""", max(1, min(limit, 300)))


# ── Phones ───────────────────────────────────────────────────────────────────

def digits(phone: Optional[str]) -> str:
    return re.sub(r"\D", "", phone or "")


def to_e164(phone: str) -> str:
    from ..whatsapp_sender import _normalize_phone
    return _normalize_phone(phone)


async def find_lead_by_whatsapp(chat_digits: str) -> Optional[Dict[str, Any]]:
    """Match an incoming chat to a lead: same number, allowing for a missing
    country code on the stored phone (at least 8 matching trailing digits)."""
    if len(chat_digits) < 8:
        return None
    rows = await db.portal_fetch("SELECT * FROM leads WHERE phone IS NOT NULL AND phone != ''")
    best = None
    for r in rows:
        d = digits(r["phone"])
        if len(d) < 8:
            continue
        if d == chat_digits or chat_digits.endswith(d) or d.endswith(chat_digits):
            if best is None or len(d) > len(digits(best["phone"])):
                best = r
    return best


# ── Campaigns ────────────────────────────────────────────────────────────────

def render(template: str, lead: Dict[str, Any]) -> str:
    """{business_name} {city} {niche} {first_name} → the lead's values."""
    first = ""
    dm = (lead.get("contact_name") or lead.get("decision_maker") or lead.get("owner_name") or "").strip()
    if dm:
        first = dm.split()[0]
    values = {"business_name": lead.get("business_name") or "", "city": lead.get("city") or "",
              "niche": lead.get("niche") or "", "first_name": first}
    out = re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), template)
    return re.sub(r"[ \t]{2,}", " ", out).replace(" ,", ",").strip()


async def audience(statuses: Optional[List[str]] = None, labels: Optional[List[str]] = None,
                   niche: Optional[str] = None, city: Optional[str] = None,
                   need_ai_draft: bool = False) -> List[Dict[str, Any]]:
    """Leads a campaign may message: with a phone, not opted out, not already
    in a conversation (REPLIED / pipeline stages)."""
    ok = [s for s in (statuses or CAMPAIGN_OK_STATUSES) if s in CAMPAIGN_OK_STATUSES] or list(CAMPAIGN_OK_STATUSES)
    sql = f"SELECT * FROM leads WHERE phone IS NOT NULL AND phone != '' AND status IN ({','.join('?' * len(ok))})"
    args: List[Any] = list(ok)
    if labels:
        sql += f" AND score_label IN ({','.join('?' * len(labels))})"
        args += labels
    if niche:
        sql += " AND lower(niche) LIKE ?"
        args.append(f"%{niche.lower()}%")
    if city:
        sql += " AND lower(city) LIKE ?"
        args.append(f"%{city.lower()}%")
    if need_ai_draft:
        sql += " AND ai_whatsapp_msg IS NOT NULL AND trim(ai_whatsapp_msg) != ''"
    rows = await db.portal_fetch(sql + " ORDER BY score DESC, id", *args)
    seen, out = set(), []
    for r in rows:                       # one message per number
        d = digits(r["phone"])
        if len(d) >= 8 and d not in seen:
            seen.add(d)
            out.append(r)
    return out


async def create_campaign(name: str, template: Optional[str], use_ai_drafts: bool, lead_ids: List[int],
                          meta_template: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    import json
    name = (name or "").strip()[:120]
    if not name:
        raise WhatsAppError("Give the campaign a name.")
    template = (template or "").strip()
    if await current_engine() == "meta":
        # Meta only allows an approved template as the first message to someone.
        mt = meta_template or {}
        if not mt.get("name"):
            raise WhatsAppError("With the Meta API, choose an approved message template.")
        allowed = {"business_name", "first_name", "city", "niche"}
        mt = {"name": str(mt["name"])[:200], "language": str(mt.get("language") or "en")[:20],
              "body": str(mt.get("body") or "")[:2000],
              "vars": [v if v in allowed else "business_name" for v in (mt.get("vars") or [])][:10]}
        template, use_ai_drafts = mt["body"] or f"[template {mt['name']}]", False
        meta_json = json.dumps(mt)
    else:
        meta_json = None
    if not use_ai_drafts and len(template) < 10:
        raise WhatsAppError("Write the message (at least a sentence).")
    if len(template) > 1500:
        raise WhatsAppError("Keep the message under 1,500 characters.")
    if not lead_ids:
        raise WhatsAppError("No leads selected.")
    cid = await db.portal_insert(
        "INSERT INTO whatsapp_campaigns (name, template, use_ai_drafts, meta_template) VALUES (?, ?, ?, ?) RETURNING id",
        name, template or None, 1 if use_ai_drafts else 0, meta_json)
    added = 0
    for lid in lead_ids:
        lead = await db.portal_fetchrow("SELECT id, phone, status FROM leads WHERE id = ?", int(lid))
        if not lead or not lead.get("phone") or lead["status"] not in CAMPAIGN_OK_STATUSES:
            continue
        added += await db.portal_execute(
            "INSERT OR IGNORE INTO whatsapp_campaign_recipients (campaign_id, lead_id, phone) VALUES (?, ?, ?)",
            cid, lead["id"], lead["phone"])
    if not added:
        await db.portal_execute("DELETE FROM whatsapp_campaigns WHERE id = ?", cid)
        raise WhatsAppError("None of those leads can be messaged (no phone, or already in a conversation).")
    await log("info", f"Campaign “{name}” created for {added} lead{'s' if added != 1 else ''}.")
    return await get_campaign(cid)


async def get_campaign(cid: int) -> Dict[str, Any]:
    c = await db.portal_fetchrow("SELECT * FROM whatsapp_campaigns WHERE id = ?", cid)
    if not c:
        raise WhatsAppError("Campaign not found.")
    counts = await db.portal_fetch(
        "SELECT status, count(*) AS n FROM whatsapp_campaign_recipients WHERE campaign_id = ? GROUP BY status", cid)
    c["counts"] = {r["status"]: r["n"] for r in counts}
    c["total"] = sum(c["counts"].values())
    return c


async def list_campaigns() -> List[Dict[str, Any]]:
    return [await get_campaign(r["id"]) for r in await db.portal_fetch("SELECT id FROM whatsapp_campaigns ORDER BY id DESC")]


async def set_campaign_status(cid: int, action: str) -> Dict[str, Any]:
    c = await get_campaign(cid)
    now = _iso(_utc())
    moves = {"start": (("DRAFT", "PAUSED"), "RUNNING"), "pause": (("RUNNING",), "PAUSED"),
             "cancel": (("DRAFT", "RUNNING", "PAUSED"), "CANCELLED")}
    if action not in moves:
        raise WhatsAppError("Unknown action.")
    allowed, new = moves[action]
    if c["status"] not in allowed:
        raise WhatsAppError(f"A {c['status'].lower()} campaign can't be {'started' if action == 'start' else action + 'ed'}.")
    await db.portal_execute(
        "UPDATE whatsapp_campaigns SET status = ?, started_at = COALESCE(started_at, CASE WHEN ? = 'RUNNING' THEN ? END),"
        " finished_at = CASE WHEN ? = 'CANCELLED' THEN ? ELSE finished_at END WHERE id = ?",
        new, new, now, new, now, cid)
    await log("info", f"Campaign “{c['name']}” {'started' if action == 'start' else 'paused' if action == 'pause' else 'cancelled'}.")
    return await get_campaign(cid)


async def _sent_today() -> int:
    row = await db.portal_fetchrow(
        "SELECT count(*) AS n FROM whatsapp_messages WHERE direction = 'OUT' AND source = 'campaign' AND created_at >= ?",
        _today_start_utc())
    return int(row["n"]) if row else 0


async def _next_send_at() -> Optional[datetime]:
    raw = await db.get_setting("wa_next_send_at")
    try:
        return datetime.fromisoformat(raw) if raw else None
    except ValueError:
        return None


async def pacing(settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Why the pacer is (not) sending right now — shown on the monitor."""
    s = settings or await get_settings()
    now = _now()
    sent = await _sent_today()
    nxt = await _next_send_at()
    utc_now = _utc()
    if not (s["wa_hours_start"] <= now.hour < s["wa_hours_end"]):
        state = f"Outside sending hours ({s['wa_hours_start']:02d}:00–{s['wa_hours_end']:02d}:00)"
    elif sent >= s["wa_daily_limit"]:
        state = f"Daily limit reached ({sent}/{s['wa_daily_limit']})"
    elif nxt and nxt > utc_now:
        state = f"Next message in {int((nxt - utc_now).total_seconds())}s"
    else:
        state = "Ready"
    return {"sent_today": sent, "daily_limit": s["wa_daily_limit"], "state": state,
            "next_send_at": _iso(nxt) if nxt else None}


async def tick(send=None, is_ready=None) -> Dict[str, Any]:
    """One pacer step (n8n calls this every minute). Sends at most one message."""
    from ..whatsapp_sender import send_whatsapp
    send = send or send_whatsapp
    is_ready = is_ready or ready
    s = await get_settings()
    p = await pacing(s)
    if p["state"] != "Ready":
        return {"sent": False, "reason": p["state"]}
    rec = await db.portal_fetchrow(
        """SELECT r.*, c.name AS campaign_name, c.template, c.use_ai_drafts, c.meta_template FROM whatsapp_campaign_recipients r
           JOIN whatsapp_campaigns c ON c.id = r.campaign_id
           WHERE c.status = 'RUNNING' AND r.status = 'PENDING' ORDER BY c.id, r.id LIMIT 1""")
    if not rec:
        await _finish_empty_campaigns()
        return {"sent": False, "reason": "Nothing to send"}
    if not await is_ready():
        return {"sent": False, "reason": "WhatsApp isn't connected"}

    lead = await db.portal_fetchrow("SELECT * FROM leads WHERE id = ?", rec["lead_id"])   # fresh read
    if not lead or lead["status"] not in CAMPAIGN_OK_STATUSES:
        why = "opted out" if lead and lead["status"] == "DO_NOT_CONTACT" else f"status {lead['status'] if lead else 'deleted'}"
        await _set_recipient(rec["id"], "SKIPPED", error=why)
        await log("skipped", f"Skipped {lead['business_name'] if lead else 'a deleted lead'} ({why}).", rec["lead_id"])
        return {"sent": False, "reason": f"Skipped ({why})"}
    send_cfg: Dict[str, Any] = {}
    if rec.get("meta_template"):
        import json
        from . import meta
        mt = json.loads(rec["meta_template"])
        params = [render("{" + v + "}", lead) or "-" for v in mt.get("vars") or []]
        send_cfg = {"template": {"name": mt["name"], "language": mt.get("language") or "en", "params": params}}
        text = meta.fill_template(mt.get("body") or f"[template {mt['name']}]", params)
    elif await current_engine() == "meta":
        await _set_recipient(rec["id"], "SKIPPED", error="Meta needs an approved template")
        await log("skipped", f"Skipped {lead['business_name']} — with the Meta API the first message must be a template.", lead["id"])
        return {"sent": False, "reason": "Skipped (needs a template)"}
    else:
        text = (lead.get("ai_whatsapp_msg") or "").strip() if rec["use_ai_drafts"] else render(rec["template"] or "", lead)
    if not text:
        await _set_recipient(rec["id"], "SKIPPED", error="no approved WhatsApp draft")
        await log("skipped", f"Skipped {lead['business_name']} — no approved WhatsApp draft.", lead["id"])
        return {"sent": False, "reason": "Skipped (no draft)"}

    phone = to_e164(rec["phone"])
    ok = await send(phone, text, send_cfg)
    gap = random.randint(s["wa_min_gap"], s["wa_max_gap"])
    await db.upsert_setting("wa_next_send_at", _iso(_utc() + timedelta(seconds=gap)))
    if not ok:
        await _set_recipient(rec["id"], "FAILED", error="WhatsApp didn't accept the message")
        await log("error", f"Couldn't send to {lead['business_name']} ({phone}).", lead["id"])
        return {"sent": False, "reason": "Send failed"}
    await _set_recipient(rec["id"], "SENT", message=text)
    await _record(lead["id"], phone, "OUT", text, "campaign")
    if lead["status"] in ("PENDING", "MESSAGES_READY", "ENRICHED", "SCORED"):
        await db.update_lead(lead["id"], {"status": "SENT"})
    await log("sent", f"Sent to {lead['business_name']} — “{rec['campaign_name']}”.", lead["id"])
    return {"sent": True, "lead_id": lead["id"], "next_in_seconds": gap}


async def pacer_loop(every_s: float = 60.0) -> None:
    """Client workspaces: call tick() every minute (the owner's n8n does this
    for the owner's dashboard)."""
    while True:
        try:
            await asyncio.sleep(every_s)
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — keep pacing
            logger.warning("WhatsApp pacer: %s", exc)


async def _set_recipient(rid: int, status: str, message: Optional[str] = None, error: Optional[str] = None) -> None:
    await db.portal_execute(
        "UPDATE whatsapp_campaign_recipients SET status = ?, message = COALESCE(?, message), error = ?,"
        " sent_at = CASE WHEN ? = 'SENT' THEN ? ELSE sent_at END WHERE id = ?",
        status, message, error, status, _iso(_utc()), rid)


async def _finish_empty_campaigns() -> None:
    for c in await db.portal_fetch(
            """SELECT id, name FROM whatsapp_campaigns c WHERE status = 'RUNNING' AND NOT EXISTS (
               SELECT 1 FROM whatsapp_campaign_recipients r WHERE r.campaign_id = c.id AND r.status = 'PENDING')"""):
        await db.portal_execute("UPDATE whatsapp_campaigns SET status = 'DONE', finished_at = ? WHERE id = ?",
                                _iso(_utc()), c["id"])
        await log("info", f"Campaign “{c['name']}” finished.")


async def _record(lead_id: Optional[int], phone_or_chat: str, direction: str, body: str, source: str,
                  wa_id: Optional[str] = None) -> bool:
    chat = phone_or_chat if "@" in phone_or_chat else f"{digits(phone_or_chat)}@c.us"
    try:
        await db.portal_execute(
            "INSERT INTO whatsapp_messages (lead_id, chat_id, direction, body, source, wa_message_id) VALUES (?, ?, ?, ?, ?, ?)",
            lead_id, chat, direction, body, source, wa_id)
        return True
    except Exception:  # noqa: BLE001 — duplicate event (same WhatsApp message id)
        return False


async def chats(limit: int = 30) -> List[Dict[str, Any]]:
    """Latest WhatsApp conversations for the live monitor: one row per chat."""
    return await db.portal_fetch(
        """SELECT m.chat_id, m.lead_id, m.body AS last_body, m.direction AS last_direction, m.source AS last_source,
                  m.created_at AS last_at, l.business_name,
                  (SELECT count(*) FROM whatsapp_messages x WHERE x.chat_id = m.chat_id) AS messages
           FROM whatsapp_messages m LEFT JOIN leads l ON l.id = m.lead_id
           WHERE m.id = (SELECT max(id) FROM whatsapp_messages y WHERE y.chat_id = m.chat_id)
           ORDER BY m.id DESC LIMIT ?""", max(1, min(limit, 100)))


async def conversation(lead_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    rows = await db.portal_fetch(
        "SELECT * FROM whatsapp_messages WHERE lead_id = ? ORDER BY id DESC LIMIT ?", lead_id, limit)
    return list(reversed(rows))


# ── Incoming messages ────────────────────────────────────────────────────────

async def ready() -> bool:
    """Is the chosen WhatsApp connection able to send right now?"""
    if await current_engine() == "meta":
        from . import meta
        return meta.configured(await meta.config())
    from . import engine
    return await engine.ready()


def parse_event(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """WAHA webhook → {chat, digits, body, id} for a real incoming 1:1 text; else None."""
    if event.get("event") != "message":
        return None
    p = event.get("payload") or {}
    chat = str(p.get("from") or "")
    if p.get("fromMe") or not (chat.endswith("@c.us") or chat.endswith("@lid")):   # own, groups, broadcasts, channels
        return None
    body = (p.get("body") or "").strip()
    if not body:
        return None
    mid = p.get("id")
    mid = mid.get("_serialized") if isinstance(mid, dict) else mid
    raw = p.get("_data") if isinstance(p.get("_data"), dict) else {}
    info = raw.get("Info") if isinstance(raw.get("Info"), dict) else {}
    name = (p.get("notifyName") or raw.get("notifyName") or info.get("PushName") or "").strip()[:80]
    try:
        ts = float(p.get("timestamp") or 0)
    except (TypeError, ValueError):
        ts = 0.0
    return {"chat": chat, "digits": digits(chat.split("@")[0]), "body": body[:4000], "id": mid,
            "name": name, "timestamp": ts}


async def handle_event(event: Dict[str, Any], reply_now: bool = False) -> Dict[str, Any]:
    msg = parse_event(event)
    if msg and msg["chat"].endswith("@lid"):
        from . import engine
        phone = await engine.lid_to_phone(msg["chat"])
        if not phone:
            await log("received", "Message from a contact whose number WhatsApp keeps private — no automatic reply.")
            return {"handled": False, "private_number": True}
        msg["digits"], msg["chat"] = phone, f"{phone}@c.us"
    if not msg:
        if event.get("event") == "session.status":
            state = str((event.get("payload") or {}).get("status") or "")
            if state and state != await db.get_setting("wa_last_session_state"):
                await db.upsert_setting("wa_last_session_state", state)
                await log("info", {"WORKING": "WhatsApp is connected.", "SCAN_QR_CODE": "Waiting for the QR code to be scanned.",
                                   "STARTING": "WhatsApp is starting…", "FAILED": "WhatsApp connection failed — reconnect it.",
                                   "STOPPED": "WhatsApp is disconnected."}.get(state, f"WhatsApp: {state.lower()}."))
        return {"handled": False}
    s = await get_settings()
    lead = await find_lead_by_whatsapp(msg["digits"])
    if not lead and s["wa_reply_scope"] == "everyone" and await db.portal_fetchrow(
            "SELECT 1 AS x FROM whatsapp_messages WHERE wa_message_id = ?", msg["id"]) is None:
        lead = await _lead_for_new_contact(msg)
    if not await _record(lead["id"] if lead else None, msg["chat"], "IN", msg["body"], "inbound", msg["id"]):
        return {"handled": False, "duplicate": True}
    if not lead:
        await log("received", f"Message from +{msg['digits']} (not one of your leads) — no automatic reply.")
        return {"handled": True, "lead": None}

    await db.portal_execute(
        "UPDATE whatsapp_campaign_recipients SET status = 'REPLIED' WHERE lead_id = ? AND status = 'SENT'", lead["id"])
    if is_opt_out_phrase(msg["body"]):
        await db.update_lead(lead["id"], {"status": "DO_NOT_CONTACT"})
        await db.portal_execute(
            "UPDATE whatsapp_campaign_recipients SET status = 'SKIPPED', error = 'opted out' WHERE lead_id = ? AND status = 'PENDING'",
            lead["id"])
        await log("opt_out", f"{lead['business_name']} asked not to be contacted — marked Do not contact. No reply sent.", lead["id"])
        return {"handled": True, "opt_out": True}
    if lead["status"] not in BLOCKED and lead["status"] not in ("INTERESTED", "MEETING", "PROPOSAL", "WON", "LOST"):
        await db.update_lead(lead["id"], {"status": "REPLIED"})
    await log("received", f"{lead['business_name']}: “{msg['body'][:140]}”", lead["id"])

    if not s["wa_auto_reply"]:
        return {"handled": True, "auto_reply": "off"}
    if msg["timestamp"] and message_age_s(msg["timestamp"]) > MAX_MESSAGE_AGE_S:
        await log("info", "Older message (delivered late) — not answered automatically.", lead["id"])
        return {"handled": True, "auto_reply": "too old"}
    task = _auto_reply(lead["id"], msg, s)
    if reply_now:
        return {"handled": True, "auto_reply": await task}
    t = asyncio.create_task(task)
    _reply_tasks.add(t)
    t.add_done_callback(_reply_tasks.discard)
    return {"handled": True, "auto_reply": "scheduled"}


def message_age_s(ts: float) -> float:
    if ts > 1e12:                          # milliseconds
        ts /= 1000.0
    sent = datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)
    return (_utc() - sent).total_seconds()


async def _lead_for_new_contact(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Someone new wrote to your WhatsApp: keep them as a lead (source WHATSAPP)
    so the chat has a name, a history and an opt-out status."""
    label = msg.get("name") or f"+{msg['digits']}"
    try:
        lid = await db.create_lead({"business_name": f"WhatsApp · {label}", "phone": f"+{msg['digits']}",
                                    "source": "WHATSAPP", "niche": "WhatsApp enquiry"})
        await db.update_lead(lid, {"status": "REPLIED"})
    except Exception as exc:  # noqa: BLE001 — e.g. the number already belongs to a lead
        logger.info("new WhatsApp contact not saved as a lead: %s", exc)
        return await find_lead_by_whatsapp(msg["digits"])
    await log("info", f"New WhatsApp contact {label} saved as a lead.", lid)
    return await db.portal_fetchrow("SELECT * FROM leads WHERE id = ?", lid)


async def _auto_reply(lead_id: int, msg: Dict[str, Any], s: Dict[str, Any], send=None, delay: Optional[float] = None) -> str:
    """Answer one incoming message automatically, with the guardrails."""
    from ..whatsapp_sender import send_whatsapp
    send = send or send_whatsapp
    try:
        we_messaged = await db.portal_fetchrow(
            "SELECT 1 AS x FROM whatsapp_messages WHERE chat_id = ? AND direction = 'OUT' LIMIT 1", msg["chat"])
        if not we_messaged and s.get("wa_reply_scope") == "leads":
            await log("info", "Reply not automatic — you haven't messaged this lead on WhatsApp yet.", lead_id)
            return "not contacted"
        today = await db.portal_fetchrow(
            "SELECT count(*) AS n FROM whatsapp_messages WHERE chat_id = ? AND source = 'auto_reply' AND created_at >= ?",
            msg["chat"], _today_start_utc())
        if today and today["n"] >= s["wa_auto_reply_per_chat"]:
            await log("info", "Daily automatic-reply limit reached for this chat — over to you.", lead_id)
            return "limit"
        try:
            text = await draft_reply(lead_id, msg["chat"])
        except asyncio.TimeoutError:
            await log("error", "The AI took too long to answer — please reply to this one yourself.", lead_id)
            return "ai timeout"
        if not text:
            await log("error", "The AI couldn't write a good reply — please answer this one yourself.", lead_id)
            return "no draft"
        wait = delay if delay is not None else random.uniform(s["wa_reply_delay_min"], s["wa_reply_delay_max"])
        await asyncio.sleep(max(0.0, wait))
        # Fresh checks right before sending: opted out meanwhile? a newer message arrived?
        lead = await db.portal_fetchrow("SELECT * FROM leads WHERE id = ?", lead_id)
        if not lead or lead["status"] in BLOCKED:
            await log("skipped", "Reply cancelled — the lead is marked Do not contact.", lead_id)
            return "blocked"
        latest = await db.portal_fetchrow(
            "SELECT wa_message_id FROM whatsapp_messages WHERE chat_id = ? AND direction = 'IN' ORDER BY id DESC LIMIT 1", msg["chat"])
        if latest and msg.get("id") and latest["wa_message_id"] != msg["id"]:
            return "superseded"          # a newer message will be answered instead
        ok = await send("+" + msg["digits"], text, {})
        if not ok:
            await log("error", f"Couldn't send the automatic reply to {lead['business_name']}.", lead_id)
            return "send failed"
        await _record(lead_id, msg["chat"], "OUT", text, "auto_reply")
        await log("auto_reply", f"Replied to {lead['business_name']}: “{text[:140]}”", lead_id)
        return "sent"
    except Exception as exc:  # noqa: BLE001 — never let a reply crash the service
        logger.exception("auto reply failed: %s", exc)
        await log("error", "Automatic reply failed — please answer this one yourself.", lead_id)
        return "error"


def offer_digest() -> str:
    """What the business offers, in a few compact lines (from the service catalogue)."""
    try:
        from ..intelligence.service_knowledge_base import SERVICE_KNOWLEDGE_BASE
    except Exception:  # noqa: BLE001
        return ""
    lines = []
    for svc in SERVICE_KNOWLEDGE_BASE:
        uses = "; ".join(svc.use_cases[:2])
        benefit = svc.benefits[0] if svc.benefits else ""
        lines.append(f"- {svc.name}: {uses}. {benefit}.")
    return "\n".join(lines)


def _contact_first_name(lead: Dict[str, Any]) -> str:
    name = lead.get("business_name") or ""
    if lead.get("source") == "WHATSAPP" and "·" in name:
        label = name.split("·", 1)[1].strip()
        return "" if label.startswith("+") else label.split()[0]
    return ""


_PRICE_Q = re.compile(r"\b(price|pricing|cost|costs|how much|charge|fee|fees|budget|rate|quote|package)\b|দাম|খরচ|কত টাকা|কত", re.I)
_TIME_Q = re.compile(r"\b(how long|when|deadline|timeline|days?|weeks?|months?|asap|urgent)\b|কত দিন|কবে|সময়", re.I)


def reply_focus(latest: str) -> List[str]:
    """Deterministic hints for what the latest message needs answered first."""
    focus = []
    if _PRICE_Q.search(latest or ""):
        focus.append("They asked about PRICE. Start your reply by answering that: the cost depends on what they need, "
                     "and you'll send a clear quote once you know a few details. Then ask for the ONE detail that "
                     "matters most for the quote. Never state or guess a number.")
    if _TIME_Q.search(latest or ""):
        focus.append("They mentioned TIMING. Acknowledge their timeline in your reply and say you'll confirm what's "
                     "realistic once you know the scope — don't promise a date.")
    return focus


def build_reply_prompt(lead: Dict[str, Any], history: List[Dict[str, Any]], dna: str, offer: str,
                       avoid: Optional[str] = None) -> str:
    """The instructions for one WhatsApp reply. `history` is oldest-first."""
    convo = "\n".join(f"{'Them' if h['direction'] == 'IN' else 'You'}: {h['body']}" for h in history)
    latest = next((h["body"] for h in reversed(history) if h["direction"] == "IN"), "")
    we_spoke = any(h["direction"] == "OUT" for h in history)
    first = _contact_first_name(lead)
    opening = (
        "You have ALREADY written in this chat: do NOT greet them again, do NOT introduce yourself again and "
        "do NOT thank them for reaching out again — just continue the conversation naturally."
        if we_spoke else
        f"This is your first message in this chat: greet them briefly{' as ' + first if first else ''} and "
        "introduce yourself once (name and company, from the profile)."
    )
    focus = "".join(f"- {f}\n" for f in reply_focus(latest))
    retry = (f"\nYour previous draft repeated something already said in this chat:\n\"{avoid}\"\n"
             "Write a DIFFERENT reply that moves the conversation forward.\n") if avoid else ""
    return f"""You reply to WhatsApp messages for the business described below, as a real person from that business.
Write like a helpful, professional human: warm, clear and confident.

COMPANY PROFILE (who we are and how we talk):
{dna}

WHAT WE OFFER:
{offer or "(see the company profile)"}

ABOUT THIS CONTACT: {lead.get('business_name') or 'unknown'}{(' — ' + lead['niche']) if lead.get('niche') and lead.get('source') != 'WHATSAPP' else ''}

CONVERSATION SO FAR (oldest first; "You" = us):
{convo}

THEIR LATEST MESSAGE: "{latest}"

HOW TO REPLY:
{focus}- {opening}
- Answer their latest message directly and specifically. If they describe a need we offer, say clearly that we can
  help and mention one or two concrete, relevant things from WHAT WE OFFER. Then ask ONE useful question that moves
  things forward (their goal, what they sell, their timeline) or suggest a short call.
- Don't assume who they are or what industry they're in. The profile's examples (e.g. the kinds of clients we
  usually serve) describe US, not them — only use what THEY told you.
- If they ask about price or cost: say it depends on what they need, that you'll send a clear quote once you know
  a few details, and ask for the single most important detail. Never state a price.
- Exact timelines, guarantees or anything the profile doesn't cover: say you'll confirm once you understand their
  needs. Never invent numbers, clients, results or promises.
- Build on the specific details they just gave you (what they sell, their goal, their timeline) — acknowledge them.
  Don't restate what you already offered earlier in this chat, and never repeat a sentence you already sent.
- 2–4 short sentences. Plain language, in the same language they write in. No markdown, no lists, at most one emoji.
{retry}
Write ONLY the reply text."""


async def draft_reply(lead_id: int, chat: str) -> Optional[str]:
    """Write the next message in this WhatsApp conversation with the local AI,
    grounded in the Company DNA and the business's offer, never repeating itself."""
    from .. import ai_brain
    lead = await db.portal_fetchrow("SELECT * FROM leads WHERE id = ?", lead_id)
    history = list(reversed(await db.portal_fetch(
        "SELECT direction, body FROM whatsapp_messages WHERE chat_id = ? ORDER BY id DESC LIMIT 14", chat)))
    dna = ai_brain._load_company_dna().strip()[:3500]
    offer = offer_digest()
    sent_before = [h["body"] for h in history if h["direction"] == "OUT"]
    cfg = await ai_brain._ollama_cfg()
    text: Optional[str] = None
    avoid = None
    for attempt in range(2):
        # (_call_llm_raw takes the one-at-a-time AI slot itself — don't take it here too)
        raw = await asyncio.wait_for(
            ai_brain._call_llm_raw(build_reply_prompt(lead, history, dna, offer, avoid), cfg,
                                   temperature=0.55 + 0.25 * attempt, num_predict=420),
            timeout=REPLY_AI_TIMEOUT_S)
        text = clean_reply(raw)
        repeat = next((p for p in sent_before if text and ai_brain._messages_too_similar(text, p)), None)
        if text and not repeat:
            return text
        avoid = repeat or avoid
    return text if text and text not in sent_before else None


_SENTENCE_END = ".!?।…)\"'”’😊🙂👍🙏"


def _trim_cut_off(text: str) -> str:
    """If the model stopped mid-sentence, keep the text up to the last full sentence."""
    if not text or text[-1] in _SENTENCE_END:
        return text
    cut = max(text.rfind(ch) for ch in ".!?।")
    return text[:cut + 1].strip() if cut > len(text) * 0.2 else text


def clean_reply(raw: Optional[str]) -> Optional[str]:
    from .. import ai_brain
    text = ai_brain._strip_thinking(raw or "").strip().strip('"').strip()
    text = re.sub(r"^(your reply|reply|you)\s*:\s*", "", text, flags=re.I).strip()
    text = _trim_cut_off(text)
    if not text or len(text) > MAX_REPLY_CHARS or "[" in text and "]" in text or text.lower().startswith(("as an ai", "i'm an ai")):
        return None
    return text
