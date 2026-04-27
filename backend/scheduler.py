"""
scheduler.py — Full campaign runner + APScheduler wrappers.

Public API (async):
  run_campaign(niche, city, channel, daily_cap, log_queue, db, config) -> dict
  check_followups(log_queue, db, config) -> dict
  request_stop() -> None

Scheduler API (sync, called by FastAPI lifespan):
  start_scheduler(hour) -> AsyncIOScheduler
  stop_scheduler() -> None
  reschedule_job(hour) -> None
  get_scheduler_status() -> dict
  run_campaign_now() -> None        (async — triggers daily job manually)
"""

import asyncio
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import ai_brain
from . import database as db
from . import email_sender, scraper, whatsapp_sender
from .config import get_settings
from .log_stream import emit as _stream_emit
from .followup_engine import (
    process_followup_queue as _process_followups,
    schedule_followups_for_lead as _schedule_fu,
)
from .reply_detector import check_replies as _check_replies
from .scoring.lead_scorer import score_lead as _score_lead
from .enrichment.website_analyzer import analyze_website as _analyze_website
from .enrichment.ai_enricher import enrich_lead_with_ai as _enrich_lead

logger   = logging.getLogger(__name__)
settings = get_settings()

# ── Module-level state ────────────────────────────────────────────────────────
_scheduler:   Optional[AsyncIOScheduler] = None
_stop_flag:   bool = False          # set by request_stop(); checked inside run_campaign


# ── Internal helpers ──────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_company_dna(path: str) -> str:
    p = Path(path)
    return p.read_text(encoding="utf-8") if p.exists() else ""


async def _qlog(q: asyncio.Queue, msg: str, level: str = "INFO") -> None:
    """
    Deliver a log entry to three sinks simultaneously:
      1. The caller's asyncio.Queue (for SSE streaming in the router)
      2. The module-level log_stream queue (for the /api/logs/stream SSE endpoint)
      3. Python's standard logger (for server stdout / file logs)

    Never blocks — drops silently if the queue is full rather than stalling the campaign.
    """
    entry = {
        "message":   msg,
        "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        "level":     level.upper(),
    }
    try:
        q.put_nowait(entry)
    except asyncio.QueueFull:
        pass

    _stream_emit(level, "CAMPAIGN", msg)

    log_fn = {"ERROR": logger.error, "WARNING": logger.warning}.get(level.upper(), logger.info)
    log_fn(msg)


def _resolve_config(config: Dict[str, Any], stored: Dict[str, str]) -> Dict[str, Any]:
    """
    Build a resolved runtime config by merging three priority layers:
      1. caller-provided `config` dict  (highest)
      2. DB `app_settings` row values   (middle)
      3. .env / pydantic Settings        (lowest)

    All keys are cast to their final types here so the rest of the code
    can use them without repeated type-casting.
    """
    def _pick(caller_key: str, db_key: str, default: Any, cast=None) -> Any:
        raw = config.get(caller_key)
        if raw is None:
            raw = stored.get(db_key)
        if raw is None:
            raw = default
        return cast(raw) if (cast and raw is not None) else raw

    # auto_send: accept bool, "true"/"false" strings, 1/0
    _as_raw = config.get("auto_send")
    if _as_raw is None:
        _as_raw = stored.get("auto_send_enabled", "true")
    auto_send = str(_as_raw).lower() not in ("false", "0", "no")

    return {
        "auto_send":        auto_send,
        "send_delay_min":   _pick("send_delay_min",    "send_delay_min",      60,  int),
        "send_delay_max":   _pick("send_delay_max",    "send_delay_max",      120, int),
        "followup_days":    _pick("followup_days",     "followup_delay_days", 3,   int),
        "dedup_days":       _pick("dedup_days",        "dedup_days",          30,  int),
        "company_dna_path": _pick("company_dna_path",  "company_dna_path",    settings.company_dna_path),
    }


async def _was_recently_contacted(
    lead_data: Dict[str, Any],
    dedup_days: int,
) -> bool:
    """
    Return True if a lead matching this email/phone was already SENT to within
    the last `dedup_days` days.  Returns False for any other state (PENDING,
    REPLIED, SKIPPED, unknown) so we still process them.
    """
    existing = await db.find_duplicate_lead(
        lead_data.get("email"),
        lead_data.get("phone"),
    )
    if not existing:
        return False
    if existing.get("status") != "SENT":
        return False

    sent_at_raw = existing.get("sent_at")
    if not sent_at_raw:
        return False

    try:
        sent_at = datetime.fromisoformat(str(sent_at_raw).replace("Z", "+00:00"))
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - sent_at).days < dedup_days
    except (ValueError, TypeError):
        return False


