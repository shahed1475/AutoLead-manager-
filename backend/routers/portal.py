"""
routers/portal.py — the client-facing API (/api/portal/*).

The front door of the client link: sign-in (emailed code), the client's
profile, and entering their own private workspace. The client link's web
server forwards /api/portal/* here and everything else to the signed-in
client's workspace — never to the owner's app. These routes are not behind the
owner's app password; they use the portal's own sessions (Authorization:
Bearer <portal token>), which never unlock owner endpoints.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel

from ..portal import config as portal_config, service, workspaces
from ..portal.service import PortalError
from ..rate_limit import limiter

router = APIRouter(prefix="/api/portal", tags=["portal"])

_NOT_READY = "Sign-in isn't open yet — the account owner is still setting it up. Please try again later."


def _raise(exc: PortalError):
    raise HTTPException(status_code=exc.status, detail=str(exc))


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


async def current_client(authorization: Optional[str] = Header(None)):
    client = await service.client_for_token(_bearer(authorization))
    if not client:
        raise HTTPException(status_code=401, detail="Please sign in.")
    return client


class CodeRequest(BaseModel):
    email: str


class VerifyRequest(BaseModel):
    email: str
    code: str


class ProfileUpdate(BaseModel):
    name: str
    company: Optional[str] = None




_CODE_EMAIL = """Hi,

Your sign-in code for the {name} client portal is:

    {code}

It works for 10 minutes. If you didn't ask for it, you can ignore this email.
"""


@router.post("/auth/request-code")
@limiter.limit("20/minute")
async def request_code(request: Request, payload: CodeRequest):
    try:
        email = service.normalize_email(payload.email)
    except PortalError as exc:
        _raise(exc)
    cfg = await portal_config.get_config()
    if not (await portal_config.sender_status(cfg))["ready"]:
        raise HTTPException(status_code=503, detail=_NOT_READY)
    try:
        code = await service.issue_code(email)
    except PortalError as exc:
        _raise(exc)
    sent = await portal_config.send_mail(email, f"Your {cfg['name']} sign-in code: {code}",
                                         _CODE_EMAIL.format(code=code, name=cfg["name"]), cfg)
    if not sent:
        await service.withdraw_code(email)   # never received: don't make them wait
        raise HTTPException(status_code=503, detail="We couldn't send the code right now. Please try again in a few minutes.")
    return {"ok": True, "email": email}


@router.get("/status")
async def portal_status():
    """Public: what the sign-in screen needs — can clients sign in right now,
    and the owner's portal name, welcome text and contact email."""
    cfg = await portal_config.get_config()
    ready = (await portal_config.sender_status(cfg))["ready"] and cfg["signup_mode"] != "closed"
    return {"sign_in_ready": ready, "signup_mode": cfg["signup_mode"], "name": cfg["name"],
            "welcome": cfg["welcome"], "contact_email": cfg["contact_email"]}


@router.post("/auth/verify")
@limiter.limit("30/minute")
async def verify(request: Request, payload: VerifyRequest):
    try:
        email = service.normalize_email(payload.email)
        return await service.verify_code(email, payload.code)
    except PortalError as exc:
        _raise(exc)


@router.post("/auth/sign-out")
async def sign_out(response: Response, authorization: Optional[str] = Header(None)):
    token = _bearer(authorization)
    if token:
        await service.end_session(token)
    response.delete_cookie(GATEWAY_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(client=Depends(current_client)):
    return service.public_client(client)


@router.put("/me")
async def update_me(payload: ProfileUpdate, client=Depends(current_client)):
    try:
        return await service.update_profile(client["id"], payload.name, payload.company)
    except PortalError as exc:
        _raise(exc)


# ── Your workspace ───────────────────────────────────────────────────────────

GATEWAY_COOKIE = "hom_ws"


def _secure(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


async def _workspace_view(client) -> dict:
    ws = await workspaces.workspace_for_client(client["id"])
    if not ws:
        ws = await workspaces.ensure_for_client(client)     # room may have opened up
    return workspaces.view(ws, waitlisted=ws is None)


@router.get("/workspace")
async def my_workspace(client=Depends(current_client)):
    """Where the client's own dashboard is: CREATING, RUNNING, WAITLIST, …"""
    v = await _workspace_view(client)
    return {"state": v["state"]}


@router.post("/workspace/enter")
@limiter.limit("30/minute")
async def enter_workspace(request: Request, response: Response, client=Depends(current_client)):
    """Open the client's own dashboard: a one-time signed sign-in link for
    THEIR workspace, plus the cookie that routes this browser to it."""
    ws = await workspaces.workspace_for_client(client["id"])
    if not ws or workspaces.view(ws)["state"] != "RUNNING":
        raise HTTPException(status_code=409, detail="Your dashboard isn't ready yet.")
    try:
        token = workspaces.handoff_token(ws, client["email"])
    except workspaces.WorkspaceError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    response.set_cookie(GATEWAY_COOKIE, workspaces.gateway_cookie_value(ws), max_age=30 * 86400,
                        path="/", httponly=True, samesite="lax", secure=_secure(request))
    return {"url": f"/?handoff={token}"}
