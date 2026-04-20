"""
log_stream.py — Thread-safe shared log bus for real-time SSE streaming.

Both email_sender and whatsapp_sender push status entries here.
The /api/logs/stream SSE endpoint (or any consumer) can drain() these.

Uses queue.SimpleQueue — safe to call put_nowait from sync threads
running inside asyncio.to_thread().
"""
import queue
from datetime import datetime, timezone
from typing import Any, Dict, List

_q: queue.SimpleQueue = queue.SimpleQueue()

LogEntry = Dict[str, Any]


def emit(level: str, channel: str, message: str) -> None:
    """
    Push a log entry. Thread-safe — callable from sync threads or async code.

    Args:
        level:   "INFO" | "WARNING" | "ERROR"
        channel: "EMAIL" | "WHATSAPP" | "SYSTEM"
        message: human-readable log line
    """
    _q.put_nowait({
        "level":     level.upper(),
        "channel":   channel.upper(),
        "message":   message,
        "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
    })


def drain() -> List[LogEntry]:
    """
    Non-blocking drain — returns all queued entries without waiting.
    Resets the queue. Suitable for polling-based SSE consumers.
    """
    entries: List[LogEntry] = []
    while True:
        try:
            entries.append(_q.get_nowait())
        except queue.Empty:
            break
    return entries


def get_queue() -> queue.SimpleQueue:
    """Return the underlying queue for consumers that prefer blocking reads."""
    return _q
