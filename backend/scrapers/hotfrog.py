"""
Hotfrog business directory scraper — requests + BeautifulSoup.

Hotfrog (hotfrog.com) is a global business directory with millions of listings.
This scraper is HTTP-only (no browser required) since Hotfrog renders server-side HTML.

Strategy
--------
Phase 1 — Search result pages (up to 3 URL format variants × 5 pages each):
  Extract business cards: name, phone, address, and listing detail URL.
  UA rotation + random delays (3-8 s) for anti-ban.
  429/503 → exponential back-off + single retry.

Phase 2 — Detail page extraction:
  Visit each listing's detail page to extract the external website URL.
  (Website is not shown on search result pages — only on detail pages.)

Email enrichment is handled by the orchestrator's email_finder after this scraper returns.
"""

import asyncio
import logging
import random
import re
import time
import urllib.parse
import urllib3
from typing import Any, Callable, Dict, List, Optional, Set

import requests
from bs4 import BeautifulSoup

from ..validators import clean_phone
from ._shared import resolve_delay

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_BASE = "https://www.hotfrog.com"

_FALLBACK_UAS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
]

# CSS selector fallback chains for search result cards
_CARD_SELS = [
    "article.listing", "div.listing-item", "div.business-listing",
    "div.search-result", "li.listing", ".organic-result",
    ".listing", ".result", "article", ".biz-card",
]
_NAME_SELS  = [
    ".company-name", ".business-name", ".listing-name",
    "h2 a", "h3 a", "h2", "h3", ".name", "a[class*='company']",
]
_PHONE_SELS = [
    'a[href^="tel:"]', ".phone", ".phone-number", ".listing-phone",
    ".tel", ".contact-phone", ".phonenumber", "[class*='phone']",
]
_ADDR_SELS  = [
    ".address", ".listing-address", ".business-address",
    ".location", "address", ".adr", ".locality", "[class*='address']",
]
_CAT_SELS   = [".category", ".categories", ".listing-category", ".business-type", "[class*='category']"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_ua() -> str:
    try:
        from fake_useragent import UserAgent
        return UserAgent(browsers=["Chrome", "Firefox", "Edge"]).random
    except Exception:
        return random.choice(_FALLBACK_UAS)


def _headers(ua: str) -> Dict[str, str]:
    return {
        "User-Agent":                ua,
        "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language":           "en-US,en;q=0.9",
        "Accept-Encoding":           "gzip, deflate, br",
        "DNT":                       "1",
        "Connection":                "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer":                   _BASE,
    }


def _safe_get(
    url:     str,
    session: requests.Session,
    ua:      str,
    log_fn:  Callable[[str], None],
    timeout: int = 12,
) -> Optional[requests.Response]:
    for attempt in range(2):
        try:
            resp = session.get(url, headers=_headers(ua), timeout=timeout,
                               verify=False, allow_redirects=True)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 503):
                wait = 25 + attempt * 20
                log_fn(f"⚠️  Hotfrog rate-limit ({resp.status_code}) — waiting {wait}s …")
                time.sleep(wait)
                ua = _get_ua()
                continue
            if resp.status_code in (404, 400, 410):
                return None
            log_fn(f"⚠️  Hotfrog HTTP {resp.status_code}")
            return None
        except requests.RequestException as exc:
            if attempt == 0:
                time.sleep(5)
            else:
                log_fn(f"⚠️  Hotfrog request error: {exc}")
    return None


# ── URL builders ──────────────────────────────────────────────────────────────

def _search_urls(niche: str, city: str) -> List[str]:
    """Return candidate search URL formats — tried in order until one yields results."""
    n_dash  = urllib.parse.quote_plus(niche.replace(" ", "-"))
    c_dash  = urllib.parse.quote_plus(city.replace(" ", "-"))
    n_space = urllib.parse.quote_plus(niche)
    c_space = urllib.parse.quote_plus(city)
    return [
        f"{_BASE}/search/{n_dash}/{c_dash}",
        f"{_BASE}/search?q={n_space}&loc={c_space}",
        f"{_BASE}/{n_dash}/{c_dash}",
        f"{_BASE}/s/{n_space}/{c_space}",
    ]


