from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import auth as _auth
from ..rate_limit import limiter

router = APIRouter(prefix="/api/auth", tags=["auth"])


class UnlockRequest(BaseModel):
    password: str


class SetPasswordRequest(BaseModel):
    password:         str
    current_password: Optional[str] = None


@router.get("/status")
async def auth_status():
    return {"password_set": await _auth.is_password_set()}


@router.post("/unlock")
@limiter.limit("10/minute")
async def unlock(request: Request, payload: UnlockRequest):
    if not await _auth.verify_password(payload.password):
        raise HTTPException(401, "Incorrect password")
    return {"token": _auth.issue_session()}


@router.post("/set-password")
async def set_password(payload: SetPasswordRequest):
    """Sets the app password. If one is already set, the current password
    must be supplied to change it."""
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
    if not await _auth.verify_password(payload.password):
        raise HTTPException(401, "Incorrect password")
    await _auth.clear_password()
    return {"ok": True}
