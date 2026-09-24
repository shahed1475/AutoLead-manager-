"""
rate_limit.py — shared slowapi Limiter instance.

Kept in its own module (rather than defined in main.py) so routers can
import `limiter` for their `@limiter.limit(...)` decorators without a
circular import on main.py. main.py registers it on the FastAPI app.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)


def visitor_ip(request) -> str:
    """The real visitor behind the client link. Requests arrive from the local
    web server/tunnel (loopback), which pass the visitor's address along
    (Cloudflare's CF-Connecting-IP, else the first X-Forwarded-For hop).
    Headers are only trusted when the direct peer is loopback."""
    peer = get_remote_address(request)
    if peer in ("127.0.0.1", "::1", "localhost"):
        ip = (request.headers.get("cf-connecting-ip")
              or (request.headers.get("x-forwarded-for") or "").split(",")[0]).strip()
        if ip:
            return ip
    return peer
