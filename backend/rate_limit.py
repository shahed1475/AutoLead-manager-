"""
rate_limit.py — shared slowapi Limiter instance.

Kept in its own module (rather than defined in main.py) so routers can
import `limiter` for their `@limiter.limit(...)` decorators without a
circular import on main.py. main.py registers it on the FastAPI app.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
