"""
auth.py — Lightweight single-user auth for the desktop app.

This is deliberately NOT a multi-tenant/user-account system — AutoLead runs
as a single local app for one operator. One app password is stored (bcrypt
hash) in app_settings. Until a password is set, the API stays fully open so
first-run and the existing dev workflow are unaffected; the frontend nudges
the user to set one from Settings. Once a password exists, every request
(except /api/health and the /api/auth/* endpoints) must carry a valid
session token issued by POST /api/auth/unlock.

Sessions are an in-memory dict — intentionally cleared on backend restart.
That's fine for a local desktop app: the user just unlocks again.

Uses the `bcrypt` package directly rather than passlib — passlib 1.7.4 (the
last release) can't detect bcrypt>=4.1's version and its 72-byte length
check breaks even for short passwords as a result (upstream passlib/bcrypt
incompatibility, unresolved since passlib is unmaintained).
"""
import logging
import secrets
import time
from typing import Dict, Optional

import bcrypt
from fastapi import Header, HTTPException, Request

from . import database as db

logger = logging.getLogger(__name__)

_SETTING_KEY   = "app_password_hash"
_SESSION_TTL_S = 60 * 60 * 12  # 12 hours
_MAX_PW_BYTES  = 72             # bcrypt's hard limit

_sessions: Dict[str, float] = {}  # token -> expires_at (epoch seconds)


def _new_token() -> str:
    return secrets.token_urlsafe(32)


async def is_password_set() -> bool:
    return bool(await db.get_setting(_SETTING_KEY))


async def set_password(password: str) -> None:
    if not password or len(password) < 4:
        raise ValueError("Password must be at least 4 characters")
    if len(password.encode("utf-8")) > _MAX_PW_BYTES:
        raise ValueError(f"Password must be at most {_MAX_PW_BYTES} bytes")
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    await db.upsert_setting(_SETTING_KEY, hashed)
    _sessions.clear()  # invalidate existing sessions on password change


async def clear_password() -> None:
    await db.upsert_setting(_SETTING_KEY, "")
    _sessions.clear()


async def verify_password(password: str) -> bool:
    stored = await db.get_setting(_SETTING_KEY)
    if not stored:
        return False
    try:
        return bcrypt.checkpw((password or "").encode("utf-8"), stored.encode("utf-8"))
    except Exception:
        return False


def issue_session() -> str:
    token = _new_token()
    _sessions[token] = time.time() + _SESSION_TTL_S
    return token


def _session_valid(token: Optional[str]) -> bool:
    if not token:
        return False
    expires = _sessions.get(token)
    if expires is None:
        return False
    if expires < time.time():
        _sessions.pop(token, None)
        return False
    return True


async def require_session(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> None:
    """
    FastAPI dependency — raises 401 unless a valid session token is present.
    No-ops (API stays open) if no app password has ever been configured.

    Token can arrive as:
      - Authorization: Bearer <token>   (normal API calls, via axios)
      - X-Session-Token: <token>        (fallback)
      - ?token=<token> query param      (SSE — native EventSource can't set
        custom headers, so /api/logs/stream is unlocked via query param)
    """
    if not await is_password_set():
        return

    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        token = request.headers.get("X-Session-Token")
    if not token:
        token = request.query_params.get("token")

    if not _session_valid(token):
        raise HTTPException(status_code=401, detail="Unlock required")
