"""
meta.py — the official WhatsApp Cloud API (Meta), with the user's own keys.

The alternative to the free WhatsApp Web engine (engine.py). Everything goes
through Meta's servers (graph.facebook.com), so it also works inside client
workspaces — it never touches the owner's number or computer.

Settings (app_settings; secrets end in _token/_secret, so they're encrypted
at rest and masked on read):
    wa_meta_phone_number_id, wa_meta_waba_id, wa_meta_api_version,
    wa_meta_access_token (secret), wa_meta_app_secret (secret),
    wa_meta_verify_token (secret — HOM generates it)

Meta's rule: the FIRST message to a person must be an approved template;
free text is only allowed within 24 hours of their last message (which is
exactly when automatic replies are sent). Incoming messages arrive on the
webhook (routers/whatsapp.py), signed with the app secret.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets as pysecrets
from typing import Any, Dict, List, Optional

import httpx

from .. import database as db

GRAPH = "https://graph.facebook.com"
DEFAULT_VERSION = "v23.0"
_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
KEYS = ("wa_meta_phone_number_id", "wa_meta_waba_id", "wa_meta_api_version",
        "wa_meta_access_token", "wa_meta_app_secret", "wa_meta_verify_token")


class MetaError(RuntimeError):
    pass


async def config() -> Dict[str, str]:
    stored = await db.get_all_settings()
    cfg = {k.replace("wa_meta_", ""): (stored.get(k) or "").strip() for k in KEYS}
    cfg["api_version"] = cfg["api_version"] or DEFAULT_VERSION
    return cfg


async def ensure_verify_token() -> str:
    tok = (await db.get_setting("wa_meta_verify_token") or "").strip()
    if not tok:
        tok = pysecrets.token_urlsafe(24)
        await db.upsert_setting("wa_meta_verify_token", tok)
    return tok


def configured(cfg: Dict[str, str]) -> bool:
    return bool(cfg.get("phone_number_id") and cfg.get("access_token"))


async def _call(method: str, path: str, cfg: Dict[str, str], **kw) -> Dict[str, Any]:
    url = f"{GRAPH}/{cfg['api_version']}/{path.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.request(method, url, headers={"Authorization": f"Bearer {cfg['access_token']}"}, **kw)
    except httpx.HTTPError as exc:
        raise MetaError(f"Can't reach Meta ({type(exc).__name__})") from exc
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400:
        err = (data.get("error") or {}) if isinstance(data, dict) else {}
        raise MetaError(err.get("message") or f"Meta returned {r.status_code}")
    return data


async def status() -> Dict[str, Any]:
    """{'state': NOT_CONFIGURED | WORKING | ERROR, 'number', 'name', 'quality', 'error'}"""
    cfg = await config()
    if not configured(cfg):
        return {"state": "NOT_CONFIGURED"}
    try:
        d = await _call("GET", cfg["phone_number_id"], cfg,
                        params={"fields": "display_phone_number,verified_name,quality_rating"})
    except MetaError as exc:
        return {"state": "ERROR", "error": str(exc)[:300]}
    return {"state": "WORKING", "number": digits(d.get("display_phone_number")), "name": d.get("verified_name"),
            "quality": d.get("quality_rating")}


def digits(v: Optional[str]) -> str:
    return re.sub(r"\D", "", v or "")


async def send_text(phone_e164: str, text: str) -> Optional[str]:
    cfg = await config()
    if not configured(cfg):
        raise MetaError("The Meta WhatsApp API isn't set up.")
    d = await _call("POST", f"{cfg['phone_number_id']}/messages", cfg, json={
        "messaging_product": "whatsapp", "recipient_type": "individual", "to": digits(phone_e164),
        "type": "text", "text": {"preview_url": False, "body": text}})
    return ((d.get("messages") or [{}])[0]).get("id")


async def send_template(phone_e164: str, name: str, language: str, params: List[str]) -> Optional[str]:
    """Send an approved template (needed for the first message to someone)."""
    cfg = await config()
    if not configured(cfg):
        raise MetaError("The Meta WhatsApp API isn't set up.")
    template: Dict[str, Any] = {"name": name, "language": {"code": language or "en"}}
    if params:
        template["components"] = [{"type": "body", "parameters": [{"type": "text", "text": p or "-"} for p in params]}]
    d = await _call("POST", f"{cfg['phone_number_id']}/messages", cfg, json={
        "messaging_product": "whatsapp", "to": digits(phone_e164), "type": "template", "template": template})
    return ((d.get("messages") or [{}])[0]).get("id")


async def templates() -> List[Dict[str, Any]]:
    """Approved message templates of the WhatsApp Business Account."""
    cfg = await config()
    if not configured(cfg) or not cfg.get("waba_id"):
        raise MetaError("Add your WhatsApp Business Account ID to list templates.")
    d = await _call("GET", f"{cfg['waba_id']}/message_templates", cfg,
                    params={"fields": "name,language,status,category,components", "limit": 200})
    out = []
    for t in d.get("data") or []:
        if t.get("status") != "APPROVED":
            continue
        body = next((c.get("text", "") for c in t.get("components") or [] if c.get("type") == "BODY"), "")
        out.append({"name": t["name"], "language": t.get("language"), "category": t.get("category"),
                    "body": body, "variables": len(set(re.findall(r"\{\{(\d+)\}\}", body)))})
    return out


def fill_template(body: str, params: List[str]) -> str:
    """How the template reads with the values filled in (for previews and the log)."""
    return re.sub(r"\{\{(\d+)\}\}", lambda m: params[int(m.group(1)) - 1] if 0 < int(m.group(1)) <= len(params) else m.group(0), body)


# ── Webhook ──────────────────────────────────────────────────────────────────

def signature_ok(body: bytes, header: Optional[str], app_secret: str) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    want = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[7:], want)


def to_events(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Meta webhook → the same event shape HOM's WhatsApp service handles."""
    events = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in value.get("contacts") or []}
            for m in value.get("messages") or []:
                if m.get("type") == "text":
                    body = (m.get("text") or {}).get("body", "")
                elif m.get("type") == "button":
                    body = (m.get("button") or {}).get("text", "")
                elif m.get("type") == "interactive":
                    i = m.get("interactive") or {}
                    body = ((i.get("button_reply") or i.get("list_reply") or {}).get("title", ""))
                else:
                    continue                          # images, audio… aren't answered automatically
                events.append({"event": "message", "payload": {
                    "from": f"{m.get('from')}@c.us", "body": body, "fromMe": False, "id": m.get("id"),
                    "timestamp": m.get("timestamp"), "notifyName": names.get(m.get("from")) or ""}})
    return events
