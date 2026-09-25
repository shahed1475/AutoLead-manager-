import asyncio
import json
import logging
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from . import auth, edition
from .config import get_settings
from .rate_limit import limiter
from .database import (
    init_db, close_db, get_db, get_all_settings,
    get_dashboard_stats, get_weekly_activity, get_recent_logs, get_results_report, get_revenue_report,
    get_avg_score,
)
from . import intelligence as intelligence_module
from .intelligence import intelligence_enabled
from .scheduler import start_scheduler, stop_scheduler, get_scheduler_status
from .routers import leads, campaigns, ai, scraper_router, settings_router, status, intelligence
from .routers import marketing as marketing_router
from .routers import pipeline as pipeline_router
from .routers import auth_router
from .routers import inbox as inbox_router
from .routers import followups as followups_router
from .routers import replies as replies_router
from .routers import discovery as discovery_router
from .routers import research_agent as research_agent_router
from .routers import automation as automation_router
from .routers import lead_search as lead_search_router
from .routers import lead_runs as lead_runs_router
from .routers import audit as audit_router
from .routers import portal as portal_router
from .routers import portal_admin as portal_admin_router
from .routers import whatsapp as whatsapp_router
from .routers import social as social_router
from .routers import email_campaigns as email_campaigns_router
from .routers import email_senders as email_senders_router
from .routers.campaigns import get_campaign_state
from .queue_worker import init_queue, get_queue

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
settings = get_settings()
logger   = logging.getLogger(__name__)


async def _reconcile_automation(queue) -> None:
    """A RUNNING automation_state after a restart means the previous slice
    worker died — re-queue it (state + per-item statuses resume from
    current_position). Module-level so it is testable."""
    from .automation.scheduler_hooks import resume_running_slice
    try:
        await resume_running_slice(queue)
    except Exception as exc:
        logger.warning("Startup automation reconcile failed: %s", exc)


