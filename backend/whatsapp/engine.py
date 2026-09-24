"""
engine.py — talks to the self-hosted WhatsApp Web engine (WAHA Core, free),
which runs next to HOM on this computer (deploy/whatsapp-compose.yml,
127.0.0.1:3100 only).

It links your WhatsApp by QR code, reports incoming messages to n8n, and
sends text. Only `whatsapp_sender.send_whatsapp` calls `send_text` — the one
WhatsApp send path.

Secrets (the engine's API key, the n8n → HOM shared secret) live in
whatsapp/config/secrets.env, created by ./start.sh (never committed).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

SESSION = "default"               # WAHA Core runs one WhatsApp session
ENGINE_URL = os.getenv("HOM_WAHA_URL", "http://127.0.0.1:3100")
N8N_URL = os.getenv("HOM_N8N_URL", "http://127.0.0.1:5679")
# Where the engine reports incoming messages: HOM's n8n for the owner; a client
# workspace's engine calls its own backend directly (HOM_WA_EVENTS_WEBHOOK).
EVENTS_WEBHOOK = os.getenv("HOM_WA_EVENTS_WEBHOOK", "http://n8n:5678/webhook/hom-whatsapp-events")
_TIMEOUT = httpx.Timeout(20.0, connect=3.0)


def _config_dir() -> Path:
    env = os.getenv("HOM_WA_CONFIG_DIR")
    if env:
        return Path(env)
    for p in (Path("/app/whatsapp-config"), Path(__file__).resolve().parents[2] / "whatsapp" / "config"):
        if p.exists():
            return p
    return Path(__file__).resolve().parents[2] / "whatsapp" / "config"


def available() -> bool:
    """Is there a WhatsApp Web engine for this app? (owner: always; a client
    workspace: only its own, when HOM_WAHA_URL is set by the supervisor)."""
    from .. import edition
    return not edition.is_client() or bool(os.getenv("HOM_WAHA_URL"))


def secrets() -> Dict[str, str]:
    # A client workspace gets its keys as environment variables from the supervisor.
    if os.getenv("WAHA_API_KEY") or os.getenv("HOM_WA_SECRET"):
        return {"WAHA_API_KEY": os.getenv("WAHA_API_KEY", ""), "HOM_WA_SECRET": os.getenv("HOM_WA_SECRET", "")}
    out: Dict[str, str] = {}
    try:
        for line in (_config_dir() / "secrets.env").read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


class EngineError(RuntimeError):
    pass


def _headers() -> Dict[str, str]:
    key = secrets().get("WAHA_API_KEY", "")
    return {"X-Api-Key": key} if key else {}


async def _request(method: str, path: str, **kw) -> httpx.Response:
    try:
        async with httpx.AsyncClient(base_url=ENGINE_URL, timeout=_TIMEOUT, headers=_headers()) as c:
            return await c.request(method, path, **kw)
    except httpx.HTTPError as exc:
        raise EngineError(f"WhatsApp engine not reachable: {type(exc).__name__}") from exc


async def session() -> Dict[str, Any]:
    """{'state': NOT_RUNNING|STOPPED|STARTING|SCAN_QR_CODE|WORKING|FAILED, 'me': {...}|None}"""
    if not available():
        return {"state": "ENGINE_OFFLINE", "me": None}
    try:
        r = await _request("GET", f"/api/sessions/{SESSION}")
    except EngineError:
        return {"state": "ENGINE_OFFLINE", "me": None}
    if r.status_code == 404:
        return {"state": "NOT_STARTED", "me": None}
    if r.status_code >= 400:
        return {"state": "ENGINE_ERROR", "me": None, "error": r.text[:200]}
    data = r.json()
    return {"state": data.get("status") or "UNKNOWN", "me": data.get("me")}


async def start() -> Dict[str, Any]:
    """Create/start the session with the n8n webhook for incoming messages."""
    hook: Dict[str, Any] = {"url": EVENTS_WEBHOOK, "events": ["message", "session.status"]}
    if os.getenv("HOM_WA_EVENTS_WEBHOOK"):           # straight to HOM: sign it with the shared secret
        hook["customHeaders"] = [{"name": "X-HOM-Secret", "value": secrets().get("HOM_WA_SECRET", "")}]
    cfg = {"webhooks": [hook]}
    r = await _request("POST", "/api/sessions", json={"name": SESSION, "start": True, "config": cfg})
    if r.status_code == 422 or (r.status_code >= 400 and "already exists" in r.text):
        await _request("PUT", f"/api/sessions/{SESSION}", json={"config": cfg})
        r = await _request("POST", f"/api/sessions/{SESSION}/start")
    if r.status_code >= 400 and "already started" not in r.text.lower():
        raise EngineError(f"Could not start WhatsApp: {r.text[:200]}")
    return await session()


async def logout() -> None:
    await _request("POST", f"/api/sessions/{SESSION}/logout")


async def qr_png() -> Optional[bytes]:
    r = await _request("GET", f"/api/{SESSION}/auth/qr", params={"format": "image"}, headers={"Accept": "image/png"})
    return r.content if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/") else None


async def screenshot_png() -> Optional[bytes]:
    r = await _request("GET", "/api/screenshot", params={"session": SESSION})
    return r.content if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/") else None


def chat_id(phone_e164: str) -> str:
    return f"{''.join(ch for ch in phone_e164 if ch.isdigit())}@c.us"


async def send_text(phone_e164: str, text: str) -> Optional[str]:
    """Send one message. Returns WhatsApp's message id, or raises EngineError."""
    r = await _request("POST", "/api/sendText", json={"session": SESSION, "chatId": chat_id(phone_e164), "text": text})
    if r.status_code >= 400:
        raise EngineError(f"send failed ({r.status_code}): {r.text[:200]}")
    try:
        data = r.json()
    except ValueError:
        return None
    mid = data.get("id")
    return (mid.get("_serialized") if isinstance(mid, dict) else mid) or None


async def lid_to_phone(lid: str) -> Optional[str]:
    """WhatsApp may identify a person by a private id ("…@lid"); map it back to
    their phone number (digits) so the message can be matched to a lead."""
    try:
        r = await _request("GET", f"/api/{SESSION}/lids/{lid}")
    except EngineError:
        return None
    if r.status_code != 200:
        return None
    try:
        pn = (r.json() or {}).get("pn") or ""
    except ValueError:
        return None
    digits = "".join(ch for ch in pn.split("@")[0] if ch.isdigit())
    return digits or None


async def ready() -> bool:
    return (await session())["state"] == "WORKING"


async def n8n_up() -> bool:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as c:
            return (await c.get(f"{N8N_URL}/healthz")).status_code == 200
    except httpx.HTTPError:
        return False
