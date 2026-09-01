"""
browser.py — Playwright browser controller for the Research Agent.

Launch/context pattern mirrors scrapers/bing_search.py's proven approach
(same UA, same viewport, same navigator.webdriver override — basic
fingerprint normalization already accepted in this codebase, not CAPTCHA
solving). CAPTCHA/block detection reuses the same string-scan approach:
record the obstacle, stop that path, let the agent continue elsewhere.

Every public method returns an ActionResult and never raises into the
agent loop — Playwright/network failures become ActionResult(status="error").
"""
from __future__ import annotations

import logging
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..scrapers.bing_search import _resolve_headless
from .actions import ActionResult
from .models import AgentAction

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
_STEALTH_INIT_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
"""
_BLOCK_MARKERS = ("captcha", "unusual traffic", "verify you are human", "are you a robot")
# Text that means "Google showed a consent / cookie wall or an empty SERP",
# NOT a real results page. Treated as blocked so the loop uses a fallback
# source instead of silently continuing with zero results.
_CONSENT_MARKERS = (
    "before you continue", "accept all", "reject all", "we use cookies",
    "consent.google", "sending you to google", "our privacy policy and terms",
)

_SCREENSHOT_DIR = Path(__file__).parent.parent / "data" / "research_agent_screenshots"


class BrowserController:
    """One instance per research session. Call start() before executing
    actions, stop() when the session ends (always, even on failure)."""

    def __init__(self, headless: bool = False, page_timeout_ms: int = 20000) -> None:
        self.headless = headless
        self.page_timeout_ms = page_timeout_ms
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._tabs: List[Any] = []
        self.blocked_sources: List[Dict[str, str]] = []

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        launch_args = [
            "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
            "--disable-blink-features=AutomationControlled", "--disable-extensions",
        ]
        headless = _resolve_headless(self.headless)
        try:
            self._browser = await self._playwright.chromium.launch(headless=headless, args=launch_args)
        except Exception as exc:
            msg = str(exc)
            if "Executable doesn't exist" in msg or "playwright install" in msg:
                # The bundled headed Chromium build isn't downloaded. Try the
                # headless-shell build (a common partial install), then fall
                # back to a clear, actionable message — never surface the raw
                # Playwright trace to the user.
                try:
                    self._browser = await self._playwright.chromium.launch(
                        headless=True, args=launch_args,
                    )
                    logger.warning("Research agent: headed Chromium unavailable — running headless-shell instead")
                except Exception:
                    await self._safe_stop_playwright()
                    raise RuntimeError(
                        "Playwright's Chromium browser is not installed. Run "
                        "`venv/Scripts/python.exe -m playwright install chromium` "
                        "on the server, then start the research session again."
                    ) from exc
            else:
                await self._safe_stop_playwright()
                raise RuntimeError(f"Could not launch the research browser: {msg[:200]}") from exc
        self._context = await self._browser.new_context(user_agent=_UA, viewport={"width": 1440, "height": 900})
        await self._context.add_init_script(_STEALTH_INIT_SCRIPT)
        self._page = await self._context.new_page()
        self._page.set_default_timeout(self.page_timeout_ms)
        self._tabs = [self._page]
        # "Chrome opens. Then Google opens." (brief §6)
        await self._page.goto("https://www.google.com", wait_until="domcontentloaded")
        await self._dismiss_consent()

    async def _dismiss_consent(self) -> None:
        """Best-effort: click the most privacy-preserving option on a
        Google/EU cookie-consent interstitial ("Reject all") so the next
        search lands on a real results page. Never raises; a missing dialog
        is the normal case."""
        for label in ("Reject all", "Reject All", "Alle ablehnen", "Tout refuser", "I disagree"):
            try:
                btn = self._page.get_by_role("button", name=label)
                if await btn.count() > 0:
                    await btn.first.click(timeout=3000)
                    await self._page.wait_for_timeout(600)
                    return
            except Exception:
                continue

    async def _safe_stop_playwright(self) -> None:
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            logger.debug("BrowserController: playwright stop error during failed launch", exc_info=True)
        finally:
            self._playwright = None

    async def stop(self) -> None:
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            logger.debug("BrowserController.stop() cleanup error", exc_info=True)

    async def __aenter__(self) -> "BrowserController":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    # ── Action dispatch ──────────────────────────────────────────────────

    async def execute(self, action: AgentAction) -> ActionResult:
        handler = {
            "google_search": self.google_search,
            "open_url": self.open_url,
            "extract_page_text": self.extract_page_text,
            "find_links": self.find_links,
            "click": self.click,
            "scroll": self.scroll,
            "go_back": self.go_back,
            "open_new_tab": self.open_new_tab,
            "screenshot": self.screenshot,
        }.get(action.action)
        if handler is None:
            return ActionResult(action=action.action, status="error", error=f"No browser handler for '{action.action}'")
        try:
            return await handler(**(action.params or {}))
        except TypeError as exc:
            return ActionResult(action=action.action, status="error", error=f"Bad parameters: {exc}")
        except Exception as exc:
            logger.warning("Browser action %s failed: %s", action.action, exc, exc_info=True)
            return ActionResult(action=action.action, status="error", error=str(exc)[:300])

    # ── Obstacle detection ───────────────────────────────────────────────

    async def _check_blocked(self, source_url: str) -> bool:
        try:
            text = (await self._page.evaluate("() => document.body ? document.body.innerText.slice(0, 1000) : ''")).lower()
        except Exception:
            return False
        if any(marker in text for marker in _BLOCK_MARKERS):
            self.blocked_sources.append({"url": source_url, "reason": "captcha_or_block_detected"})
            logger.info("Research agent: obstacle detected at %s — abandoning this path", source_url)
            return True
        return False

    # ── Actions ───────────────────────────────────────────────────────────

    async def google_search(self, query: str, **_) -> ActionResult:
        url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"
        await self._page.goto(url, wait_until="domcontentloaded", timeout=self.page_timeout_ms)
        await self._page.wait_for_timeout(1200)
        if await self._check_blocked(url):
            return await self._search_fallback(query, reason="captcha_or_block")

        # Result containers change often; scrape by the stable shape (an <a>
        # wrapping/adjacent to an <h3>) as well as the historical selectors.
        results = await self._page.evaluate("""
            () => {
                const out = [];
                const seen = new Set();
                const push = (a, h3, snip) => {
                    if (!a || !h3 || !a.href || seen.has(a.href)) return;
                    if (!a.href.startsWith('http') || a.href.includes('google.com')) return;
                    seen.add(a.href);
                    out.push({
                        title: (h3.innerText || '').slice(0, 200),
                        url: a.href,
                        snippet: snip ? (snip.innerText || '').slice(0, 400) : '',
                    });
                };
                document.querySelectorAll('div.g, div[data-sokoban-container], div.MjjYud, div[data-hveid]').forEach(el => {
                    push(el.querySelector('a[href]'), el.querySelector('h3'),
                         el.querySelector('div[data-sncf], .VwiC3b, .IsZvec'));
                });
                document.querySelectorAll('a:has(h3)').forEach(a => push(a, a.querySelector('h3'), null));
                return out;
            }
        """) or []

        if not results:
            # 0 usable results is NOT success — it's a consent wall, a parser
            # miss, or a genuinely empty SERP. Record it and try a fallback
            # source rather than pretending the search worked.
            try:
                body = (await self._page.evaluate(
                    "() => document.body ? document.body.innerText.slice(0, 2000) : ''"
                )).lower()
            except Exception:
                body = ""
            reason = "consent_or_empty" if any(m in body for m in _CONSENT_MARKERS) else "no_usable_results"
            return await self._search_fallback(query, reason=reason)

        return ActionResult(action="google_search", status="success",
                            data={"query": query, "results": results[:10]})

    async def _search_fallback(self, query: str, reason: str) -> ActionResult:
        """Google returned nothing usable. Record the obstacle, then try
        DuckDuckGo's HTML endpoint (no consent wall, stable markup) in the
        same page. This is the sanctioned fallback source — not evasion."""
        self.blocked_sources.append({"url": "google_search", "reason": reason})
        logger.info("Research agent: google_search unusable (%s) for %r — trying DuckDuckGo", reason, query)
        try:
            ddg = await self._duckduckgo_search(query)
        except Exception as exc:
            logger.warning("DuckDuckGo fallback failed for %r: %s", query, exc)
            ddg = []
        if ddg:
            return ActionResult(action="google_search", status="success",
                                data={"query": query, "results": ddg[:10], "via": "duckduckgo"})
        return ActionResult(action="google_search", status="blocked",
                            data={"query": query, "reason": reason})

    async def _duckduckgo_search(self, query: str) -> List[Dict[str, str]]:
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
        await self._page.goto(url, wait_until="domcontentloaded", timeout=self.page_timeout_ms)
        await self._page.wait_for_timeout(600)
        return await self._page.evaluate("""
            () => {
                const out = [];
                document.querySelectorAll('div.result, div.result__body').forEach(el => {
                    const a = el.querySelector('a.result__a, a.result__url');
                    const snip = el.querySelector('.result__snippet');
                    if (!a || !a.href) return;
                    let href = a.href;
                    try {
                        const u = new URL(href, location.href);
                        const uddg = u.searchParams.get('uddg');
                        if (uddg) href = uddg;
                    } catch (e) {}
                    if (!href.startsWith('http') || href.includes('duckduckgo.com')) return;
                    out.push({
                        title: (a.innerText || '').slice(0, 200),
                        url: href,
                        snippet: snip ? (snip.innerText || '').slice(0, 400) : '',
                    });
                });
                return out;
            }
        """) or []

    async def open_url(self, url: str, **_) -> ActionResult:
        await self._page.goto(url, wait_until="domcontentloaded", timeout=self.page_timeout_ms)
        if await self._check_blocked(url):
            return ActionResult(action="open_url", status="blocked", data={"url": url})
        title = await self._page.title()
        return ActionResult(action="open_url", status="success", data={"url": self._page.url, "title": title})

    async def extract_page_text(self, **_) -> ActionResult:
        text = await self._page.evaluate("() => document.body ? document.body.innerText : ''")
        # Links (incl. mailto:/tel:) and headings alongside the text — an
        # explicit mailto:/tel: is a far stronger signal than a regex hit in
        # body text, and the headings help classify the page (Team/Contact/…).
        links = await self._page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                text: (a.innerText || '').trim().slice(0, 80),
                href: a.href || a.getAttribute('href') || '',
            })).filter(l => l.href)
        """) or []
        headings = await self._page.evaluate("""
            () => Array.from(document.querySelectorAll('h1, h2, h3')).map(h => (h.innerText || '').trim()).filter(Boolean).slice(0, 30)
        """) or []
        return ActionResult(action="extract_page_text", status="success", data={
            "url": self._page.url, "text": (text or "")[:20000],
            "links": links[:80], "headings": headings,
        })

    async def find_links(self, keyword: Optional[str] = None, **_) -> ActionResult:
        links = await self._page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                text: (a.innerText || '').trim().slice(0, 80),
                href: a.href,
            })).filter(l => l.text && l.href.startsWith('http'))
        """)
        if keyword:
            kw = keyword.lower()
            links = [l for l in links if kw in l["text"].lower() or kw in l["href"].lower()]
        # Dedup by href, cap
        seen, out = set(), []
        for l in links:
            if l["href"] not in seen:
                seen.add(l["href"])
                out.append(l)
        return ActionResult(action="find_links", status="success", data={"links": out[:30]})

    async def click(self, text: Optional[str] = None, selector: Optional[str] = None, **_) -> ActionResult:
        try:
            if selector:
                await self._page.click(selector, timeout=self.page_timeout_ms)
            else:
                await self._page.get_by_text(text, exact=False).first.click(timeout=self.page_timeout_ms)
            await self._page.wait_for_timeout(800)
        except Exception as exc:
            return ActionResult(action="click", status="error", error=f"Could not click '{text or selector}': {exc}")
        return ActionResult(action="click", status="success", data={"url": self._page.url})

    async def scroll(self, direction: str = "down", **_) -> ActionResult:
        delta = 800 if direction == "down" else -800
        await self._page.evaluate(f"window.scrollBy(0, {delta})")
        await self._page.wait_for_timeout(400)
        return ActionResult(action="scroll", status="success", data={"direction": direction})

    async def go_back(self, **_) -> ActionResult:
        await self._page.go_back(timeout=self.page_timeout_ms)
        return ActionResult(action="go_back", status="success", data={"url": self._page.url})

    async def open_new_tab(self, url: str, **_) -> ActionResult:
        primary = self._tabs[0] if self._tabs else None
        # Close the tab we're leaving unless it's the primary one — otherwise
        # over a 20+ lead session (one BrowserController for the whole run)
        # abandoned pages/renderers accumulate unbounded.
        if self._page is not None and self._page is not primary:
            try:
                await self._page.close()
                if self._page in self._tabs:
                    self._tabs.remove(self._page)
            except Exception:
                logger.debug("open_new_tab: could not close the previous tab", exc_info=True)
        page = await self._context.new_page()
        page.set_default_timeout(self.page_timeout_ms)
        await page.goto(url, wait_until="domcontentloaded", timeout=self.page_timeout_ms)
        self._tabs.append(page)
        self._page = page  # active tab becomes the new one
        if await self._check_blocked(url):
            return ActionResult(action="open_new_tab", status="blocked", data={"url": url})
        return ActionResult(action="open_new_tab", status="success", data={"url": page.url})

    async def screenshot(self, **_) -> ActionResult:
        _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = _SCREENSHOT_DIR / f"shot_{int(time.time() * 1000)}.png"
        await self._page.screenshot(path=str(path))
        return ActionResult(action="screenshot", status="success", data={"path": str(path)})
