"""
routers/whatsapp.py — WhatsApp Campaigns (owner only).

  /api/whatsapp/*        your dashboard (behind the app password): status,
                         QR / live screen, campaigns, settings, activity.
  /api/whatsapp/hooks/*  called by HOM's own n8n (X-HOM-Secret header):
                         incoming messages and the minute-by-minute pacer.
"""
import hmac
import os
from typing import List, Optional

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel

from .. import edition
from ..whatsapp import contacts, engine, meta, service
from ..whatsapp.service import WhatsAppError

router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])
hooks = APIRouter(prefix="/api/whatsapp/hooks", tags=["whatsapp-hooks"])
meta_hooks = APIRouter(prefix="/api/whatsapp/meta", tags=["whatsapp-meta-webhook"])   # public, signed by Meta


def _err(exc: Exception, status: int = 400):
    raise HTTPException(status, str(exc))


# ── Status & WhatsApp session ────────────────────────────────────────────────

@router.get("/status")
async def status():
    s = await service.get_settings()
    eng = await service.current_engine()
    if eng == "meta":
        st = await meta.status()
        wa = {"state": st["state"], "number": st.get("number"), "name": st.get("name"), "error": st.get("error"),
              "quality": st.get("quality")}
        automation = {"n8n": True if edition.is_client() else await engine.n8n_up()}
    elif edition.is_client():
        session = await engine.session()
        me = session.get("me") or {}
        wa = {"state": session["state"], "number": (me.get("id") or "").split("@")[0] or None, "name": me.get("pushName")}
        automation = {"n8n": True}                    # built in: the workspace paces and receives itself
    else:
        session = await engine.session()
        me = session.get("me") or {}
        wa = {"state": session["state"], "number": (me.get("id") or "").split("@")[0] or None, "name": me.get("pushName")}
        automation = {"n8n": await engine.n8n_up()}
    return {"engine": eng, "edition": edition.edition(), "web_available": engine.available(),
            "whatsapp": wa, "automation": automation,
            "pacing": await service.pacing(s), "settings": s}


def _owner_only():
    if not engine.available():
        raise HTTPException(404, "No WhatsApp Web engine here — connect WhatsApp with your Meta API.")


@router.post("/session/start")
async def session_start():
    _owner_only()
    try:
        return await engine.start()
    except engine.EngineError as exc:
        _err(exc, 503)


@router.post("/session/logout")
async def session_logout():
    _owner_only()
    try:
        await engine.logout()
    except engine.EngineError as exc:
        _err(exc, 503)
    await service.log("info", "WhatsApp was unlinked from HOM.")
    return {"ok": True}


