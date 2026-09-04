"""
routers/email_senders.py — Sender Profiles API (Checkpoint 4).

A sender profile is the authorized email account a campaign sends from. The
transport layer (backend/email_campaigns/senders.py) is still the ONE send
path; a profile only selects the authenticated account + transport (SMTP or
Gmail API).

Single-operator app: routes sit behind the shared session dependency and the
`email_campaigns_enabled` feature flag. The Gmail OAuth *callback* is the sole
exception — Google redirects the top-level browser to it with no session, so it
is protected instead by a one-time, TTL'd, replay-proof `oauth_states` token.

Responses carry ONLY safe metadata — never a password, token, refresh token,
client secret, or any *_enc column.
"""
import logging
import secrets as _secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from .. import auth
from .. import database as db
from ..email_campaigns import gmail_oauth
from ..email_campaigns import senders
from ..email_campaigns.service import is_feature_enabled
from ..secrets_crypto import decrypt, encrypt
from ..validators import is_valid_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/email-senders", tags=["email-senders"])

_MASKED = "••••set••••"          # matches settings_router — a redacted secret placeholder


async def _require_feature_enabled() -> None:
    if not await is_feature_enabled():
        raise HTTPException(
            503, "Email Campaigns is not enabled on this instance "
                 "(app_settings 'email_campaigns_enabled')."
        )


_gated = [Depends(auth.require_session), Depends(_require_feature_enabled)]
_authed = [Depends(auth.require_session)]


# ── models ───────────────────────────────────────────────────────────────────

class SmtpSenderCreate(BaseModel):
    name:          str = Field(min_length=1, max_length=120)
    email_address: str = Field(max_length=254)              # the From identity
    display_name:  Optional[str] = Field(default=None, max_length=120)
    reply_to:      Optional[str] = Field(default=None, max_length=254)
    smtp_host:     str = Field(min_length=1, max_length=255)
    smtp_port:     int = 465
    smtp_security: str = "ssl"                              # 'ssl' | 'starttls'
    smtp_username: str = Field(min_length=1, max_length=254)
    smtp_password: str = Field(min_length=1, max_length=1024)
    is_default:    bool = False


class SenderPatch(BaseModel):
    name:          Optional[str] = Field(default=None, max_length=120)
    display_name:  Optional[str] = Field(default=None, max_length=120)
    reply_to:      Optional[str] = Field(default=None, max_length=254)
    smtp_host:     Optional[str] = Field(default=None, max_length=255)
    smtp_port:     Optional[int] = None
    smtp_security: Optional[str] = None
    smtp_username: Optional[str] = Field(default=None, max_length=254)
    smtp_password: Optional[str] = Field(default=None, max_length=1024)
    is_default:    Optional[bool] = None


# ── helpers ──────────────────────────────────────────────────────────────────

async def _get_or_404(profile_id: int) -> dict:
    p = await db.get_sender_profile(profile_id)
    if not p:
        raise HTTPException(404, "sender profile not found")
    return p


def _validate_identity(email_address: str, provider: str, smtp_host: str, smtp_username: str) -> None:
    if not is_valid_email(email_address):
        raise HTTPException(422, f"email_address is not a valid email: {email_address!r}")
    if provider == "smtp" and (smtp_host or "").lower() == "smtp.gmail.com":
        # Gmail SMTP rewrites/rejects a mismatched From — pin it to the account.
        if email_address.strip().lower() != (smtp_username or "").strip().lower():
            raise HTTPException(
                422, "for Gmail SMTP the From address must equal the SMTP username",
            )


# ── CRUD ─────────────────────────────────────────────────────────────────────

@router.get("", dependencies=_gated)
async def list_senders():
    return {"senders": [senders.public_profile(p) for p in await db.list_sender_profiles()]}


