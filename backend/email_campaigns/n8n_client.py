"""
n8n_client.py — the ONLY code in the Email Campaign module that speaks to n8n.

Boundary rules (non-negotiable):
  * n8n is an OPTIONAL preparation / orchestration engine.
  * This client NEVER sends email and exposes no method that could make n8n
    send email. The one production sender stays `email_sender.send_email()`.
  * It is fail-closed: missing / partial configuration -> `N8nNotConfigured`,
    never a guessed default.
  * The Email Campaign send path never depends on this client. If n8n is
    offline / unreachable / misconfigured, native preparation + sending still
    work and the safety gate stays authoritative.

Configuration lives in `app_settings` (reused, with the existing at-rest
encryption for the secret keys):
    n8n_base_url          e.g. http://localhost:5678
    n8n_form_webhook_id   the Workflow-A form trigger id (opaque)
    n8n_api_key           secret  (encrypted at rest: key ends with _api_key)
    n8n_callback_secret   secret  (encrypted at rest: key ends with _secret)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

from .. import database as db

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(5.0, connect=3.0)

_BASE_URL_KEY  = "n8n_base_url"
_WEBHOOK_KEY   = "n8n_form_webhook_id"
_API_KEY_KEY   = "n8n_api_key"          # secret
_CALLBACK_KEY  = "n8n_callback_secret"  # secret


class N8nNotConfigured(RuntimeError):
    """n8n settings are missing or incomplete — fail closed, do not guess."""


class N8nUnavailable(RuntimeError):
    """n8n is configured but unreachable / unhealthy."""


async def get_n8n_config(*, require_callback_secret: bool = False) -> Dict[str, str]:
    """Load + validate n8n settings. Raises N8nNotConfigured if anything required
    is absent. Never returns secret values to callers that don't need them: the
    dict it returns is for backend use only and must not be serialised to an API
    response."""
    base_url = (await db.get_setting(_BASE_URL_KEY) or "").strip().rstrip("/")
    webhook  = (await db.get_setting(_WEBHOOK_KEY) or "").strip()
    api_key  = (await db.get_setting(_API_KEY_KEY) or "").strip()
    callback = (await db.get_setting(_CALLBACK_KEY) or "").strip()

    missing = []
    if not base_url:
        missing.append(_BASE_URL_KEY)
    if not base_url.startswith(("http://", "https://")):
        missing.append(f"{_BASE_URL_KEY} (must be http/https)")
    if require_callback_secret and not callback:
        missing.append(_CALLBACK_KEY)
    if missing:
        raise N8nNotConfigured("n8n is not configured: " + ", ".join(missing))

    return {
        "base_url": base_url,
        "form_webhook_id": webhook,
        "api_key": api_key,
        "callback_secret": callback,
    }


async def is_configured() -> bool:
    try:
        await get_n8n_config()
        return True
    except N8nNotConfigured:
        return False


async def verify_callback_secret(presented: Optional[str]) -> bool:
    """Constant-ish time comparison of an inbound n8n callback secret."""
    import hmac

    cfg = await get_n8n_config(require_callback_secret=True)
    expected = cfg["callback_secret"]
    return bool(presented) and hmac.compare_digest(str(presented), expected)


async def health_check() -> Dict[str, Any]:
    """GET {base_url}/healthz. Returns a safe status dict — never raises for an
    unhealthy n8n (callers treat 'ok': False as 'no n8n right now'). Raises only
    N8nNotConfigured when there is nothing to check."""
    cfg = await get_n8n_config()
    url = f"{cfg['base_url']}/healthz"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url)
        return {
            "ok": resp.status_code == 200,
            "status_code": resp.status_code,
            "base_url": cfg["base_url"],
        }
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as exc:
        logger.info("n8n health check failed: %s", exc)
        return {"ok": False, "status_code": None, "base_url": cfg["base_url"],
                "error": type(exc).__name__}


async def describe() -> Dict[str, Any]:
    """Boundary description for the API / dashboard. No secrets."""
    configured = await is_configured()
    out: Dict[str, Any] = {
        "configured": configured,
        "role": "optional preparation / orchestration engine",
        "sends_email": False,
        "authoritative_sender": "email_sender.send_email",
    }
    if configured:
        cfg = await get_n8n_config()
        out["base_url"] = cfg["base_url"]
        out["has_form_webhook"] = bool(cfg["form_webhook_id"])
        out["has_api_key"] = bool(cfg["api_key"])
        out["has_callback_secret"] = bool(cfg["callback_secret"])
        out["health"] = await health_check()
    return out
