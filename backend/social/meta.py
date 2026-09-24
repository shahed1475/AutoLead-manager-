"""
meta.py — Facebook Pages + Instagram (Business/Creator) through Meta's Graph API.

Connect: paste one Meta access token (a System-user or long-lived user token
with pages_show_list, pages_manage_posts, pages_read_engagement,
instagram_basic, instagram_content_publish). HOM lists the Pages it can
manage — and each Page's linked Instagram account — and stores each Page's
own token, encrypted.

Publish:
  Facebook  text → POST /{page}/feed ; image → POST /{page}/photos (uploaded bytes)
  Instagram needs an image at a PUBLIC address: POST /{ig}/media {image_url,
            caption} → POST /{ig}/media_publish {creation_id}
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

GRAPH = "https://graph.facebook.com"
VERSION = "v23.0"
_TIMEOUT = httpx.Timeout(60.0, connect=5.0)


class MetaError(RuntimeError):
    pass


async def _call(method: str, path: str, token: str, **kw) -> Dict[str, Any]:
    params = dict(kw.pop("params", {}) or {})
    params["access_token"] = token
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.request(method, f"{GRAPH}/{VERSION}/{path.lstrip('/')}", params=params, **kw)
    except httpx.HTTPError as exc:
        raise MetaError(f"Can't reach Meta ({type(exc).__name__})") from exc
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400:
        err = (data.get("error") or {}) if isinstance(data, dict) else {}
        raise MetaError(err.get("message") or f"Meta returned {r.status_code}")
    return data


async def discover(token: str) -> List[Dict[str, Any]]:
    """Pages (and linked Instagram accounts) this token can post to:
    [{platform, external_id, name, token}]."""
    token = (token or "").strip()
    if not token:
        raise MetaError("Paste a Meta access token.")
    out: List[Dict[str, Any]] = []
    try:
        pages = (await _call("GET", "me/accounts", token, params={
            "fields": "id,name,access_token,instagram_business_account{id,username}", "limit": 100})).get("data") or []
    except MetaError:
        pages = []
    if not pages:
        # Maybe it's a Page token already.
        me = await _call("GET", "me", token, params={"fields": "id,name,instagram_business_account{id,username}"})
        pages = [{**me, "access_token": token}]
    for p in pages:
        ptoken = p.get("access_token") or token
        out.append({"platform": "facebook", "external_id": p["id"], "name": p.get("name"), "token": ptoken,
                    "extra": {"page_id": p["id"]}})
        ig = p.get("instagram_business_account")
        if ig and ig.get("id"):
            out.append({"platform": "instagram", "external_id": ig["id"],
                        "name": f"@{ig['username']}" if ig.get("username") else f"Instagram of {p.get('name')}",
                        "token": ptoken, "extra": {"page_id": p["id"], "username": ig.get("username")}})
    return out


async def check(platform: str, external_id: str, token: str) -> str:
    """Name of the account if the token still works; raises MetaError otherwise."""
    fields = "username" if platform == "instagram" else "name"
    d = await _call("GET", external_id, token, params={"fields": fields})
    return (f"@{d['username']}" if platform == "instagram" and d.get("username") else d.get("name")) or external_id


async def publish_facebook(page_id: str, token: str, text: str, image: Optional[bytes] = None,
                           filename: str = "image.jpg") -> Dict[str, Optional[str]]:
    if image:
        d = await _call("POST", f"{page_id}/photos", token, data={"caption": text},
                        files={"source": (filename, image)})
        post_id = d.get("post_id") or d.get("id")
    else:
        if not text.strip():
            raise MetaError("A Facebook post needs text or an image.")
        d = await _call("POST", f"{page_id}/feed", token, data={"message": text})
        post_id = d.get("id")
    return {"id": post_id, "url": f"https://www.facebook.com/{post_id}" if post_id else None}


async def publish_instagram(ig_id: str, token: str, text: str, image_url: Optional[str]) -> Dict[str, Optional[str]]:
    if not image_url:
        raise MetaError("Instagram posts need an image.")
    created = await _call("POST", f"{ig_id}/media", token, data={"image_url": image_url, "caption": text})
    creation_id = created.get("id")
    if not creation_id:
        raise MetaError("Instagram didn't accept the image.")
    done = await _call("POST", f"{ig_id}/media_publish", token, data={"creation_id": creation_id})
    media_id = done.get("id")
    link = None
    if media_id:
        try:
            link = (await _call("GET", media_id, token, params={"fields": "permalink"})).get("permalink")
        except MetaError:
            pass
    return {"id": media_id, "url": link}


# ── Inbox (Phase 2): comments + DMs, and replies ─────────────────────────────
# Needs pages_read_engagement / pages_manage_engagement (Facebook comments),
# pages_messaging (Messenger), instagram_manage_comments and
# instagram_manage_messages (Instagram). A missing permission only disables
# that part — HOM reports it on the account.

def _msg(kind: str, ext_id: str, thread: str, author: Dict[str, Any], text: str, when: str) -> Dict[str, Any]:
    return {"kind": kind, "external_id": ext_id, "thread_id": thread, "author_id": str(author.get("id") or ""),
            "author_name": author.get("name") or (f"@{author['username']}" if author.get("username") else ""),
            "text": text or "", "created_at": when}


async def facebook_comments(page_id: str, token: str) -> List[Dict[str, Any]]:
    d = await _call("GET", f"{page_id}/feed", token, params={
        "fields": "id,created_time,comments.limit(50){id,from{id,name},message,created_time}", "limit": 10})
    out = []
    for post in d.get("data") or []:
        for c in ((post.get("comments") or {}).get("data") or []):
            out.append(_msg("comment", c["id"], post["id"], c.get("from") or {}, c.get("message"), c.get("created_time")))
    return out


async def instagram_comments(ig_id: str, token: str) -> List[Dict[str, Any]]:
    d = await _call("GET", f"{ig_id}/media", token, params={
        "fields": "id,timestamp,comments.limit(50){id,text,timestamp,username,from{id,username}}", "limit": 10})
    out = []
    for media in d.get("data") or []:
        for c in ((media.get("comments") or {}).get("data") or []):
            author = c.get("from") or {"username": c.get("username")}
            out.append(_msg("comment", c["id"], media["id"], author, c.get("text"), c.get("timestamp")))
    return out


async def conversations(page_id: str, token: str, platform: str) -> List[Dict[str, Any]]:
    """Messenger (platform='messenger') or Instagram DMs (platform='instagram')."""
    d = await _call("GET", f"{page_id}/conversations", token, params={
        "platform": platform, "limit": 10,
        "fields": "id,updated_time,messages.limit(10){id,from{id,name,username},message,created_time}"})
    out = []
    for conv in d.get("data") or []:
        for m in ((conv.get("messages") or {}).get("data") or []):
            out.append(_msg("dm", m["id"], conv["id"], m.get("from") or {}, m.get("message"), m.get("created_time")))
    return out


async def reply_comment(platform: str, comment_id: str, token: str, text: str) -> Optional[str]:
    path = f"{comment_id}/replies" if platform == "instagram" else f"{comment_id}/comments"
    return (await _call("POST", path, token, data={"message": text})).get("id")


async def send_dm(page_id: str, token: str, recipient_id: str, text: str) -> Optional[str]:
    """Reply in Messenger / Instagram DMs (inside Meta's 24-hour window)."""
    d = await _call("POST", f"{page_id}/messages", token, json={
        "recipient": {"id": recipient_id}, "messaging_type": "RESPONSE", "message": {"text": text}})
    return d.get("message_id")
