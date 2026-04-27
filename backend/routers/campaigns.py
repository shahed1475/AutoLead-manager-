import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from typing import Dict, List, Optional
from .. import database as db
from .. import email_sender, whatsapp_sender, ai_brain, scraper
from ..followup_engine import schedule_followups_for_lead as _schedule_fu
from ..models import CampaignSendRequest, CampaignStartRequest

# Progress update every N leads to avoid hammering the DB on every iteration
_PROGRESS_EVERY = 5

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
    """Send initial outreach to a single lead via the given channel."""
    lead = await db.get_lead_by_id(lead_id)
    if not lead:
        return {"lead_id": lead_id, "success": False, "error": "Lead not found"}

    channel = channel.upper()
    errors: list[str] = []

    try:
        if channel == "EMAIL":
            await email_sender.send_email_lead(lead)
            now_dt = datetime.now(timezone.utc)
            await db.update_lead(lead_id, {"status": "SENT", "sent_at": now_dt.isoformat()})
            await db.log_campaign_action(lead_id, channel, "SEND", True)
            await _schedule_fu(lead_id, now_dt, dict(lead))
            return {"lead_id": lead_id, "success": True}

        elif channel == "WHATSAPP":
            await whatsapp_sender.send_whatsapp_lead(lead)
            now_dt = datetime.now(timezone.utc)
            await db.update_lead(lead_id, {"status": "SENT", "sent_at": now_dt.isoformat()})
            await db.log_campaign_action(lead_id, channel, "SEND", True)
            await _schedule_fu(lead_id, now_dt, dict(lead))
            return {"lead_id": lead_id, "success": True}

        elif channel == "BOTH":
            # Attempt both independently — one failure should not block the other
            sent_any = False
            try:
                await email_sender.send_email_lead(lead)
                await db.log_campaign_action(lead_id, "EMAIL", "SEND", True)
                sent_any = True
            except Exception as exc:
                errors.append(f"email: {exc}")
                await db.log_campaign_action(lead_id, "EMAIL", "SEND", False, str(exc))

            try:
                await whatsapp_sender.send_whatsapp_lead(lead)
                await db.log_campaign_action(lead_id, "WHATSAPP", "SEND", True)
                sent_any = True
            except Exception as exc:
                errors.append(f"whatsapp: {exc}")
                await db.log_campaign_action(lead_id, "WHATSAPP", "SEND", False, str(exc))

            if sent_any:
                now_dt = datetime.now(timezone.utc)
                await db.update_lead(lead_id, {"status": "SENT", "sent_at": now_dt.isoformat()})
                await _schedule_fu(lead_id, now_dt, dict(lead))
                return {
                    "lead_id": lead_id,
                    "success": True,
                    "partial_errors": errors if errors else None,
                }
            return {"lead_id": lead_id, "success": False, "error": "; ".join(errors)}

        return {"lead_id": lead_id, "success": False, "error": f"Unknown channel: {channel}"}

    except Exception as exc:
        await db.log_campaign_action(lead_id, channel, "SEND", False, str(exc))
        return {"lead_id": lead_id, "success": False, "error": str(exc)}


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
        scraped_leads = await scraper.scrape_multi_source(
            sources     = sources,
            niche       = niche,
            city        = city,
            max_results = daily_cap,
            headless    = headless,
            source_caps = source_caps or None,
        )

        leads_scraped_total = len(scraped_leads)

        for lead_data in scraped_leads:
            if _run_state["stop_requested"]:
                break

            # Deduplication — skip if we already know this business
            lead_id, is_new = await db.create_lead_deduped(lead_data)
            if not is_new:
                continue

            # HOT+WARM filter — skip COLD leads that have already been scored
            if hot_warm_only:
                lead_check = await db.get_lead_by_id(lead_id)
                if lead_check:
                    score = lead_check.get("score") or 0
                    label = (lead_check.get("score_label") or "COLD").upper()
                    if score > 0 and label == "COLD":
                        await db.update_lead(lead_id, {"status": "SKIPPED"})
                        continue

            await db.log_campaign_action(lead_id, "SCRAPE", "FOUND", True)
            _run_state["leads_found"] += 1

            await db.update_lead(lead_id, {"channel": channel})
            lead = await db.get_lead_by_id(lead_id)

            # Generate AI messages
            try:
                msgs = await ai_brain.generate_all_messages(dict(lead))
                await db.update_lead(lead_id, {
                    "ai_whatsapp_msg":  msgs.get("whatsapp"),
                    "ai_email_subject": msgs.get("email_subject"),
                    "ai_email_body":    msgs.get("email_body"),
                    "ai_followup_msg":  msgs.get("follow_up_1"),
                    "ai_follow_up_1":   msgs.get("follow_up_1"),
                    "ai_follow_up_2":   msgs.get("follow_up_2"),
                    "ai_follow_up_3":   msgs.get("follow_up_3"),
                })
                await db.log_campaign_action(lead_id, "AI", "GENERATE", True)
                lead = await db.get_lead_by_id(lead_id)
            except Exception as exc:
                await db.log_campaign_action(lead_id, "AI", "GENERATE", False, str(exc))
                logger.warning("AI generation failed for lead %s: %s", lead_id, exc)
                continue  # skip send if no messages were generated

            # Send outreach
            result = await _send_one(lead_id, channel)
            if result.get("success"):
                _run_state["leads_sent"] += 1

            # Push live progress to DB every N leads
            if (_run_state["leads_found"] % _PROGRESS_EVERY) == 0:
                await db.update_campaign_run_progress(
                    run_id, _run_state["leads_found"], _run_state["leads_sent"]
                )

        final_status = "STOPPED" if _run_state["stop_requested"] else "COMPLETED"
        await db.update_campaign_run(run_id, {
            "status":      final_status,
            "leads_found": _run_state["leads_found"],
            "leads_sent":  _run_state["leads_sent"],
            "finished_at": _now_iso(),
        })

    except Exception as exc:
        logger.error("Campaign task failed (run_id=%s): %s", run_id, exc, exc_info=True)
        await db.update_campaign_run(run_id, {
            "status":      "FAILED",
            "leads_found": _run_state["leads_found"],
            "leads_sent":  _run_state["leads_sent"],
            "finished_at": _now_iso(),
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
