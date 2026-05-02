import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from typing import Dict, List, Optional
from .. import database as db
from .. import email_sender, whatsapp_sender, ai_brain
from .. import scrapers
from ..followup_engine import schedule_followups_for_lead as _schedule_fu
from ..log_stream import emit as _ls_emit
from ..models import CampaignSendRequest, CampaignStartRequest

# Update DB after every lead so real-time counts are always accurate
_PROGRESS_EVERY = 1

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/campaign", tags=["campaign"])

# ── In-memory campaign run state ──────────────────────────────────────────────
_run_state: dict = {
    "running":          False,
    "stop_requested":   False,
    "niche":            None,
    "city":             None,
    "country":          None,
    "channel":          None,
    "daily_cap":        20,
    "sources":          ["GOOGLE_MAPS"],
    "headless":         False,
    "hot_warm_only":    True,
    "google_maps_cap":  None,
    "google_search_cap": None,
    "leads_found":      0,
    "leads_sent":       0,
    "run_id":           None,
}


def get_campaign_state() -> dict:
    return dict(_run_state)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Internal send helper ──────────────────────────────────────────────────────

async def _send_one(lead_id: int, channel: str) -> dict:
    """Send initial outreach to a single lead via the given channel.

    Channel-adaptive: if the preferred channel is unavailable (no email or no phone),
    automatically falls back to the other channel rather than failing silently.
    """
    lead = await db.get_lead_by_id(lead_id)
    if not lead:
        return {"lead_id": lead_id, "success": False, "error": "Lead not found"}

    channel    = channel.upper()
    has_email  = bool(lead.get("email"))
    has_phone  = bool(lead.get("phone"))
    errors: list[str] = []
    sent_any   = False

    # ── Resolve effective channels based on what contact info is available ────
    if channel == "EMAIL":
        # Prefer email; fall back to WhatsApp if no email but phone exists
        effective = ["EMAIL"] if has_email else (["WHATSAPP"] if has_phone else [])
    elif channel == "WHATSAPP":
        # Prefer WhatsApp; fall back to email if no phone but email exists
        effective = ["WHATSAPP"] if has_phone else (["EMAIL"] if has_email else [])
    elif channel == "BOTH":
        effective = (["EMAIL"] if has_email else []) + (["WHATSAPP"] if has_phone else [])
    else:
        return {"lead_id": lead_id, "success": False, "error": f"Unknown channel: {channel}"}

    if not effective:
        msg = f"Lead {lead_id} has no email and no phone — cannot send"
        await db.log_campaign_action(lead_id, channel, "SEND", False, msg)
        return {"lead_id": lead_id, "success": False, "error": msg}

    # ── Attempt each effective channel ────────────────────────────────────────
    for ch in effective:
        try:
            if ch == "EMAIL":
                await email_sender.send_email_lead(lead)
            else:
                await whatsapp_sender.send_whatsapp_lead(lead)

            await db.log_campaign_action(lead_id, ch, "SEND", True)
            sent_any = True

        except Exception as exc:
            errors.append(f"{ch}: {exc}")
            await db.log_campaign_action(lead_id, ch, "SEND", False, str(exc))
            logger.warning("Send failed lead=%s channel=%s: %s", lead_id, ch, exc)

    if sent_any:
        now_dt = datetime.now(timezone.utc)
        await db.update_lead(lead_id, {"status": "SENT", "sent_at": now_dt.isoformat()})
        await _schedule_fu(lead_id, now_dt, dict(lead))
        return {
            "lead_id":       lead_id,
            "success":       True,
            "partial_errors": errors if errors else None,
        }

    return {"lead_id": lead_id, "success": False, "error": "; ".join(errors)}


# ── Background campaign task ──────────────────────────────────────────────────