def _paginate(base_url: str, page: int) -> str:
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}page={page}"


# ── Extraction ────────────────────────────────────────────────────────────────

def _first_text(root: Any, selectors: List[str]) -> str:
    for sel in selectors:
        el = root.select_one(sel)
        if el:
            text = el.get_text(strip=True)
            if text:
                return text[:200]
    return ""


def _extract_cards(html: str, niche: str, city: str) -> List[Dict[str, Any]]:
    """Parse search result page → list of partial lead dicts (no website yet)."""
    soup  = BeautifulSoup(html, "lxml")
    cards = []
    for sel in _CARD_SELS:
        found = soup.select(sel)
        if found:
            cards = found
            break

    # Fallback: any block containing a /company/ or /business/ link
    if not cards:
        for a in soup.select('a[href*="/company/"], a[href*="/business/"]'):
            parent = a.find_parent(["article", "div", "li", "section"])
            if parent and parent not in cards:
                cards.append(parent)

    leads: List[Dict[str, Any]] = []
    for card in cards:
        name = _first_text(card, _NAME_SELS)
        if not name:
            continue

        # Detail URL
        detail_url = ""
        for link_sel in ['a[href*="/company/"]', 'a[href*="/business/"]'] + _NAME_SELS:
            el = card.select_one(link_sel)
            if el and el.name == "a":
                href = el.get("href", "")
                if href:
                    detail_url = href if href.startswith("http") else _BASE + href
                    break

        # Phone (tel: links first, then display text)
        phone = ""
        for sel in _PHONE_SELS:
            el = card.select_one(sel)
            if el:
                raw = (el.get("href") or "").replace("tel:", "") or el.get_text(strip=True)
                phone = clean_phone(raw) or ""
                if phone:
                    break

        address  = _first_text(card, _ADDR_SELS)
        category = _first_text(card, _CAT_SELS)

        leads.append({
            "business_name": name,
            "phone":         phone    or None,
            "website":       None,
            "email":         None,
            "address":       address  or None,
            "niche":         niche,
            "city":          city,
            "source":        "HOTFROG",
            "_detail_url":   detail_url or None,
            "_category":     category or None,
        })

    return leads


def _website_from_detail(html: str) -> Optional[str]:
    """Extract external website URL from a Hotfrog business detail/profile page."""
    soup = BeautifulSoup(html, "lxml")

    # Priority 1: explicit visit-website / external link buttons
    for sel in [
        'a.visit-website', 'a[class*="website"]', 'a[class*="external"]',
        'a[class*="visit"]', 'a[data-track*="website"]',
    ]:
        el = soup.select_one(sel)
        if el:
            href = el.get("href", "")
            if href and "hotfrog" not in href.lower():
                if href.startswith("http"):
                    return href
                # May be a redirect — try to parse the real URL out
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                for key in ("url", "redirect", "target", "goto", "link", "u"):
                    if qs.get(key):
                        return urllib.parse.unquote(qs[key][0])

    # Priority 2: any external <a> that's clearly a business website
    for a in soup.find_all("a", href=re.compile(r"^https?://", re.I)):
        href = a.get("href", "")
        if href and "hotfrog" not in href.lower() and len(href) > 10:
            return href

    return None


# ── Core sync scraper ─────────────────────────────────────────────────────────

