"""
service.py — client-portal logic: emailed sign-in codes and client sessions.
The portal is the front door of the client link: after sign-in the client
enters their own private workspace (workspaces.py).

Security model
- Sign-in: a 6-digit code emailed to the address (proves the client owns it).
  Only a salted hash of the code is stored; 10-minute expiry; 5 attempts.
- Abuse limits (anyone can sign up, and codes go out from the owner's Gmail):
  one code per address per minute, 5 per hour, and a daily cap overall.
- Sessions: random 256-bit token given to the client; only its SHA-256 is
  stored. Portal sessions are separate from the owner's app session — a
  client token never unlocks owner endpoints, and vice versa.
- A client session only ever opens that client's own workspace.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import bcrypt

from .. import database as db
from . import config as portal_config

CODE_TTL = timedelta(minutes=10)
CODE_MAX_ATTEMPTS = 5
CODE_RESEND_SECONDS = 60
CODES_PER_EMAIL_PER_HOUR = 5
CODES_PER_DAY = 200
SESSION_TTL = timedelta(days=30)
PASSWORD_MIN = 10
PASSWORD_MAX_BYTES = 72                  # bcrypt's limit
LOGIN_MAX_FAILURES = 8                   # then the account waits LOCK_TIME
LOCK_TIME = timedelta(minutes=15)
RESET_WINDOW = timedelta(minutes=15)     # a code-opened session may set a new password
_COMMON = {"password12", "password123", "1234567890", "qwertyuiop", "0123456789", "iloveyou12",
           "passw0rd12", "letmein123", "welcome123", "admin12345", "abc1234567", "qwerty1234"}
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt()).decode()
# Request limits and who may sign up are the owner's portal settings (config.py).

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


class PortalError(Exception):
    """A client-facing problem; `status` is the HTTP code to return."""
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(dt: datetime) -> str:
    return dt.isoformat(sep=" ", timespec="seconds")


def normalize_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    if len(email) > 254 or not _EMAIL_RE.match(email):
        raise PortalError("Enter a valid email address.")
    return email


def _hash_code(email: str, code: str) -> str:
    return hashlib.sha256(f"{email}:{code}".encode()).hexdigest()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── Sign-in codes ────────────────────────────────────────────────────────────

async def check_can_sign_in(email: str, cfg: Optional[Dict[str, Any]] = None) -> None:
    """Apply the owner's sign-up setting: open to anyone, only clients the owner
    added (invite), or closed. Blocked clients never get in."""
    cfg = cfg or await portal_config.get_config()
    mode = cfg.get("signup_mode", "open")
    if mode == "closed":
        raise PortalError("Sign-in is closed right now. Please try again later.", 403)
    client = await db.portal_fetchrow("SELECT status FROM portal_clients WHERE email = ?", email)
    if client and client["status"] == "BLOCKED":
        raise PortalError("This account has been disabled. Contact the owner.", 403)
    if mode == "invite" and not client:
        raise PortalError(f"This portal is invite-only. Ask {cfg.get('name') or 'the owner'} to add your email.", 403)


async def issue_code(email: str, payload: Optional[Dict[str, Any]] = None, check: bool = True) -> str:
    """Create a new code for `email` (after the abuse limits) and return it;
    the caller emails it. Raises PortalError(429) when limited. `payload`
    (a sign-up's name/company/password hash) is applied only once the code
    proves the email is theirs."""
    if check:
        await check_can_sign_in(email)
    now = _now()
    last = await db.portal_fetchrow(
        "SELECT created_at FROM portal_login_codes WHERE email = ? ORDER BY id DESC LIMIT 1", email)
    if last and now - datetime.fromisoformat(str(last["created_at"])) < timedelta(seconds=CODE_RESEND_SECONDS):
        raise PortalError("A code was just sent. Wait a minute before asking for another.", 429)
    hour = await db.portal_fetchrow(
        "SELECT count(*) AS n FROM portal_login_codes WHERE email = ? AND created_at > ?",
        email, _iso(now - timedelta(hours=1)))
    if hour and hour["n"] >= CODES_PER_EMAIL_PER_HOUR:
        raise PortalError("Too many codes for this address. Try again in an hour.", 429)
    day = await db.portal_fetchrow(
        "SELECT count(*) AS n FROM portal_login_codes WHERE created_at > ?", _iso(now - timedelta(days=1)))
    if day and day["n"] >= CODES_PER_DAY:
        raise PortalError("Sign-in is busy right now. Please try again later.", 429)

    code = f"{secrets.randbelow(10**6):06d}"
    # Only the newest code works: retire any earlier unused ones.
    await db.portal_execute(
        "UPDATE portal_login_codes SET used_at = ? WHERE email = ? AND used_at IS NULL", _iso(now), email)
    await db.portal_execute(
        "INSERT INTO portal_login_codes (email, code_hash, expires_at, created_at, payload) VALUES (?, ?, ?, ?, ?)",
        email, _hash_code(email, code), _iso(now + CODE_TTL), _iso(now), json.dumps(payload) if payload else None)
    return code


async def withdraw_code(email: str) -> None:
    """Forget a code that could not be emailed, so it doesn't count against the
    resend/hourly limits (the client never received it)."""
    await db.portal_execute(
        """DELETE FROM portal_login_codes WHERE id = (SELECT id FROM portal_login_codes
           WHERE email = ? AND used_at IS NULL ORDER BY id DESC LIMIT 1)""", email)


async def client_exists(email: str) -> bool:
    row = await db.portal_fetchrow("SELECT status FROM portal_clients WHERE email = ?", email)
    return bool(row) and row["status"] != "BLOCKED"


async def verify_code(email: str, code: str) -> Dict[str, Any]:
    """Check the latest unused code (it proves the email is theirs). Completes
    a sign-up (creates the account with its password) or, for an existing
    account, opens a session that may set a new password for a short while.
    Returns {"token", "client"}."""
    code = (code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        raise PortalError("Enter the 6-digit code from the email.")
    row = await db.portal_fetchrow(
        "SELECT * FROM portal_login_codes WHERE email = ? AND used_at IS NULL ORDER BY id DESC LIMIT 1", email)
    now = _now()
    if not row or datetime.fromisoformat(str(row["expires_at"])) < now:
        raise PortalError("This code has expired. Ask for a new one.", 400)
    if row["attempts"] >= CODE_MAX_ATTEMPTS:
        raise PortalError("Too many wrong tries. Ask for a new code.", 429)
    if not hmac.compare_digest(row["code_hash"], _hash_code(email, code)):
        await db.portal_execute("UPDATE portal_login_codes SET attempts = attempts + 1 WHERE id = ?", row["id"])
        left = CODE_MAX_ATTEMPTS - row["attempts"] - 1
        raise PortalError(f"That code isn't right. {left} tr{'y' if left == 1 else 'ies'} left." if left > 0
                          else "Too many wrong tries. Ask for a new code.", 400 if left > 0 else 429)
    await db.portal_execute("UPDATE portal_login_codes SET used_at = ? WHERE id = ?", _iso(now), row["id"])

    await check_can_sign_in(email)    # settings may have changed since the code went out
    payload = json.loads(row["payload"]) if row.get("payload") else {}
    client = await db.portal_fetchrow("SELECT * FROM portal_clients WHERE email = ?", email)
    if not client and not payload.get("password_hash"):
        raise PortalError("There's no account for this email yet. Sign up first.", 404)
    if not client:
        cid = await db.portal_insert("INSERT INTO portal_clients (email) VALUES (?) RETURNING id", email)
        client = await db.portal_fetchrow("SELECT * FROM portal_clients WHERE id = ?", cid)
    if payload.get("password_hash"):
        await db.portal_execute(
            "UPDATE portal_clients SET password_hash = ?, failed_logins = 0, locked_until = NULL WHERE id = ?",
            payload["password_hash"], client["id"])
    for field in ("name", "company"):
        if payload.get(field) and not client.get(field):
            await db.portal_execute(f"UPDATE portal_clients SET {field} = ? WHERE id = ?", payload[field], client["id"])
    await db.portal_execute(
        "UPDATE portal_clients SET last_login_at = ?, email_verified_at = COALESCE(email_verified_at, ?) WHERE id = ?",
        _iso(now), _iso(now), client["id"])
    client = await db.portal_fetchrow("SELECT * FROM portal_clients WHERE id = ?", client["id"])

    # Automatic workspaces: the first sign-in creates the client's own dashboard
    # (if there is room; otherwise they wait in line — see workspaces.py).
    from . import workspaces
    await workspaces.ensure_for_client(client)

    return await _new_session(client, via_code=True)


async def _new_session(client: Dict[str, Any], via_code: bool) -> Dict[str, Any]:
    now = _now()
    token = secrets.token_urlsafe(32)
    await db.portal_execute(
        "INSERT INTO portal_sessions (token_hash, client_id, expires_at, via_code_at) VALUES (?, ?, ?, ?)",
        _hash_token(token), client["id"], _iso(now + SESSION_TTL), _iso(now) if via_code else None)
    await db.portal_execute("DELETE FROM portal_sessions WHERE expires_at < ?", _iso(now))
    return {"token": token, "client": public_client(client)}


# ── Passwords ────────────────────────────────────────────────────────────────

def check_password_strength(password: str, email: str = "") -> None:
    pw = password or ""
    if len(pw) < PASSWORD_MIN:
        raise PortalError(f"Use at least {PASSWORD_MIN} characters for your password.")
    if len(pw.encode("utf-8")) > PASSWORD_MAX_BYTES:
        raise PortalError("That password is too long — use at most 72 characters.")
    low = pw.lower()
    if low in _COMMON or len(set(low)) < 4:
        raise PortalError("That password is too easy to guess. Try a longer phrase.")
    if email and (low == email.lower() or low == email.split("@")[0].lower()):
        raise PortalError("Don't use your email address as your password.")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _password_ok(password: str, hashed: Optional[str]) -> bool:
    try:
        return bcrypt.checkpw((password or "").encode("utf-8"), (hashed or _DUMMY_HASH).encode("utf-8")) and bool(hashed)
    except (ValueError, TypeError):
        return False


async def start_signup(email: str, password: str, name: str, company: Optional[str]) -> str:
    """Sign-up step 1: check everything, then email a code. Nothing is saved on
    the account until the code proves the email belongs to them."""
    name = (name or "").strip()[:120]
    if not name:
        raise PortalError("Tell us your name.")
    check_password_strength(password, email)
    cfg = await portal_config.get_config()
    mode = cfg.get("signup_mode", "open")
    client = await db.portal_fetchrow("SELECT * FROM portal_clients WHERE email = ?", email)
    if client and client.get("password_hash"):
        raise PortalError("You already have an account. Log in instead.", 409)
    if mode == "closed":
        raise PortalError("New sign-ups are closed right now. Please check back later.", 403)
    if mode == "invite" and not client:
        raise PortalError(f"Sign-up is by invitation only. Ask {cfg.get('name') or 'us'} to invite this email.", 403)
    return await issue_code(email, {"password_hash": hash_password(password), "name": name,
                                    "company": (company or "").strip()[:160] or None})


async def login(email: str, password: str) -> Dict[str, Any]:
    now = _now()
    client = await db.portal_fetchrow("SELECT * FROM portal_clients WHERE email = ?", email)
    if client and client.get("locked_until") and datetime.fromisoformat(str(client["locked_until"])) > now:
        raise PortalError("Too many wrong passwords. Try again in 15 minutes, or reset your password.", 429)
    ok = _password_ok(password, client.get("password_hash") if client else None)
    if client and not client.get("password_hash"):
        raise PortalError("Your account doesn't have a password yet. Use “Forgot password?” to set one.", 409)
    if not client or not ok:
        if client:
            fails = int(client.get("failed_logins") or 0) + 1
            lock = _iso(now + LOCK_TIME) if fails >= LOGIN_MAX_FAILURES else None
            await db.portal_execute("UPDATE portal_clients SET failed_logins = ?, locked_until = ? WHERE id = ?",
                                    0 if lock else fails, lock, client["id"])
        raise PortalError("Email or password is incorrect.", 401)
    if client["status"] == "BLOCKED":
        raise PortalError("This account has been disabled. Contact the owner.", 403)
    await check_can_sign_in(email)
    await db.portal_execute("UPDATE portal_clients SET failed_logins = 0, locked_until = NULL, last_login_at = ? WHERE id = ?",
                            _iso(now), client["id"])
    client = await db.portal_fetchrow("SELECT * FROM portal_clients WHERE id = ?", client["id"])
    from . import workspaces
    await workspaces.ensure_for_client(client)
    return await _new_session(client, via_code=False)


async def change_password(client: Dict[str, Any], token: str, new_password: str,
                          current_password: Optional[str]) -> None:
    """Set a new password: with the current one, or right after an emailed
    code (forgot password / first password). Signs out every other session."""
    session = await db.portal_fetchrow("SELECT via_code_at FROM portal_sessions WHERE token_hash = ?", _hash_token(token))
    via_code = session and session.get("via_code_at") and \
        _now() - datetime.fromisoformat(str(session["via_code_at"])) < RESET_WINDOW
    if not via_code and not (client.get("password_hash") and _password_ok(current_password or "", client["password_hash"])):
        raise PortalError("Enter your current password." if client.get("password_hash")
                          else "For your security, confirm your email again to set a password.", 403)
    check_password_strength(new_password, client["email"])
    await db.portal_execute("UPDATE portal_clients SET password_hash = ?, failed_logins = 0, locked_until = NULL WHERE id = ?",
                            hash_password(new_password), client["id"])
    await db.portal_execute("DELETE FROM portal_sessions WHERE client_id = ? AND token_hash != ?",
                            client["id"], _hash_token(token))
    await db.portal_execute("UPDATE portal_sessions SET via_code_at = NULL WHERE token_hash = ?", _hash_token(token))


# ── Sessions ─────────────────────────────────────────────────────────────────

async def client_for_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    row = await db.portal_fetchrow(
        """SELECT c.* , s.expires_at AS session_expires FROM portal_sessions s
           JOIN portal_clients c ON c.id = s.client_id WHERE s.token_hash = ?""", _hash_token(token))
    if not row or datetime.fromisoformat(str(row["session_expires"])) < _now() or row["status"] == "BLOCKED":
        return None
    return row


async def end_session(token: str) -> None:
    await db.portal_execute("DELETE FROM portal_sessions WHERE token_hash = ?", _hash_token(token))


def public_client(c: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": c["id"], "email": c["email"], "name": c.get("name"), "company": c.get("company"),
            "needs_profile": not (c.get("name") or "").strip(), "has_password": bool(c.get("password_hash")),
            "sector": c.get("sector"), "needs_onboarding": not c.get("onboarded_at")}


async def update_profile(client_id: int, name: str, company: Optional[str]) -> Dict[str, Any]:
    name = (name or "").strip()[:120]
    if not name:
        raise PortalError("Tell us your name.")
    await db.portal_execute("UPDATE portal_clients SET name = ?, company = ? WHERE id = ?",
                            name, (company or "").strip()[:160] or None, client_id)
    return public_client(await db.portal_fetchrow("SELECT * FROM portal_clients WHERE id = ?", client_id))


# ── Owner: add a client ──────────────────────────────────────────────────────

async def add_client(email: str, name: Optional[str], company: Optional[str]) -> Dict[str, Any]:
    """Create a client account ahead of their first sign-in (needed for
    invite-only portals). Raises PortalError(409) if the email already exists."""
    email = normalize_email(email)
    if await db.portal_fetchrow("SELECT id FROM portal_clients WHERE email = ?", email):
        raise PortalError("A client with this email already exists.", 409)
    cid = await db.portal_insert(
        "INSERT INTO portal_clients (email, name, company) VALUES (?, ?, ?) RETURNING id",
        email, (name or "").strip()[:120] or None, (company or "").strip()[:160] or None)
    return await db.portal_fetchrow("SELECT * FROM portal_clients WHERE id = ?", cid)


# ── First-run setup ──────────────────────────────────────────────────────────

ONBOARDING_MIN_WORDS = 20
DNA_MAX_CHARS = 20000


async def complete_onboarding(client_id: int, name: str, company: Optional[str], sector: str,
                              company_dna: str) -> Dict[str, Any]:
    """Save the client's name, sector and company profile (Company DNA). The
    profile is copied into their workspace by the workspace service."""
    name = (name or "").strip()[:120]
    sector = (sector or "").strip()[:80]
    dna = (company_dna or "").strip()
    if not name:
        raise PortalError("Tell us your name.")
    if not sector:
        raise PortalError("Choose your sector.")
    written = re.sub(r"(?m)^\s*#+.*$", " ", dna)          # outline headings don't count
    if len(written.split()) < ONBOARDING_MIN_WORDS:
        raise PortalError(f"Tell us a bit more about your company — at least {ONBOARDING_MIN_WORDS} words.")
    if len(dna) > DNA_MAX_CHARS:
        raise PortalError("Your company profile is too long — keep it under 20,000 characters.")
    await db.portal_execute(
        """UPDATE portal_clients SET name = ?, company = ?, sector = ?, company_dna = ?,
           onboarded_at = COALESCE(onboarded_at, ?) WHERE id = ?""",
        name, (company or "").strip()[:160] or None, sector, dna, _iso(_now()), client_id)
    from . import workspaces
    await workspaces.write_control()
    return public_client(await db.portal_fetchrow("SELECT * FROM portal_clients WHERE id = ?", client_id))