@router.post("", dependencies=_gated, status_code=201)
async def create_smtp_sender(payload: SmtpSenderCreate):
    _validate_identity(payload.email_address, "smtp", payload.smtp_host, payload.smtp_username)
    if payload.smtp_security not in ("ssl", "starttls"):
        raise HTTPException(422, "smtp_security must be 'ssl' or 'starttls'")

    pid = await db.create_sender_profile({
        "name": payload.name.strip(),
        "provider": "smtp",
        "transport": "smtp",
        "email_address": payload.email_address.strip().lower(),
        "display_name": (payload.display_name or "").strip() or None,
        "reply_to": (payload.reply_to or "").strip() or None,
        "status": "connected",
        "smtp_host": payload.smtp_host.strip(),
        "smtp_port": int(payload.smtp_port),
        "smtp_security": payload.smtp_security,
        "smtp_username": payload.smtp_username.strip(),
        "smtp_password_enc": encrypt(payload.smtp_password),
    })
    if payload.is_default:
        await db.set_default_sender_profile(pid)
    return senders.public_profile(await db.get_sender_profile(pid))


@router.get("/{profile_id}", dependencies=_gated)
async def get_sender(profile_id: int):
    return senders.public_profile(await _get_or_404(profile_id))


@router.patch("/{profile_id}", dependencies=_gated)
async def patch_sender(profile_id: int, payload: SenderPatch):
    p = await _get_or_404(profile_id)
    data = payload.model_dump(exclude_unset=True)
    patch: dict = {}

    for k in ("name", "display_name", "reply_to", "smtp_host", "smtp_username"):
        if k in data:
            patch[k] = (str(data[k]).strip() or None) if data[k] is not None else None
    if "smtp_port" in data and data["smtp_port"] is not None:
        patch["smtp_port"] = int(data["smtp_port"])
    if "smtp_security" in data and data["smtp_security"] is not None:
        if data["smtp_security"] not in ("ssl", "starttls"):
            raise HTTPException(422, "smtp_security must be 'ssl' or 'starttls'")
        patch["smtp_security"] = data["smtp_security"]

    # secret: never persist the redaction mask / empty over a real password
    if "smtp_password" in data and data["smtp_password"] not in (None, "", _MASKED):
        patch["smtp_password_enc"] = encrypt(data["smtp_password"])

    if "is_default" in data and data["is_default"]:
        await db.set_default_sender_profile(profile_id)

    # re-validate identity if the relevant fields changed
    host = patch.get("smtp_host", p.get("smtp_host"))
    user = patch.get("smtp_username", p.get("smtp_username"))
    if p["provider"] == "smtp" and ("smtp_host" in patch or "smtp_username" in patch):
        _validate_identity(p["email_address"], "smtp", host or "", user or "")

    if patch:
        await db.update_sender_profile(profile_id, patch)
    return senders.public_profile(await db.get_sender_profile(profile_id))


@router.delete("/{profile_id}", dependencies=_gated, status_code=204)
async def delete_sender(profile_id: int):
    p = await _get_or_404(profile_id)
    if p["provider"] == "gmail" and p.get("oauth_refresh_token_enc"):
        await gmail_oauth.revoke(decrypt(p["oauth_refresh_token_enc"]) or "")
    n = await db.count_campaigns_using_sender(profile_id)
    await db.delete_sender_profile(profile_id)
    return None if not n else None   # campaigns are nulled by delete_sender_profile


@router.post("/{profile_id}/test", dependencies=_gated)
async def test_sender(profile_id: int):
    p = await _get_or_404(profile_id)
    try:
        transport = await senders.resolve_transport_for_profile(p)
    except senders.SenderUnavailable as exc:
        await db.update_sender_profile(profile_id, {"status": "error", "last_error": str(exc)[:300]})
        return {"ok": False, "message": str(exc)[:300]}

    res = await transport.verify()          # connection / auth only — never sends
    await db.update_sender_profile(profile_id, {
        "status": "connected" if res.ok else "error",
        "last_tested_at": db._now_naive_iso(),
        "last_error": None if res.ok else (res.error or "connection failed")[:300],
    })
    return {"ok": res.ok, "message": "Connected successfully" if res.ok else (res.error or "Connection failed")}


@router.post("/{profile_id}/disconnect", dependencies=_gated)
async def disconnect_sender(profile_id: int):
    p = await _get_or_404(profile_id)
    if p["provider"] == "gmail" and p.get("oauth_refresh_token_enc"):
        await gmail_oauth.revoke(decrypt(p["oauth_refresh_token_enc"]) or "")
    await db.update_sender_profile(profile_id, {
        "status": "disconnected",
        "smtp_password_enc": None,
        "oauth_refresh_token_enc": None,
        "oauth_access_token_enc": None,
        "oauth_expires_at": None,
        "last_error": "disconnected by user",
    })
    return senders.public_profile(await db.get_sender_profile(profile_id))