async def _run_campaign_task(
    niche: str, city: str, channel: str, daily_cap: int, run_id: int,
    sources: List[str] = None, headless: bool = False,
    country: str = None, hot_warm_only: bool = True,
    source_caps: Dict[str, int] = None,
) -> None:
    if sources is None:
        sources = ["GOOGLE_MAPS"]

    try:
        def _log_sync(msg: str) -> None:
            _ls_emit("INFO", "SCRAPE", msg)
            logger.info("[campaign] %s", msg)

        # ── Bulk scrape: dedup + validate + email-enrich + DB save all handled ──
        scraped_leads = await scrapers.run_bulk_scrape(
            campaign={
                "niche":     niche,
                "city":      city,
                "country":   country or "",
                "sources":   sources,
                "max_leads": daily_cap,
                "headless":  headless,
            },
            log_callback=_log_sync,
        )
        n_from_scraper = len(scraped_leads)

        # ── Merge with existing PENDING leads so repeat runs aren't empty ──────
        existing_pending = await db.get_pending_leads_for_niche_city(
            niche, city, limit=daily_cap * 3
        )
        all_leads_map: Dict[int, dict] = {l["id"]: l for l in scraped_leads}
        for lead in existing_pending:
            if lead["id"] not in all_leads_map:
                all_leads_map[lead["id"]] = lead

        all_leads = list(all_leads_map.values())[:daily_cap]
        _run_state["leads_found"] = len(all_leads)
        _log_sync(
            f"📋 Leads to process: {len(all_leads)} "
            f"({n_from_scraper} from scraper, "
            f"{len(existing_pending)} existing PENDING)"
        )

        await db.update_campaign_run_progress(
            run_id, _run_state["leads_found"], _run_state["leads_sent"]
        )

        # ── HOT/WARM filter — auto-disable if all leads would be skipped ───────
        if hot_warm_only:
            n_passing = sum(
                1 for l in all_leads
                if not (
                    (l.get("score") or 0) > 0
                    and (l.get("score_label") or "COLD").upper() == "COLD"
                )
            )
            if n_passing == 0:
                _log_sync(
                    "⚠️  hot_warm_only: all leads are unscored or COLD — "
                    "processing all PENDING leads anyway"
                )
                hot_warm_only = False

        if not all_leads:
            _log_sync("⚠️  No leads available to process — nothing to score, generate, or send")
            _log_sync(
                "💡 Tip: run POST /api/campaign/test-pipeline to verify the DB write path"
            )

        # ── Per-lead: filter → AI generate → send ─────────────────────────────
        for lead in all_leads:
            if _run_state["stop_requested"]:
                break

            lead_id = lead["id"]
            biz     = lead.get("business_name", f"lead#{lead_id}")

            # HOT+WARM filter — skip COLD leads that have already been scored
            if hot_warm_only:
                score = lead.get("score") or 0
                label = (lead.get("score_label") or "COLD").upper()
                if score > 0 and label == "COLD":
                    _log_sync(f"⏭️  Skipping COLD lead: {biz}")
                    await db.update_lead(lead_id, {"status": "SKIPPED"})
                    continue

            await db.update_lead(lead_id, {"channel": channel})
            lead_fresh = await db.get_lead_by_id(lead_id)

            _log_sync(f"✍️  Generating AI messages for: {biz}")

            # Generate AI messages — failure is non-fatal; email_sender has fallbacks
            try:
                msgs = await ai_brain.generate_all_messages(dict(lead_fresh))
                await db.update_lead(lead_id, {
                    "ai_whatsapp_msg":  msgs.get("whatsapp") or msgs.get("whatsapp_message"),
                    "ai_email_subject": msgs.get("email_subject"),
                    "ai_email_body":    msgs.get("email_body"),
                    "ai_followup_msg":  msgs.get("follow_up_1") or msgs.get("followup_day3_body"),
                    "ai_follow_up_1":   msgs.get("follow_up_1") or msgs.get("followup_day3_body"),
                    "ai_follow_up_2":   msgs.get("follow_up_2") or msgs.get("followup_day7_body"),
                    "ai_follow_up_3":   msgs.get("follow_up_3") or msgs.get("followup_day7_body"),
                })
                await db.log_campaign_action(lead_id, "AI", "GENERATE", True)
                lead_fresh = await db.get_lead_by_id(lead_id)
            except Exception as exc:
                await db.log_campaign_action(lead_id, "AI", "GENERATE", False, str(exc))
                _log_sync(f"⚠️  AI generation failed for {biz}: {exc} — using fallback messages")
                logger.warning("AI generation failed for lead %s: %s", lead_id, exc)

            # Send outreach (proceeds even if AI failed — sender has built-in fallbacks)
            _log_sync(f"📤 Sending {channel} to: {biz}")
            result = await _send_one(lead_id, channel)
            if result.get("success"):
                _run_state["leads_sent"] += 1
            else:
                _log_sync(f"❌ Send failed for {biz}: {result.get('error', 'unknown')}")

            if (_run_state["leads_sent"] % _PROGRESS_EVERY) == 0:
                await db.update_campaign_run_progress(
                    run_id, _run_state["leads_found"], _run_state["leads_sent"]
                )

        final_status = "STOPPED" if _run_state["stop_requested"] else "COMPLETED"
        await db.update_campaign_run(run_id, {
            "status":      final_status,
            "leads_found": _run_state["leads_found"],
            "leads_sent":  _run_state["leads_sent"],
            "finished_at": datetime.now(timezone.utc),
        })

    except Exception as exc:
        logger.error("Campaign task failed (run_id=%s): %s", run_id, exc, exc_info=True)
        await db.update_campaign_run(run_id, {
            "status":      "FAILED",
            "leads_found": _run_state["leads_found"],
            "leads_sent":  _run_state["leads_sent"],
            "finished_at": datetime.now(timezone.utc),
        })

    finally:
        _run_state["running"]        = False
        _run_state["stop_requested"] = False


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/start")
async def start_campaign(payload: CampaignStartRequest, background_tasks: BackgroundTasks):
    if _run_state["running"]:
        raise HTTPException(409, "A campaign is already running")

    sources_list = [s.value for s in payload.sources]

    # Build per-source cap dict for the scraper
    source_caps: Dict[str, int] = {}
    if payload.google_maps_cap and "GOOGLE_MAPS" in sources_list:
        source_caps["GOOGLE_MAPS"] = payload.google_maps_cap
    if payload.google_search_cap and "GOOGLE_SEARCH" in sources_list:
        source_caps["GOOGLE_SEARCH"] = payload.google_search_cap

    run_id = await db.create_campaign_run(
        payload.niche, payload.city, payload.channel.value, payload.daily_cap,
        sources=",".join(sources_list),
    )

    _run_state.update({
        "running":          True,
        "stop_requested":   False,
        "niche":            payload.niche,
        "city":             payload.city,
        "country":          payload.country,
        "channel":          payload.channel.value,
        "daily_cap":        payload.daily_cap,
        "sources":          sources_list,
        "headless":         payload.headless,
        "hot_warm_only":    payload.hot_warm_only,
        "google_maps_cap":  payload.google_maps_cap,
        "google_search_cap": payload.google_search_cap,
        "leads_found":      0,
        "leads_sent":       0,
        "run_id":           run_id,
    })

    background_tasks.add_task(
        _run_campaign_task,
        payload.niche, payload.city, payload.channel.value, payload.daily_cap, run_id,
        sources_list, payload.headless,
        payload.country, payload.hot_warm_only, source_caps or None,
    )
    return {"started": True, "run_id": run_id}


