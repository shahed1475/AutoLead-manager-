import asyncio
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query, Request
from typing import Dict, List, Optional
from .. import database as db
from .. import email_sender, whatsapp_sender, ai_brain
from .. import scrapers
from ..enrichment.ai_enricher import enrich_lead_with_ai
from ..enrichment.website_analyzer import analyze_website
from ..intelligence import intelligence_enabled, run_pending_research
from ..followup_engine import schedule_followups_for_lead as _schedule_fu
from ..log_stream import emit as _ls_emit
from ..models import CampaignSendRequest, CampaignStartRequest
from ..rate_limit import limiter
from ..scoring.lead_scorer import score_lead
from ..discovery.planner import DiscoveryPlanner

# Update DB after every lead so real-time counts are always accurate
_PROGRESS_EVERY = 1

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/campaign", tags=["campaign"])

# ── In-memory campaign run state ──────────────────────────────────────────────
# `stage` is the fine-grained pipeline phase shown in the UI (mirrors
# campaign_runs.stage in the DB): QUEUED, STARTING, SCRAPING, ENRICHING,
# SCORING, WRITING, SENDING, COMPLETED, STOPPED, FAILED, PAUSED.
_run_state: dict = {
    "running":          False,
    "stop_requested":   False,
    "paused":           False,
    "stage":            "QUEUED",
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
    "current_lead":     None,   # business_name of the lead currently being written/sent
    "messages_generated": 0,
    "started_at":       None,   # ISO timestamp — drives leads/min + ETA in /api/engine/status
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

    if (lead.get("status") or "").upper() == "DO_NOT_CONTACT":
        msg = f"Lead {lead_id} is marked DO_NOT_CONTACT — send blocked"
        await db.log_campaign_action(lead_id, channel, "SEND", False, msg)
        return {"lead_id": lead_id, "success": False, "error": msg}

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

    def _log_sync(msg: str) -> None:
        _ls_emit("INFO", "SCRAPE", msg)
        logger.info("[campaign] %s", msg)

    async def _set_stage(stage: str) -> None:
        """Persist the real pipeline stage — mirrors campaign_runs.stage in the DB
        so the UI reflects what's actually happening, not a log-text guess."""
        _run_state["stage"] = stage
        try:
            await db.update_campaign_run(run_id, {"stage": stage})
        except Exception as exc:
            logger.debug("Stage persist failed (run_id=%s, stage=%s): %s", run_id, stage, exc)

    async def _wait_while_paused() -> bool:
        """Blocks between leads while paused. Stop always wins over pause.
        Returns True if the caller should stop (stop was requested)."""
        while _run_state["paused"] and not _run_state["stop_requested"]:
            await asyncio.sleep(1)
        return _run_state["stop_requested"]

    try:
        await _set_stage("STARTING")
        _log_sync("🚀 Starting campaign...")

        # ── Discovery Planner: adaptive niche-phrasing query variants ──────────
        # Phase 1 (docs/superpowers/specs/2026-08-25-lead-discovery-planner-design.md).
        # scrapers.run_bulk_scrape accepts exactly one niche/city pair per call —
        # there is no multi-query parameter — so "adaptive query variants
        # replacing a single fixed query" means looping it, bounded by both the
        # planner's own variant cap and daily_cap (so a small cap never splits
        # into unusably small per-variant budgets). Manual source selection is
        # unchanged; only the niche text passed to each call varies.
        discovery_run_id = None
        niche_variants = [niche]
        plan = None
        try:
            plan = await DiscoveryPlanner().plan(
                query=niche, niche=niche, city=city, country=country or "", mode="CAMPAIGN",
            )
            if plan.query_variants:
                niche_variants = plan.query_variants
        except Exception as exc:
            logger.warning(
                "Discovery planner unavailable for campaign run %s: %s — using original niche only",
                run_id, exc,
            )

        # Bookkeeping row is separate from the planner call above — a
        # transient DB error here (e.g. SQLite busy) must not discard an
        # already-successful plan's query variants.
        if plan is not None:
            try:
                discovery_run_id = await db.create_discovery_run({
                    "mode": "CAMPAIGN", "raw_query": niche, "niche": niche, "city": city,
                    "country": country, "target_count": daily_cap,
                    "planner_intent": plan.intent, "planner_confidence": plan.confidence,
                    "sources_planned": sources, "campaign_run_id": run_id,
                })
            except Exception as exc:
                logger.debug(
                    "Discovery run bookkeeping row failed for campaign run %s: %s (non-fatal, variants still used)",
                    run_id, exc, exc_info=True,
                )

        # Never split daily_cap into a per-variant budget smaller than ~5 leads.
        niche_variants = niche_variants[: max(1, daily_cap // 5)] or [niche]
        n_variants = len(niche_variants)
        per_variant_cap = max(1, daily_cap // n_variants)

        # ── Bulk scrape: dedup + validate + email-enrich + DB save all handled ──
        # (per-variant call; results merged by id — same pattern the existing
        # PENDING-leads merge below already uses, so no new dedup code needed)
        scraped_leads: List[dict] = []
        seen_scraped_ids: set = set()
        for i, variant_niche in enumerate(niche_variants):
            if await _wait_while_paused():
                break
            is_last = i == n_variants - 1
            budget = max(1, daily_cap - per_variant_cap * (n_variants - 1)) if is_last else per_variant_cap
            batch = await scrapers.run_bulk_scrape(
                campaign={
                    "niche":     variant_niche,
                    "city":      city,
                    "country":   country or "",
                    "sources":   sources,
                    "max_leads": budget,
                    "headless":  headless,
                },
                log_callback=_log_sync,
                # stage_callback/pause_callback only need to fire once — every
                # variant hits the same SCRAPING stage, and re-setting it is a
                # harmless no-op, but only the first iteration needs to drive it.
                stage_callback=_set_stage if i == 0 else None,
                pause_callback=lambda: _wait_while_paused(),
            )
            for lead in batch:
                if lead["id"] not in seen_scraped_ids:
                    seen_scraped_ids.add(lead["id"])
                    scraped_leads.append(lead)
        n_from_scraper = len(scraped_leads)

        if discovery_run_id:
            try:
                await db.update_discovery_run(discovery_run_id, {
                    "raw_candidates": n_from_scraper, "results_count": n_from_scraper,
                    "status": "COMPLETED", "finished_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                })
            except Exception:
                logger.debug("Discovery run %s status update failed (non-fatal)", discovery_run_id, exc_info=True)

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

        # ── Enrich every lead that has a website with business intelligence ────
        # (website structure + AI summary/gaps/growth) — this used to only run
        # via a manual single-lead endpoint, so scoring below always saw an
        # empty enriched_data row. Bounded concurrency (3) since each lead
        # does one HTTP fetch + one LLM call.
        await _set_stage("ENRICHING")
        if not await _wait_while_paused():
            stored_settings = await db.get_all_settings()
            if intelligence_enabled(stored_settings):
                _log_sync(f"🔎 Running sales-intelligence research on {len(all_leads)} lead(s)...")
                campaign_ctx = {"niche": niche, "city": city, "country": country}
                try:
                    await run_pending_research(
                        lead_ids=[l["id"] for l in all_leads],
                        max_concurrent=3,
                        campaign=campaign_ctx,
                    )
                except Exception as exc:
                    # Setup-phase failures here (e.g. a transient DB lock) must not
                    # abort the whole campaign — per-lead failures are already
                    # contained inside run_research_pipeline; this is the equivalent
                    # safety net for the on-path's setup phase, restoring parity with
                    # the toggle-off path's _enrich_one per-lead try/except below.
                    logger.warning(
                        "Sales-intelligence research failed for campaign run %s: %s", run_id, exc
                    )
                fresh_map = await db.get_leads_by_ids([l["id"] for l in all_leads])
                all_leads = [fresh_map.get(l["id"], l) for l in all_leads]
            else:
                leads_with_site = [l for l in all_leads if l.get("website")]
                if leads_with_site:
                    _log_sync(f"🔎 Enriching {len(leads_with_site)} lead(s) with business intelligence...")
                    company_dna = ai_brain._load_company_dna()
                    sem = asyncio.Semaphore(3)

                    async def _enrich_one(lead: dict) -> None:
                        async with sem:
                            try:
                                website_data = await analyze_website(lead["website"])
                                await enrich_lead_with_ai(dict(lead), website_data, company_dna)
                            except Exception as exc:
                                logger.warning("Enrichment failed for lead %s: %s", lead.get("id"), exc)

                    await asyncio.gather(*[_enrich_one(l) for l in leads_with_site])

                    # Re-fetch (batched — one query, not one per lead) so scoring
                    # below sees the enriched_at/status fields
                    fresh_map = await db.get_leads_by_ids([l["id"] for l in all_leads])
                    all_leads = [fresh_map.get(l["id"], l) for l in all_leads]

        # ── Score every lead that hasn't been scored yet ────────────────────────
        await _set_stage("SCORING")
        unscored = [l for l in all_leads if not (l.get("score") or 0)]
        if unscored:
            _log_sync(f"📊 Scoring {len(unscored)} lead(s)...")
            for lead in unscored:
                if await _wait_while_paused():
                    break
                try:
                    enriched = await db.get_enriched_data(lead["id"])
                    scored = await score_lead(dict(lead), enriched=enriched)
                    _log_sync(
                        f"   📊 AI Score: {scored.get('final_score', 0):.0f} "
                        f"({scored.get('category', 'COLD')}) — {lead.get('business_name', '?')}"
                    )
                except Exception as exc:
                    logger.warning("Scoring failed for lead %s: %s", lead.get("id"), exc)

            # Re-fetch (batched) so the hot/warm filter below sees persisted scores
            fresh_map = await db.get_leads_by_ids([l["id"] for l in all_leads])
            all_leads = [fresh_map.get(l["id"], l) for l in all_leads]

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

        # ── WRITING: filter → generate AI messages for every eligible lead ─────
        await _set_stage("WRITING")
        to_send: List[int] = []
        for lead in all_leads:
            if await _wait_while_paused():
                break

            lead_id = lead["id"]
            biz     = lead.get("business_name", f"lead#{lead_id}")
            _run_state["current_lead"] = biz

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
                _run_state["messages_generated"] += 1
            except Exception as exc:
                await db.log_campaign_action(lead_id, "AI", "GENERATE", False, str(exc))
                _log_sync(f"⚠️  AI generation failed for {biz}: {exc} — using fallback messages")
                logger.warning("AI generation failed for lead %s: %s", lead_id, exc)

            to_send.append(lead_id)

        # ── SENDING: send outreach to every generated lead ─────────────────────
        await _set_stage("SENDING")
        for lead_id in to_send:
            if await _wait_while_paused():
                break

            lead = await db.get_lead_by_id(lead_id)
            biz  = (lead or {}).get("business_name", f"lead#{lead_id}")
            _run_state["current_lead"] = biz

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

        _run_state["current_lead"] = None
        final_stage = "STOPPED" if _run_state["stop_requested"] else "COMPLETED"
        await _set_stage(final_stage)
        _log_sync(f"🏁 Campaign {final_stage.lower()} — {_run_state['leads_sent']} sent")
        await db.update_campaign_run(run_id, {
            "status":      final_stage,
            "leads_found": _run_state["leads_found"],
            "leads_sent":  _run_state["leads_sent"],
            "finished_at": datetime.now(timezone.utc),
        })

    except Exception as exc:
        logger.error("Campaign task failed (run_id=%s): %s", run_id, exc, exc_info=True)
        await _set_stage("FAILED")
        await db.update_campaign_run(run_id, {
            "status":      "FAILED",
            "leads_found": _run_state["leads_found"],
            "leads_sent":  _run_state["leads_sent"],
            "finished_at": datetime.now(timezone.utc),
        })

    finally:
        _run_state["running"]        = False
        _run_state["stop_requested"] = False
        _run_state["paused"]         = False
        _run_state["current_lead"]   = None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/start")
@limiter.limit("5/minute")
async def start_campaign(request: Request, payload: CampaignStartRequest, background_tasks: BackgroundTasks):
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
        sources=",".join(sources_list), country=payload.country,
    )

    _run_state.update({
        "running":          True,
        "stop_requested":   False,
        "paused":           False,
        "stage":            "QUEUED",
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
        "current_lead":     None,
        "messages_generated": 0,
        "started_at":       datetime.now(timezone.utc).isoformat(),
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
    _run_state["paused"]         = False  # stop always wins over pause
    return {"stopped": True, "message": "Stop signal sent — finishing current lead"}


@router.post("/pause")
async def pause_campaign():
    """
    Sets the `paused` flag only — `stage` is deliberately left alone so it
    keeps showing the real phase (e.g. "WRITING") the campaign will resume
    into. The frontend renders `paused` as an overlay on top of that stage.
    """
    if not _run_state["running"]:
        return {"paused": False, "message": "No campaign is currently running"}
    if _run_state["paused"]:
        return {"paused": True, "message": "Already paused"}
    _run_state["paused"] = True
    _ls_emit("INFO", "SYSTEM", "⏸️  Campaign paused — will hold after the current lead")
    return {"paused": True, "message": "Pausing — finishing current lead, then holding"}


@router.post("/resume")
async def resume_campaign():
    if not _run_state["running"]:
        return {"resumed": False, "message": "No campaign is currently running"}
    if not _run_state["paused"]:
        return {"resumed": False, "message": "Campaign is not paused"}
    _run_state["paused"] = False
    _ls_emit("INFO", "SYSTEM", "▶️  Campaign resumed")
    return {"resumed": True}


@router.get("/history")
async def campaign_history(limit: int = Query(50, ge=1, le=200)):
    return await db.get_campaign_history(limit)


@router.get("/history/{run_id}")
async def campaign_history_detail(run_id: int):
    run = await db.get_campaign_run_by_id(run_id)
    if not run:
        raise HTTPException(404, "Campaign run not found")
    return run


@router.delete("/history/{run_id}")
async def delete_campaign_history(run_id: int):
    if _run_state.get("run_id") == run_id and _run_state["running"]:
        raise HTTPException(409, "Cannot delete the currently running campaign")
    ok = await db.delete_campaign_run(run_id)
    if not ok:
        raise HTTPException(404, "Campaign run not found")
    return {"deleted": True, "run_id": run_id}


@router.get("/stats")
async def campaign_stats():
    return await db.get_dashboard_stats()


@router.post("/send/{lead_id}")
@limiter.limit("60/minute")
async def send_to_lead(request: Request, lead_id: int, channel: str = Query("EMAIL")):
    return await _send_one(lead_id, channel.upper())


@router.post("/send-followup/{lead_id}")
@limiter.limit("60/minute")
async def send_followup(request: Request, lead_id: int, channel: str = Query("EMAIL")):
    lead = await db.get_lead_by_id(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    if (lead.get("status") or "").upper() == "DO_NOT_CONTACT":
        raise HTTPException(400, "Lead is marked DO_NOT_CONTACT — cannot send follow-up")

    channel  = channel.upper()
    sent_via = []
    errors   = []

    if channel in ("EMAIL", "BOTH") and lead.get("email"):
        try:
            await email_sender.send_followup_email(lead)
            await db.log_campaign_action(lead_id, "EMAIL", "FOLLOWUP", True)
            sent_via.append("EMAIL")
        except Exception as exc:
            logger.error("Follow-up email failed for lead %s: %s", lead_id, exc, exc_info=True)
            await db.log_campaign_action(lead_id, "EMAIL", "FOLLOWUP", False, str(exc))
            errors.append("email: send failed — see server logs")

    if channel in ("WHATSAPP", "BOTH") and lead.get("phone"):
        try:
            await whatsapp_sender.send_followup_whatsapp(lead)
            await db.log_campaign_action(lead_id, "WHATSAPP", "FOLLOWUP", True)
            sent_via.append("WHATSAPP")
        except Exception as exc:
            logger.error("Follow-up WhatsApp failed for lead %s: %s", lead_id, exc, exc_info=True)
            await db.log_campaign_action(lead_id, "WHATSAPP", "FOLLOWUP", False, str(exc))
            errors.append("whatsapp: send failed — see server logs")

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
@limiter.limit("10/minute")
async def bulk_send(request: Request, payload: CampaignSendRequest, background_tasks: BackgroundTasks):
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
