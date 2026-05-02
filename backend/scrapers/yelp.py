"""
Yelp business directory scraper — requests + BeautifulSoup.

Strategy
--------
Phase 1 — Search pages (up to 5 × 10-result pages):
  Primary:  JSON-LD <script type="application/ld+json"> LocalBusiness blocks.
  Secondary: CSS card selectors as fallback.
  Collects /biz/ profile URLs for Phase 2.

Phase 2 — Profile page enrichment:
  For leads missing phone or website, visit /biz/{slug}.
  Profile pages reliably contain JSON-LD with phone, website, address.

Anti-bot: UA rotation, 4-8 s delays per page, 403/429 → graceful skip.
Yelp uses Cloudflare; we do NOT attempt to bypass it — if blocked, return
whatever we have rather than hanging or crashing.
"""

import asyncio
import json
import logging
import random
import re
import time
import urllib.parse
import urllib3
from typing import Any, Callable, Dict, List, Optional, Set

import requests
from bs4 import BeautifulSoup

from ..validators import clean_phone, clean_email

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger(__name__)

_BASE = "https://www.yelp.com"

_FALLBACK_UAS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# CSS selectors for Yelp's SSR HTML (subject to class-hash rotation, use broad patterns)
_CARD_SELS = [
    '[class*="businessName"]',
    '.businessName__09f24',
    'h3[class*="css"] a',
    '.lemon--li__373c0 a[href*="/biz/"]',
    'li[class*="lemon"] a[href*="/biz/"]',
    '.css-1m051bk a[href*="/biz/"]',
    'a[href^="/biz/"]',
]


def _get_ua() -> str:
    try:
        from fake_useragent import UserAgent
        return UserAgent(browsers=["Chrome", "Firefox"]).random
    except Exception:
        return random.choice(_FALLBACK_UAS)


def _headers(ua: str, referer: str = _BASE) -> Dict[str, str]:
    return {
        "User-Agent":                ua,
        "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language":           "en-US,en;q=0.9",
        "Accept-Encoding":           "gzip, deflate, br",
        "DNT":                       "1",
        "Connection":                "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer":                   referer,
        "Sec-Fetch-Dest":            "document",
        "Sec-Fetch-Mode":            "navigate",
        "Sec-Fetch-Site":            "same-origin",
    }


def _safe_get(
    url:     str,
    session: requests.Session,
    ua:      str,
    log_fn:  Callable[[str], None],
    timeout: int = 15,
) -> Optional[requests.Response]:
    for attempt in range(2):
        try:
            resp = session.get(
                url, headers=_headers(ua, _BASE), timeout=timeout,
                verify=False, allow_redirects=True,
            )
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 503):
                wait = 30 + attempt * 20
                log_fn(f"⚠️  Yelp rate-limit ({resp.status_code}) — waiting {wait}s …")
                time.sleep(wait)
                ua = _get_ua()
                continue
            if resp.status_code in (403, 999):
                log_fn(f"⚠️  Yelp blocked ({resp.status_code}) — Cloudflare active; skipping page")
                return None
            if resp.status_code in (404, 400, 410):
                return None
            log_fn(f"⚠️  Yelp HTTP {resp.status_code} for {url}")
            return None
        except requests.RequestException as exc:
            if attempt == 0:
                time.sleep(5)
            else:
                log_fn(f"⚠️  Yelp request error: {exc}")
    return None


# ── JSON-LD extraction ─────────────────────────────────────────────────────────

def _extract_ld_blocks(html: str) -> List[Dict]:
    """Return all parsed JSON-LD dicts from <script type="application/ld+json">."""
    soup   = BeautifulSoup(html, "lxml")
    blocks = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
            if isinstance(data, list):
                blocks.extend(data)
            else:
                blocks.append(data)
        except Exception:
            pass
    return blocks


