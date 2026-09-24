"""
xapi.py — X (Twitter) posting through the official API v2.

Connect: from your X developer app (developer.x.com → your app → "User
authentication settings", OAuth 2.0) paste a user access token with the
scopes tweet.read tweet.write users.read media.write offline.access.
X user tokens expire after ~2 hours: also paste the refresh token and the
app's Client ID (and Client secret for a confidential app) — HOM then renews
the token by itself. Posting needs an X API plan that allows writes.
"""
from __future__ import annotations

import base64
from typing import Any, Callable, Dict, Optional

import httpx

API = "https://api.x.com/2"
_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
LIMIT = 280


class XError(RuntimeError):
    pass


class Expired(XError):
    pass


async def _call(method: str, path: str, token: str, **kw) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.request(method, f"{API}/{path.lstrip('/')}", headers={"Authorization": f"Bearer {token}"}, **kw)
    except httpx.HTTPError as exc:
        raise XError(f"Can't reach X ({type(exc).__name__})") from exc
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code == 401:
        raise Expired("X says the token is invalid or expired.")
    if r.status_code >= 400:
        msg = data.get("detail") or data.get("title") or ((data.get("errors") or [{}])[0].get("message"))
        raise XError(msg or f"X returned {r.status_code}")
    return data


async def refresh(creds: Dict[str, str]) -> Dict[str, str]:
    """New access (+refresh) token from the refresh token."""
    if not creds.get("refresh_token") or not creds.get("client_id"):
        raise XError("The X token expired — paste a new one (or add the refresh token and Client ID so HOM renews it).")
    headers = {}
    data = {"grant_type": "refresh_token", "refresh_token": creds["refresh_token"], "client_id": creds["client_id"]}
    if creds.get("client_secret"):
        basic = base64.b64encode(f"{creds['client_id']}:{creds['client_secret']}".encode()).decode()
        headers["Authorization"] = f"Basic {basic}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(f"{API}/oauth2/token", data=data, headers=headers)
    except httpx.HTTPError as exc:
        raise XError(f"Can't reach X ({type(exc).__name__})") from exc
    if r.status_code >= 400 or not r.json().get("access_token"):
        raise XError("X didn't renew the token — connect the account again.")
    d = r.json()
    return {**creds, "access_token": d["access_token"], "refresh_token": d.get("refresh_token") or creds["refresh_token"]}


async def with_token(creds: Dict[str, str], fn: Callable, save: Callable) -> Any:
    """Run fn(token); on an expired token renew it once (and save), then retry."""
    try:
        return await fn(creds["access_token"])
    except Expired:
        creds = await refresh(creds)
        await save(creds)
        return await fn(creds["access_token"])


async def me(token: str) -> Dict[str, str]:
    d = (await _call("GET", "users/me", token)).get("data") or {}
    if not d.get("id"):
        raise XError("X didn't return your account.")
    return {"external_id": d["id"], "name": f"@{d.get('username')}" if d.get("username") else d.get("name") or "X account"}


async def publish(token: str, text: str, image: Optional[bytes] = None, filename: str = "image.jpg") -> Dict[str, Optional[str]]:
    text = (text or "").strip()
    if not text and not image:
        raise XError("An X post needs text.")
    if len(text) > LIMIT:
        raise XError(f"X posts are limited to {LIMIT} characters.")
    body: Dict[str, Any] = {"text": text}
    if image:
        ctype = "image/png" if filename.endswith(".png") else "image/jpeg"
        up = await _call("POST", "media/upload", token, files={"media": (filename, image, ctype)},
                         data={"media_category": "tweet_image", "media_type": ctype})
        media_id = (up.get("data") or {}).get("id") or up.get("media_id_string")
        if not media_id:
            raise XError("X didn't accept the image.")
        body["media"] = {"media_ids": [str(media_id)]}
    d = (await _call("POST", "tweets", token, json=body)).get("data") or {}
    tid = d.get("id")
    return {"id": tid, "url": f"https://x.com/i/web/status/{tid}" if tid else None}
