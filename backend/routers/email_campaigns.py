"""
routers/email_campaigns.py — Email Campaign (PopupGenix) REST API.

All routes sit behind the app's existing single-operator session dependency
(applied in main.py, like every other router) and behind the
`email_campaigns_enabled` feature flag (default OFF -> 503).

AutoLead is single-operator: there is no user/tenant/workspace model, so there
is no cross-user ownership to enforce — the security boundary is the session
token + the feature flag + the fail-closed TEST_MODE safety gate in
EmailCampaignService. This router holds NO business logic; it validates input
and delegates to the service.

n8n is optional. Nothing here can make n8n send email; the only production
sender remains email_sender.send_email().
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from .. import database as db
from ..email_campaigns import n8n_client
from ..email_campaigns.service import EmailCampaignError, get_email_campaign_service, is_feature_enabled
from ..rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/email-campaigns", tags=["email-campaigns"])

_MAX_IMPORT_BYTES = 10 * 1024 * 1024   # 10 MB lead file
_MAX_ATTACH_BYTES = 15 * 1024 * 1024   # 15 MB attachment (service re-checks)
_IMPORT_EXT = (".csv", ".xlsx", ".xls")


# ── shared dependency: feature flag ───────────────────────────────────────────

async def require_feature_enabled() -> None:
    if not await is_feature_enabled():
        raise HTTPException(
            status_code=503,
            detail="Email Campaigns is not enabled on this instance "
                   "(app_settings 'email_campaigns_enabled').",
        )


_gated = [Depends(require_feature_enabled)]
_svc = get_email_campaign_service()


def _handle(exc: EmailCampaignError) -> HTTPException:
    msg = str(exc)
    low = msg.lower()
    if "not found" in low:
        return HTTPException(404, msg)
    if "disabled" in low:
        return HTTPException(503, msg)
    if any(w in low for w in ("cannot", "can only", "invalid transition", "must be",
                              "no leads", "already", "no editable", "not a valid")):
        return HTTPException(409, msg)
    return HTTPException(422, msg)


# ── request models ───────────────────────────────────────────────────────────

class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    ai_enabled: bool = True
    from_name: Optional[str] = Field(default=None, max_length=200)
    from_email: Optional[str] = Field(default=None, max_length=254)
    sender_profile_id: Optional[int] = None          # which authorized account sends (NULL = global SMTP)
    reply_to: Optional[str] = Field(default=None, max_length=254)
    # NOTE: test_mode / test_recipient are deliberately NOT accepted — the
    # backend forces test_mode=true for this development phase.


class CampaignPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    ai_enabled: Optional[bool] = None
    from_name: Optional[str] = Field(default=None, max_length=200)
    from_email: Optional[str] = Field(default=None, max_length=254)
    sender_profile_id: Optional[int] = None
    reply_to: Optional[str] = Field(default=None, max_length=254)


class CampaignFromSearchRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    sender_profile_id: Optional[int] = None
    reply_to: Optional[str] = Field(default=None, max_length=254)
    lead_ids: List[int] = Field(min_length=1, max_length=2000)


class StartBody(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=200)


class N8nCallbackLead(BaseModel):
    lead_key: str
    ai_subject: Optional[str] = None
    ai_body: Optional[str] = None


class N8nCallbackBody(BaseModel):
    run_id: int
    event: str
    leads: List[N8nCallbackLead] = Field(default_factory=list)


# ── CRUD ─────────────────────────────────────────────────────────────────────

@router.post("", dependencies=_gated, status_code=201)
async def create_campaign(payload: CampaignCreate):
    try:
        camp = await _svc.create_campaign(payload.model_dump(exclude_none=True))
        return await _svc.get_campaign_public(camp["id"])
    except EmailCampaignError as exc:
        raise _handle(exc)


@router.post("/from-search", dependencies=_gated, status_code=201)
@limiter.limit("20/minute")
async def create_campaign_from_search(request: Request, payload: CampaignFromSearchRequest):
    """Create a new DRAFT campaign from a Lead Search selection and add the
    selected global leads to it. Never prepares or sends — the campaign is
    driven normally from /email-campaigns afterwards."""
    try:
        camp = await _svc.create_campaign(
            payload.model_dump(exclude_none=True, exclude={"lead_ids"})
        )
        summary = await _svc.add_leads_from_db(camp["id"], payload.lead_ids)
        return {"campaign_id": camp["id"], "name": camp["name"], **summary}
    except EmailCampaignError as exc:
        raise _handle(exc)


@router.get("", dependencies=_gated)
async def list_campaigns(offset: int = 0, limit: int = 50):
    limit = max(1, min(limit, 200))
    return {"campaigns": await _svc.list_campaigns_public(limit=limit, offset=max(0, offset))}


@router.get("/{campaign_id}", dependencies=_gated)
async def get_campaign(campaign_id: int):
    try:
        return await _svc.get_campaign_public(campaign_id)
    except EmailCampaignError as exc:
        raise _handle(exc)


@router.patch("/{campaign_id}", dependencies=_gated)
async def patch_campaign(campaign_id: int, payload: CampaignPatch):
    try:
        # exclude_unset so an explicit `null` (unset sender / reply-to) is honored
        return await _svc.update_campaign(campaign_id, payload.model_dump(exclude_unset=True))
    except EmailCampaignError as exc:
        raise _handle(exc)


# ── lead import ──────────────────────────────────────────────────────────────

@router.post("/{campaign_id}/leads/import", dependencies=_gated)
@limiter.limit("20/minute")
async def import_leads(request: Request, campaign_id: int, file: UploadFile = File(...)):
    name = (file.filename or "").lower()
    if not name.endswith(_IMPORT_EXT):
        raise HTTPException(422, f"Unsupported file type. Allowed: {', '.join(_IMPORT_EXT)}")
    data = await file.read()
    if not data:
        raise HTTPException(422, "Uploaded file is empty.")
    if len(data) > _MAX_IMPORT_BYTES:
        raise HTTPException(413, f"Lead file exceeds {_MAX_IMPORT_BYTES // (1024*1024)} MB.")
    try:
        return await _svc.import_leads(campaign_id, file.filename, data)
    except EmailCampaignError as exc:
        raise _handle(exc)
    except ValueError as exc:               # parse errors from file_import
        raise HTTPException(422, str(exc))


@router.get("/{campaign_id}/leads", dependencies=_gated)
async def list_leads(campaign_id: int, status: Optional[str] = None,
                     offset: int = 0, limit: int = 100):
    limit = max(1, min(limit, 500))
    try:
        return await _svc.list_leads(campaign_id, status=status,
                                     offset=max(0, offset), limit=limit)
    except EmailCampaignError as exc:
        raise _handle(exc)


# ── attachment ───────────────────────────────────────────────────────────────

@router.post("/{campaign_id}/attachment", dependencies=_gated)
@limiter.limit("20/minute")
async def upload_attachment(request: Request, campaign_id: int,
                            file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(422, "Attachment is empty.")
    if len(data) > _MAX_ATTACH_BYTES:
        raise HTTPException(413, f"Attachment exceeds {_MAX_ATTACH_BYTES // (1024*1024)} MB.")
    try:
        return await _svc.set_attachment(campaign_id, file.filename or "attachment", data)
    except EmailCampaignError as exc:
        raise _handle(exc)
    except ValueError as exc:               # AttachmentError (bad type / name / size)
        raise HTTPException(422, str(exc))


@router.delete("/{campaign_id}/attachment", dependencies=_gated, status_code=204)
async def delete_attachment(campaign_id: int):
    try:
        await _svc.clear_attachment(campaign_id)
    except EmailCampaignError as exc:
        raise _handle(exc)


# ── lifecycle ────────────────────────────────────────────────────────────────

@router.post("/{campaign_id}/prepare", dependencies=_gated)
async def prepare_campaign(campaign_id: int):
    try:
        result = await _svc.prepare_campaign(campaign_id)
        camp = await _svc.get_campaign_public(campaign_id)
    except EmailCampaignError as exc:
        raise _handle(exc)
    return {"prepared": result, "campaign": camp}


@router.post("/{campaign_id}/ready", dependencies=_gated)
async def mark_ready(campaign_id: int):
    try:
        await _svc.mark_ready(campaign_id)
        return await _svc.get_campaign_public(campaign_id)
    except EmailCampaignError as exc:
        raise _handle(exc)


@router.post("/{campaign_id}/start", dependencies=_gated)
async def start_campaign(campaign_id: int, payload: StartBody):
    try:
        run = await _svc.start(campaign_id, payload.idempotency_key)
    except EmailCampaignError as exc:
        raise _handle(exc)
    return {"run_id": run.get("id"), "status": run.get("status")}


@router.post("/{campaign_id}/pause", dependencies=_gated)
async def pause_campaign(campaign_id: int):
    try:
        await _svc.pause(campaign_id)
        return await _svc.get_campaign_public(campaign_id)
    except EmailCampaignError as exc:
        raise _handle(exc)


@router.post("/{campaign_id}/resume", dependencies=_gated)
async def resume_campaign(campaign_id: int, payload: StartBody):
    try:
        run = await _svc.resume(campaign_id, payload.idempotency_key)
    except EmailCampaignError as exc:
        raise _handle(exc)
    return {"run_id": run.get("id"), "status": run.get("status")}


# ── stats / activity ─────────────────────────────────────────────────────────

@router.get("/{campaign_id}/stats", dependencies=_gated)
async def campaign_stats(campaign_id: int):
    try:
        return await _svc.get_stats(campaign_id)
    except EmailCampaignError as exc:
        raise _handle(exc)


@router.get("/{campaign_id}/activity", dependencies=_gated)
async def campaign_activity(campaign_id: int, limit: int = 200):
    limit = max(1, min(limit, 500))
    try:
        rows = await _svc.get_activity(campaign_id, limit=limit)
    except EmailCampaignError as exc:
        raise _handle(exc)
    # activity rows are already secret-free (see log_email_campaign_activity)
    return {"activity": rows}


# ── optional n8n boundary ────────────────────────────────────────────────────

@router.get("/n8n/status", dependencies=_gated)
async def n8n_status():
    """Describe the optional n8n boundary. Fail-safe: an unconfigured or
    unreachable n8n is reported, never raised."""
    try:
        return await n8n_client.describe()
    except n8n_client.N8nNotConfigured as exc:
        return {"configured": False, "detail": str(exc), "sends_email": False,
                "authoritative_sender": "email_sender.send_email"}


@router.post("/{campaign_id}/n8n-callback", dependencies=_gated)
async def n8n_callback(
    campaign_id: int,
    payload: N8nCallbackBody,
    x_n8n_callback_secret: Optional[str] = Header(default=None),
):
    """Ingest preparation results from the optional n8n workflow.

    Authenticated by the configured shared secret. This CANNOT send email or
    mark a lead SENT — it only fills prepared subject/body and moves
    VALIDATED -> GENERATED, always through EmailCampaignService.
    """
    try:
        ok = await n8n_client.verify_callback_secret(x_n8n_callback_secret)
    except n8n_client.N8nNotConfigured as exc:
        raise HTTPException(503, f"n8n callback not configured: {exc}")
    if not ok:
        raise HTTPException(401, "Invalid n8n callback secret.")

    if payload.event not in ("preparation_complete", "preparation_partial"):
        raise HTTPException(422, f"Unsupported callback event: {payload.event!r}")

    try:
        result = await _svc.apply_preparation_callback(
            campaign_id, payload.run_id,
            [lead.model_dump() for lead in payload.leads],
        )
    except EmailCampaignError as exc:
        raise _handle(exc)
    return {"event": payload.event, **result}
