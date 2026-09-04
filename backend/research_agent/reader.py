"""
reader.py — PageReader: reads the page currently open in the browser the
way a person would. The caller (agent.py) fetches the first snapshot
through the normal extract_page_text action and hands it in as `initial`;
read_page's job is to keep scrolling and accumulate more, stopping when
nothing new shows up (or a cap is hit) — and to fall back to `initial`
unchanged when the page object can't actually be scrolled (no working
evaluate(), e.g. a test double). Never raises — every exit path returns a
PageContent.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .pacing import PacingController

_MAX_TEXT_CHARS = 40000
_MAX_LINKS = 120
_MAX_HEADINGS = 40

_JS_TEXT = "() => document.body ? document.body.innerText : ''"
_JS_LINKS = """
() => Array.from(document.querySelectorAll('a[href]')).map(a => ({
    text: (a.innerText || '').trim().slice(0, 80),
    href: a.href || a.getAttribute('href') || '',
})).filter(l => l.href)
"""
_JS_METRICS = """
() => ({
    scrollHeight: document.body ? document.body.scrollHeight : 0,
    scrollY: window.scrollY,
    innerHeight: window.innerHeight,
    cardCount: document.querySelectorAll(
        "article, li, .card, [class*='card'], [class*='team'], [class*='member']"
    ).length,
})
"""
_JS_SCROLL_STEP = "() => window.scrollBy(0, Math.round(window.innerHeight * 0.8))"


@dataclass
class PageContent:
    url: str = ""
    text: str = ""
    links: List[Dict[str, str]] = field(default_factory=list)
    headings: List[str] = field(default_factory=list)
    scroll_rounds: int = 0
    stopped_reason: str = "bottom"  # "stable" | "max_scrolls" | "page_time_cap" | "bottom" | "empty" | "error"


def _dedup_links(links: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen, out = set(), []
    for l in links or []:
        href = l.get("href", "")
        if href and href not in seen:
            seen.add(href)
            out.append(l)
    return out[:_MAX_LINKS]


def _from_initial(url: str, initial: Dict[str, Any], scroll_rounds: int, stopped_reason: str) -> PageContent:
    text = initial.get("text") or ""
    links = _dedup_links(initial.get("links") or [])
    headings = (initial.get("headings") or [])[:_MAX_HEADINGS]
    if not text.strip() and not links:
        return PageContent(url=url, links=[], headings=headings,
                            scroll_rounds=scroll_rounds, stopped_reason="empty")
    return PageContent(url=url, text=text[:_MAX_TEXT_CHARS], links=links, headings=headings,
                        scroll_rounds=scroll_rounds, stopped_reason=stopped_reason)


async def read_page(
    page: Any, pacing: PacingController, *, initial: Dict[str, Any],
    max_scrolls: int, page_time_cap_s: float,
) -> PageContent:
    start = time.monotonic()
    url = getattr(page, "url", "") or initial.get("url") or ""

    try:
        await pacing.settle(page)
    except Exception:
        pass  # best-effort — proceed regardless

    try:
        metrics = (await page.evaluate(_JS_METRICS)) or {}
    except Exception:
        # No real scroll capability (a test double, or a page that's already
        # closed/navigated away) — the initial snapshot is all there is.
        return _from_initial(url, initial, scroll_rounds=0, stopped_reason="bottom")

    headings = (initial.get("headings") or [])[:_MAX_HEADINGS]
    last_scroll_height = metrics.get("scrollHeight", 0)
    scroll_rounds = 0
    stable_rounds = 0
    stopped_reason = "bottom"

    # Check if already at bottom before re-reading content
    at_bottom = metrics.get("scrollY", 0) + metrics.get("innerHeight", 0) >= last_scroll_height
    if at_bottom:
        # Already at bottom; return initial unchanged
        return _from_initial(url, initial, scroll_rounds=0, stopped_reason="bottom")

    # Not at bottom; re-read current content to establish baseline
    try:
        text = (await page.evaluate(_JS_TEXT)) or initial.get("text") or ""
        links = (await page.evaluate(_JS_LINKS)) or initial.get("links") or []
    except Exception:
        # Can't read current content; fall back to initial
        text = initial.get("text") or ""
        links = initial.get("links") or []

    last_text_len = len(text)
    last_card_count = metrics.get("cardCount", 0)

    while True:
        at_bottom = metrics.get("scrollY", 0) + metrics.get("innerHeight", 0) >= last_scroll_height
        if at_bottom:
            stopped_reason = "bottom"
            break
        if scroll_rounds >= max_scrolls:
            stopped_reason = "max_scrolls"
            break
        if time.monotonic() - start >= page_time_cap_s:
            stopped_reason = "page_time_cap"
            break

        try:
            await page.evaluate(_JS_SCROLL_STEP)
        except Exception:
            stopped_reason = "error"
            break
        scroll_rounds += 1
        await pacing.wait("after_scroll")

        try:
            new_text = (await page.evaluate(_JS_TEXT)) or ""
            new_links = (await page.evaluate(_JS_LINKS)) or []
            metrics = (await page.evaluate(_JS_METRICS)) or {}
        except Exception:
            stopped_reason = "error"
            break

        grew = (
            len(new_text) > last_text_len
            or metrics.get("scrollHeight", 0) > last_scroll_height
            or metrics.get("cardCount", 0) > last_card_count
        )
        text, links = new_text, new_links
        last_text_len = len(new_text)
        last_scroll_height = metrics.get("scrollHeight", last_scroll_height)
        last_card_count = metrics.get("cardCount", last_card_count)

        if grew:
            stable_rounds = 0
        else:
            stable_rounds += 1

        # Check time cap before stable to give it priority
        if time.monotonic() - start >= page_time_cap_s:
            stopped_reason = "page_time_cap"
            break

        if stable_rounds >= 2:
            stopped_reason = "stable"
            break

    if stopped_reason == "error":
        return PageContent(url=url, stopped_reason="error")

    links = _dedup_links(links)
    if not text.strip() and not links:
        return PageContent(url=url, text="", links=[], headings=headings,
                            scroll_rounds=scroll_rounds, stopped_reason="empty")
    return PageContent(
        url=url, text=text[:_MAX_TEXT_CHARS], links=links, headings=headings,
        scroll_rounds=scroll_rounds, stopped_reason=stopped_reason,
    )
