"""
email_sender.py — SMTP email delivery with HTML templating.

Public API:
  send_email(to_email, subject, body, config) -> bool   sync low-level (thread-safe)
  send_email_lead(lead)                                 async high-level wrapper
  send_followup_email(lead)                             async high-level wrapper
  test_connection(config=None) -> dict                  async — no-arg reads DB
"""

import asyncio
import html as _html
import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Any, Dict, Optional

from . import database as db
from .config import get_settings
from .log_stream import emit as _emit

_env   = get_settings()
logger = logging.getLogger(__name__)


# ── Config helpers ─────────────────────────────────────────────────────────────


async def _smtp_cfg() -> Dict[str, Any]:
    """Read DB settings (live UI values) with .env fallback."""
    stored = await db.get_all_settings()
    return {
        "host":       stored.get("smtp_host")       or _env.smtp_host,
        "port":       int(stored.get("smtp_port")   or _env.smtp_port),
        "username":   stored.get("smtp_username")   or _env.smtp_username,
        "password":   stored.get("smtp_password")   or _env.smtp_password,
        "from_name":  stored.get("smtp_from_name")  or _env.smtp_from_name,
        "from_email": stored.get("smtp_from_email") or _env.smtp_from_email,
    }


def _normalize_smtp_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Accept two config formats and normalise to a unified internal dict.

    Simple Gmail format   →  {gmail_address, app_password[, from_name]}
    Full SMTP format      →  {host, port, username, password, from_name, from_email}
    """
    if "gmail_address" in config:
        addr = config["gmail_address"].strip()
        return {
            "host":       "smtp.gmail.com",
            "port":       465,                              # SMTP_SSL — Gmail port
            "username":   addr,
            "password":   config.get("app_password", "").strip(),
            "from_name":  config.get("from_name", "").strip(),
            "from_email": addr,
        }
    return {
        "host":       config.get("host",       "smtp.gmail.com"),
        "port":       int(config.get("port",   587)),
        "username":   config.get("username",   "").strip(),
        "password":   config.get("password",   "").strip(),
        "from_name":  config.get("from_name",  "").strip(),
        "from_email": (config.get("from_email") or config.get("username", "")).strip(),
    }


# ── HTML template ──────────────────────────────────────────────────────────────


def _html_wrap(plain_body: str, from_name: str, from_email: str) -> str:
    """
    Convert a plain-text email body into a clean, responsive HTML email.

    Paragraph detection: double newlines → <p>; single newlines → <br>.
    All user content is HTML-escaped before insertion.
    """
    safe_name  = _html.escape(from_name)
    safe_email = _html.escape(from_email)
    year       = datetime.now().year

    # Split on paragraph breaks, escape each paragraph, convert line breaks
    raw_paras = plain_body.replace("\r\n", "\n").replace("\r", "\n").split("\n\n")
    para_html = "".join(
        f"<p>{_html.escape(p.strip()).replace(chr(10), '<br>')}</p>"
        for p in raw_paras
        if p.strip()
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>Message</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#f2f4f6;font-family:Arial,Helvetica,sans-serif;padding:32px 16px}}
    .wrap{{max-width:600px;margin:0 auto}}
    .card{{background:#fff;border-radius:10px;padding:40px 44px;
           box-shadow:0 2px 8px rgba(0,0,0,.08)}}
    p{{margin-bottom:18px;line-height:1.75;color:#1a1a1a;font-size:15px}}
    p:last-child{{margin-bottom:0}}
    .footer{{margin-top:20px;padding-top:14px;border-top:1px solid #e2e2e2;
             font-size:12px;color:#9e9e9e;text-align:center;line-height:1.8}}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      {para_html}
    </div>
    <div class="footer">
      {safe_name}&nbsp;&bull;&nbsp;{safe_email}<br>
      <small>&copy; {year}</small>
    </div>
  </div>
</body>
</html>"""


# ── Core SMTP send (sync — designed to run via asyncio.to_thread) ──────────────


