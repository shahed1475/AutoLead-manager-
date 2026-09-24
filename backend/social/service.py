"""
service.py — Social media automation (owner dashboard first).

  accounts  Facebook Pages / Instagram (Meta API), LinkedIn (API or browser),
            X (API or browser) — tokens encrypted; browser accounts keep only
            the site's cookies in their own profile (you sign in yourself)
  compose   the local AI writes a post from the Company DNA, per platform
  posts     drafts → you press Publish now / Schedule (that's the approval)
  publish   publish_target() is THE one way a post goes out (per platform)
  tick      HOM's n8n calls it every minute (a client workspace runs its own
            loop, tick_loop): publishes posts that are due and runs the
            comment/DM inbox (inbox.py) in the background

Client workspaces connect with their OWN API keys only — the browser
transport (posting through a signed-in website) is owner-only, because an
automated website session can get the client's account restricted.

Instagram needs a public image address: post images are served at an
unguessable URL through the dashboard link, or, in a client workspace,
through the client link's /social-media/<port>/ route (media_url()).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import database as db
from .. import edition
from ..secrets_crypto import decrypt, encrypt
from . import browser, linkedin, meta, xapi

logger = logging.getLogger(__name__)

PLATFORMS = ("facebook", "instagram", "linkedin", "x")
BROWSER_PLATFORMS = ("linkedin", "x")
PUBLISH_ERRORS = (meta.MetaError, linkedin.LinkedInError, xapi.XError, browser.BrowserError)
MEDIA_DIR = Path(__file__).resolve().parents[1] / "data" / "social_media"
MEDIA_TYPES = {"image/jpeg": "jpg", "image/png": "png"}
MAX_MEDIA_BYTES = 8 * 1024 * 1024
_MEDIA_NAME = re.compile(r"^[a-f0-9]{32}\.(jpg|png)$")
BROWSER_OWNER_ONLY = "Client workspaces connect with the platform's API (your own keys) — browser posting isn't available."
LIMITS = {"facebook": 5000, "instagram": 2200, "linkedin": 3000, "x": xapi.LIMIT}


class SocialError(ValueError):
    pass


def _utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(dt: datetime) -> str:
    return dt.isoformat(sep=" ", timespec="seconds")


async def log(kind: str, text: str) -> None:
    await db.portal_execute("INSERT INTO social_activity (kind, text) VALUES (?, ?)", kind, text[:500])
    await db.portal_execute("DELETE FROM social_activity WHERE id <= (SELECT max(id) - 1000 FROM social_activity)")


async def activity(limit: int = 60) -> List[Dict[str, Any]]:
    return await db.portal_fetch("SELECT * FROM social_activity ORDER BY id DESC LIMIT ?", max(1, min(limit, 200)))


# ── Accounts ─────────────────────────────────────────────────────────────────

def _extra(a: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return json.loads(a.get("extra") or "{}") or {}
    except ValueError:
        return {}


def _public_account(a: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: a.get(k) for k in ("id", "platform", "transport", "external_id", "name", "status", "last_error",
                                 "created_at", "inbox_synced_at", "inbox_error")}
    ex = _extra(a)
    out["target"] = "company" if ex.get("company_id") or str(a.get("external_id") or "").startswith("org-") else "profile"
    out["company_id"] = ex.get("company_id")
    out["inbox"] = a.get("platform") in ("facebook", "instagram") and a.get("transport") == "api"
    return out


async def accounts() -> List[Dict[str, Any]]:
    return [_public_account(a) for a in await db.portal_fetch("SELECT * FROM social_accounts ORDER BY platform, name")]


async def discover_meta(token: str) -> List[Dict[str, Any]]:
    found = await meta.discover(token)
    return [{k: f[k] for k in ("platform", "external_id", "name")} for f in found]


async def connect_meta(token: str, pick: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Save the chosen Pages / Instagram accounts ("platform:id") with their tokens."""
    found = await meta.discover(token)
    chosen = [f for f in found if not pick or f"{f['platform']}:{f['external_id']}" in pick]
    if not chosen:
        raise SocialError("No Facebook Page or Instagram account was chosen.")
    for f in chosen:
        await db.portal_execute(
            """INSERT INTO social_accounts (platform, transport, external_id, name, token_enc, status, extra)
               VALUES (?, 'api', ?, ?, ?, 'connected', ?)
               ON CONFLICT (platform, external_id) DO UPDATE SET name = excluded.name, extra = excluded.extra,
                 token_enc = excluded.token_enc, status = 'connected', last_error = NULL""",
            f["platform"], f["external_id"], f["name"], encrypt(f["token"]), json.dumps(f.get("extra") or {}))
    await log("info", "Connected " + ", ".join(f"{f['name']} ({f['platform'].title()})" for f in chosen) + ".")
    return await accounts()