async def _dispatch_send(
    lead:      Dict[str, Any],
    channel:   str,
    db_mod:    Any,
    log_queue: asyncio.Queue,
) -> Tuple[bool, str]:
    """
    Send a message to a lead via the requested channel(s).

    For BOTH: attempts EMAIL then WHATSAPP independently so a failure
    in one channel does not prevent the other.

    Returns (any_succeeded: bool, combined_error_msg: str).
    """
    channel = channel.upper()
    errors: List[str] = []
    sent_any = False

    # ── EMAIL ──────────────────────────────────────────────────────────────────
    if channel in ("EMAIL", "BOTH"):
        if lead.get("email"):
            try:
                await email_sender.send_email_lead(lead)
                await db_mod.log_campaign_action(lead["id"], "EMAIL", "SEND", True)
                sent_any = True
            except Exception as exc:
                err = f"Email failed: {exc}"
                await db_mod.log_campaign_action(lead["id"], "EMAIL", "SEND", False, str(exc))
                errors.append(err)
        else:
            errors.append("No email address — cannot send via EMAIL")

    # ── WHATSAPP ───────────────────────────────────────────────────────────────
    if channel in ("WHATSAPP", "BOTH"):
        if lead.get("phone"):
            try:
                await whatsapp_sender.send_whatsapp_lead(lead)
                await db_mod.log_campaign_action(lead["id"], "WHATSAPP", "SEND", True)
                sent_any = True
            except Exception as exc:
                err = f"WhatsApp failed: {exc}"
                await db_mod.log_campaign_action(lead["id"], "WHATSAPP", "SEND", False, str(exc))
                errors.append(err)
        else:
            errors.append("No phone number — cannot send via WHATSAPP")

    return sent_any, "; ".join(errors)


# ══ PUBLIC API ═════════════════════════════════════════════════════════════════