# ── App lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    stored = await get_all_settings()
    hour   = int(stored.get("schedule_hour") or settings.schedule_hour)
    start_scheduler(hour)

    # Resume the sales-intelligence research backlog (profiles reset to PENDING
    # by the restart sweep in _run_migrations) — fire-and-forget so startup is
    # never blocked or failed by it. Complete no-op when the toggle is off.
    if intelligence_enabled(stored):
        async def _drain_intelligence_backlog() -> None:
            try:
                await intelligence_module.run_pending_research()
            except Exception as exc:
                logger.warning("Sales-intelligence backlog drain failed at startup: %s", exc)
        asyncio.create_task(_drain_intelligence_backlog())

    # Start parallel job queue
    n_workers = int(stored.get("queue_workers") or settings.queue_workers)
    queue = init_queue(n_workers=n_workers)
    await queue.start()

    # Reconcile background jobs left mid-run by a previous process:
    #   - research sessions get re-queued (resumable — saved leads + processed
    #     keys let the worker skip finished work)
    #   - Quick Search discovery runs are fast; a stuck one is just marked
    #     FAILED so the UI stops polling it forever.
    async def _reconcile_interrupted_jobs() -> None:
        from datetime import datetime, timezone
        from . import database as _db
        from .research_agent.session import reconcile_interrupted_sessions
        from .lead_runs.runner import reconcile_interrupted_runs
        try:
            n = await reconcile_interrupted_runs(queue)
            if n:
                logger.info("Startup: re-queued %d interrupted Find leads run(s)", n)
        except Exception as exc:
            logger.warning("Startup lead-run reconcile failed: %s", exc)
        try:
            n = await reconcile_interrupted_sessions(queue)
            if n:
                logger.info("Startup: re-queued %d interrupted research session(s)", n)
        except Exception as exc:
            logger.warning("Startup research-session reconcile failed: %s", exc)
        try:
            for run in await _db.list_interrupted_discovery_runs():
                await _db.update_discovery_run(run["id"], {
                    "status": "FAILED",
                    "error_message": "Interrupted by a server restart — start a new search.",
                    "finished_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                })
        except Exception as exc:
            logger.warning("Startup discovery-run reconcile failed: %s", exc)
        await _reconcile_automation(queue)
    asyncio.create_task(_reconcile_interrupted_jobs())
    loops = []
    if edition.is_client():
        # Workspaces have no n8n: pace WhatsApp campaigns and publish due social
        # posts / check the social inbox here (one step a minute).
        from .whatsapp.service import pacer_loop
        from .social.service import tick_loop as social_tick_loop
        loops = [asyncio.create_task(pacer_loop()), asyncio.create_task(social_tick_loop())]

    yield

    await queue.stop()
    for task in loops:
        task.cancel()
    stop_scheduler()
    await close_db()


# ── App factory ───────────────────────────────────────────────────────────────

if edition.is_client():
    # A client workspace may reach the internet, never private addresses.
    edition.install_egress_guard()

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

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

_authed = [Depends(auth.require_session)]

app.include_router(auth_router.router)    # public — issues/checks the session token itself
app.include_router(inbox_router.router,    dependencies=_authed)  # first — static /leads/score-* paths before /{lead_id}
app.include_router(followups_router.router, dependencies=_authed)
app.include_router(replies_router.router,  dependencies=_authed)
app.include_router(audit_router.router,    dependencies=_authed)  # before leads: static /leads/audit-batch before /{lead_id}
app.include_router(leads.router,           dependencies=_authed)
app.include_router(campaigns.router,       dependencies=_authed)
app.include_router(ai.router,              dependencies=_authed)
app.include_router(scraper_router.router,  dependencies=_authed)
app.include_router(settings_router.router, dependencies=_authed)
app.include_router(status.router,          dependencies=_authed)
app.include_router(intelligence.router,    dependencies=_authed)
app.include_router(marketing_router.router, dependencies=_authed)
app.include_router(pipeline_router.router,  dependencies=_authed)
app.include_router(discovery_router.router, dependencies=_authed)
app.include_router(research_agent_router.router, dependencies=_authed)
app.include_router(automation_router.router, dependencies=_authed)
app.include_router(lead_search_router.router, dependencies=_authed)
app.include_router(lead_runs_router.router, dependencies=_authed)
if not edition.is_client():
    # Owner only. A client workspace has no portal and no Clients admin.
    app.include_router(portal_admin_router.router, dependencies=_authed)   # owner's view of the client portal
    # Client portal API: public, but protected by its own client sessions.
    app.include_router(portal_router.router)
# Social media automation (both editions; workspaces connect with their own
# API keys only — no browser posting) and its public image/tick routes.
app.include_router(social_router.router, dependencies=_authed)
app.include_router(social_router.public)
# WhatsApp Campaigns (both editions; workspaces use their own Meta API) and
# Meta's signed webhook (public).
app.include_router(whatsapp_router.router, dependencies=_authed)
app.include_router(whatsapp_router.meta_hooks)
# Incoming WhatsApp events + the pacer (shared secret): the owner's n8n, or a
# client workspace's own engine.
app.include_router(whatsapp_router.hooks)
app.include_router(email_campaigns_router.router, dependencies=_authed)  # feature-flagged (email_campaigns_enabled, default OFF)
app.include_router(email_senders_router.router)  # per-route session/flag deps (Gmail OAuth callback must stay public)


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


@app.get("/api/stats", dependencies=_authed)
async def dashboard_stats():
    """
    Returns full dashboard stats including:
    - lead counts (total, pending, sent, replied, skipped)
    - today's send counts per channel
    - reply_rate, estimated_revenue
    - hot_leads_count, warm_leads_count, avg_score, total_replies
    - engine_status: "idle" | "running" | "scheduled"
    - next_run: ISO timestamp of next scheduled job
    """
    stats = await get_dashboard_stats()
    avg   = await get_avg_score()

    camp  = get_campaign_state()
    sched = get_scheduler_status()

    if camp["running"]:
        engine_status = "running"
    elif sched["running"]:
        engine_status = "scheduled"
    else:
        engine_status = "idle"

    stats["engine_status"]   = engine_status
    stats["next_run"]        = sched.get("next_run")
    stats["avg_score"]       = avg
    stats["total_replies"]   = stats.get("replied", 0)
    stats["hot_leads_count"] = stats.get("hot_leads", 0)
    stats["warm_leads_count"]= stats.get("warm_leads", 0)
    return stats


@app.get("/api/stats/results", dependencies=_authed)
async def results_stats(days: int = Query(7, ge=1, le=90)):
    """Results for the last `days` vs the period before: leads, messages,
    replies, interested, meetings, won. This workspace's own data only."""
    return await get_results_report(days)


@app.get("/api/stats/revenue", dependencies=_authed)
async def revenue_stats():
    """Won revenue by lead source + latest wins (recorded deal values only)."""
    return await get_revenue_report()


@app.get("/api/stats/weekly", dependencies=_authed)
async def weekly_stats():
    """7-day activity: leads found + email/WA sent per day."""
    return await get_weekly_activity()


@app.get("/api/logs", dependencies=_authed)
async def recent_logs(limit: int = Query(20, ge=1, le=100)):
    return await get_recent_logs(limit)


@app.get("/api/logs/stream", dependencies=_authed)
async def stream_logs(request: Request):
    """
    SSE endpoint — merges two log sources every 2 s:
      1. campaign_log DB table  (persisted FOUND/SEND/AI actions)
      2. log_stream SimpleQueue (real-time scrape progress + send events)
    Client receives: { message: string, timestamp: string }

    Auth note: native EventSource can't set custom headers, so the frontend
    authenticates this endpoint via ?token=<session_token> instead of the
    Authorization header — see auth.require_session().
    """
    from .log_stream import drain as _drain_queue
    from datetime import datetime, timezone as _tz

    async def event_gen():
        try:
            async with get_db() as conn:
                last_id: int = await conn.fetchval(
                    "SELECT COALESCE(MAX(id), 0) FROM campaign_log"
                ) or 0
        except Exception:
            last_id = 0
        last_id = last_id or 0

        tick = 0
        try:
            while True:
                if await request.is_disconnected():
                    break

                # ── Source 1: DB campaign_log ─────────────────────────────
                try:
                    async with get_db() as conn:
                        rows = await conn.fetch(
                            """SELECT cl.id,
                                      cl.channel,
                                      cl.action,
                                      cl.success,
                                      cl.error_msg,
                                      strftime('%H:%M:%S', cl.timestamp) AS ts,
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
                            "level":     "INFO" if d.get("success") else "ERROR",
                            "channel":   d.get("channel") or "SYSTEM",
                        })
                        yield f"data: {payload}\n\n"

                except Exception:
                    pass  # transient DB error — skip this tick

                # ── Source 2: in-memory log_stream (scrape progress) ──────
                # Forward the level/channel that log_stream.emit() already
                # recorded instead of dropping them — previously only
                # message+timestamp reached the client, so the frontend had
                # to guess severity by sniffing emoji in the message text.
                now_ts = datetime.now(_tz.utc).strftime("%H:%M:%S")
                for entry in _drain_queue():
                    payload = json.dumps({
                        "message":   entry.get("message", ""),
                        "timestamp": entry.get("timestamp", now_ts),
                        "level":     entry.get("level", "INFO"),
                        "channel":   entry.get("channel", "SYSTEM"),
                    })
                    yield f"data: {payload}\n\n"

                # keepalive every 15 s
                tick += 1
                if tick % 8 == 0:
                    yield ": keepalive\n\n"

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