async def _upsert(platform: str, transport: str, external_id: str, name: str, secret: Optional[str],
                  extra: Dict[str, Any], status: str = "connected") -> None:
    await db.portal_execute(
        """INSERT INTO social_accounts (platform, transport, external_id, name, token_enc, status, extra)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (platform, external_id) DO UPDATE SET name = excluded.name, extra = excluded.extra,
             transport = excluded.transport, token_enc = excluded.token_enc, status = excluded.status, last_error = NULL""",
        platform, transport, external_id, name, encrypt(secret) if secret else None, status, json.dumps(extra))


async def connect_linkedin(token: str, org_id: Optional[str] = None) -> List[Dict[str, Any]]:
    who = await linkedin.identify(token, org_id)
    await _upsert("linkedin", "api", who["external_id"], who["name"], token.strip(), {"author": who["author"]})
    await log("info", f"Connected LinkedIn {who['name']} (API).")
    return await accounts()


async def connect_x(token: str, refresh_token: str = "", client_id: str = "", client_secret: str = "") -> List[Dict[str, Any]]:
    creds = {"access_token": (token or "").strip(), "refresh_token": (refresh_token or "").strip(),
             "client_id": (client_id or "").strip(), "client_secret": (client_secret or "").strip()}
    if not creds["access_token"]:
        raise SocialError("Paste your X access token.")
    try:
        who = await xapi.me(creds["access_token"])
    except xapi.Expired:
        creds = await xapi.refresh(creds)
        who = await xapi.me(creds["access_token"])
    await _upsert("x", "api", who["external_id"], who["name"], json.dumps(creds), {})
    await log("info", f"Connected X {who['name']} (API).")
    return await accounts()


async def connect_browser(platform: str, name: str, company_id: Optional[str] = None) -> Dict[str, Any]:
    """A browser account: HOM opens the site and you sign in yourself."""
    if edition.is_client():
        raise SocialError(BROWSER_OWNER_ONLY)
    if platform not in BROWSER_PLATFORMS:
        raise SocialError("Browser posting is available for LinkedIn and X.")
    name = (name or "").strip()[:80] or f"My {platform.title() if platform != 'x' else 'X'} account"
    extra: Dict[str, Any] = {}
    if company_id:
        cid = re.sub(r"\D", "", str(company_id))
        if not cid or platform != "linkedin":
            raise SocialError("The Company Page id is the number in its admin link (linkedin.com/company/<number>/admin).")
        extra["company_id"] = cid
    ext = f"browser-{secrets.token_hex(6)}"
    await _upsert(platform, "browser", ext, name, None, extra, status="needs_login")
    a = await db.portal_fetchrow("SELECT * FROM social_accounts WHERE platform = ? AND external_id = ?", platform, ext)
    await log("info", f"Added {name} ({'X' if platform == 'x' else platform.title()}, browser) — sign in to finish.")
    return _public_account(a)


async def browser_login(account_id: int) -> Dict[str, Any]:
    a = await _account(account_id)
    if a["transport"] != "browser":
        raise SocialError("This account uses the API.")
    if edition.is_client():
        raise SocialError(BROWSER_OWNER_ONLY)
    return await browser.open_login(account_id, a["platform"])


async def browser_done(account_id: int) -> Dict[str, Any]:
    """You finished signing in: check the account really is signed in."""
    a = await _account(account_id)
    ok = await browser.logged_in(account_id, a["platform"])
    await db.portal_execute("UPDATE social_accounts SET status = ?, last_error = ? WHERE id = ?",
                            "connected" if ok else "needs_login",
                            None if ok else "Not signed in yet — finish signing in in the window.", account_id)
    if ok:
        await log("info", f"{a['name']} is signed in (browser).")
    return _public_account(await _account(account_id))