async def run_campaign(
    niche:     str,
    city:      str,
    channel:   str,
    daily_cap: int,
    log_queue: asyncio.Queue,
    db:        Any,           # the database module — dependency-injected
    config:    Dict[str, Any],
) -> Dict[str, Any]:
    """
    Full campaign pipeline: daily-cap check → scrape → enrich → AI → send → followups.

    Parameters
    ----------
    niche     : business type to search (e.g. "dentist", "restaurant")
    city      : target city/area (e.g. "New York", "Dubai")
    channel   : "EMAIL" | "WHATSAPP" | "BOTH"
    daily_cap : maximum outreach sends for this run
    log_queue : asyncio.Queue — each log line pushed here for real-time SSE
    db        : the database module (pass your `from . import database as db`)
    config    : runtime overrides; merged with DB settings (caller wins)

    Returns
    -------
    dict
        leads_scraped  : int   — total raw results from Maps
        leads_saved    : int   — new leads written to DB
        leads_sent     : int   — messages successfully delivered
        leads_skipped  : int   — dedup / no-contact-info skips
        errors         : list  — error strings (non-fatal; campaign continues)
    """
    global _stop_flag
    _stop_flag = False

    channel = channel.upper()

    # ── Load DB settings ───────────────────────────────────────────────────────
    stored = await db.get_all_settings()
    cfg    = _resolve_config(config, stored)

    await _qlog(log_queue,
        f"🚀 Campaign starting — {niche} in {city} | channel={channel} | cap={daily_cap} | auto_send={cfg['auto_send']}")

    # ── Load company DNA ───────────────────────────────────────────────────────
    company_dna = _load_company_dna(cfg["company_dna_path"])
    if not company_dna.strip():
        await _qlog(log_queue,
            "⚠️  company_dna.txt is empty — AI messages will be generic. "
            "Edit Settings → Company DNA to personalise outreach.", "WARNING")

    # ── Step 1: Daily cap check ────────────────────────────────────────────────
    stats      = await db.get_dashboard_stats()
    sent_today = stats.get("sent_today", 0)

    if sent_today >= daily_cap:
        msg = (f"⚠️  Daily cap of {daily_cap} already reached "
               f"({sent_today} sent today) — campaign aborted")
        await _qlog(log_queue, msg, "WARNING")
        return {
            "leads_scraped": 0, "leads_saved": 0,
            "leads_sent": 0,    "leads_skipped": 0,
            "errors": [msg],
        }

    budget = daily_cap - sent_today
    await _qlog(log_queue,
        f"📊 Budget: {budget} sends remaining today (cap={daily_cap}, sent={sent_today})")

    # ── Step 2: Scrape Google Maps ─────────────────────────────────────────────
    loop = asyncio.get_running_loop()

    def _scrape_log(msg: str) -> None:
        """Sync → async bridge: safely schedule a queue.put from a thread."""
        entry = {
            "message":   msg,
            "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "level":     "INFO",
        }
        try:
            loop.call_soon_threadsafe(log_queue.put_nowait, entry)
        except Exception:
            pass
        _stream_emit("INFO", "SCRAPE", msg)

    await _qlog(log_queue,
        f"🔍 Searching Google Maps: '{niche}' in '{city}' (max {budget} results)...")

    try:
        raw_leads: List[Dict[str, Any]] = await scraper.scrape_google_maps(
            niche        = niche,
            city         = city,
            max_results  = budget,
            log_callback = _scrape_log,
        )
    except Exception as exc:
        err = f"Scraper error: {type(exc).__name__}: {exc}"
        await _qlog(log_queue, f"❌ {err}", "ERROR")
        logger.error(err, exc_info=True)
        return {
            "leads_scraped": 0, "leads_saved": 0,
            "leads_sent": 0,    "leads_skipped": 0,
            "errors": [err],
        }

    await _qlog(log_queue, f"📋 Scraped {len(raw_leads)} raw leads — beginning enrichment loop")

    # ── Step 3: Process each scraped lead ──────────────────────────────────────
    results: Dict[str, Any] = {
        "leads_scraped": len(raw_leads),
        "leads_saved":   0,
        "leads_sent":    0,
        "leads_skipped": 0,
        "errors":        [],
    }

    for idx, raw_lead in enumerate(raw_leads, start=1):

        # ── Stop-signal check ──────────────────────────────────────────────────
        if _stop_flag:
            await _qlog(log_queue,
                f"🛑 Stop signal received after {idx - 1} leads — halting campaign",
                "WARNING")
            break

        biz = raw_lead.get("business_name", f"Lead {idx}")
        await _qlog(log_queue, f"── [{idx}/{len(raw_leads)}] {biz}")

        # ── 3a. Deduplication: skip if SENT within dedup window ────────────────
        if await _was_recently_contacted(raw_lead, cfg["dedup_days"]):
            await _qlog(log_queue,
                f"   ⏭️  Skip — already contacted within {cfg['dedup_days']} days")
            results["leads_skipped"] += 1
            continue

        # ── 3b. Email enrichment (second attempt if scraper found nothing) ─────
        # The scraper already tries to discover emails; this is a second-chance
        # pass for leads whose websites returned 200 only some of the time.
        if not raw_lead.get("email") and raw_lead.get("website"):
            await _qlog(log_queue, f"   📧 Probing website for email: {raw_lead['website']}")
            try:
                found = await scraper.find_email_from_website(raw_lead["website"])
                if found:
                    raw_lead["email"] = found
                    await _qlog(log_queue, f"   ✉️  Found: {found}")
                else:
                    await _qlog(log_queue, "   — No email discovered on website")
            except Exception as exc:
                await _qlog(log_queue, f"   ⚠️  Email probe error: {exc}", "WARNING")

        # ── Channel feasibility: skip if we have no way to reach them ──────────
        can_email    = bool(raw_lead.get("email"))
        can_whatsapp = bool(raw_lead.get("phone"))
        reachable    = (
            (channel == "EMAIL"    and can_email)    or
            (channel == "WHATSAPP" and can_whatsapp) or
            (channel == "BOTH"     and (can_email or can_whatsapp))
        )
        if not reachable:
            await _qlog(log_queue,
                f"   ⏭️  Skip — no contact info suitable for channel={channel}")
            results["leads_skipped"] += 1
            continue

        # ── 3c. Persist lead to DB ─────────────────────────────────────────────
        try:
            lead_id, is_new = await db.create_lead_deduped({
                "business_name": raw_lead.get("business_name"),
                "phone":         raw_lead.get("phone"),
                "email":         raw_lead.get("email"),
                "website":       raw_lead.get("website"),
                "niche":         raw_lead.get("niche"),
                "city":          raw_lead.get("city"),
                "channel":       channel,
            })
            if is_new:
                await db.log_campaign_action(lead_id, "SCRAPE", "FOUND", True)
                results["leads_saved"] += 1
                await _qlog(log_queue, f"   💾 Saved new lead (id={lead_id})")
            else:
                await _qlog(log_queue, f"   ♻️  Existing lead (id={lead_id}) — refreshing")
        except Exception as exc:
            err = f"DB save failed for '{biz}': {exc}"
            await _qlog(log_queue, f"   ❌ {err}", "ERROR")
            logger.error(err, exc_info=True)
            results["errors"].append(err)
            continue

        # ── 3d. Fetch canonical lead row (includes DB-assigned id + timestamps) ─
        lead = await db.get_lead_by_id(lead_id)
        if not lead:
            await _qlog(log_queue, f"   ❌ Could not re-fetch lead {lead_id} from DB", "ERROR")
            continue
        lead = dict(lead)

        # ── 3e. AI message generation ──────────────────────────────────────────
        if not (lead.get("ai_email_subject") or lead.get("ai_whatsapp_msg")):
            await _qlog(log_queue, f"   🤖 Generating personalised messages for {biz}...")
            try:
                msgs = await ai_brain.generate_all_messages(lead)
                ai_update = {
                    "ai_whatsapp_msg":  msgs.get("first_message",  ""),
                    "ai_email_subject": msgs.get("email_subject",  ""),
                    "ai_email_body":    msgs.get("email_body",     ""),
                    "ai_followup_msg":  msgs.get("follow_up_1",   ""),
                    "ai_follow_up_1":   msgs.get("follow_up_1",   ""),
                    "ai_follow_up_2":   msgs.get("follow_up_2",   ""),
                    "ai_follow_up_3":   msgs.get("follow_up_3",   ""),
                }
                await db.update_lead(lead_id, ai_update)
                await db.log_campaign_action(lead_id, "AI", "GENERATE", True)
                lead.update(ai_update)
                await _qlog(log_queue, f"   ✨ Messages ready for {biz}")
            except Exception as exc:
                err = f"AI generation failed for '{biz}': {exc}"
                await _qlog(log_queue, f"   ❌ {err}", "ERROR")
                await db.log_campaign_action(lead_id, "AI", "GENERATE", False, str(exc))
                results["errors"].append(err)
                if cfg["auto_send"]:
                    continue

        # ── 3f. Send outreach ──────────────────────────────────────────────────
        if cfg["auto_send"]:
            await _qlog(log_queue, f"   📤 Sending via {channel}...")
            sent_ok, send_err = await _dispatch_send(lead, channel, db, log_queue)

            if sent_ok:
                now_dt = datetime.now(timezone.utc)
                await db.update_lead(lead_id, {"status": "SENT", "sent_at": now_dt.isoformat()})
                await _schedule_fu(lead_id, now_dt, lead)
                results["leads_sent"] += 1
                await _qlog(log_queue, f"   ✅ Delivered → {biz}")

                # ── 3g. Inter-send delay (skip after the last lead) ────────────
                if idx < len(raw_leads) and not _stop_flag:
                    delay = random.uniform(cfg["send_delay_min"], cfg["send_delay_max"])
                    await _qlog(log_queue,
                        f"   ⏳ Pausing {delay:.0f}s before next send "
                        f"(range {cfg['send_delay_min']}–{cfg['send_delay_max']}s)...")
                    await asyncio.sleep(delay)
            else:
                await _qlog(log_queue, f"   ❌ Send failed — {send_err}", "ERROR")
                results["errors"].append(f"{biz}: {send_err}")
        else:
            await _qlog(log_queue,
                f"   💡 auto_send=False — lead {lead_id} saved with AI messages (not sent)")

    # ── Step 4: Follow-up pass ─────────────────────────────────────────────────
    await _qlog(log_queue, "🔄 Running follow-up pass...")
    fu_results = await check_followups(log_queue, db, config)

    # ── Summary ────────────────────────────────────────────────────────────────
    await _qlog(log_queue,
        f"🏁 Done — "
        f"scraped={results['leads_scraped']} | "
        f"saved={results['leads_saved']} | "
        f"sent={results['leads_sent']} | "
        f"skipped={results['leads_skipped']} | "
        f"followups_sent={fu_results.get('sent', 0)} | "
        f"errors={len(results['errors'])}")

    return results