@router.post("/stop")
async def stop_campaign():
    if not _run_state["running"]:
        return {"stopped": False, "message": "No campaign is currently running"}
    _run_state["stop_requested"] = True
    return {"stopped": True, "message": "Stop signal sent — finishing current lead"}


@router.get("/history")
async def campaign_history(limit: int = Query(10, ge=1, le=50)):
    return await db.get_campaign_history(limit)


@router.get("/stats")
async def campaign_stats():
    return await db.get_dashboard_stats()


@router.post("/send/{lead_id}")
async def send_to_lead(lead_id: int, channel: str = Query("EMAIL")):
    return await _send_one(lead_id, channel.upper())


@router.post("/send-followup/{lead_id}")
async def send_followup(lead_id: int, channel: str = Query("EMAIL")):
    lead = await db.get_lead_by_id(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")

    channel  = channel.upper()
    sent_via = []
    errors   = []

    if channel in ("EMAIL", "BOTH") and lead.get("email"):
        try:
            await email_sender.send_followup_email(lead)
            await db.log_campaign_action(lead_id, "EMAIL", "FOLLOWUP", True)
            sent_via.append("EMAIL")
        except Exception as exc:
            await db.log_campaign_action(lead_id, "EMAIL", "FOLLOWUP", False, str(exc))
            errors.append(f"email: {exc}")

    if channel in ("WHATSAPP", "BOTH") and lead.get("phone"):
        try:
            await whatsapp_sender.send_followup_whatsapp(lead)
            await db.log_campaign_action(lead_id, "WHATSAPP", "FOLLOWUP", True)
            sent_via.append("WHATSAPP")
        except Exception as exc:
            await db.log_campaign_action(lead_id, "WHATSAPP", "FOLLOWUP", False, str(exc))
            errors.append(f"whatsapp: {exc}")

    if sent_via:
        now = _now_iso()
        # Update legacy field + the first un-sent stage timestamp so the
        # scheduler doesn't re-send a follow-up that was just sent manually.
        lead_fresh = await db.get_lead_by_id(lead_id)
        stage_update: dict = {"followup_sent_at": now}
        if lead_fresh:
            if not lead_fresh.get("follow_up_1_sent_at"):
                stage_update["follow_up_1_sent_at"] = now
            elif not lead_fresh.get("follow_up_2_sent_at"):
                stage_update["follow_up_2_sent_at"] = now
            elif not lead_fresh.get("follow_up_3_sent_at"):
                stage_update["follow_up_3_sent_at"] = now
        await db.update_lead(lead_id, stage_update)

    if not sent_via and errors:
        raise HTTPException(500, "; ".join(errors))

    return {"success": bool(sent_via), "sent_via": sent_via, "errors": errors}


@router.post("/mark-replied/{lead_id}")
async def mark_replied(lead_id: int):
    ok = await db.mark_lead_replied(lead_id)
    if not ok:
        raise HTTPException(404, "Lead not found")
    await db.cancel_pending_followups(lead_id)
    return {"lead_id": lead_id, "status": "REPLIED"}


@router.post("/bulk-send")
async def bulk_send(payload: CampaignSendRequest, background_tasks: BackgroundTasks):
    async def _run():
        for lead_id in payload.lead_ids:
            await _send_one(lead_id, payload.channel.value)

    background_tasks.add_task(_run)
    return {"queued": len(payload.lead_ids), "channel": payload.channel}


@router.post("/test-pipeline")
async def test_pipeline():
    """
    Diagnostic: inserts 5 dummy leads, reads them back, confirms DB write/read works.
    Use this to verify the pipeline before running a real campaign.
    Dummy leads are tagged source='TEST' and can be deleted via DELETE /api/leads?status=PENDING.
    """
    def _log(msg: str) -> None:
        _ls_emit("INFO", "TEST", msg)
        logger.info("[test-pipeline] %s", msg)

    result = await scrapers.run_test_pipeline(log_callback=_log)
    return {"ok": result["ok"], "result": result}


@router.get("/db-health")
async def db_health():
    """Quick DB write/read health check — returns counts and any error."""
    try:
        async with db.get_db() as conn:
            total      = await conn.fetchval("SELECT COUNT(*) FROM leads") or 0
            pending    = await conn.fetchval(
                "SELECT COUNT(*) FROM leads WHERE status = 'PENDING'"
            ) or 0
            runs       = await conn.fetchval("SELECT COUNT(*) FROM campaign_runs") or 0
            log_count  = await conn.fetchval("SELECT COUNT(*) FROM campaign_log") or 0
        return {
            "ok":         True,
            "db_path":    db.DB_PATH,
            "total_leads": total,
            "pending":     pending,
            "runs":        runs,
            "log_entries": log_count,
        }
    except Exception as exc:
        logger.error("DB health check failed: %s", exc, exc_info=True)
        return {"ok": False, "error": str(exc), "db_path": db.DB_PATH}
