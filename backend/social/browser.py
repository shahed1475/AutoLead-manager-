"""
browser.py — posting to LinkedIn and X through a real browser, for when you
don't have (or can't get) API access.

  login    HOM opens the site in its own Chromium (one saved profile per
           account in data/social_browser/<id>/). You see it live on the
           Social page and sign in yourself — password, 2FA, everything.
           HOM never sees or stores your password; only the site's cookies
           stay in the profile.
  publish  opens the composer, types the post, attaches the image, presses
           Post — the way you would.
  safety   a security check, CAPTCHA or sign-in page → HOM stops, marks the
           account "needs sign-in" and asks you to finish it in the live
           window. No CAPTCHA solving, no evasion, no proxies. A daily post
           cap per account keeps the account's activity normal.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

PROFILE_DIR = Path(__file__).resolve().parents[1] / "data" / "social_browser"
VIEWPORT = {"width": 1280, "height": 800}
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
LOGIN_URLS = {"linkedin": "https://www.linkedin.com/login", "x": "https://x.com/i/flow/login"}
HOME_URLS = {"linkedin": "https://www.linkedin.com/feed/", "x": "https://x.com/home"}
DOMAINS = {"linkedin": ("linkedin.com",), "x": ("x.com", "twitter.com")}
_STOP_URL = ("checkpoint", "challenge", "captcha", "/account/access", "authwall", "/login", "/uas/", "i/flow/login", "/signup")
_STOP_TEXT = ("verify you are human", "are you a robot", "security verification", "let's do a quick security check",
              "unusual activity", "confirm it's you", "prove you're not a robot")
KEYS = {"Enter", "Tab", "Backspace", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Delete"}
POSTS_PER_DAY = 8
IDLE_CLOSE_S = 600


class BrowserError(RuntimeError):
    pass


class NeedsLogin(BrowserError):
    pass


class _Session:
    def __init__(self, pw, ctx, page):
        self.pw, self.ctx, self.page = pw, ctx, page
        self.lock = asyncio.Lock()
        self.used = time.monotonic()


_sessions: Dict[int, _Session] = {}
_open_lock = asyncio.Lock()


def profile_dir(account_id: int) -> Path:
    return PROFILE_DIR / str(int(account_id))


async def _session(account_id: int) -> _Session:
    async with _open_lock:
        s = _sessions.get(account_id)
        if s and not s.page.is_closed():
            s.used = time.monotonic()
            return s
        from playwright.async_api import async_playwright
        from .. import edition
        d = profile_dir(account_id)
        d.mkdir(parents=True, exist_ok=True)
        pw = await async_playwright().start()
        try:
            ctx = await pw.chromium.launch_persistent_context(
                str(d), headless=True, viewport=VIEWPORT, user_agent=_UA, locale="en-US",
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
        except Exception as exc:  # noqa: BLE001
            await pw.stop()
            raise BrowserError(f"Couldn't start the browser: {str(exc)[:160]}") from exc
        await edition.guard_browser_context(ctx)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        page.set_default_timeout(20000)
        s = _sessions[account_id] = _Session(pw, ctx, page)
        return s


async def close(account_id: int) -> None:
    s = _sessions.pop(account_id, None)
    if s:
        try:
            await s.ctx.close()
        finally:
            await s.pw.stop()


async def close_idle() -> None:
    for aid, s in list(_sessions.items()):
        if time.monotonic() - s.used > IDLE_CLOSE_S and not s.lock.locked():
            await close(aid)


async def forget(account_id: int) -> None:
    """Disconnecting an account deletes its browser profile (cookies)."""
    await close(account_id)
    shutil.rmtree(profile_dir(account_id), ignore_errors=True)


# ── Live window (you sign in yourself) ───────────────────────────────────────

async def open_login(account_id: int, platform: str) -> Dict[str, Any]:
    s = await _session(account_id)
    async with s.lock:
        await s.page.goto(LOGIN_URLS[platform], wait_until="domcontentloaded")
    return {"url": s.page.url}


async def screenshot(account_id: int) -> bytes:
    s = _sessions.get(account_id)
    if not s:
        raise BrowserError("The browser window is closed — press Sign in again.")
    s.used = time.monotonic()
    return await s.page.screenshot(type="jpeg", quality=70)


async def act(account_id: int, platform: str, action: Dict[str, Any]) -> Dict[str, Any]:
    """Your clicks and typing in the live window."""
    s = _sessions.get(account_id)
    if not s:
        raise BrowserError("The browser window is closed — press Sign in again.")
    s.used = time.monotonic()
    kind = action.get("type")
    async with s.lock:
        p = s.page
        if kind == "click":
            x, y = float(action.get("x", -1)), float(action.get("y", -1))
            if not (0 <= x <= VIEWPORT["width"] and 0 <= y <= VIEWPORT["height"]):
                raise BrowserError("Click outside the window.")
            await p.mouse.click(x, y)
        elif kind == "type":
            text = str(action.get("text") or "")[:500]
            await p.keyboard.type(text, delay=30)
        elif kind == "key":
            key = str(action.get("key") or "")
            if key not in KEYS:
                raise BrowserError("That key isn't supported.")
            await p.keyboard.press(key)
        elif kind == "scroll":
            await p.mouse.wheel(0, max(-2000, min(2000, float(action.get("dy") or 0))))
        elif kind == "goto":
            url = str(action.get("url") or "")
            host = (urlparse(url).hostname or "").lower()
            if urlparse(url).scheme != "https" or not any(host == d or host.endswith("." + d) for d in DOMAINS[platform]):
                raise BrowserError(f"Only {platform.title()} pages can be opened here.")
            await p.goto(url, wait_until="domcontentloaded")
        elif kind == "back":
            await p.go_back()
        else:
            raise BrowserError("Unknown action.")
        await asyncio.sleep(0.4)
    return {"url": s.page.url}


async def _blocked(page) -> Optional[str]:
    url = page.url.lower()
    if any(m in url for m in _STOP_URL):
        return "sign-in"
    try:
        body = (await page.inner_text("body", timeout=3000)).lower()[:20000]
    except Exception:  # noqa: BLE001
        return None
    return "security check" if any(m in body for m in _STOP_TEXT) else None


async def logged_in(account_id: int, platform: str) -> bool:
    s = await _session(account_id)
    async with s.lock:
        p = s.page
        try:
            await p.goto(HOME_URLS[platform], wait_until="domcontentloaded")
            await p.wait_for_timeout(2500)
        except Exception:  # noqa: BLE001
            return False
        if await _blocked(p):
            return False
        marker = ('[data-testid="SideNav_NewTweet_Button"], [data-testid="AppTabBar_Home_Link"]' if platform == "x"
                  else '.share-box-feed-entry__trigger, button:has-text("Start a post"), .global-nav__me')
        try:
            await p.wait_for_selector(marker, timeout=8000)
            return True
        except Exception:  # noqa: BLE001
            return False


# ── Publishing ───────────────────────────────────────────────────────────────

async def _first(page, selectors, timeout=8000):
    """The first selector that becomes visible (sites change their markup)."""
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        for sel in selectors:
            loc = page.locator(sel).first
            try:
                if await loc.is_visible():
                    return loc
            except Exception:  # noqa: BLE001
                continue
        await page.wait_for_timeout(300)
    return None


async def _stop_if_blocked(page) -> None:
    why = await _blocked(page)
    if why:
        raise NeedsLogin(f"The site is asking for a {why}. Open the account on the Social page, finish it in the "
                         "live window, then retry — HOM doesn't get past these checks by itself.")


async def publish(account: Dict[str, Any], text: str, image: Optional[Path]) -> Dict[str, Optional[str]]:
    s = await _session(account["id"])
    async with s.lock:
        if account["platform"] == "x":
            return await _publish_x(s.page, text, image)
        if account["platform"] == "linkedin":
            return await _publish_linkedin(s.page, account, text, image)
    raise BrowserError(f"Browser posting isn't available for {account['platform']}.")


async def _publish_x(page, text: str, image: Optional[Path]) -> Dict[str, Optional[str]]:
    await page.goto("https://x.com/compose/post", wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)
    await _stop_if_blocked(page)
    box = await _first(page, ['[data-testid="tweetTextarea_0"]', 'div[role="textbox"][contenteditable="true"]'], 15000)
    if not box:
        raise BrowserError("Couldn't find X's post box — X may have changed its page.")
    if image:
        await page.locator('input[data-testid="fileInput"], input[type="file"]').first.set_input_files(str(image))
        await page.wait_for_selector('[data-testid="attachments"]', timeout=30000)
    await box.click()
    await page.keyboard.insert_text(text)
    await page.wait_for_timeout(800)
    btn = await _first(page, ['[data-testid="tweetButton"]:not([aria-disabled="true"])',
                              '[data-testid="tweetButtonInline"]:not([aria-disabled="true"])'], 10000)
    if not btn:
        raise BrowserError("X didn't enable the Post button (text too long?).")
    await btn.click()
    link = None
    try:
        toast = page.locator('[data-testid="toast"] a[href*="/status/"]').first
        await toast.wait_for(timeout=15000)
        href = await toast.get_attribute("href")
        link = f"https://x.com{href}" if href and href.startswith("/") else href
    except Exception:  # noqa: BLE001
        pass
    try:
        await page.locator('[data-testid="tweetTextarea_0"]').first.wait_for(state="detached", timeout=20000)
    except Exception:  # noqa: BLE001
        await _stop_if_blocked(page)
        if not link:
            raise BrowserError("X didn't confirm the post — check the account before retrying (it may have posted).")
    return {"id": link.rsplit("/", 1)[-1] if link else None, "url": link}


async def _publish_linkedin(page, account: Dict[str, Any], text: str, image: Optional[Path]) -> Dict[str, Optional[str]]:
    import json
    extra = json.loads(account.get("extra") or "{}")
    company = extra.get("company_id")
    url = (f"https://www.linkedin.com/company/{company}/admin/page-posts/published/" if company
           else "https://www.linkedin.com/feed/")
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(2500)
    await _stop_if_blocked(page)
    start = await _first(page, ['button:has-text("Start a post")', '.share-box-feed-entry__trigger',
                                'button[aria-label*="Start a post"]', 'button:has-text("Create")'], 15000)
    if not start:
        raise BrowserError("Couldn't find LinkedIn's “Start a post” — LinkedIn may have changed its page"
                           + (" (are you an admin of this Company Page?)" if company else "") + ".")
    await start.click()
    if company:
        item = await _first(page, ['[role="menuitem"]:has-text("Start a post")', 'div[role="menu"] >> text=Start a post'], 3000)
        if item:
            await item.click()
    editor = await _first(page, ['.ql-editor[contenteditable="true"]', 'div[role="textbox"][contenteditable="true"]'], 15000)
    if not editor:
        raise BrowserError("LinkedIn's post editor didn't open.")
    await editor.click()
    await page.keyboard.insert_text(text)
    await page.wait_for_timeout(800)
    if image:
        media = await _first(page, ['button[aria-label="Add media"]', 'button[aria-label*="Add a photo"]',
                                    'button[aria-label*="media" i]'], 5000)
        if not media:
            raise BrowserError("Couldn't find LinkedIn's “Add media” button.")
        async with page.expect_file_chooser(timeout=10000) as fc:
            await media.click()
        await (await fc.value).set_files(str(image))
        nxt = await _first(page, ['button:has-text("Next")', 'button:has-text("Done")'], 20000)
        if nxt:
            await nxt.click()
        await page.wait_for_timeout(1500)
    post = await _first(page, ['button.share-actions__primary-action:not([disabled])',
                               'button:has-text("Post"):not([disabled])'], 15000)
    if not post:
        raise BrowserError("LinkedIn didn't enable the Post button.")
    await post.click()
    try:
        await page.locator('.ql-editor[contenteditable="true"], div[role="textbox"][contenteditable="true"]').first.wait_for(
            state="detached", timeout=30000)
    except Exception:  # noqa: BLE001
        await _stop_if_blocked(page)
        raise BrowserError("LinkedIn didn't confirm the post — check the account before retrying (it may have posted).")
    link = None
    try:
        a = page.locator('a[href*="/feed/update/urn:li:"]').first
        await a.wait_for(timeout=6000)
        link = await a.get_attribute("href")
    except Exception:  # noqa: BLE001
        pass
    return {"id": None, "url": link}
