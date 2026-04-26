import asyncio
import json
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .config import get_settings
from .database import (
    init_db, close_db, get_db, get_all_settings,
    get_dashboard_stats, get_weekly_activity, get_recent_logs,
)
from .cache import close_redis
from .scheduler import start_scheduler, stop_scheduler, get_scheduler_status
from .routers import leads, campaigns, ai, scraper_router, settings_router, status
from .routers import inbox as inbox_router
from .routers.campaigns import get_campaign_state
from .queue_worker import init_queue, get_queue

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
settings = get_settings()


# ── App lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    stored = await get_all_settings()
    hour   = int(stored.get("schedule_hour") or settings.schedule_hour)
    start_scheduler(hour)

    # Start parallel job queue
    n_workers = int(stored.get("queue_workers") or settings.queue_workers)
    queue = init_queue(n_workers=n_workers)
    await queue.start()

    yield

    await queue.stop()
    stop_scheduler()
    await close_db()
    await close_redis()


# ── App factory ───────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.app_name,
    version="2.0.0",
    description="Zero-Cost Marketing Engine — REST API",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(inbox_router.router)   # first — static /leads/score-* paths before /{lead_id}
app.include_router(leads.router)
app.include_router(campaigns.router)
app.include_router(ai.router)
app.include_router(scraper_router.router)
app.include_router(settings_router.router)
app.include_router(status.router)


# ── Log line formatter (shared by SSE stream) ─────────────────────────────────

def _fmt_log(row: dict) -> str:
    biz     = row.get("business_name") or "Unknown"
    action  = row.get("action") or ""
    channel = row.get("channel") or ""
    success = bool(row.get("success"))
    error   = row.get("error_msg") or ""
    email   = row.get("email") or ""
    phone   = row.get("phone") or ""

    if action == "FOUND":
        parts = [f"✅ Found: {biz}"]
        if email: parts.append(f"📧 {email}")
        if phone: parts.append(f"📱 {phone}")
        return " | ".join(parts)

    if action == "GENERATE":
        return (
            f"🤖 AI wrote messages for {biz}"
            if success
            else f"⚠️  AI failed for {biz}: {error}"
        )

    if action == "SEND":
        if success:
            icon  = {"EMAIL": "📤", "WHATSAPP": "💬", "BOTH": "📨"}.get(channel, "📤")
            label = {"EMAIL": "Email", "WHATSAPP": "WhatsApp", "BOTH": "Email+WA"}.get(channel, channel)
            return f"{icon} {label} sent → {biz}"
        return f"❌ Send failed → {biz}: {error}"

    if action == "FOLLOWUP":
        return (
            f"🔄 Follow-up sent → {biz}"
            if success
            else f"❌ Follow-up failed → {biz}: {error}"
        )

    if action == "PROGRESS":
        # Real-time scraper status messages — error_msg holds the text
        return error or f"[{channel}]"

    return f"ℹ️  [{action}] {biz}"


# ── Core routes ───────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    q     = get_queue()
    queue = q.stats if q else {"running": False}
    return {"status": "ok", "app": settings.app_name, "version": "3.0.0", "queue": queue}


@app.get("/api/stats")
async def dashboard_stats():
    """
    Returns full dashboard stats including:
    - lead counts (total, pending, sent, replied, skipped)
    - today's send counts per channel
    - reply_rate, estimated_revenue
    - engine_status: "idle" | "running" | "scheduled"
    - next_run: ISO timestamp of next scheduled job
    """
    stats = await get_dashboard_stats()

    camp  = get_campaign_state()
    sched = get_scheduler_status()

    if camp["running"]:
        engine_status = "running"
    elif sched["running"]:
        engine_status = "scheduled"
    else:
        engine_status = "idle"

    stats["engine_status"] = engine_status
    stats["next_run"]      = sched.get("next_run")
    return stats


@app.get("/api/stats/weekly")
async def weekly_stats():
    """7-day activity: leads found + email/WA sent per day."""
    return await get_weekly_activity()


@app.get("/api/logs")
async def recent_logs(limit: int = Query(20, ge=1, le=100)):
    return await get_recent_logs(limit)


@app.get("/api/logs/stream")
async def stream_logs(request: Request):
    """
    SSE endpoint — pushes new campaign_log entries every 2 s.
    Client receives: { message: string, timestamp: string }
    """

    async def event_gen():
        async with get_db() as conn:
            last_id: int = await conn.fetchval(
                "SELECT COALESCE(MAX(id), 0) FROM campaign_log"
            ) or 0

        try:
            while True:
                if await request.is_disconnected():
                    break

                async with get_db() as conn:
                    rows = await conn.fetch(
                        """SELECT cl.id,
                                  cl.channel,
                                  cl.action,
                                  cl.success,
                                  cl.error_msg,
                                  TO_CHAR(cl.timestamp, 'HH24:MI:SS') AS ts,
                                  COALESCE(l.business_name, 'Unknown') AS business_name,
                                  l.email,
                                  l.phone
                           FROM campaign_log cl
                           LEFT JOIN leads l ON l.id = cl.lead_id
                           WHERE cl.id > $1
                           ORDER BY cl.id ASC
                           LIMIT 50""",
                        last_id,
                    )

                for row in rows:
                    d       = dict(row)
                    last_id = d["id"]
                    payload = json.dumps({
                        "message":   _fmt_log(d),
                        "timestamp": d["ts"] or "",
                    })
                    yield f"data: {payload}\n\n"

                await asyncio.sleep(2)

        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":        "keep-alive",
        },
    )