def scrape_sync(
    niche:     str,
    city:      str,
    country:   str,
    max_leads: int,
    log_fn:    Callable[[str], None],
    cfg:       Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Synchronous Hotfrog scraper — always called inside asyncio.to_thread().

    Phase 1: Query multiple URL formats; paginate up to 5 pages per format.
    Phase 2: Visit each listing's detail page to get the external website URL.
    """
    log_fn(f"🔥 Hotfrog: '{niche}' in '{city}' (target {max_leads})")
    delay_min, delay_max = resolve_delay(cfg, 3.0, 7.0)

    session    = requests.Session()
    ua         = _get_ua()
    pool:  List[Dict[str, Any]] = []
    seen_names: Set[str]        = set()

    # ── Phase 1: collect listings ─────────────────────────────────────────────
    for base_url in _search_urls(niche, city):
        if len(pool) >= max_leads * 2:
            break
        log_fn(f"  🌐 {base_url}")
        got_results = False

        for page in range(1, 6):
            url  = base_url if page == 1 else _paginate(base_url, page)
            resp = _safe_get(url, session, ua, log_fn)
            if not resp:
                break

            cards = _extract_cards(resp.text, niche, city)
            if not cards:
                if page == 1:
                    log_fn("  ⚠️  No cards found at this URL format — trying next")
                break

            got_results = True
            new_count   = 0
            for lead in cards:
                key = (lead["business_name"] or "").lower().strip()
                if key and key not in seen_names:
                    seen_names.add(key)
                    pool.append(lead)
                    new_count += 1

            log_fn(f"  Page {page}: {new_count} new listings ({len(pool)} total)")
            if len(pool) >= max_leads * 2 or new_count == 0:
                break
            time.sleep(random.uniform(delay_min, delay_max))

        if got_results:
            break  # found results — no need to try other URL formats

    if not pool:
        log_fn("⚠️  Hotfrog returned no results — niche/city may have no listings")
        return []

    log_fn(f"📋 Pool: {len(pool)} listings — extracting websites from detail pages …")

    # ── Phase 2: detail pages for website URLs ────────────────────────────────
    target = pool[:max_leads]
    d_sess = requests.Session()

    for i, lead in enumerate(target, 1):
        detail_url = lead.pop("_detail_url", None)
        lead.pop("_category", None)   # clean internal key

        if not detail_url:
            continue

        resp = _safe_get(detail_url, d_sess, _get_ua(), log_fn, timeout=10)
        if resp:
            website = _website_from_detail(resp.text)
            if website:
                lead["website"] = website

        log_fn(
            f"  [{i}/{len(target)}] "
            + ("✅" if lead.get("website") else "📋")
            + f" {lead['business_name']}"
            + (f" → {lead['website']}" if lead.get("website") else "")
        )
        time.sleep(random.uniform(2.0, 5.0))

    # Final cleanup — remove any leftover internal keys
    for lead in target:
        lead.pop("_detail_url", None)
        lead.pop("_category", None)

    phone_cnt = sum(1 for l in target if l.get("phone"))
    web_cnt   = sum(1 for l in target if l.get("website"))
    log_fn(f"🏁 Hotfrog done — {len(target)} leads | {phone_cnt} phones | {web_cnt} websites")
    return target


# ── Public async API ──────────────────────────────────────────────────────────

async def scrape_hotfrog(
    niche:        str,
    city:         str,
    country:      str = "",
    max_leads:    int = 30,
    log_callback: Optional[Callable[[str], None]] = None,
    cfg:          Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Async entry-point — delegates sync work to asyncio.to_thread()."""
    def _log(msg: str) -> None:
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass
    _log(f"🚀 Hotfrog scraper: {niche} in {city} (max {max_leads})")
    return await asyncio.to_thread(scrape_sync, niche, city, country, max_leads, _log, cfg)


async def scrape(
    niche:        str,
    city:         str,
    max_results:  int = 30,
    cfg:          Optional[Dict[str, Any]] = None,
    log_callback: Optional[Callable[[str], None]] = None,
    country:      str = "",
) -> List[Dict[str, Any]]:
    """Orchestrator-compatible dispatch alias."""
    return await scrape_hotfrog(
        niche=niche, city=city, country=country,
        max_leads=max_results, log_callback=log_callback, cfg=cfg,
    )
