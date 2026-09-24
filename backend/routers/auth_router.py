from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import os

from .. import auth as _auth, edition as _edition
from ..rate_limit import limiter

router = APIRouter(prefix="/api/auth", tags=["auth"])


class UnlockRequest(BaseModel):
    password: str


class HandoffRequest(BaseModel):
    token: str


def _no_passwords_here():
    if _edition.is_client():
        raise HTTPException(403, "Sign in through your client link.")


class SetPasswordRequest(BaseModel):
    password:         str
    current_password: Optional[str] = None


@router.get("/status")
async def auth_status():
    if _edition.is_client():
        return {"password_set": True, "edition": "client", "sign_in": "/login"}
    return {"password_set": await _auth.is_password_set(), "edition": "owner"}


@router.post("/handoff")
@limiter.limit("20/minute")
async def handoff(request: Request, payload: HandoffRequest):
    """Client workspace: exchange the portal's one-time signed link for a session."""
    if not _edition.is_client():
        raise HTTPException(404, "Not found")
    try:
        data = _edition.verify_handoff(payload.token, os.getenv("HOM_HANDOFF_SECRET", ""),
                                       int(os.getenv("HOM_WORKSPACE_ID", "0") or 0))
    except (_edition.HandoffError, ValueError) as exc:
        raise HTTPException(401, str(exc))
    return {"token": _auth.issue_session(), "email": data.get("e")}


@router.post("/unlock")
@limiter.limit("10/minute")
async def unlock(request: Request, payload: UnlockRequest):
    _no_passwords_here()
    if not await _auth.verify_password(payload.password):
        raise HTTPException(401, "Incorrect password")
    return {"token": _auth.issue_session()}


@router.post("/set-password")
async def set_password(payload: SetPasswordRequest):
    """Sets the app password. If one is already set, the current password
    must be supplied to change it."""
    _no_passwords_here()
    if await _auth.is_password_set():
        if not payload.current_password or not await _auth.verify_password(payload.current_password):
            raise HTTPException(401, "Current password is incorrect")
    try:
        await _auth.set_password(payload.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "token": _auth.issue_session()}


@router.post("/clear-password")
async def clear_password(payload: UnlockRequest):
    """Disables the password lock — requires the current password."""
    _no_passwords_here()
    if not await _auth.verify_password(payload.password):
        raise HTTPException(401, "Incorrect password")
    await _auth.clear_password()
    return {"ok": True}