async def check_followups(
    log_queue: asyncio.Queue,
    db:        Any,
    config:    Dict[str, Any],
) -> Dict[str, Any]:
    """
    3-stage automated follow-up sequence.

    Stage 1 → sent 3 days after initial outreach   (ai_follow_up_1)
    Stage 2 → sent 7 days after Stage 1            (ai_follow_up_2)
    Stage 3 → sent 7 days after Stage 2            (ai_follow_up_3)

    A lead is skipped if it becomes REPLIED or SKIPPED at any point.
    Each stage generates the message on-demand if not already stored.
    """
    stored      = await db.get_all_settings()
    dna_path    = (config.get("company_dna_path")
                   or stored.get("company_dna_path")
                   or settings.company_dna_path)
    company_dna = _load_company_dna(dna_path)

    results: Dict[str, Any] = {"checked": 0, "sent": 0, "skipped": 0, "errors": []}

    # ── Stage definitions ──────────────────────────────────────────────────────
    STAGES = [
        # (stage_num, msg_field, sent_field, label, action_tag)
        (1, "ai_follow_up_1", "follow_up_1_sent_at", "Follow-up #1 (Day 3)",  "FOLLOWUP_1"),
        (2, "ai_follow_up_2", "follow_up_2_sent_at", "Follow-up #2 (Day 10)", "FOLLOWUP_2"),
        (3, "ai_follow_up_3", "follow_up_3_sent_at", "Follow-up #3 (Day 17)", "FOLLOWUP_3"),
    ]

    for stage_num, msg_field, sent_field, label, action_tag in STAGES:
        due = await db.get_leads_due_for_stage(stage_num)
        if not due:
            continue

        await _qlog(log_queue, f"📬 {label}: {len(due)} leads due")
        results["checked"] += len(due)

        for lead_row in due:
            lead    = dict(lead_row)
            lead_id = lead["id"]
            biz     = lead.get("business_name", f"Lead {lead_id}")
            channel = (lead.get("channel") or "EMAIL").upper()
            sent_via: List[str] = []

            # ── Generate this follow-up if not already stored ──────────────────
            if not lead.get(msg_field):
                await _qlog(log_queue, f"   🤖 Generating {label} for {biz}…")
                try:
                    msgs = await ai_brain.generate_all_messages(lead)
                    fu_update = {
                        "ai_followup_msg": msgs.get("follow_up_1", ""),
                        "ai_follow_up_1":  msgs.get("follow_up_1", ""),
                        "ai_follow_up_2":  msgs.get("follow_up_2", ""),
                        "ai_follow_up_3":  msgs.get("follow_up_3", ""),
                    }
                    await db.update_lead(lead_id, fu_update)
                    await db.log_campaign_action(lead_id, "AI", f"{action_tag}_GEN", True)
                    lead.update(fu_update)
                    await _qlog(log_queue, f"   ✨ {label} message ready for {biz}")
                except Exception as exc:
                    err = f"{label} AI gen failed for '{biz}': {exc}"
                    await _qlog(log_queue, f"   ❌ {err}", "ERROR")
                    await db.log_campaign_action(lead_id, "AI", f"{action_tag}_GEN", False, str(exc))
                    results["errors"].append(err)
                    continue

            msg_text = (lead.get(msg_field) or "").strip()
            if not msg_text:
                await _qlog(log_queue, f"   ⚠️  Empty {label} for {biz} — skipping", "WARNING")
                results["skipped"] += 1
                continue

            # ── Send via lead's original channel ───────────────────────────────
            # Temporarily override the followup message field so senders pick it up
            send_lead = {**lead, "ai_followup_msg": msg_text}

            if channel in ("EMAIL", "BOTH") and lead.get("email"):
                try:
                    await email_sender.send_followup_email(send_lead)
                    await db.log_campaign_action(lead_id, "EMAIL", action_tag, True)
                    sent_via.append("EMAIL")
                    await _qlog(log_queue, f"   📧 {label} email sent → {biz}")
                except Exception as exc:
                    err = f"{label} email failed for '{biz}': {exc}"
                    await _qlog(log_queue, f"   ❌ {err}", "ERROR")
                    await db.log_campaign_action(lead_id, "EMAIL", action_tag, False, str(exc))
                    results["errors"].append(err)

            if channel in ("WHATSAPP", "BOTH") and lead.get("phone"):
                try:
                    await whatsapp_sender.send_followup_whatsapp(send_lead)
                    await db.log_campaign_action(lead_id, "WHATSAPP", action_tag, True)
                    sent_via.append("WHATSAPP")
                    await _qlog(log_queue, f"   💬 {label} WhatsApp sent → {biz}")
                except Exception as exc:
                    err = f"{label} WhatsApp failed for '{biz}': {exc}"
                    await _qlog(log_queue, f"   ❌ {err}", "ERROR")
                    await db.log_campaign_action(lead_id, "WHATSAPP", action_tag, False, str(exc))
                    results["errors"].append(err)

            if sent_via:
                # Mark this stage as sent + keep legacy followup_sent_at updated
                await db.update_lead(lead_id, {
                    sent_field:         _now_iso(),
                    "followup_sent_at": _now_iso(),
                })
                results["sent"] += 1
            else:
                await _qlog(log_queue, f"   ⏭️  Skip {biz} — no contact info for channel={channel}")
                results["skipped"] += 1

    return results


