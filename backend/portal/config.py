"""
config.py — the owner's client-portal settings (stored in app_settings as
portal_* keys) and the email account sign-in codes / invitations go out from.

Sender: "smtp" = the main email in Settings → Email (SMTP); otherwise the id
of an Email Sender profile (the same list Email Campaigns uses). Mail goes out
through that account's existing transport — SMTP profiles through
email_sender.send_email, the one SMTP path. Portal mail is transactional
(codes, invitations) — never outreach to leads.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from .. import database as db, email_sender

logger = logging.getLogger(__name__)

SIGNUP_MODES = ("open", "invite", "closed")
MAX_WORKSPACES_CAP = 20      # each running workspace uses ~1 GB of memory

DEFAULTS: Dict[str, Any] = {
    "sender": "auto",        # auto | smtp | <sender profile id>
    "signup_mode": "open",
    "name": "HOM",
    "welcome": "",
    "contact_email": "",
    "max_workspaces": 5,
}
_INT_KEYS = {"max_workspaces": (0, MAX_WORKSPACES_CAP)}

# start.sh writes the current client link here (mounted read-only in Docker).
LINK_FILE = Path(__file__).resolve().parents[2] / ".run" / "portal-link.txt"


class ConfigError(ValueError):
    pass


async def get_config() -> Dict[str, Any]:
    stored = await db.get_all_settings()
    cfg = dict(DEFAULTS)
    for key, default in DEFAULTS.items():
        raw = stored.get(f"portal_{key}")
        if raw is None or raw == "":
            continue
        if key in _INT_KEYS:
            try:
                lo, hi = _INT_KEYS[key]
                cfg[key] = max(lo, min(hi, int(raw)))
            except ValueError:
                pass
        else:
            cfg[key] = raw
    return cfg


async def update_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    clean: Dict[str, str] = {}
    for key, value in patch.items():
        if key not in DEFAULTS or value is None:
            continue
        if key in _INT_KEYS:
            lo, hi = _INT_KEYS[key]
            try:
                n = int(value)
            except (TypeError, ValueError):
                raise ConfigError(f"{key} must be a number")
            if not lo <= n <= hi:
                raise ConfigError(f"{key} must be between {lo} and {hi}")
            clean[key] = str(n)
        elif key == "signup_mode":
            if value not in SIGNUP_MODES:
                raise ConfigError("signup_mode must be open, invite or closed")
            clean[key] = value
        elif key == "sender":
            value = str(value)
            if value not in ("auto", "smtp"):
                if not value.isdigit() or not await db.get_sender_profile(int(value)):
                    raise ConfigError("That email sender doesn't exist")
            clean[key] = value
        elif key == "name":
            v = str(value).strip()[:60]
            if not v:
                raise ConfigError("Give the portal a name")
            clean[key] = v
        elif key == "welcome":
            clean[key] = str(value).strip()[:400]
        elif key == "contact_email":
            v = str(value).strip().lower()[:254]
            if v and "@" not in v:
                raise ConfigError("Enter a valid contact email")
            clean[key] = v
    for key, value in clean.items():
        await db.upsert_setting(f"portal_{key}", value)
    return await get_config()


def client_link() -> Optional[str]:
    try:
        link = LINK_FILE.read_text().strip()
    except OSError:
        return None
    return link if link.startswith("https://") else None


# ── The sign-in email account ────────────────────────────────────────────────

async def _resolve(cfg: Dict[str, Any]):
    """-> (kind, profile|None). kind: 'smtp' | 'profile' | 'missing'."""
    sender = str(cfg.get("sender") or "auto")
    if sender == "smtp":
        return "smtp", None
    if sender.isdigit():
        p = await db.get_sender_profile(int(sender))
        return ("profile", p) if p else ("missing", None)
    # auto: the main email if it is set up, else the default / first connected sender
    if await email_sender.system_email_ready():
        return "smtp", None
    p = await db.get_default_sender_profile()
    if not p or p.get("status") != "connected":
        p = next((x for x in await db.list_sender_profiles() if x.get("status") == "connected"), None)
    return ("profile", p) if p else ("missing", None)


def _profile_usable(p: Dict[str, Any]) -> bool:
    return p.get("status") == "connected" and bool(p.get("smtp_password_enc") or p.get("oauth_refresh_token_enc"))


async def sender_status(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or await get_config()
    kind, p = await _resolve(cfg)
    if kind == "smtp":
        ready = await email_sender.system_email_ready()
        return {"ready": ready, "kind": "smtp",
                "label": "Main email (Settings → Email)",
                "problem": None if ready else "The main email isn't set up in Settings → Email."}
    if kind == "profile":
        ok = _profile_usable(p)
        return {"ready": ok, "kind": "profile", "profile_id": p["id"],
                "label": f"{p.get('name')} · {p.get('email_address')}",
                "problem": None if ok else (p.get("last_error") or f"This sender is {p.get('status')} — reconnect it.")}
    return {"ready": False, "kind": "missing", "label": "No email account",
            "problem": "Add an email account so sign-in codes can be sent."}


async def send_mail(to: str, subject: str, body: str, cfg: Optional[Dict[str, Any]] = None) -> bool:
    """Send one portal email (code / invitation / test) from the chosen account."""
    cfg = cfg or await get_config()
    reply_to = cfg.get("contact_email") or None
    kind, p = await _resolve(cfg)
    if kind == "smtp":
        return await email_sender.send_system_email(to, subject, body, reply_to=reply_to)
    if kind == "profile" and _profile_usable(p):
        from ..email_campaigns import senders
        try:
            transport = await senders.resolve_transport_for_profile(p)
            res = await transport.send(to, subject, body, reply_to=reply_to)
        except Exception as exc:  # noqa: BLE001 — SenderUnavailable or transport failure
            logger.warning("portal mail via sender %s failed: %s", p.get("id"), exc)
            return False
        if not res.ok:
            logger.warning("portal mail via sender %s failed: %s", p.get("id"), res.error)
        return res.ok
    logger.warning("portal mail: no usable email account")
    return False