def x_creds(a: Dict[str, Any]) -> Dict[str, str]:
    raw = decrypt(a["token_enc"]) or ""
    try:
        return json.loads(raw)
    except ValueError:
        return {"access_token": raw}


async def _save_x_creds(account_id: int, creds: Dict[str, str]) -> None:
    await db.portal_execute("UPDATE social_accounts SET token_enc = ? WHERE id = ?", encrypt(json.dumps(creds)), account_id)


async def check_account(account_id: int) -> Dict[str, Any]:
    a = await _account(account_id)
    if a["transport"] == "browser":
        return await browser_done(account_id)
    try:
        if a["platform"] == "linkedin":
            org = a["external_id"][4:] if a["external_id"].startswith("org-") else None
            name = (await linkedin.identify(decrypt(a["token_enc"]) or "", org))["name"]
        elif a["platform"] == "x":
            name = (await xapi.with_token(x_creds(a), xapi.me, lambda c: _save_x_creds(account_id, c)))["name"]
        else:
            name = await meta.check(a["platform"], a["external_id"], decrypt(a["token_enc"]) or "")
        await db.portal_execute("UPDATE social_accounts SET status = 'connected', last_error = NULL, name = ? WHERE id = ?",
                                name, account_id)
    except PUBLISH_ERRORS as exc:
        await db.portal_execute("UPDATE social_accounts SET status = 'error', last_error = ? WHERE id = ?",
                                str(exc)[:300], account_id)
    return _public_account(await _account(account_id))


async def remove_account(account_id: int) -> None:
    a = await _account(account_id)
    if a["transport"] == "browser":
        await browser.forget(account_id)
    await db.portal_execute("DELETE FROM social_accounts WHERE id = ?", account_id)
    await log("info", f"Disconnected {a['name']} ({a['platform'].title()}).")


async def _account(account_id: int) -> Dict[str, Any]:
    a = await db.portal_fetchrow("SELECT * FROM social_accounts WHERE id = ?", account_id)
    if not a:
        raise SocialError("Account not found.")
    return a


# ── Media ────────────────────────────────────────────────────────────────────