async def run_full_pipeline(
    niche:         str,
    city:          str,
    channel:       str,
    daily_cap:     int,
    log_queue:     asyncio.Queue,
    db_mod:        Any,
    config:        Dict[str, Any],
    sources:       Optional[List[str]]     = None,
    country:       Optional[str]           = None,
    hot_warm_only: bool                    = True,
    source_caps:   Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """
    8-step full pipeline orchestrator for multi-source campaigns.

    Step 1  Bulk scrape from all configured sources
    Step 2  Validate + deduplicate → save new leads to DB
    Step 3  Enrich in parallel (max 5 concurrent, asyncio.Semaphore)
    Step 4  Score each enriched lead
    Step 5  Filter HOT+WARM (if hot_warm_only) then apply daily_cap
    Step 6  Generate personalised messages via generate_messages_v2
    Step 7  Send outreach with inter-send delay (if auto_send)
    Step 8  Schedule follow-up sequence for each sent lead
    """
    global _stop_flag
    _stop_flag = False
    channel = channel.upper()
    if sources is None:
        sources = ["GOOGLE_MAPS"]

    stored = await db_mod.get_all_settings()
    cfg    = _resolve_config(config, stored)

    await _qlog(log_queue,
        f"🚀 Full pipeline — {niche} in {city} | sources={sources} | "
        f"channel={channel} | cap={daily_cap} | hot_warm_only={hot_warm_only}")

    company_dna = _load_company_dna(cfg["company_dna_path"])
    if not company_dna.strip():
        await _qlog(log_queue,
            "⚠️  company_dna.txt is empty — messages will be generic", "WARNING")

    results: Dict[str, Any] = {
        "leads_scraped":  0,
        "leads_saved":    0,
        "leads_enriched": 0,
        "leads_scored":   0,
        "leads_filtered": 0,
        "leads_sent":     0,
        "errors":         [],
    }

    # ── Step 1: Bulk scrape ────────────────────────────────────────────────────
    await _qlog(log_queue, f"🕷️  Step 1/8: Scraping {', '.join(sources)} …")

    loop = asyncio.get_running_loop()

    def _scrape_log(msg: str) -> None:
        entry = {
            "message":   msg,
            "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "level":     "INFO",
        }
        try:
            loop.call_soon_threadsafe(log_queue.put_nowait, entry)
        except Exception:
            pass
        _stream_emit("INFO", "SCRAPE", msg)

    try:
        raw_leads: List[Dict[str, Any]] = await scraper.scrape_multi_source(
            sources     = sources,
            niche       = niche,
            city        = city,
            max_results = daily_cap,
            headless    = bool(config.get("headless", False)),
            source_caps = source_caps,
        )
    except Exception as exc:
        err = f"Scraper error: {exc}"
        await _qlog(log_queue, f"❌ {err}", "ERROR")
        results["errors"].append(err)
        return results

    results["leads_scraped"] = len(raw_leads)
    await _qlog(log_queue, f"📋 Step 1 done — scraped {len(raw_leads)} raw results")

    if not raw_leads:
        await _qlog(log_queue, "⚠️  No results from scraper — stopping", "WARNING")
        return results

    # ── Step 2: Validate + deduplicate ────────────────────────────────────────
    await _qlog(log_queue, "🔍 Step 2/8: Deduplication …")
    new_lead_ids: List[int] = []

    for raw in raw_leads:
        if not raw.get("business_name"):
            continue
        try:
            lead_id, is_new = await db_mod.create_lead_deduped({
                "business_name": raw.get("business_name"),
                "phone":         raw.get("phone"),
                "email":         raw.get("email"),
                "website":       raw.get("website"),
                "niche":         raw.get("niche") or niche,
                "city":          raw.get("city") or city,
                "country":       raw.get("country") or country,
                "channel":       channel,
            })
            if is_new:
                new_lead_ids.append(lead_id)
                await db_mod.log_campaign_action(lead_id, "SCRAPE", "FOUND", True)
                results["leads_saved"] += 1
        except Exception as exc:
            biz = raw.get("business_name", "unknown")
            results["errors"].append(f"Dedup error for '{biz}': {exc}")

    await _qlog(log_queue, f"✅ Step 2 done — {results['leads_saved']} new leads saved")

    if not new_lead_ids:
        await _qlog(log_queue, "⚠️  All scraped leads are duplicates — nothing to process")
        return results

    # ── Step 3: Parallel enrichment ────────────────────────────────────────────
    await _qlog(log_queue, f"🔬 Step 3/8: Enriching {len(new_lead_ids)} leads (≤5 parallel) …")
    sem = asyncio.Semaphore(5)

    async def _enrich_one(lid: int) -> None:
        async with sem:
            if _stop_flag:
                return
            lead = await db_mod.get_lead_by_id(lid)
            if not lead or not lead.get("website"):
                return
            lead = dict(lead)
            try:
                site_data  = await _analyze_website(lead["website"],
                                                    timeout=settings.enrichment_timeout)
                await _enrich_lead(lead, site_data, company_dna)
                await db_mod.log_campaign_action(lid, "AI", "ENRICH", True)
                results["leads_enriched"] += 1
            except Exception as exc:
                await db_mod.log_campaign_action(lid, "AI", "ENRICH", False, str(exc))
                results["errors"].append(f"Enrichment failed for lead {lid}: {exc}")

    await asyncio.gather(*[_enrich_one(lid) for lid in new_lead_ids])
    await _qlog(log_queue, f"✅ Step 3 done — enriched {results['leads_enriched']} leads")

    # ── Step 4: Score all leads ────────────────────────────────────────────────
    await _qlog(log_queue, f"📊 Step 4/8: Scoring {len(new_lead_ids)} leads …")

    for lid in new_lead_ids:
        if _stop_flag:
            break
        try:
            lead = await db_mod.get_lead_by_id(lid)
            if not lead:
                continue
            await _score_lead(dict(lead))
            results["leads_scored"] += 1
        except Exception as exc:
            results["errors"].append(f"Scoring failed for lead {lid}: {exc}")

    await _qlog(log_queue, f"✅ Step 4 done — scored {results['leads_scored']} leads")

    # ── Step 5: Filter HOT+WARM + apply daily cap ──────────────────────────────
    await _qlog(log_queue,
        f"🎯 Step 5/8: Filtering "
        f"({'HOT+WARM only' if hot_warm_only else 'all leads'}) …")

    candidate_ids: List[int] = []
    for lid in new_lead_ids:
        if _stop_flag:
            break
        lead = await db_mod.get_lead_by_id(lid)
        if not lead:
            continue
        lead = dict(lead)

        if hot_warm_only:
            label = (lead.get("score_label") or "COLD").upper()
            score = lead.get("score") or 0
            if score > 0 and label == "COLD":
                continue

        candidate_ids.append(lid)

    # Cap by remaining daily budget
    stats_now  = await db_mod.get_dashboard_stats()
    sent_today = stats_now.get("sent_today", 0)
    budget     = max(0, daily_cap - sent_today)
    candidate_ids = candidate_ids[:budget]

    results["leads_filtered"] = len(candidate_ids)
    await _qlog(log_queue,
        f"✅ Step 5 done — {len(candidate_ids)} leads pass filter (budget={budget})")

    if not candidate_ids:
        await _qlog(log_queue, "⚠️  No leads pass filter — pipeline complete")
        return results

    # ── Step 6: Generate personalised messages ─────────────────────────────────
    await _qlog(log_queue, f"✍️  Step 6/8: Generating messages for {len(candidate_ids)} leads …")

    for lid in candidate_ids:
        if _stop_flag:
            break
        lead = await db_mod.get_lead_by_id(lid)
        if not lead:
            continue
        lead = dict(lead)

        # Skip if messages already exist (e.g. re-run scenario)
        if lead.get("ai_email_subject") or lead.get("ai_whatsapp_msg"):
            continue

        try:
            enriched = await db_mod.get_enriched_data(lid) or {}
            scores   = await db_mod.get_score(lid) or {}
            # generate_messages_v2 persists to DB internally — no manual update needed
            await ai_brain.generate_messages_v2(lead, enriched, scores, company_dna)
            await db_mod.log_campaign_action(lid, "AI", "GENERATE", True)
        except Exception as exc:
            err = f"Message gen failed for lead {lid}: {exc}"
            await _qlog(log_queue, f"   ❌ {err}", "ERROR")
            await db_mod.log_campaign_action(lid, "AI", "GENERATE", False, str(exc))
            results["errors"].append(err)

    await _qlog(log_queue, "✅ Step 6 done — messages generated")

    # ── Step 7: Send outreach ──────────────────────────────────────────────────
    if not cfg["auto_send"]:
        await _qlog(log_queue,
            f"💡 Step 7/8: auto_send=False — {len(candidate_ids)} leads saved with messages")
        return results

    await _qlog(log_queue,
        f"📤 Step 7/8: Sending {len(candidate_ids)} leads via {channel} …")

    for idx, lid in enumerate(candidate_ids, 1):
        if _stop_flag:
            await _qlog(log_queue, "🛑 Stop signal received — halting send phase", "WARNING")
            break

        lead = await db_mod.get_lead_by_id(lid)
        if not lead:
            continue
        lead = dict(lead)
        biz  = lead.get("business_name", f"Lead {lid}")

        sent_ok, send_err = await _dispatch_send(lead, channel, db_mod, log_queue)

        if sent_ok:
            now_dt = datetime.now(timezone.utc)
            await db_mod.update_lead(lid, {"status": "SENT", "sent_at": now_dt.isoformat()})
            results["leads_sent"] += 1
            await _qlog(log_queue, f"   ✅ [{idx}/{len(candidate_ids)}] Sent → {biz}")

            # ── Step 8: Schedule follow-ups ────────────────────────────────────
            try:
                await _schedule_fu(lid, now_dt, lead)
            except Exception as exc:
                logger.warning("follow-up scheduling failed for lead %d: %s", lid, exc)

            if idx < len(candidate_ids) and not _stop_flag:
                delay = random.uniform(cfg["send_delay_min"], cfg["send_delay_max"])
                await _qlog(log_queue, f"   ⏳ Pausing {delay:.0f}s …")
                await asyncio.sleep(delay)
        else:
            await _qlog(log_queue, f"   ❌ [{idx}/{len(candidate_ids)}] {biz}: {send_err}", "ERROR")
            results["errors"].append(f"{biz}: {send_err}")

    await _qlog(log_queue,
        f"🏁 Full pipeline complete — "
        f"scraped={results['leads_scraped']} | "
        f"saved={results['leads_saved']} | "
        f"enriched={results['leads_enriched']} | "
        f"sent={results['leads_sent']} | "
        f"errors={len(results['errors'])}")

    return results


def request_stop() -> None:
    """
    Signal any active run_campaign() call to stop after its current lead.
    Thread-safe (just sets a bool — checked at each loop iteration).
    """
    global _stop_flag
    _stop_flag = True
    logger.info("Stop signal sent to active campaign runner")


# ── APScheduler — internal job ────────────────────────────────────────────────


async def _run_pending_only(stored: Dict[str, str], log_queue: asyncio.Queue) -> None:
    """
    Fallback path used when no campaign niche/city is configured in DB settings.
    Sends AI-generated messages to existing PENDING leads and runs the follow-up pass.
    """
    email_limit = int(stored.get("daily_email_limit") or settings.daily_email_limit)
    wa_limit    = int(stored.get("daily_whatsapp_limit") or settings.daily_whatsapp_limit)

    stats        = await db.get_dashboard_stats()
    email_budget = email_limit - stats.get("email_sent_today", 0)
    wa_budget    = wa_limit    - stats.get("whatsapp_sent_today", 0)

    if email_budget <= 0 and wa_budget <= 0:
        logger.info("Daily channel limits already reached — skipping pending outreach")
        return

    page_size = max(email_budget + wa_budget, 1)
    pending   = await db.get_leads(status="PENDING", page_size=page_size)
    leads     = pending.get("items", [])
    logger.info(f"Processing {len(leads)} PENDING leads")

    company_dna = _load_company_dna(settings.company_dna_path)

    for lead_row in leads:
        lead    = dict(lead_row)
        channel = (lead.get("channel") or "EMAIL").upper()

        # ── AI generation if messages are missing ──────────────────────────────
        if not (lead.get("ai_email_subject") or lead.get("ai_whatsapp_msg")):
            try:
                msgs = await ai_brain.generate_all_messages(lead)
                ai_update = {
                    "ai_whatsapp_msg":  msgs.get("first_message",  ""),
                    "ai_email_subject": msgs.get("email_subject",  ""),
                    "ai_email_body":    msgs.get("email_body",     ""),
                    "ai_followup_msg":  msgs.get("follow_up_1",   ""),
                    "ai_follow_up_1":   msgs.get("follow_up_1",   ""),
                    "ai_follow_up_2":   msgs.get("follow_up_2",   ""),
                    "ai_follow_up_3":   msgs.get("follow_up_3",   ""),
                }
                await db.update_lead(lead["id"], ai_update)
                await db.log_campaign_action(lead["id"], "AI", "GENERATE", True)
                lead.update(ai_update)
            except Exception as exc:
                logger.error(f"AI gen failed for lead {lead['id']}: {exc}")
                await db.log_campaign_action(lead["id"], "AI", "GENERATE", False, str(exc))

        # ── EMAIL ──────────────────────────────────────────────────────────────
        if channel in ("EMAIL", "BOTH") and email_budget > 0 and lead.get("email"):
            try:
                await email_sender.send_email_lead(lead)
                now_dt = datetime.now(timezone.utc)
                await db.update_lead(lead["id"], {"status": "SENT", "sent_at": now_dt.isoformat()})
                await _schedule_fu(lead["id"], now_dt, lead)
                await db.log_campaign_action(lead["id"], "EMAIL", "SEND", True)
                email_budget -= 1
            except Exception as exc:
                logger.error(f"Email failed for lead {lead['id']}: {exc}")
                await db.log_campaign_action(lead["id"], "EMAIL", "SEND", False, str(exc))

        # ── WHATSAPP (separate if so BOTH leads hit both paths) ────────────────
        if channel in ("WHATSAPP", "BOTH") and wa_budget > 0 and lead.get("phone"):
            try:
                await whatsapp_sender.send_whatsapp_lead(lead)
                current = await db.get_lead_by_id(lead["id"])
                if current and current.get("status") != "SENT":
                    now_dt = datetime.now(timezone.utc)
                    await db.update_lead(lead["id"], {"status": "SENT", "sent_at": now_dt.isoformat()})
                    await _schedule_fu(lead["id"], now_dt, lead)
                await db.log_campaign_action(lead["id"], "WHATSAPP", "SEND", True)
                wa_budget -= 1
            except Exception as exc:
                logger.error(f"WhatsApp failed for lead {lead['id']}: {exc}")
                await db.log_campaign_action(lead["id"], "WHATSAPP", "SEND", False, str(exc))

    # ── Follow-up pass ─────────────────────────────────────────────────────────
    await check_followups(log_queue, db, {})


async def _followup_job() -> None:
    """APScheduler entry point — runs the follow-up engine every 6 hours."""
    logger.info("▶ Follow-up engine triggered by scheduler")
    try:
        stored = await db.get_all_settings()
        config = {
            "ollama_base_url": stored.get("ollama_base_url") or settings.ollama_base_url,
            "ollama_model":    stored.get("ollama_model")    or settings.ollama_model,
        }
        result = await _process_followups(config)
        if result.get("sent", 0) > 0 or result.get("cancelled", 0) > 0:
            logger.info("Follow-up engine: %s", result)
    except Exception as exc:
        logger.error("Follow-up engine job failed: %s", exc, exc_info=True)
    logger.info("▶ Follow-up engine job complete")


async def _daily_campaign_job() -> None:
    """
    APScheduler entry point — reads all runtime config from DB and executes
    either the full scrape-and-send pipeline (if niche+city are set) or the
    legacy pending-leads-only path.
    """
    logger.info("▶ Daily campaign job triggered by scheduler")
    stored = await db.get_all_settings()

    if stored.get("auto_send_enabled", "true").lower() == "false":
        logger.info("Auto-send disabled in Settings — daily job skipped")
        return

    niche     = (stored.get("campaign_niche")   or "").strip()
    city      = (stored.get("campaign_city")    or "").strip()
    channel   = (stored.get("campaign_channel") or "EMAIL").upper()
    daily_cap = int(
        stored.get("daily_cap")
        or stored.get("daily_email_limit")
        or settings.daily_email_limit
    )

    # Dedicated queue for this run — SSE consumers read via log_stream module
    log_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

    if niche and city:
        logger.info(f"Full campaign: '{niche}' in '{city}' | {channel} | cap={daily_cap}")
        try:
            result = await run_campaign(niche, city, channel, daily_cap, log_queue, db, {})
            logger.info(f"Campaign finished: {result}")
        except Exception as exc:
            logger.error(f"Campaign job unhandled error: {exc}", exc_info=True)
    else:
        logger.info("No campaign niche/city in settings — processing PENDING leads only")
        try:
            await _run_pending_only(stored, log_queue)
        except Exception as exc:
            logger.error(f"Pending-only job error: {exc}", exc_info=True)

    # ── Automatic reply detection ──────────────────────────────────────────────
    try:
        reply_result = await _check_replies(since_days=1)
        if reply_result.get("new_replies", 0) > 0:
            logger.info(
                "Reply detection: %d new, %d matched leads",
                reply_result["new_replies"], reply_result["matched"]
            )
    except Exception as exc:
        logger.warning("Reply detection skipped: %s", exc)

    # ── Auto-score any un-scored leads ─────────────────────────────────────────
    try:
        unscored = await db.get_leads_without_score(limit=200)
        if unscored:
            for lead in unscored:
                await _score_lead(lead)
            logger.info("Auto-scored %d leads", len(unscored))
    except Exception as exc:
        logger.warning("Auto-scoring skipped: %s", exc)

    logger.info("▶ Daily campaign job complete")


# ── APScheduler lifecycle — called by main.py lifespan ────────────────────────


def start_scheduler(hour: Optional[int] = None) -> AsyncIOScheduler:
    """
    Create and start the APScheduler instance with a daily cron job.
    `hour` overrides settings.schedule_hour — use this to apply DB-stored value.
    """
    global _scheduler
    h          = hour if hour is not None else settings.schedule_hour
    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        _daily_campaign_job,
        CronTrigger(hour=h, minute=0),
        id                 = "daily_campaign",
        replace_existing   = True,
        misfire_grace_time = 3600,
    )
    _scheduler.add_job(
        _followup_job,
        IntervalTrigger(hours=6),
        id                 = "followup_sequence",
        replace_existing   = True,
        misfire_grace_time = 3600,
    )
    _scheduler.start()
    logger.info("Scheduler started — daily campaign at %02d:00 UTC, follow-ups every 6 h", h)
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


def reschedule_job(hour: int) -> None:
    """Move the daily job to a new UTC hour without restarting the server."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.reschedule_job(
            "daily_campaign",
            trigger = CronTrigger(hour=max(0, min(23, hour)), minute=0),
        )
        logger.info(f"Daily campaign rescheduled → {hour:02d}:00 UTC")


def get_scheduler_status() -> Dict[str, Any]:
    global _scheduler
    if _scheduler is None or not _scheduler.running:
        return {"running": False, "next_run": None}
    job      = _scheduler.get_job("daily_campaign")
    next_run = job.next_run_time.isoformat() if (job and job.next_run_time) else None
    return {"running": True, "next_run": next_run}


async def run_campaign_now() -> None:
    """Manually fire the daily campaign job — used by /api/engine/run-now."""
    await _daily_campaign_job()
