"""
cache.py — Redis async client for AutoLead v3.

Provides a module-level connection (lazy-initialised) and simple
get/set/delete helpers. More complex patterns (pub/sub, sorted sets)
can be accessed via get_redis() directly.
"""
import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

from .config import get_settings

logger   = logging.getLogger(__name__)
settings = get_settings()

_redis: Optional[aioredis.Redis] = None


# ── Lifecycle ─────────────────────────────────────────────────────────────────

async def get_redis() -> aioredis.Redis:
    """Return the module-level Redis client, creating it on first call."""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis:
        try:
            await _redis.aclose()
        except Exception:
            pass
        _redis = None
        logger.info("Redis connection closed")


async def ping() -> bool:
    """Return True if Redis is reachable."""
    try:
        r = await get_redis()
        return await r.ping()
    except Exception:
        return False


# ── Key/value helpers ─────────────────────────────────────────────────────────

async def cache_get(key: str) -> Optional[Any]:
    """Return the cached value for key, or None if missing/expired."""
    try:
        r   = await get_redis()
        raw = await r.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw
    except Exception as exc:
        logger.debug("cache_get failed for %s: %s", key, exc)
        return None


async def cache_set(key: str, value: Any, ttl: int = 300) -> None:
    """Serialise value as JSON and store with a TTL (seconds)."""
    try:
        r   = await get_redis()
        raw = json.dumps(value, default=str)
        await r.setex(key, ttl, raw)
    except Exception as exc:
        logger.debug("cache_set failed for %s: %s", key, exc)


async def cache_delete(key: str) -> None:
    try:
        r = await get_redis()
        await r.delete(key)
    except Exception as exc:
        logger.debug("cache_delete failed for %s: %s", key, exc)


async def cache_delete_pattern(pattern: str) -> int:
    """Delete all keys matching a glob pattern. Returns count deleted."""
    try:
        r    = await get_redis()
        keys = await r.keys(pattern)
        if keys:
            return await r.delete(*keys)
    except Exception as exc:
        logger.debug("cache_delete_pattern failed for %s: %s", pattern, exc)
    return 0


# ── Pub/sub helpers (for real-time notifications) ─────────────────────────────

async def publish(channel: str, message: Any) -> None:
    """Publish a JSON-serialised message to a Redis pub/sub channel."""
    try:
        r   = await get_redis()
        raw = json.dumps(message, default=str)
        await r.publish(channel, raw)
    except Exception as exc:
        logger.debug("publish failed on %s: %s", channel, exc)