def save_media(data: bytes, content_type: str) -> str:
    ext = MEDIA_TYPES.get((content_type or "").split(";")[0].strip().lower())
    if not ext:
        raise SocialError("Use a JPG or PNG image.")
    if not data or len(data) > MAX_MEDIA_BYTES:
        raise SocialError("The image must be under 8 MB.")
    if not (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"):
        raise SocialError("That file isn't a JPG or PNG image.")
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{secrets.token_hex(16)}.{ext}"
    (MEDIA_DIR / name).write_bytes(data)
    return name


def media_path(name: str) -> Optional[Path]:
    if not name or not _MEDIA_NAME.match(name):
        return None
    p = MEDIA_DIR / name
    return p if p.exists() else None


CLIENT_LINK_FILE = Path(__file__).resolve().parents[1] / "data" / "client-link.txt"


def public_base() -> Optional[str]:
    """The public link Instagram fetches post images from: the dashboard link
    (start.sh writes it), or in a client workspace the client link (the
    supervisor copies it into the workspace's data folder)."""
    if edition.is_client():
        port = re.sub(r"\D", "", os.getenv("HOM_WORKSPACE_PORT", ""))
        try:
            link = CLIENT_LINK_FILE.read_text().strip()
        except OSError:
            return None
        return f"{link.rstrip('/')}/social-media/{port}" if port and link.startswith("https://") else None
    for p in (Path("/app/.run/share-link.txt"), Path(__file__).resolve().parents[2] / ".run" / "share-link.txt"):
        try:
            link = p.read_text().strip()
        except OSError:
            continue
        if link.startswith("https://"):
            return link.rstrip("/")
    return None


def media_url(name: str) -> Optional[str]:
    base = public_base()
    if not base or not media_path(name):
        return None
    return f"{base}/{name}" if edition.is_client() else f"{base}/api/social/media/{name}"


# ── Compose (AI) ─────────────────────────────────────────────────────────────

STYLES = {
    "facebook": "Facebook: 60–150 words, friendly and clear, 1–2 short paragraphs, a clear call to action, "
                "at most 3 hashtags at the end.",
    "instagram": "Instagram: a strong first line, 40–120 words, short lines, 2–4 fitting emojis, a call to action, "
                 "then 6–10 relevant hashtags on the last line.",
    "linkedin": "LinkedIn: professional and insightful, 80–200 words, a hook in the first line, short paragraphs, "
                "one practical takeaway, a soft call to action, 3–5 hashtags at the end, no more than one emoji.",
    "x": "X (Twitter): ONE post of at most 260 characters in total, punchy, one clear point, "
         "at most 2 hashtags. Count carefully — it must be under 260 characters.",
}

async def compose(idea: str, platforms: List[str], tone: str = "") -> Dict[str, str]:
    """Write one post per platform from the idea + Company DNA."""
    from .. import ai_brain
    from ..whatsapp.service import offer_digest
    idea = (idea or "").strip()[:1000]
    if not idea:
        raise SocialError("Write what the post should be about.")
    platforms = [p for p in platforms if p in PLATFORMS] or ["facebook"]
    dna = ai_brain._load_company_dna().strip()[:3000]
    cfg = await ai_brain._ollama_cfg()
    out: Dict[str, str] = {}
    for platform in platforms:
        style = STYLES[platform]
        prompt = f"""You write social media posts for the business described below, in its voice.
Use ONLY facts from the profile and the offer list — never invent prices, results, clients or statistics.
{style}
{f'Tone: {tone}.' if tone else ''}
Write in the language the post idea is written in. Write ONLY the post text — no title, no quotes, no notes.

COMPANY PROFILE:
{dna}

WHAT WE OFFER:
{offer_digest()}

POST IDEA: {idea}

Post:"""
        raw = await ai_brain._call_llm_raw(prompt, cfg, temperature=0.7, num_predict=500)
        text = ai_brain._strip_thinking(raw or "").strip().strip('"').strip()
        text = re.sub(r"^(post|caption)\s*:\s*", "", text, flags=re.I).strip()
        if not text:
            raise SocialError("The AI couldn't write this post — try rephrasing the idea.")
        if platform == "x" and len(text) > xapi.LIMIT:
            cut = text[:xapi.LIMIT]
            text = cut[:max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? ")) + 1] or cut[:cut.rfind(" ")]
        out[platform] = text[:LIMITS[platform]].strip()
    return out


# ── Posts ────────────────────────────────────────────────────────────────────

async def _post_view(p: Dict[str, Any]) -> Dict[str, Any]:
    targets = await db.portal_fetch(
        """SELECT t.*, a.platform, a.name AS account_name FROM social_post_targets t
           JOIN social_accounts a ON a.id = t.account_id WHERE t.post_id = ? ORDER BY a.platform""", p["id"])
    return {**p, "captions": json.loads(p["captions"] or "{}"), "targets": targets}


async def get_post(post_id: int) -> Dict[str, Any]:
    p = await db.portal_fetchrow("SELECT * FROM social_posts WHERE id = ?", post_id)
    if not p:
        raise SocialError("Post not found.")
    return await _post_view(p)


async def list_posts(limit: int = 100) -> List[Dict[str, Any]]:
    rows = await db.portal_fetch(
        """SELECT * FROM social_posts ORDER BY CASE status WHEN 'SCHEDULED' THEN 0 WHEN 'DRAFT' THEN 1 ELSE 2 END,
           COALESCE(scheduled_at, published_at, created_at) DESC LIMIT ?""", limit)
    return [await _post_view(p) for p in rows]


async def save_post(post_id: Optional[int], text: str, captions: Dict[str, str], account_ids: List[int],
                    media_name: Optional[str]) -> Dict[str, Any]:
    captions = {k: (v or "").strip()[:LIMITS[k]] for k, v in (captions or {}).items() if k in PLATFORMS}
    text = (text or "").strip()[:5000]
    if media_name and not media_path(media_name):
        raise SocialError("The image is missing — upload it again.")
    accts = [await _account(int(a)) for a in dict.fromkeys(account_ids or [])]
    if not accts:
        raise SocialError("Choose at least one account to post to.")
    for a in accts:
        body = captions.get(a["platform"]) or text
        if a["platform"] == "instagram" and not media_name:
            raise SocialError("Instagram posts need an image.")
        if not body and not media_name:
            raise SocialError("Write the post text.")
        if a["platform"] == "x" and len(body) > xapi.LIMIT:
            raise SocialError(f"The X version is {len(body)} characters — X allows {xapi.LIMIT}. Shorten it.")
    if post_id:
        p = await get_post(post_id)
        if p["status"] not in ("DRAFT", "SCHEDULED", "FAILED"):
            raise SocialError("This post was already published.")
        await db.portal_execute("UPDATE social_posts SET text = ?, captions = ?, media_name = ? WHERE id = ?",
                                text, json.dumps(captions), media_name, post_id)
        await db.portal_execute("DELETE FROM social_post_targets WHERE post_id = ?", post_id)
    else:
        post_id = await db.portal_insert(
            "INSERT INTO social_posts (text, captions, media_name) VALUES (?, ?, ?) RETURNING id",
            text, json.dumps(captions), media_name)
    for a in accts:
        await db.portal_execute("INSERT INTO social_post_targets (post_id, account_id) VALUES (?, ?)", post_id, a["id"])
    return await get_post(post_id)


async def schedule(post_id: int, when_utc: Optional[str]) -> Dict[str, Any]:
    """Schedule (your approval). when_utc=None → publish on the next minute."""
    p = await get_post(post_id)
    if p["status"] not in ("DRAFT", "SCHEDULED", "FAILED", "PARTIAL"):
        raise SocialError("This post can't be scheduled now.")
    if when_utc:
        try:
            when = datetime.fromisoformat(when_utc.replace("Z", "+00:00"))
        except ValueError:
            raise SocialError("Pick a valid date and time.")
        when = when.astimezone(timezone.utc).replace(tzinfo=None) if when.tzinfo else when
    else:
        when = _utc()
    await db.portal_execute("UPDATE social_posts SET status = 'SCHEDULED', scheduled_at = ? WHERE id = ?", _iso(when), post_id)
    await db.portal_execute("UPDATE social_post_targets SET status = 'PENDING', error = NULL WHERE post_id = ? AND status = 'FAILED'", post_id)
    await log("scheduled", f"Post #{post_id} scheduled for {_iso(when)} UTC." if when_utc else f"Post #{post_id} will publish now.")
    return await get_post(post_id)


async def unschedule(post_id: int) -> Dict[str, Any]:
    p = await get_post(post_id)
    if p["status"] != "SCHEDULED":
        raise SocialError("Only scheduled posts can be moved back to drafts.")
    await db.portal_execute("UPDATE social_posts SET status = 'DRAFT', scheduled_at = NULL WHERE id = ?", post_id)
    return await get_post(post_id)


async def delete_post(post_id: int) -> None:
    p = await get_post(post_id)
    if p["status"] in ("PUBLISHING",):
        raise SocialError("It's being published right now.")
    await db.portal_execute("DELETE FROM social_posts WHERE id = ?", post_id)


# ── Publishing ───────────────────────────────────────────────────────────────

async def publish_target(post: Dict[str, Any], target: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """THE one way a post goes out to a platform."""
    a = await _account(target["account_id"])
    text = (post["captions"] or {}).get(a["platform"]) or post["text"]
    img = media_path(post["media_name"]) if post.get("media_name") else None
    if post.get("media_name") and not img:
        raise SocialError("The post's image is missing.")
    if a["transport"] == "browser":
        if edition.is_client():
            raise browser.BrowserError(BROWSER_OWNER_ONLY)
        if a["status"] != "connected":
            raise browser.NeedsLogin("Sign in to this account first (Social → Accounts → Sign in).")
        today = await db.portal_fetchrow(
            "SELECT count(*) AS n FROM social_post_targets WHERE account_id = ? AND status = 'PUBLISHED' AND published_at >= ?",
            a["id"], _iso(_utc() - timedelta(hours=24)))
        if today and today["n"] >= browser.POSTS_PER_DAY:
            raise browser.BrowserError(f"{browser.POSTS_PER_DAY} browser posts in 24 hours on this account — "
                                       "wait a little to keep the account's activity normal.")
        try:
            return await browser.publish(a, text, img)
        except browser.NeedsLogin as exc:
            await db.portal_execute("UPDATE social_accounts SET status = 'needs_login', last_error = ? WHERE id = ?",
                                    str(exc)[:300], a["id"])
            raise
    if a["platform"] == "linkedin":
        author = _extra(a).get("author") or f"urn:li:person:{a['external_id']}"
        return await linkedin.publish(author, decrypt(a["token_enc"]) or "", text, img.read_bytes() if img else None)
    if a["platform"] == "x":
        image = img.read_bytes() if img else None
        return await xapi.with_token(x_creds(a), lambda tok: xapi.publish(tok, text, image, img.name if img else "image.jpg"),
                                     lambda c: _save_x_creds(a["id"], c))
    token = decrypt(a["token_enc"]) or ""
    if a["platform"] == "facebook":
        image = media_path(post["media_name"]).read_bytes() if post.get("media_name") and media_path(post["media_name"]) else None
        return await meta.publish_facebook(a["external_id"], token, text, image, post.get("media_name") or "image.jpg")
    if a["platform"] == "instagram":
        url = media_url(post["media_name"]) if post.get("media_name") else None
        if post.get("media_name") and not url:
            raise meta.MetaError("Instagram fetches the image from your HOM link, and it isn't online right now — try again in a few minutes."
                                 if edition.is_client() else
                                 "Instagram needs your dashboard link to be online (it fetches the image from it). Start HOM with ./start.sh.")
        return await meta.publish_instagram(a["external_id"], token, text, url)
    raise meta.MetaError(f"Unknown platform {a['platform']}")


async def publish_post(post_id: int) -> Dict[str, Any]:
    post = await get_post(post_id)
    await db.portal_execute("UPDATE social_posts SET status = 'PUBLISHING' WHERE id = ?", post_id)
    ok = failed = 0
    for t in post["targets"]:
        if t["status"] == "PUBLISHED":
            ok += 1
            continue
        try:
            res = await publish_target(post, t)
            await db.portal_execute(
                "UPDATE social_post_targets SET status = 'PUBLISHED', external_id = ?, url = ?, error = NULL, published_at = ? WHERE id = ?",
                res.get("id"), res.get("url"), _iso(_utc()), t["id"])
            await log("published", f"Published post #{post_id} on {t['account_name']} ({t['platform'].title()}).")
            ok += 1
        except Exception as exc:  # noqa: BLE001 — one platform failing must not stop the others
            await db.portal_execute("UPDATE social_post_targets SET status = 'FAILED', error = ? WHERE id = ?", str(exc)[:300], t["id"])
            await log("failed", f"Post #{post_id} failed on {t['account_name']} ({t['platform'].title()}): {str(exc)[:160]}")
            failed += 1
    status = "PUBLISHED" if not failed else ("PARTIAL" if ok else "FAILED")
    await db.portal_execute("UPDATE social_posts SET status = ?, published_at = ? WHERE id = ?",
                            status, _iso(_utc()) if ok else None, post_id)
    return await get_post(post_id)


async def tick() -> Dict[str, Any]:
    """Publish the next post that is due (n8n calls this every minute)."""
    due = await db.portal_fetchrow(
        "SELECT id FROM social_posts WHERE status = 'SCHEDULED' AND scheduled_at <= ? ORDER BY scheduled_at LIMIT 1",
        _iso(_utc()))
    from . import inbox
    inbox_started = inbox.start_background_sync()
    await browser.close_idle()
    if not due:
        return {"published": False, "reason": "Nothing due", "inbox": inbox_started}
    post = await publish_post(due["id"])
    return {"published": True, "post_id": due["id"], "status": post["status"], "inbox": inbox_started}


async def tick_loop(every_s: float = 60.0) -> None:
    """Client workspaces: call tick() every minute (the owner's n8n does this
    for the owner's dashboard)."""
    while True:
        try:
            await asyncio.sleep(every_s)
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — keep going
            logger.warning("Social tick: %s", exc)
