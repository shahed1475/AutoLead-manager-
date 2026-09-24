"""
routers/social.py — Social media automation (owner dashboard and client workspaces).

  /api/social/*            behind the sign-in: accounts (Meta, LinkedIn,
                           X — API, or on the owner's dashboard also a
                           browser with a live sign-in window),
                           AI compose, image upload, posts, schedule /
                           publish, the comment/DM inbox, activity
  /api/social/media/<file> public, unguessable: post images for Instagram
  /api/social/hooks/tick   HOM's n8n, every minute (X-HOM-Secret); client
                           workspaces tick themselves (service.tick_loop)
"""
import hmac
from typing import Dict, List, Optional

from fastapi import APIRouter, File, Header, HTTPException, Response, UploadFile
from pydantic import BaseModel

from .. import edition
from ..social import browser, inbox, meta, service
from ..social.service import PUBLISH_ERRORS, SocialError
from ..whatsapp import engine as wa_engine

router = APIRouter(prefix="/api/social", tags=["social"])
public = APIRouter(prefix="/api/social", tags=["social-public"])


def _err(exc: Exception, status: int = 400):
    raise HTTPException(status, str(exc))


def _owner_only_browser() -> None:
    """The browser transport is owner-only: clients use their own API keys."""
    if edition.is_client():
        raise HTTPException(403, service.BROWSER_OWNER_ONLY)


class MetaToken(BaseModel):
    token: str
    pick: Optional[List[str]] = None


class LinkedInToken(BaseModel):
    token: str
    org_id: Optional[str] = None


class XToken(BaseModel):
    token: str
    refresh_token: Optional[str] = ""
    client_id: Optional[str] = ""
    client_secret: Optional[str] = ""


class BrowserAccount(BaseModel):
    platform: str
    name: Optional[str] = ""
    company_id: Optional[str] = None


class BrowserAction(BaseModel):
    type: str
    x: Optional[float] = None
    y: Optional[float] = None
    text: Optional[str] = None
    key: Optional[str] = None
    dy: Optional[float] = None
    url: Optional[str] = None


class ReplyIn(BaseModel):
    text: str


class MarkIn(BaseModel):
    status: str


class InboxSettings(BaseModel):
    social_auto_reply: Optional[bool] = None
    social_reply_comments: Optional[bool] = None
    social_reply_per_person: Optional[int] = None


class Compose(BaseModel):
    idea: str
    platforms: List[str] = ["facebook", "instagram"]
    tone: Optional[str] = None


class PostIn(BaseModel):
    text: str = ""
    captions: Dict[str, str] = {}
    account_ids: List[int] = []
    media_name: Optional[str] = None


class When(BaseModel):
    when: Optional[str] = None      # ISO time; empty = publish now


@router.get("/accounts")
async def list_accounts():
    return {"accounts": await service.accounts()}


@router.post("/accounts/meta/discover")
async def discover(payload: MetaToken):
    try:
        return {"found": await service.discover_meta(payload.token)}
    except (meta.MetaError, SocialError) as exc:
        _err(exc)


@router.post("/accounts/meta")
async def connect(payload: MetaToken):
    try:
        return {"accounts": await service.connect_meta(payload.token, payload.pick)}
    except (meta.MetaError, SocialError) as exc:
        _err(exc)


@router.post("/accounts/linkedin")
async def connect_linkedin(payload: LinkedInToken):
    try:
        return {"accounts": await service.connect_linkedin(payload.token, payload.org_id)}
    except PUBLISH_ERRORS as exc:
        _err(exc)


@router.post("/accounts/x")
async def connect_x(payload: XToken):
    try:
        return {"accounts": await service.connect_x(payload.token, payload.refresh_token or "",
                                                    payload.client_id or "", payload.client_secret or "")}
    except (SocialError, *PUBLISH_ERRORS) as exc:
        _err(exc)


@router.post("/accounts/browser", status_code=201)
async def add_browser_account(payload: BrowserAccount):
    _owner_only_browser()
    try:
        return await service.connect_browser(payload.platform, payload.name or "", payload.company_id)
    except SocialError as exc:
        _err(exc)


@router.post("/accounts/{account_id}/browser/open")
async def browser_open(account_id: int):
    _owner_only_browser()
    try:
        return await service.browser_login(account_id)
    except (SocialError, browser.BrowserError) as exc:
        _err(exc)


@router.get("/accounts/{account_id}/browser/screen")
async def browser_screen(account_id: int):
    _owner_only_browser()
    try:
        return Response(await browser.screenshot(account_id), media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})
    except browser.BrowserError as exc:
        _err(exc, 409)


@router.post("/accounts/{account_id}/browser/act")
async def browser_act(account_id: int, payload: BrowserAction):
    _owner_only_browser()
    try:
        a = await service._account(account_id)
        return await browser.act(account_id, a["platform"], payload.model_dump())
    except (SocialError, browser.BrowserError) as exc:
        _err(exc)
    except Exception as exc:  # noqa: BLE001 — a page that didn't load etc.
        _err(RuntimeError(f"The browser couldn't do that: {str(exc)[:160]}"))


