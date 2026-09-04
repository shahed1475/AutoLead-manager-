"""
senders.py — the sender-profile transport layer for Email Campaigns.

There is still exactly ONE application send path:

    EmailCampaignService.run_batch
        -> resolve_transport(campaign)         (picks the authorized account)
        -> assert_send_allowed(...)             (fail-closed TEST_MODE gate)
        -> transport.send(...)                  (SMTP or Gmail API)

Both transports build the message with `email_sender.build_message` — MIME
formatting is never duplicated. Secrets are decrypted only at the moment of
use (via secrets_crypto) and never logged or returned.

Fail-closed rules:
  * a campaign that names a sender_profile_id gets THAT profile or the run is
    blocked — never a silent fallback to another sender / the global SMTP;
  * a disconnected / errored / unconfigured profile blocks the run;
  * a Gmail token that cannot be refreshed blocks the run.
Only sender_profile_id IS NULL falls back to the legacy global SMTP config.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .. import database as db
from .. import email_sender
from ..secrets_crypto import decrypt, encrypt
from . import gmail_oauth

logger = logging.getLogger(__name__)

# public (frontend-safe) profile fields — everything else, especially *_enc,
# stays server-side.
_PUBLIC_PROFILE_FIELDS = (
    "id", "name", "provider", "transport", "email_address", "display_name",
    "reply_to", "status", "is_default", "smtp_host", "smtp_port", "smtp_security",
    "smtp_username", "oauth_client_id", "oauth_scopes",
    "created_at", "updated_at", "last_tested_at", "last_error",
)


class SenderUnavailable(RuntimeError):
    """The selected sender profile cannot be used — the run must be blocked."""


@dataclass
class SendResult:
    ok: bool
    message_id: Optional[str] = None
    error: Optional[str] = None


# ── transports ────────────────────────────────────────────────────────────

class EmailTransport:
    provider = "base"
    email_address = ""

    async def send(self, to_email: str, subject: str, body: str, *,
                   to_name: str = "", attachments: Optional[List[Dict[str, Any]]] = None,
                   reply_to: Optional[str] = None) -> SendResult:
        raise NotImplementedError

    async def verify(self) -> SendResult:
        """Connection / auth check — never sends an email."""
        raise NotImplementedError


class SmtpTransport(EmailTransport):
    """SMTP transport — a thin adapter over the existing `email_sender`
    primitives so `email_sender.send_email()` stays the one SMTP send path."""
    provider = "smtp"

    def __init__(self, cfg: Dict[str, Any]):
        self._cfg = cfg
        self.email_address = cfg.get("from_email") or cfg.get("username") or ""

    async def send(self, to_email, subject, body, *, to_name="", attachments=None,
                   reply_to=None) -> SendResult:
        ok = await asyncio.to_thread(
            email_sender.send_email, to_email, subject, body, self._cfg, attachments, reply_to
        )
        return SendResult(ok=bool(ok), error=None if ok else "SMTP send failed")

    async def verify(self) -> SendResult:
        if not self._cfg.get("username") or not self._cfg.get("password"):
            return SendResult(ok=False, error="SMTP username/password not set")
        res = await email_sender.test_connection(self._cfg)     # login only, no send
        return SendResult(
            ok=bool(res.get("success")),
            error=None if res.get("success") else str(res.get("message") or "connection failed")[:300],
        )


class GmailApiTransport(EmailTransport):
    provider = "gmail"

    def __init__(self, profile: Dict[str, Any], oauth_cfg: Dict[str, str]):
        self._profile = profile
        self._oauth_cfg = oauth_cfg
        self.email_address = profile["email_address"]
        self._access_token: Optional[str] = None

    async def _ensure_token(self) -> str:
        """Return a valid access token, refreshing (and persisting) if needed.
        Raises SenderUnavailable if the refresh token is dead."""
        p = self._profile

        if not self._access_token and p.get("oauth_access_token_enc"):
            self._access_token = decrypt(p["oauth_access_token_enc"])

        exp = p.get("oauth_expires_at")
        if self._access_token and exp:
            try:
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                if datetime.fromisoformat(str(exp)) - now > timedelta(seconds=120):
                    return self._access_token
            except ValueError:
                pass  # unparseable expiry -> refresh

        refresh_enc = p.get("oauth_refresh_token_enc")
        refresh_token = decrypt(refresh_enc) if refresh_enc else None
        if not refresh_token:
            await db.update_sender_profile(p["id"], {"status": "error",
                                                     "last_error": "no refresh token stored"})
            raise SenderUnavailable("Gmail profile has no refresh token — reconnect it")
        try:
            tok = await gmail_oauth.refresh_access_token(refresh_token, self._oauth_cfg)
        except gmail_oauth.TokenRefreshError as exc:
            await db.update_sender_profile(p["id"], {"status": "error",
                                                     "last_error": "token refresh failed — reconnect Gmail"})
            raise SenderUnavailable(f"Gmail token refresh failed: {exc}") from exc

        self._access_token = tok["access_token"]
        new_exp = (datetime.now(timezone.utc)
                   + timedelta(seconds=int(tok.get("expires_in", 3000)))).replace(tzinfo=None).isoformat()
        await db.update_sender_profile(p["id"], {
            "oauth_access_token_enc": encrypt(self._access_token),
            "oauth_expires_at": new_exp,
            "status": "connected",
            "last_error": None,
        })
        self._profile["oauth_expires_at"] = new_exp
        return self._access_token

    async def send(self, to_email, subject, body, *, to_name="", attachments=None,
                   reply_to=None) -> SendResult:
        try:
            token = await self._ensure_token()
        except SenderUnavailable as exc:
            return SendResult(ok=False, error=str(exc)[:300])

        msg = email_sender.build_message(
            to_email, to_name, subject, body,
            self._profile.get("display_name") or "", self._profile["email_address"],
            reply_to=reply_to, attachments=attachments,
        )
        raw_b64 = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        try:
            res = await gmail_oauth.gmail_send_raw(token, raw_b64)
            return SendResult(ok=True, message_id=res.get("id"))
        except Exception as exc:  # noqa: BLE001
            return SendResult(ok=False, error=f"{type(exc).__name__}: {exc}"[:300])

    async def verify(self) -> SendResult:
        try:
            token = await self._ensure_token()
            info = await gmail_oauth.get_userinfo(token)
        except SenderUnavailable as exc:
            return SendResult(ok=False, error=str(exc)[:300])
        except Exception as exc:  # noqa: BLE001
            return SendResult(ok=False, error=f"{type(exc).__name__}: {exc}"[:300])
        got = (info.get("email") or "").lower()
        if got and got != self._profile["email_address"].lower():
            return SendResult(ok=False,
                              error="authenticated Gmail account no longer matches this profile")
        return SendResult(ok=True)


# ── resolution ────────────────────────────────────────────────────────────

def _smtp_cfg_from_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    security = (profile.get("smtp_security") or "").lower()
    port = int(profile.get("smtp_port") or (465 if security == "ssl" else 587))
    return {
        "host": profile.get("smtp_host") or "",
        "port": port,
        "username": profile.get("smtp_username") or "",
        "password": decrypt(profile.get("smtp_password_enc")) or "" if profile.get("smtp_password_enc") else "",
        "from_name": profile.get("display_name") or "",
        "from_email": profile.get("email_address") or profile.get("smtp_username") or "",
    }


async def resolve_transport_for_profile(profile: Dict[str, Any]) -> EmailTransport:
    if not profile:
        raise SenderUnavailable("sender profile not found")
    if profile.get("status") != "connected":
        raise SenderUnavailable(
            f"sender profile '{profile.get('name')}' is {profile.get('status')} — reconnect it"
        )
    if profile["provider"] == "smtp":
        return SmtpTransport(_smtp_cfg_from_profile(profile))
    if profile["provider"] == "gmail":
        try:
            oauth_cfg = await gmail_oauth.get_config()
        except gmail_oauth.GoogleOAuthNotConfigured as exc:
            raise SenderUnavailable(f"Gmail sending is not configured: {exc}") from exc
        t = GmailApiTransport(profile, oauth_cfg)
        await t._ensure_token()          # fail fast if the refresh token is dead
        return t
    raise SenderUnavailable(f"unknown provider '{profile['provider']}'")


async def resolve_transport(campaign: Dict[str, Any]) -> EmailTransport:
    """The campaign send loop's single resolution point. NEVER falls back from a
    selected profile to another sender."""
    pid = campaign.get("sender_profile_id")
    if pid:
        profile = await db.get_sender_profile(int(pid))
        return await resolve_transport_for_profile(profile)   # raises SenderUnavailable

    # sender_profile_id IS NULL -> legacy global SMTP (back-compat only)
    cfg = await email_sender._smtp_cfg()
    return SmtpTransport(cfg)


# ── safe views ────────────────────────────────────────────────────────────

def public_profile(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    out = {k: row.get(k) for k in _PUBLIC_PROFILE_FIELDS}
    out["is_default"] = bool(row.get("is_default"))
    out["has_credentials"] = bool(
        row.get("smtp_password_enc") or row.get("oauth_refresh_token_enc")
    )
    return out


async def campaign_sender_view(campaign: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    pid = campaign.get("sender_profile_id")
    if not pid:
        return None
    return public_profile(await db.get_sender_profile(int(pid)))
