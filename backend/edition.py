"""
edition.py — owner app vs. client workspace.

The same code runs in two editions:
  * owner   (default)            — your dashboard: everything, incl. Clients.
  * client  (HOM_EDITION=client) — one client's private workspace, started by
    scripts/hom_supervisor.py from the last *published* commit. Differences:
      - sign-in only through the client link (a signed, one-time hand-off from
        the portal); no app password can be set or used;
      - no client portal / Clients admin; WhatsApp-desktop sending is off
        (it would drive the owner's own desktop);
      - owner-level settings (local AI model, automation) are locked;
      - an outbound guard: the workspace may reach the public internet (search,
        websites, the client's own email) but never private addresses — the
        owner's machine, home network, or other workspaces. Only the local AI
        relay is allowed.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import socket
import sys
import time
from functools import lru_cache
from typing import Any, Dict, Optional, Set, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def is_client() -> bool:
    return os.getenv("HOM_EDITION", "").strip().lower() == "client"


def edition() -> str:
    return "client" if is_client() else "owner"


# Settings a client workspace may not change (shared resources of the owner's
# machine, or owner-only integrations).
LOCKED_SETTING_PREFIXES = ("ollama_", "llm_", "n8n_", "automation_", "company_dna_path", "app_password",
                           "queue_workers", "scraper_headless", "whatsapp_", "portal_", "email_campaigns_enabled")


def setting_locked(key: str) -> bool:
    return is_client() and any(key.startswith(p) for p in LOCKED_SETTING_PREFIXES)


# ── Hand-off tokens (portal → workspace sign-in) ─────────────────────────────
#
# token = b64url(json{w: workspace id, e: email, x: expiry, n: nonce}) "." b64url(HMAC-SHA256)
# The owner app signs with the workspace's secret; only that workspace knows it
# (HOM_HANDOFF_SECRET), so a token for one client's workspace is useless in any
# other. Tokens live 90 seconds and work once.

HANDOFF_TTL_S = 90


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def workspace_secret(master_key: bytes, workspace_id: int) -> str:
    """Per-workspace hand-off secret, derived from the supervisor's master key."""
    return hmac.new(master_key, f"hom-workspace:{int(workspace_id)}".encode(), hashlib.sha256).hexdigest()


def sign_handoff(secret: str, workspace_id: int, email: str, nonce: str, now: Optional[float] = None) -> str:
    payload = json.dumps({"w": int(workspace_id), "e": email, "x": int((now or time.time()) + HANDOFF_TTL_S),
                          "n": nonce}, separators=(",", ":")).encode()
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(sig)}"


class HandoffError(ValueError):
    pass


_used_nonces: Dict[str, float] = {}


def verify_handoff(token: str, secret: str, workspace_id: int, now: Optional[float] = None) -> Dict[str, Any]:
    now = now or time.time()
    if not secret:
        raise HandoffError("this workspace has no sign-in key")
    try:
        p64, s64 = (token or "").split(".", 1)
        payload, sig = _unb64(p64), _unb64(s64)
    except ValueError:
        raise HandoffError("malformed sign-in link")
    want = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, want):
        raise HandoffError("invalid sign-in link")
    data = json.loads(payload)
    if int(data.get("w", -1)) != int(workspace_id):
        raise HandoffError("this sign-in link is for another workspace")
    if float(data.get("x", 0)) < now:
        raise HandoffError("this sign-in link has expired")
    for n, exp in list(_used_nonces.items()):
        if exp < now:
            _used_nonces.pop(n, None)
    nonce = str(data.get("n", ""))
    if not nonce or nonce in _used_nonces:
        raise HandoffError("this sign-in link was already used")
    _used_nonces[nonce] = float(data["x"])
    return data


# ── Outbound guard ────────────────────────────────────────────────────────────

def _allowed_private() -> Set[Tuple[str, int]]:
    """Private (ip, port) pairs a workspace may still reach: the AI relay."""
    out: Set[Tuple[str, int]] = set()
    for var in ("OLLAMA_BASE_URL",):
        url = os.getenv(var, "")
        if not url:
            continue
        u = urlparse(url)
        if not u.hostname:
            continue
        port = u.port or (443 if u.scheme == "https" else 80)
        try:
            for info in socket.getaddrinfo(u.hostname, port, proto=socket.IPPROTO_TCP):
                out.add((info[4][0], port))
        except OSError:
            pass
    return out


def ip_is_private(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return True          # unparseable -> treat as unsafe
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return not a.is_global or a.is_multicast


class EgressBlocked(ConnectionRefusedError):
    pass


_guard_installed = False


def install_egress_guard() -> None:
    """Block every outbound TCP/UDP connection from this Python process to a
    private address (checked on the resolved IP, so DNS tricks don't help).
    Uses an audit hook: it covers every library (httpx, requests, aiohttp,
    smtplib, asyncio) and cannot be removed once installed."""
    global _guard_installed
    if _guard_installed:
        return
    allowed = _allowed_private()

    def hook(event: str, args: tuple) -> None:
        if event != "socket.connect":
            return
        sock, address = args[0], args[1]
        if getattr(sock, "family", None) not in (socket.AF_INET, socket.AF_INET6):
            return
        if not isinstance(address, tuple) or not address:
            return
        ip, port = str(address[0]), int(address[1]) if len(address) > 1 else 0
        if ip_is_private(ip) and (ip, port) not in allowed:
            raise EgressBlocked(f"blocked: client workspaces can't connect to private address {ip}:{port}")

    sys.addaudithook(hook)
    _guard_installed = True
    logger.info("Client edition: outbound guard on (private addresses blocked, allowed: %s)", sorted(allowed))


@lru_cache(maxsize=2048)
def _host_is_private(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False       # unresolvable: the browser will fail on its own
    return any(ip_is_private(i[4][0]) for i in infos)


def url_is_private(url: str) -> bool:
    try:
        u = urlparse(url)
    except ValueError:
        return True
    if u.scheme in ("data", "blob", "about"):
        return False
    if u.scheme not in ("http", "https", "ws", "wss"):
        return True
    host = (u.hostname or "").strip("[]").lower()
    if not host or host == "localhost" or host.endswith(".localhost") or host.endswith(".internal"):
        return True
    return _host_is_private(host)


async def guard_browser_context(context) -> None:
    """Playwright: in a client workspace, abort any browser request to a
    private address (the browser is a separate process the audit hook can't see)."""
    if not is_client():
        return

    async def _route(route):
        if url_is_private(route.request.url):
            await route.abort("blockedbyclient")
        else:
            await route.continue_()

    await context.route("**/*", _route)