@router.post("/accounts/{account_id}/browser/done")
async def browser_done(account_id: int):
    _owner_only_browser()
    try:
        out = await service.browser_done(account_id)
        if out["status"] == "connected":
            await browser.close(account_id)      # signed in: cookies are saved in the profile
        else:
            await service.browser_login(account_id)   # not yet: back to the sign-in page to finish
        return out
    except (SocialError, browser.BrowserError) as exc:
        _err(exc)


@router.post("/accounts/{account_id}/check")
async def check(account_id: int):
    try:
        return await service.check_account(account_id)
    except SocialError as exc:
        _err(exc, 404)


@router.delete("/accounts/{account_id}", status_code=204)
async def remove(account_id: int):
    try:
        await service.remove_account(account_id)
    except SocialError as exc:
        _err(exc, 404)


@router.post("/compose")
async def compose(payload: Compose):
    try:
        return {"captions": await service.compose(payload.idea, payload.platforms, payload.tone or "")}
    except SocialError as exc:
        _err(exc)


@router.post("/media")
async def upload_media(file: UploadFile = File(...)):
    data = await file.read(service.MAX_MEDIA_BYTES + 1)
    try:
        return {"media_name": service.save_media(data, file.content_type or "")}
    except SocialError as exc:
        _err(exc)


@router.get("/posts")
async def posts():
    return {"posts": await service.list_posts(), "public_link": bool(service.public_base())}


@router.post("/posts", status_code=201)
async def create(payload: PostIn):
    try:
        return await service.save_post(None, payload.text, payload.captions, payload.account_ids, payload.media_name)
    except SocialError as exc:
        _err(exc)


@router.put("/posts/{post_id}")
async def update(post_id: int, payload: PostIn):
    try:
        return await service.save_post(post_id, payload.text, payload.captions, payload.account_ids, payload.media_name)
    except SocialError as exc:
        _err(exc)


@router.post("/posts/{post_id}/schedule")
async def schedule(post_id: int, payload: When):
    try:
        return await service.schedule(post_id, payload.when)
    except SocialError as exc:
        _err(exc)


@router.post("/posts/{post_id}/publish")
async def publish_now(post_id: int):
    """Publish right away (instead of waiting for the next minute)."""
    try:
        await service.schedule(post_id, None)
        return await service.publish_post(post_id)
    except SocialError as exc:
        _err(exc)


@router.post("/posts/{post_id}/unschedule")
async def unschedule(post_id: int):
    try:
        return await service.unschedule(post_id)
    except SocialError as exc:
        _err(exc)


@router.delete("/posts/{post_id}", status_code=204)
async def delete(post_id: int):
    try:
        await service.delete_post(post_id)
    except SocialError as exc:
        _err(exc)


@router.get("/inbox")
async def inbox_list():
    return {"threads": await inbox.threads(), "summary": await inbox.summary(), "settings": await inbox.get_settings()}


@router.put("/inbox/settings")
async def inbox_settings(payload: InboxSettings):
    try:
        return await inbox.save_settings({k: v for k, v in payload.model_dump().items() if v is not None})
    except SocialError as exc:
        _err(exc)


@router.post("/inbox/sync")
async def inbox_sync():
    """Check for new comments and messages now (and answer them)."""
    return await inbox.sync_all(force=True)


@router.get("/inbox/{message_id}")
async def inbox_thread(message_id: int):
    try:
        return {"messages": await inbox.thread(message_id)}
    except SocialError as exc:
        _err(exc, 404)


@router.post("/inbox/{message_id}/draft")
async def inbox_draft(message_id: int):
    try:
        text = await inbox.draft(message_id)
    except SocialError as exc:
        _err(exc, 404)
    except Exception:  # noqa: BLE001 — AI offline / too slow
        _err(RuntimeError("The AI couldn't write a reply right now."))
    if not text:
        _err(RuntimeError("The AI couldn't write a good reply — write it yourself."))
    return {"text": text}


@router.post("/inbox/{message_id}/reply")
async def inbox_reply(message_id: int, payload: ReplyIn):
    """You answer (your approval = pressing Send)."""
    try:
        return await inbox.send_reply(message_id, payload.text, "manual")
    except (SocialError, meta.MetaError) as exc:
        _err(exc)


@router.post("/inbox/{message_id}/mark")
async def inbox_mark(message_id: int, payload: MarkIn):
    try:
        return await inbox.mark(message_id, payload.status)
    except SocialError as exc:
        _err(exc)


@router.get("/activity")
async def activity():
    return {"activity": await service.activity()}


@public.get("/media/{name}")
async def media(name: str):
    p = service.media_path(name)
    if not p:
        raise HTTPException(404, "Not found")
    return Response(p.read_bytes(), media_type="image/png" if name.endswith(".png") else "image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400"})


@public.post("/hooks/tick")
async def hook_tick(x_hom_secret: Optional[str] = Header(None)):
    want = wa_engine.secrets().get("HOM_WA_SECRET", "")
    if not want or not x_hom_secret or not hmac.compare_digest(x_hom_secret, want):
        raise HTTPException(401, "Unauthorized")
    return await service.tick()