def _png(data: Optional[bytes]) -> Response:
    if not data:
        raise HTTPException(404, "Not available right now")
    return Response(data, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/qr.png")
async def qr():
    _owner_only()
    try:
        return _png(await engine.qr_png())
    except engine.EngineError as exc:
        _err(exc, 503)


@router.get("/screen.png")
async def screen():
    _owner_only()
    try:
        return _png(await engine.screenshot_png())
    except engine.EngineError as exc:
        _err(exc, 503)


# ── Settings & activity ──────────────────────────────────────────────────────

class Settings(BaseModel):
    wa_daily_limit: Optional[int] = None
    wa_min_gap: Optional[int] = None
    wa_max_gap: Optional[int] = None
    wa_hours_start: Optional[int] = None
    wa_hours_end: Optional[int] = None
    wa_auto_reply: Optional[bool] = None
    wa_reply_scope: Optional[str] = None
    wa_engine: Optional[str] = None
    wa_auto_reply_per_chat: Optional[int] = None
    wa_reply_delay_min: Optional[int] = None
    wa_reply_delay_max: Optional[int] = None


@router.put("/settings")
async def put_settings(payload: Settings):
    try:
        return await service.save_settings(payload.model_dump(exclude_none=True))
    except WhatsAppError as exc:
        _err(exc, 422)


@router.get("/activity")
async def get_activity(limit: int = 80):
    return {"activity": await service.activity(limit)}


@router.get("/chats")
async def get_chats():
    return {"chats": await service.chats()}


@router.get("/conversation/{lead_id}")
async def get_conversation(lead_id: int):
    return {"messages": await service.conversation(lead_id)}


# ── Campaigns ────────────────────────────────────────────────────────────────

class AudienceQuery(BaseModel):
    statuses: Optional[List[str]] = None
    labels: Optional[List[str]] = None
    niche: Optional[str] = None
    city: Optional[str] = None
    use_ai_drafts: bool = False


class NewCampaign(AudienceQuery):
    name: str
    template: Optional[str] = None
    lead_ids: Optional[List[int]] = None
    meta_template: Optional[dict] = None


@router.post("/audience")
async def preview_audience(q: AudienceQuery):
    leads = await service.audience(q.statuses, q.labels, q.niche, q.city, q.use_ai_drafts)
    return {"count": len(leads), "leads": [
        {k: l.get(k) for k in ("id", "business_name", "phone", "city", "niche", "score_label", "status")} for l in leads[:200]]}


@router.post("/contacts")
async def upload_contacts(file: UploadFile = File(...), country_code: str = Form(""), save: bool = Form(False)):
    """Upload a contact file (CSV / XLSX). save=false: check it and show what's
    usable. save=true: add the usable contacts as leads and return their ids."""
    data = await file.read(contacts.MAX_BYTES + 1)
    try:
        parsed = contacts.parse_file(file.filename or "", data, country_code)
    except contacts.ContactFileError as exc:
        _err(exc)
    result = await contacts.check_and_import(parsed, save=save)
    if save:
        await service.log("info", f"Imported {result['ready']} WhatsApp contacts from “{file.filename}” "
                                  f"({result['new_leads']} new leads).")
    return result


@router.get("/campaigns")
async def campaigns():
    return {"campaigns": await service.list_campaigns()}


@router.post("/campaigns", status_code=201)
async def create(payload: NewCampaign):
    ids = payload.lead_ids
    if not ids:
        ids = [l["id"] for l in await service.audience(payload.statuses, payload.labels, payload.niche,
                                                       payload.city, payload.use_ai_drafts)]
    try:
        return await service.create_campaign(payload.name, payload.template, payload.use_ai_drafts, ids,
                                             payload.meta_template)
    except WhatsAppError as exc:
        _err(exc)


@router.post("/campaigns/{cid}/{action}")
async def campaign_action(cid: int, action: str):
    try:
        return await service.set_campaign_status(cid, action)
    except WhatsAppError as exc:
        _err(exc)


@router.post("/preview")
async def preview_message(payload: dict):
    """Show how a template reads for a real lead."""
    lead = payload.get("lead") or {}
    return {"text": service.render(str(payload.get("template") or ""), lead)}


# ── Meta WhatsApp Cloud API (your own keys) ──────────────────────────────────

_MASK = "••••set••••"


class MetaSettings(BaseModel):
    phone_number_id: Optional[str] = None
    waba_id: Optional[str] = None
    api_version: Optional[str] = None
    access_token: Optional[str] = None
    app_secret: Optional[str] = None


def _webhook_path() -> str:
    if edition.is_client():
        return f"/wa-hook/{os.getenv('HOM_WORKSPACE_PORT', '')}"
    return "/api/whatsapp/meta/webhook"


@router.get("/meta")
async def get_meta():
    cfg = await meta.config()
    return {"phone_number_id": cfg["phone_number_id"], "waba_id": cfg["waba_id"], "api_version": cfg["api_version"],
            "access_token": _MASK if cfg["access_token"] else "", "app_secret": _MASK if cfg["app_secret"] else "",
            "verify_token": await meta.ensure_verify_token(), "webhook_path": _webhook_path()}


@router.put("/meta")
async def put_meta(payload: MetaSettings):
    for field, value in payload.model_dump(exclude_none=True).items():
        value = value.strip()
        if field in ("access_token", "app_secret") and value in ("", _MASK):
            continue                                  # keep the stored secret
        if field in ("phone_number_id", "waba_id") and value and not value.isdigit():
            raise HTTPException(422, f"{field.replace('_', ' ')} should be the number from Meta.")
        if field == "api_version" and value and not (value.startswith("v") and value[1:].replace(".", "").isdigit()):
            raise HTTPException(422, "API version looks like v23.0")
        await service.db.upsert_setting(f"wa_meta_{field}", value)
    return await get_meta()


@router.post("/meta/test")
async def test_meta():
    return await meta.status()


@router.get("/meta/templates")
async def meta_templates():
    try:
        return {"templates": await meta.templates()}
    except meta.MetaError as exc:
        _err(exc)


@meta_hooks.get("/webhook")
async def meta_verify(request: Request):
    """Meta's one-time webhook check: echo the challenge if the verify token matches."""
    q = request.query_params
    want = await meta.ensure_verify_token()
    if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") and hmac.compare_digest(q["hub.verify_token"], want):
        return Response(q.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(403, "Verification failed")


@meta_hooks.post("/webhook")
async def meta_webhook(request: Request, x_hub_signature_256: Optional[str] = Header(None)):
    """Incoming WhatsApp messages from Meta — signed with your app secret."""
    body = await request.body()
    cfg = await meta.config()
    if not meta.signature_ok(body, x_hub_signature_256, cfg.get("app_secret", "")):
        raise HTTPException(401, "Bad signature")
    if await service.current_engine() != "meta":
        return {"ok": True, "ignored": "engine is not meta"}
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(400, "Expected JSON")
    for event in meta.to_events(payload):
        await service.handle_event(event)
    return {"ok": True}


# ── n8n hooks (shared secret) ────────────────────────────────────────────────

def _check_secret(given: Optional[str]) -> None:
    want = engine.secrets().get("HOM_WA_SECRET", "")
    if not want or not given or not hmac.compare_digest(given, want):
        raise HTTPException(401, "Unauthorized")


@hooks.post("/event")
async def hook_event(request: Request, x_hom_secret: Optional[str] = Header(None)):
    _check_secret(x_hom_secret)
    try:
        event = await request.json()
    except ValueError:
        raise HTTPException(400, "Expected JSON")
    events = event if isinstance(event, list) else [event]
    results = [await service.handle_event(e) for e in events if isinstance(e, dict)]
    return {"ok": True, "results": results}


@hooks.post("/tick")
async def hook_tick(x_hom_secret: Optional[str] = Header(None)):
    _check_secret(x_hom_secret)
    return await service.tick()