def _send_smtp(
    to_email:   str,
    to_name:    str,
    subject:    str,
    body:       str,
    cfg:        Dict[str, Any],
) -> None:
    """
    Blocking SMTP send. Raises on any failure — callers decide how to handle it.

    Transport selection:
      port 465 → smtplib.SMTP_SSL  (direct TLS — Gmail recommended)
      port 587  → smtplib.SMTP + STARTTLS
      any other → STARTTLS (safe default)
    """
    if not cfg["username"] or not cfg["password"]:
        raise ValueError(
            "SMTP credentials not configured. "
            "Open Settings and enter your Gmail address and App Password."
        )

    html_body = _html_wrap(body, cfg["from_name"], cfg["from_email"])

    msg              = MIMEMultipart("alternative")
    msg["Subject"]   = subject
    msg["From"]      = formataddr((cfg["from_name"], cfg["from_email"]))
    msg["To"]        = formataddr((to_name, to_email)) if to_name else to_email
    msg["X-Mailer"]  = "AutoLead-Engine/2.0"
    msg.attach(MIMEText(body,      "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html",  "utf-8"))

    host, port = cfg["host"], cfg["port"]
    _emit("INFO", "EMAIL", f"Connecting to {host}:{port}…")

    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(cfg["username"], cfg["password"])
            server.sendmail(cfg["from_email"], to_email, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(cfg["username"], cfg["password"])
            server.sendmail(cfg["from_email"], to_email, msg.as_string())

    _emit("INFO", "EMAIL", f"Delivered → {to_email}")


# ══ PUBLIC API ══════════════════════════════════════════════════════════════════


def send_email(to_email: str, subject: str, body: str, config: Dict[str, Any]) -> bool:
    """
    Send an HTML email synchronously.
    Thread-safe — intended to be called via asyncio.to_thread().

    Args:
        to_email: recipient address
        subject:  subject line
        body:     plain-text body (auto-wrapped in a clean HTML template)
        config:   {gmail_address, app_password[, from_name]}
                  OR {host, port, username, password, from_name, from_email}

    Returns:
        True on success, False on any failure (error is logged + pushed to log_stream)
    """
    cfg = _normalize_smtp_config(config)
    try:
        _send_smtp(to_email, "", subject, body, cfg)
        return True

    except smtplib.SMTPAuthenticationError as exc:
        raw = exc.smtp_error
        hint = raw.decode(errors="replace") if isinstance(raw, bytes) else str(exc)
        msg = f"Gmail authentication failed — verify your App Password. Detail: {hint}"
        logger.error("SMTP auth error for %s: %s", to_email, hint)
        _emit("ERROR", "EMAIL", msg)
        return False

    except smtplib.SMTPRecipientsRefused:
        msg = f"Recipient refused by server: {to_email}"
        logger.error(msg)
        _emit("ERROR", "EMAIL", msg)
        return False

    except smtplib.SMTPSenderRefused as exc:
        msg = f"Sender address refused ({cfg['from_email']}): {exc.smtp_error}"
        logger.error(msg)
        _emit("ERROR", "EMAIL", msg)
        return False

    except smtplib.SMTPDataError as exc:
        msg = f"Server rejected message data: {exc.smtp_error}"
        logger.error("SMTP data error to %s: %s", to_email, exc)
        _emit("ERROR", "EMAIL", msg)
        return False

    except smtplib.SMTPConnectError as exc:
        msg = f"Cannot connect to {cfg['host']}:{cfg['port']} — {exc}"
        logger.error(msg)
        _emit("ERROR", "EMAIL", msg)
        return False

    except smtplib.SMTPServerDisconnected as exc:
        msg = f"Server disconnected unexpectedly: {exc}"
        logger.error(msg)
        _emit("ERROR", "EMAIL", msg)
        return False

    except (OSError, ConnectionRefusedError, TimeoutError) as exc:
        msg = f"Network error reaching {cfg['host']}:{cfg['port']} — {exc}"
        logger.error(msg)
        _emit("ERROR", "EMAIL", msg)
        return False

    except ValueError as exc:
        logger.error("Config error: %s", exc)
        _emit("ERROR", "EMAIL", str(exc))
        return False

    except Exception as exc:
        logger.error("Unexpected error sending to %s: %s", to_email, exc, exc_info=True)
        _emit("ERROR", "EMAIL", f"Unexpected error: {type(exc).__name__}: {exc}")
        return False


async def test_connection(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Test SMTP connectivity and authentication.

    Args:
        config: optional — same format as send_email().
                If None (default), reads live settings from DB (backward-compat).

    Returns:
        {success: bool, message: str[, host: str, from_email: str]}
    """
    if config is None:
        cfg = await _smtp_cfg()
    else:
        cfg = _normalize_smtp_config(config)

    _emit("INFO", "EMAIL", f"Testing SMTP → {cfg['host']}:{cfg['port']}…")

    def _test_sync() -> None:
        if cfg["port"] == 465:
            with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=10) as server:
                server.login(cfg["username"], cfg["password"])
        else:
            with smtplib.SMTP(cfg["host"], cfg["port"], timeout=10) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(cfg["username"], cfg["password"])

    try:
        await asyncio.to_thread(_test_sync)
        msg = f"Connected successfully as {cfg['from_email']}"
        _emit("INFO", "EMAIL", f"SMTP test OK — {cfg['from_email']}")
        return {"success": True, "message": msg, "host": cfg["host"], "from_email": cfg["from_email"]}

    except smtplib.SMTPAuthenticationError as exc:
        raw  = exc.smtp_error
        hint = raw.decode(errors="replace") if isinstance(raw, bytes) else str(exc)
        msg  = (
            f"Authentication failed. "
            f"For Gmail: enable 2-Step Verification, then create an App Password at "
            f"myaccount.google.com/apppasswords. Detail: {hint}"
        )
        _emit("ERROR", "EMAIL", msg)
        return {"success": False, "message": msg}

    except (smtplib.SMTPConnectError, OSError, ConnectionRefusedError) as exc:
        msg = f"Cannot reach {cfg['host']}:{cfg['port']} — check host/port and firewall. Error: {exc}"
        _emit("ERROR", "EMAIL", msg)
        return {"success": False, "message": msg}

    except TimeoutError:
        msg = f"Connection timed out after 10 s — {cfg['host']}:{cfg['port']}"
        _emit("ERROR", "EMAIL", msg)
        return {"success": False, "message": msg}

    except Exception as exc:
        msg = f"Unexpected error: {type(exc).__name__}: {exc}"
        _emit("ERROR", "EMAIL", msg)
        return {"success": False, "message": msg}


# ── Legacy high-level API (called by scheduler.py and campaigns.py) ────────────


_PLACEHOLDER_USERS = frozenset({"", "you@gmail.com", "your@email.com", "user@gmail.com"})
_PLACEHOLDER_PWDS  = frozenset({"", "your_app_password_here", "your-app-password", "yourpassword", "password"})
_MSG_GARBAGE       = ("[Generation failed", "[Message generation failed", "[Subject —", "[Body —")


def _clean_subject(raw: Optional[str]) -> str:
    if raw and not any(raw.startswith(g) for g in _MSG_GARBAGE):
        return raw
    return "Quick question about your business"


def _clean_body(raw: Optional[str]) -> str:
    if raw and not any(raw.startswith(g) for g in _MSG_GARBAGE):
        return raw
    return (
        "Hi,\n\n"
        "I came across your business and wanted to reach out about a growth opportunity.\n\n"
        "We help local businesses attract more customers through targeted digital marketing "
        "— typically seeing results within the first 30 days.\n\n"
        "Would you be open to a quick 10-minute call to see if we'd be a good fit?\n\n"
        "Best regards"
    )


async def send_email_lead(lead: Dict[str, Any]) -> None:
    """
    Send initial outreach email for a lead.
    Reads SMTP config from DB. Raises RuntimeError on delivery failure.
    """
    to_email = lead.get("email")
    if not to_email:
        raise ValueError(f"Lead {lead.get('id')} has no email address")

    cfg_dict = await _smtp_cfg()

    if cfg_dict.get("username", "") in _PLACEHOLDER_USERS:
        raise ValueError(
            "SMTP email not configured — open Settings → Email and enter your Gmail address."
        )
    if cfg_dict.get("password", "") in _PLACEHOLDER_PWDS:
        raise ValueError(
            "SMTP password not configured — open Settings → Email and enter your Gmail App Password."
        )

    subject = _clean_subject(lead.get("ai_email_subject"))
    body    = _clean_body(lead.get("ai_email_body"))

    _emit("INFO", "EMAIL", f"Sending → {lead.get('business_name', to_email)}")

    ok = await asyncio.to_thread(send_email, to_email, subject, body, cfg_dict)
    if not ok:
        raise RuntimeError(f"Email delivery failed for {to_email}")


async def send_followup_email(lead: Dict[str, Any]) -> None:
    """Send a follow-up email to a lead that hasn't responded."""
    to_email = lead.get("email")
    if not to_email:
        raise ValueError(f"Lead {lead.get('id')} has no email address")

    cfg_dict = await _smtp_cfg()

    if cfg_dict.get("username", "") in _PLACEHOLDER_USERS:
        raise ValueError(
            "SMTP email not configured — open Settings → Email and enter your Gmail address."
        )
    if cfg_dict.get("password", "") in _PLACEHOLDER_PWDS:
        raise ValueError(
            "SMTP password not configured — open Settings → Email and enter your Gmail App Password."
        )

    body    = _clean_body(lead.get("ai_followup_msg"))
    subject = f"Re: {_clean_subject(lead.get('ai_email_subject'))}"

    _emit("INFO", "EMAIL", f"Follow-up → {lead.get('business_name', to_email)}")

    ok = await asyncio.to_thread(send_email, to_email, subject, body, cfg_dict)
    if not ok:
        raise RuntimeError(f"Follow-up email failed for {to_email}")