def _ld_to_lead(item: Dict, niche: str, city: str) -> Optional[Dict]:
    """Convert a JSON-LD LocalBusiness/Restaurant/etc. block to a lead dict."""
    t = item.get("@type", "")
    if isinstance(t, list):
        t = " ".join(t)
    if not any(k in t for k in ("LocalBusiness", "Restaurant", "Organization",
                                 "MedicalBusiness", "Store", "Service", "Establishment")):
        return None

    name = item.get("name", "").strip()
    if not name:
        return None

    # Phone
    phone = ""
    raw_phone = item.get("telephone", "") or ""
    if raw_phone:
        phone = clean_phone(raw_phone) or ""

    # Website
    website = item.get("url", "") or ""
    if website and "yelp.com" in website.lower():
        website = ""

    # Address
    addr_obj = item.get("address", {}) or {}
    if isinstance(addr_obj, str):
        address = addr_obj
    else:
        parts = [
            addr_obj.get("streetAddress", ""),
            addr_obj.get("addressLocality", ""),
            addr_obj.get("addressRegion", ""),
        ]
        address = ", ".join(p for p in parts if p)

    # Rating / reviews
    agg = item.get("aggregateRating", {}) or {}
    try:
        rating = float(agg.get("ratingValue", 0) or 0)
    except (TypeError, ValueError):
        rating = None
    try:
        reviews = int(agg.get("reviewCount", 0) or 0)
    except (TypeError, ValueError):
        reviews = None

    return {
        "business_name": name,
        "phone":         phone    or None,
        "website":       website  or None,
        "email":         None,
        "address":       address  or None,
        "niche":         niche,
        "city":          city,
        "source":        "YELP",
        "rating":        rating,
        "reviews_count": reviews,
    }


# ── CSS card extraction (fallback) ────────────────────────────────────────────

def _extract_cards_css(html: str, niche: str, city: str) -> List[Dict]:
    """Fall back to CSS selectors when JSON-LD is absent or empty."""
    soup  = BeautifulSoup(html, "lxml")
    leads = []
    seen: Set[str] = set()

    # Collect all /biz/ anchors (name links)
    biz_anchors = soup.select('a[href^="/biz/"]')
    for a in biz_anchors:
        name = a.get_text(strip=True)
        href = a.get("href", "")
        if not name or not href or len(name) < 3:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        full_url = _BASE + href if href.startswith("/") else href
        leads.append({
            "business_name": name[:200],
            "phone":         None,
            "website":       None,
            "email":         None,
            "address":       None,
            "niche":         niche,
            "city":          city,
            "source":        "YELP",
            "rating":        None,
            "reviews_count": None,
            "_biz_url":      full_url,
        })

    return leads


# ── Profile page enrichment ───────────────────────────────────────────────────

def _enrich_from_profile(html: str, lead: Dict) -> None:
    """Mutate lead in-place using data from a /biz/ profile page."""
    for block in _extract_ld_blocks(html):
        t = block.get("@type", "")
        if isinstance(t, list):
            t = " ".join(t)
        if not any(k in t for k in ("LocalBusiness", "Restaurant", "Organization",
                                     "MedicalBusiness", "Store", "Service", "Establishment")):
            continue
        if not lead.get("phone"):
            raw = block.get("telephone", "") or ""
            if raw:
                lead["phone"] = clean_phone(raw) or None
        if not lead.get("website"):
            url = block.get("url", "") or ""
            if url and "yelp.com" not in url.lower():
                lead["website"] = url
        if not lead.get("address"):
            addr = block.get("address", {}) or {}
            if isinstance(addr, dict):
                parts = [
                    addr.get("streetAddress", ""),
                    addr.get("addressLocality", ""),
                    addr.get("addressRegion", ""),
                ]
                lead["address"] = ", ".join(p for p in parts if p) or None
        if not lead.get("email"):
            raw_email = block.get("email", "") or ""
            if raw_email:
                lead["email"] = clean_email(raw_email) or None
        break  # first matching block is enough


# ── Core sync scraper ─────────────────────────────────────────────────────────

