from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from typing import List
from .. import database as db
from .. import email_sender, whatsapp_sender, ai_brain, scraper
from ..models import CampaignSendRequest, CampaignStartRequest

router = APIRouter(prefix="/api/campaign", tags=["campaign"])

# ── In-memory campaign run state ──────────────────────────────────────────────
_run_state: dict = {
    "running":        False,
    "stop_requested": False,
    "niche":          None,
    "city":           None,
    "channel":        None,
    "daily_cap":      20,
    "sources":        ["GOOGLE_MAPS"],
    "headless":       False,
    "leads_found":    0,
    "leads_sent":     0,
    "run_id":         None,
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
        elif channel == "WHATSAPP":
            await whatsapp_sender.send_whatsapp_lead(lead)
        elif channel == "BOTH":
            # Attempt both independently — one failure shouldn't block the other
            try:
                await email_sender.send_email_lead(lead)
            except Exception as exc:
                errors.append(f"email: {exc}")
                await db.log_campaign_action(lead_id, "EMAIL", "SEND", False, str(exc))

            try:
                await whatsapp_sender.send_whatsapp_lead(lead)
            except Exception as exc:
                errors.append(f"whatsapp: {exc}")
                await db.log_campaign_action(lead_id, "WHATSAPP", "SEND", False, str(exc))

            if errors:
                return {"lead_id": lead_id, "success": False, "error": "; ".join(errors)}

        await db.update_lead(lead_id, {"status": "SENT", "sent_at": _now_iso()})
        await db.log_campaign_action(lead_id, channel, "SEND", True)
        return {"lead_id": lead_id, "success": True}

    except Exception as exc:
        await db.log_campaign_action(lead_id, channel, "SEND", False, str(exc))
        return {"lead_id": lead_id, "success": False, "error": str(exc)}


# ── Background campaign task ──────────────────────────────────────────────────

async def _run_campaign_task(
    niche: str, city: str, channel: str, daily_cap: int, run_id: int,
    sources: List[str] = None, headless: bool = False,
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
        )

        for lead_data in scraped_leads:
            if _run_state["stop_requested"]:
                break

            # Deduplication — skip if we already know this business
            lead_id, is_new = await db.create_lead_deduped(lead_data)
            if not is_new:
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
                    "ai_followup_msg":  msgs.get("followup"),
                })
                await db.log_campaign_action(lead_id, "AI", "GENERATE", True)
                lead = await db.get_lead_by_id(lead_id)
            except Exception as exc:
                await db.log_campaign_action(lead_id, "AI", "GENERATE", False, str(exc))

            # Send outreach
            result = await _send_one(lead_id, channel)
            if result.get("success"):
                _run_state["leads_sent"] += 1

        final_status = "STOPPED" if _run_state["stop_requested"] else "COMPLETED"
        await db.update_campaign_run(run_id, {
            "status":      final_status,
            "leads_found": _run_state["leads_found"],
            "leads_sent":  _run_state["leads_sent"],
            "finished_at": _now_iso(),
        })

    except Exception as exc:
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

    run_id = await db.create_campaign_run(
        payload.niche, payload.city, payload.channel.value, payload.daily_cap,
        sources=",".join(sources_list),
    )

    _run_state.update({
        "running":        True,
        "stop_requested": False,
        "niche":          payload.niche,
        "city":           payload.city,
        "channel":        payload.channel.value,
        "daily_cap":      payload.daily_cap,
        "sources":        sources_list,
        "headless":       payload.headless,
        "leads_found":    0,
        "leads_sent":     0,
        "run_id":         run_id,
    })

    background_tasks.add_task(
        _run_campaign_task,
        payload.niche, payload.city, payload.channel.value, payload.daily_cap, run_id,
        sources_list, payload.headless,
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
        await db.update_lead(lead_id, {"followup_sent_at": _now_iso()})

    if not sent_via and errors:
        raise HTTPException(500, "; ".join(errors))

    return {"success": bool(sent_via), "sent_via": sent_via, "errors": errors}


@router.post("/mark-replied/{lead_id}")
async def mark_replied(lead_id: int):
    ok = await db.mark_lead_replied(lead_id)
    if not ok:
        raise HTTPException(404, "Lead not found")
    return {"lead_id": lead_id, "status": "REPLIED"}


@router.post("/bulk-send")
async def bulk_send(payload: CampaignSendRequest, background_tasks: BackgroundTasks):
    async def _run():
        for lead_id in payload.lead_ids:
            await _send_one(lead_id, payload.channel.value)

    background_tasks.add_task(_run)
    return {"queued": len(payload.lead_ids), "channel": payload.channel}
