"""
linkedin.py — LinkedIn posting through the official API.

Connect: an access token from your own LinkedIn developer app
(developer.linkedin.com → your app → Auth → token generator). Scopes:
  openid profile w_member_social      → post on your personal profile
  + w_organization_social (r_organization_admin) → post on a Company Page
    you administer (the app needs the "Community Management API" product)
Tokens last about 60 days; HOM shows when a token stops working.

Publish: POST /rest/posts (commentary in LinkedIn's "little text" format);
an image is uploaded first via /rest/images?action=initializeUpload.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

import httpx

API = "https://api.linkedin.com"
VERSION = "202506"           # LinkedIn-Version (YYYYMM); versions are supported ~1 year
_TIMEOUT = httpx.Timeout(60.0, connect=5.0)


class LinkedInError(RuntimeError):
    pass


def _headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "LinkedIn-Version": VERSION,
            "X-Restli-Protocol-Version": "2.0.0"}


async def _call(method: str, path: str, token: str, **kw) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.request(method, f"{API}/{path.lstrip('/')}", headers=_headers(token), **kw)
    except httpx.HTTPError as exc:
        raise LinkedInError(f"Can't reach LinkedIn ({type(exc).__name__})") from exc
    if r.status_code >= 400:
        try:
            msg = r.json().get("message")
        except ValueError:
            msg = None
        if r.status_code == 401:
            msg = "LinkedIn says the token is invalid or expired — create a new one."
        raise LinkedInError(msg or f"LinkedIn returned {r.status_code}")
    return r


async def identify(token: str, org_id: Optional[str] = None) -> Dict[str, str]:
    """Who the token posts as: {external_id, author, name}."""
    token = (token or "").strip()
    if not token:
        raise LinkedInError("Paste a LinkedIn access token.")
    if org_id:
        org_id = re.sub(r"\D", "", str(org_id))
        if not org_id:
            raise LinkedInError("The Company Page id is the number in its admin link (linkedin.com/company/<number>/admin).")
        name = f"Company page {org_id}"
        try:
            name = (await _call("GET", f"rest/organizations/{org_id}", token)).json().get("localizedName") or name
        except LinkedInError:
            pass                    # posting may still work with w_organization_social only
        return {"external_id": f"org-{org_id}", "author": f"urn:li:organization:{org_id}", "name": name}
    me = (await _call("GET", "v2/userinfo", token)).json()
    if not me.get("sub"):
        raise LinkedInError("This token can't read your profile — add the openid and profile scopes.")
    return {"external_id": me["sub"], "author": f"urn:li:person:{me['sub']}", "name": me.get("name") or "LinkedIn profile"}


_RESERVED = re.compile(r"([\\|{}@\[\]()<>*_~])")


def little_text(text: str) -> str:
    """Escape LinkedIn's reserved characters; keep #hashtags as real hashtags."""
    parts = re.split(r"(#\w+)", text or "")
    out = []
    for p in parts:
        if re.fullmatch(r"#\w+", p):
            out.append("{hashtag|\\#|" + p[1:] + "}")
        else:
            out.append(_RESERVED.sub(r"\\\1", p).replace("#", "\\#"))
    return "".join(out)


async def publish(author: str, token: str, text: str, image: Optional[bytes] = None) -> Dict[str, Optional[str]]:
    if not (text or "").strip() and not image:
        raise LinkedInError("A LinkedIn post needs text.")
    body: Dict[str, Any] = {
        "author": author, "commentary": little_text(text), "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
        "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False,
    }
    if image:
        init = (await _call("POST", "rest/images?action=initializeUpload", token,
                            json={"initializeUploadRequest": {"owner": author}})).json().get("value") or {}
        if not init.get("uploadUrl") or not init.get("image"):
            raise LinkedInError("LinkedIn didn't accept the image upload.")
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
                up = await c.put(init["uploadUrl"], content=image, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            raise LinkedInError(f"Image upload failed ({type(exc).__name__})") from exc
        if up.status_code >= 400:
            raise LinkedInError(f"Image upload failed ({up.status_code})")
        body["content"] = {"media": {"id": init["image"]}}
    r = await _call("POST", "rest/posts", token, json=body)
    urn = r.headers.get("x-restli-id") or r.headers.get("x-linkedin-id")
    return {"id": urn, "url": f"https://www.linkedin.com/feed/update/{urn}/" if urn else None}
