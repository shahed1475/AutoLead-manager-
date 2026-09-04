"""
gmail_oauth.py — Google OAuth2 helpers for Gmail *sender* profiles.

Scope is minimal and send-only:
    openid
    https://www.googleapis.com/auth/userinfo.email   (learn the account address)
    https://www.googleapis.com/auth/gmail.send        (send only — no read/modify/delete)

Raw HTTP via httpx — no google-* dependency. Fail-closed: missing client
id/secret -> GoogleOAuthNotConfigured (never a guessed default that could send).

Client credentials live in app_settings (google_oauth_client_secret ends with
`_secret` -> auto-encrypted at rest + redacted in GET /api/settings), with a
config.py / env fallback. This module NEVER logs or returns a secret or a token.
"""
from __future__ import annotations

import logging
from typing import Any, Dict
from urllib.parse import urlencode

import httpx

from .. import database as db
from ..config import get_settings

logger = logging.getLogger(__name__)

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.send",
]

_AUTH_ENDPOINT     = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT    = "https://oauth2.googleapis.com/token"
_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
_REVOKE_ENDPOINT   = "https://oauth2.googleapis.com/revoke"
_GMAIL_SEND        = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

_DEFAULT_REDIRECT = "http://localhost:8001/api/email-senders/gmail/callback"
_DEFAULT_FRONTEND = "http://localhost:5173"

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class GoogleOAuthNotConfigured(RuntimeError):
    """client id/secret not set — fail closed."""


class TokenExchangeError(RuntimeError):
    """authorization-code exchange failed."""


class TokenRefreshError(RuntimeError):
    """refresh-token grant failed (revoked / expired / invalid)."""


async def _setting_or_config(key: str, cfg_attr: str) -> str:
    val = (await db.get_setting(key) or "").strip()
    if val:
        return val
    return str(getattr(get_settings(), cfg_attr, "") or "").strip()


async def get_config(*, require_secret: bool = True) -> Dict[str, str]:
    client_id     = await _setting_or_config("google_oauth_client_id", "google_oauth_client_id")
    client_secret = await _setting_or_config("google_oauth_client_secret", "google_oauth_client_secret")
    redirect_uri  = await _setting_or_config("google_oauth_redirect_uri", "google_oauth_redirect_uri") or _DEFAULT_REDIRECT
    frontend_url  = await _setting_or_config("frontend_base_url", "frontend_base_url") or _DEFAULT_FRONTEND

    missing = []
    if not client_id:
        missing.append("google_oauth_client_id")
    if require_secret and not client_secret:
        missing.append("google_oauth_client_secret")
    if not redirect_uri.startswith(("http://", "https://")):
        missing.append("google_oauth_redirect_uri (must be http/https)")
    if missing:
        raise GoogleOAuthNotConfigured("Google OAuth is not configured: " + ", ".join(missing))

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "frontend_base_url": frontend_url.rstrip("/"),
    }


async def is_configured() -> bool:
    try:
        await get_config()
        return True
    except GoogleOAuthNotConfigured:
        return False


async def public_status() -> Dict[str, Any]:
    """Non-secret view of the OAuth *application* config, for the Settings UI.

    NEVER returns the client secret value — only a boolean that one is stored.
    The client id and redirect uri are not secrets (the id is sent to Google
    in a browser redirect; the redirect uri must be registered publicly in the
    Google Cloud console)."""
    client_id     = await _setting_or_config("google_oauth_client_id", "google_oauth_client_id")
    client_secret = await _setting_or_config("google_oauth_client_secret", "google_oauth_client_secret")
    redirect_uri  = await _setting_or_config("google_oauth_redirect_uri", "google_oauth_redirect_uri")
    return {
        "configured": await is_configured(),
        "client_id": client_id,
        "client_secret_set": bool(client_secret),
        "redirect_uri": redirect_uri or _DEFAULT_REDIRECT,
        "default_redirect_uri": _DEFAULT_REDIRECT,
    }


def build_auth_url(state: str, cfg: Dict[str, str]) -> str:
    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",                 # force a refresh_token every time
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{_AUTH_ENDPOINT}?{urlencode(params)}"


async def exchange_code(code: str, cfg: Dict[str, str]) -> Dict[str, Any]:
    data = {
        "code": code,
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "redirect_uri": cfg["redirect_uri"],
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(_TOKEN_ENDPOINT, data=data)
    if resp.status_code != 200:
        # resp text can contain 'error_description' but never our secret
        raise TokenExchangeError(f"token exchange failed ({resp.status_code})")
    tok = resp.json()
    if not tok.get("access_token"):
        raise TokenExchangeError("token exchange returned no access_token")
    return tok


async def refresh_access_token(refresh_token: str, cfg: Dict[str, str]) -> Dict[str, Any]:
    data = {
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(_TOKEN_ENDPOINT, data=data)
    if resp.status_code != 200:
        raise TokenRefreshError(f"token refresh failed ({resp.status_code})")
    tok = resp.json()
    if not tok.get("access_token"):
        raise TokenRefreshError("token refresh returned no access_token")
    return tok


async def get_userinfo(access_token: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(
            _USERINFO_ENDPOINT, headers={"Authorization": f"Bearer {access_token}"}
        )
    if resp.status_code != 200:
        raise TokenExchangeError(f"userinfo request failed ({resp.status_code})")
    return resp.json()


async def revoke(token: str) -> bool:
    """Best-effort revoke on disconnect. Never raises."""
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_REVOKE_ENDPOINT, data={"token": token})
        return resp.status_code == 200
    except httpx.HTTPError as exc:  # noqa: BLE001
        logger.info("gmail_oauth.revoke failed (ignored): %s", exc)
        return False


async def gmail_send_raw(access_token: str, raw_b64url: str) -> Dict[str, Any]:
    """POST a base64url-encoded MIME message to the Gmail API. Returns the API
    response ({id, threadId, labelIds}) on success; raises on failure."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            _GMAIL_SEND,
            headers={"Authorization": f"Bearer {access_token}"},
            json={"raw": raw_b64url},
        )
    if resp.status_code not in (200, 202):
        raise RuntimeError(f"Gmail API send failed ({resp.status_code})")
    return resp.json()