@router.post("/{profile_id}/default", dependencies=_gated)
async def set_default(profile_id: int):
    await _get_or_404(profile_id)
    await db.set_default_sender_profile(profile_id)
    return senders.public_profile(await db.get_sender_profile(profile_id))


# ── Gmail OAuth ──────────────────────────────────────────────────────────────

@router.get("/gmail/config-status", dependencies=_gated)
async def gmail_config_status():
    """Non-secret status of the Google OAuth *application* credentials, for the
    Settings → Email Senders UI. The client secret value is NEVER returned —
    only `client_secret_set`."""
    return await gmail_oauth.public_status()


@router.get("/gmail/connect", dependencies=_gated)
async def gmail_connect():
    try:
        cfg = await gmail_oauth.get_config()
    except gmail_oauth.GoogleOAuthNotConfigured as exc:
        raise HTTPException(503, str(exc))
    state = _secrets.token_urlsafe(32)
    await db.create_oauth_state(state, "gmail_sender", ttl_seconds=600)
    return {"auth_url": gmail_oauth.build_auth_url(state, cfg)}


@router.get("/gmail/callback")
async def gmail_callback(
    state: Optional[str] = Query(default=None),
    code: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
):
    """Google redirects the browser here. NOT session-gated — protected by the
    one-time state. Always ends in a redirect to the configured frontend."""
    try:
        cfg = await gmail_oauth.get_config()
    except gmail_oauth.GoogleOAuthNotConfigured:
        return RedirectResponse(f"{gmail_oauth._DEFAULT_FRONTEND}/settings?sender=error&reason=not_configured")

    front = cfg["frontend_base_url"]

    if error:
        return RedirectResponse(f"{front}/settings?sender=error&reason=denied")
    if not code or not await db.consume_oauth_state(state or "", "gmail_sender"):
        return RedirectResponse(f"{front}/settings?sender=error&reason=bad_state")

    try:
        tok = await gmail_oauth.exchange_code(code, cfg)
        info = await gmail_oauth.get_userinfo(tok["access_token"])
    except Exception as exc:  # noqa: BLE001 — never leak details in the URL
        logger.warning("gmail callback exchange failed: %s", type(exc).__name__)
        return RedirectResponse(f"{front}/settings?sender=error&reason=exchange_failed")

    email = (info.get("email") or "").strip().lower()
    if not email or info.get("email_verified") is False:
        return RedirectResponse(f"{front}/settings?sender=error&reason=no_email")

    refresh = tok.get("refresh_token")
    from datetime import datetime, timedelta, timezone
    expires_at = (datetime.now(timezone.utc)
                  + timedelta(seconds=int(tok.get("expires_in", 3000)))).replace(tzinfo=None).isoformat()

    existing = next((p for p in await db.list_sender_profiles()
                     if p["provider"] == "gmail" and p["email_address"] == email), None)
    fields = {
        "status": "connected",
        "oauth_client_id": cfg["client_id"],
        "oauth_access_token_enc": encrypt(tok["access_token"]),
        "oauth_expires_at": expires_at,
        "oauth_scopes": tok.get("scope") or " ".join(gmail_oauth.SCOPES),
        "last_error": None,
    }
    if refresh:                                     # Google omits it on re-consent sometimes
        fields["oauth_refresh_token_enc"] = encrypt(refresh)

    if existing:
        if not refresh and not existing.get("oauth_refresh_token_enc"):
            return RedirectResponse(f"{front}/settings?sender=error&reason=no_refresh_token")
        await db.update_sender_profile(existing["id"], fields)
    else:
        if not refresh:
            return RedirectResponse(f"{front}/settings?sender=error&reason=no_refresh_token")
        await db.create_sender_profile({
            "name": f"Gmail — {email}",
            "provider": "gmail",
            "transport": "gmail_api",
            "email_address": email,
            "display_name": info.get("name") or None,
            **fields,
        })

    return RedirectResponse(f"{front}/settings?sender=connected&email={email}")