def scrape_sync(
    niche:     str,
    city:      str,
    country:   str,
    max_leads: int,
    log_fn:    Callable[[str], None],
) -> List[Dict[str, Any]]:
    log_fn(f"⭐ Yelp: '{niche}' in '{city}' (target {max_leads})")

    session = requests.Session()
    ua      = _get_ua()

    # Prime session cookies by hitting the homepage first
    try:
        session.get(_BASE, headers=_headers(ua), timeout=10, verify=False)
        time.sleep(random.uniform(1.0, 2.0))
    except Exception:
        pass

    pool: List[Dict] = []
    seen_names: Set[str] = set()

    # ── Phase 1: search pages ─────────────────────────────────────────────────
    n_enc = urllib.parse.quote_plus(niche)
    c_enc = urllib.parse.quote_plus(f"{city}{', ' + country if country else ''}")

    for page_idx in range(5):
        if len(pool) >= max_leads * 2:
            break

        offset  = page_idx * 10
        url     = f"{_BASE}/search?find_desc={n_enc}&find_loc={c_enc}&start={offset}"
        log_fn(f"  🌐 Yelp page {page_idx + 1}: {url}")

        resp = _safe_get(url, session, ua, log_fn)
        if not resp:
            break

        # JSON-LD first
        ld_blocks  = _extract_ld_blocks(resp.text)
        ld_leads   = [_ld_to_lead(b, niche, city) for b in ld_blocks]
        page_leads = [l for l in ld_leads if l is not None]

        # CSS fallback
        if not page_leads:
            page_leads = _extract_cards_css(resp.text, niche, city)

        if not page_leads:
            log_fn(f"  ⚠️  Yelp page {page_idx + 1}: no leads (likely blocked or last page)")
            break

        new_count = 0
        for lead in page_leads:
            key = (lead.get("business_name") or "").lower().strip()
            if key and key not in seen_names:
                seen_names.add(key)
                pool.append(lead)
                new_count += 1

        log_fn(f"  Page {page_idx + 1}: {new_count} new leads ({len(pool)} total)")

        if new_count == 0:
            break  # no new results → stop paginating
        if page_idx < 4:
            time.sleep(random.uniform(4.0, 8.0))

    if not pool:
        log_fn("⚠️  Yelp returned no results")
        return []

    log_fn(f"📋 Pool: {len(pool)} — enriching profiles for missing data …")

    # ── Phase 2: profile page visits ─────────────────────────────────────────
    target    = pool[:max_leads]
    p_session = requests.Session()

    for i, lead in enumerate(target, 1):
        biz_url = lead.pop("_biz_url", None)

        # Skip enrichment if we already have both phone and website
        if lead.get("phone") and lead.get("website"):
            log_fn(
                f"  [{i}/{len(target)}] ✅ {lead['business_name']}"
                f" (phone+web already known)"
            )
            continue

        if not biz_url:
            continue

        resp = _safe_get(biz_url, p_session, _get_ua(), log_fn, timeout=12)
        if resp:
            _enrich_from_profile(resp.text, lead)

        log_fn(
            f"  [{i}/{len(target)}] "
            + ("✅" if lead.get("phone") or lead.get("website") else "📋")
            + f" {lead['business_name']}"
            + (f" — {lead.get('website', '')}" if lead.get("website") else "")
        )
        time.sleep(random.uniform(2.5, 5.0))

    # Final cleanup
    for lead in target:
        lead.pop("_biz_url", None)

    phone_cnt = sum(1 for l in target if l.get("phone"))
    web_cnt   = sum(1 for l in target if l.get("website"))
    log_fn(f"🏁 Yelp done — {len(target)} leads | {phone_cnt} phones | {web_cnt} websites")
    return target


# ── Public async API ──────────────────────────────────────────────────────────

async def scrape_yelp(
    niche:        str,
    city:         str,
    country:      str = "",
    max_leads:    int = 30,
    log_callback: Optional[Callable[[str], None]] = None,
) -> List[Dict[str, Any]]:
    def _log(msg: str) -> None:
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass
    _log(f"🚀 Yelp scraper: {niche} in {city} (max {max_leads})")
    return await asyncio.to_thread(scrape_sync, niche, city, country, max_leads, _log)


async def scrape(
    niche:        str,
    city:         str,
    max_results:  int = 30,
    cfg:          Optional[Dict[str, Any]] = None,
    log_callback: Optional[Callable[[str], None]] = None,
    country:      str = "",
) -> List[Dict[str, Any]]:
    """Orchestrator-compatible dispatch alias."""
    return await scrape_yelp(
        niche=niche, city=city, country=country,
        max_leads=max_results, log_callback=log_callback,
    )
