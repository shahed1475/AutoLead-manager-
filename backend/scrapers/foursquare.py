"""
Foursquare Places scraper — requests + BeautifulSoup / JSON extraction.

Strategy
--------
Phase 1 — Explore page (Next.js SSR):
  URL: https://foursquare.com/explore?near={city}&q={niche}
  Primary:  __NEXT_DATA__ JSON embedded in <script id="__NEXT_DATA__">.
            Contains props.pageProps.venues (array of venue objects).
  Fallback: JSON-LD LocalBusiness blocks from the page.

  Each venue contains: id, name, location (address, city, country),
  contact (phone, twitter, formattedPhone), url (website), categories.

Phase 2 — Venue page enrichment:
  For venues missing phone or website, visit
  https://foursquare.com/v/{slug}/{id} to get the full JSON-LD profile.

Anti-ban: UA rotation, 3-7 s delays, 429 → 30 s backoff.
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

_BASE = "https://foursquare.com"

_FALLBACK_UAS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]


def _get_ua() -> str:
    try:
        from fake_useragent import UserAgent
        return UserAgent(browsers=["Chrome", "Firefox"]).random
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
    timeout: int = 15,
) -> Optional[requests.Response]:
    for attempt in range(2):
        try:
            resp = session.get(
                url, headers=_headers(ua), timeout=timeout,
                verify=False, allow_redirects=True,
            )
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 503):
                wait = 30 + attempt * 20
                log_fn(f"⚠️  Foursquare rate-limit ({resp.status_code}) — waiting {wait}s …")
                time.sleep(wait)
                ua = _get_ua()
                continue
            if resp.status_code in (403, 404, 400, 410):
                return None
            log_fn(f"⚠️  Foursquare HTTP {resp.status_code}")
            return None
        except requests.RequestException as exc:
            if attempt == 0:
                time.sleep(5)
            else:
                log_fn(f"⚠️  Foursquare request error: {exc}")
    return None


# ── __NEXT_DATA__ extraction ───────────────────────────────────────────────────

def _extract_next_data(html: str) -> Optional[Dict]:
    """Parse the __NEXT_DATA__ script tag from a Next.js page."""
    soup = BeautifulSoup(html, "lxml")

    # Method 1: <script id="__NEXT_DATA__">
    tag = soup.find("script", id="__NEXT_DATA__")
    if tag and tag.string:
        try:
            return json.loads(tag.string)
        except Exception:
            pass

    # Method 2: window.__NEXT_DATA__ = {...}; inline script
    for script in soup.find_all("script"):
        text = script.string or ""
        if "__NEXT_DATA__" in text:
            m = re.search(r"__NEXT_DATA__\s*=\s*(\{.*?\});", text, re.S)
            if m:
                try:
                    return json.loads(m.group(1))
                except Exception:
                    pass

    return None


def _walk_for_venues(obj: Any, depth: int = 0) -> List[Dict]:
    """Recursively find venue-like objects inside __NEXT_DATA__."""
    if depth > 10 or not obj:
        return []

    venues = []

    if isinstance(obj, list):
        for item in obj:
            venues.extend(_walk_for_venues(item, depth + 1))

    elif isinstance(obj, dict):
        # Check if this looks like a venue record
        if obj.get("name") and (obj.get("contact") or obj.get("location")):
            venues.append(obj)
        else:
            for v in obj.values():
                venues.extend(_walk_for_venues(v, depth + 1))

    return venues


def _venue_to_lead(venue: Dict, niche: str, city: str) -> Optional[Dict]:
    """Convert a Foursquare venue dict to a lead dict."""
    name = (venue.get("name") or "").strip()
    if not name:
        return None

    # Contact info
    contact = venue.get("contact", {}) or {}
    phone_raw = (
        contact.get("formattedPhone") or
        contact.get("phone") or
        ""
    )
    phone   = clean_phone(phone_raw) or None
    website = (venue.get("url") or contact.get("url") or "").strip() or None

    # Reject internal Foursquare URLs
    if website and "foursquare.com" in website.lower():
        website = None

    # Location
    location = venue.get("location", {}) or {}
    if isinstance(location, dict):
        parts = [
            location.get("address", ""),
            location.get("crossStreet", ""),
            location.get("city", "") or city,
            location.get("country", ""),
        ]
        address = ", ".join(p for p in parts if p) or None
    else:
        address = str(location) or None

    # Build venue profile URL for Phase 2
    vid   = venue.get("id", "")
    slug  = re.sub(r"[^\w]", "-", name.lower())[:50]
    v_url = f"{_BASE}/v/{slug}/{vid}" if vid else None

    # Stats
    stats = venue.get("stats", {}) or {}
    try:
        reviews = int(stats.get("checkinsCount", 0) or 0)
    except (TypeError, ValueError):
        reviews = None

    return {
        "business_name": name[:200],
        "phone":         phone,
        "website":       website,
        "email":         None,
        "address":       address,
        "niche":         niche,
        "city":          city,
        "source":        "FOURSQUARE",
        "reviews_count": reviews,
        "_venue_url":    v_url,
    }


# ── JSON-LD fallback ───────────────────────────────────────────────────────────

def _extract_ld_leads(html: str, niche: str, city: str) -> List[Dict]:
    soup   = BeautifulSoup(html, "lxml")
    leads  = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        blocks = data if isinstance(data, list) else [data]
        for block in blocks:
            t = block.get("@type", "")
            if isinstance(t, list):
                t = " ".join(t)
            if not any(k in t for k in ("LocalBusiness", "Restaurant", "Organization",
                                         "MedicalBusiness", "Store", "Service")):
                continue
            name = (block.get("name") or "").strip()
            if not name:
                continue
            phone   = clean_phone(block.get("telephone", "") or "") or None
            website = (block.get("url") or "").strip() or None
            if website and "foursquare.com" in (website or "").lower():
                website = None
            addr    = block.get("address", {}) or {}
            if isinstance(addr, dict):
                parts   = [addr.get("streetAddress", ""), addr.get("addressLocality", "")]
                address = ", ".join(p for p in parts if p) or None
            else:
                address = str(addr)[:200] or None
            leads.append({
                "business_name": name[:200],
                "phone":         phone,
                "website":       website,
                "email":         None,
                "address":       address,
                "niche":         niche,
                "city":          city,
                "source":        "FOURSQUARE",
                "reviews_count": None,
            })
    return leads


# ── Venue page enrichment ─────────────────────────────────────────────────────

def _enrich_from_venue_page(html: str, lead: Dict) -> None:
    """Mutate lead in-place using data from the Foursquare venue profile page."""
    # Try __NEXT_DATA__ first
    next_data = _extract_next_data(html)
    if next_data:
        venues = _walk_for_venues(next_data)
        for v in venues:
            if not lead.get("phone"):
                contact = v.get("contact", {}) or {}
                raw = contact.get("formattedPhone") or contact.get("phone") or ""
                lead["phone"] = clean_phone(raw) or None
            if not lead.get("website"):
                url = (v.get("url") or "").strip()
                if url and "foursquare.com" not in url.lower():
                    lead["website"] = url
            if lead.get("phone") or lead.get("website"):
                break

    # JSON-LD fallback
    if not lead.get("phone") and not lead.get("website"):
        for ld in _extract_ld_leads(html, lead["niche"], lead["city"]):
            if not lead.get("phone") and ld.get("phone"):
                lead["phone"] = ld["phone"]
            if not lead.get("website") and ld.get("website"):
                lead["website"] = ld["website"]
            break


# ── Core sync scraper ─────────────────────────────────────────────────────────

def scrape_sync(
    niche:     str,
    city:      str,
    country:   str,
    max_leads: int,
    log_fn:    Callable[[str], None],
) -> List[Dict[str, Any]]:
    log_fn(f"📍 Foursquare: '{niche}' in '{city}' (target {max_leads})")

    session    = requests.Session()
    ua         = _get_ua()
    pool:      List[Dict] = []
    seen_names: Set[str] = set()

    n_enc = urllib.parse.quote_plus(niche)
    c_enc = urllib.parse.quote_plus(city)

    # Foursquare explore URL variants
    urls = [
        f"{_BASE}/explore?near={c_enc}&q={n_enc}",
        f"{_BASE}/explore?near={c_enc}&q={n_enc}&mode=url",
        f"{_BASE}/search?near={c_enc}&q={n_enc}",
    ]

    # ── Phase 1: collect venues ───────────────────────────────────────────────
    for base_url in urls:
        if pool:
            break  # stop once we have results

        log_fn(f"  🌐 {base_url}")
        resp = _safe_get(base_url, session, ua, log_fn)
        if not resp:
            continue

        # Try __NEXT_DATA__
        next_data = _extract_next_data(resp.text)
        page_leads: List[Dict] = []

        if next_data:
            venues = _walk_for_venues(next_data)
            log_fn(f"  📦 Found {len(venues)} venue(s) in __NEXT_DATA__")
            for v in venues:
                ld = _venue_to_lead(v, niche, city)
                if ld:
                    page_leads.append(ld)

        # JSON-LD fallback
        if not page_leads:
            page_leads = _extract_ld_leads(resp.text, niche, city)
            if page_leads:
                log_fn(f"  📦 Found {len(page_leads)} lead(s) via JSON-LD fallback")

        if not page_leads:
            log_fn(f"  ⚠️  No venues found at {base_url} — trying next variant")
            time.sleep(random.uniform(2.0, 4.0))
            continue

        for lead in page_leads:
            key = (lead.get("business_name") or "").lower().strip()
            if key and key not in seen_names:
                seen_names.add(key)
                pool.append(lead)

        log_fn(f"  ✅ {len(pool)} unique venues collected")
        time.sleep(random.uniform(3.0, 6.0))

    if not pool:
        log_fn("⚠️  Foursquare returned no results for this niche/city")
        return []

    log_fn(f"📋 Pool: {len(pool)} — enriching venue pages …")

    # ── Phase 2: venue page enrichment ───────────────────────────────────────
    target    = pool[:max_leads]
    v_session = requests.Session()

    for i, lead in enumerate(target, 1):
        venue_url = lead.pop("_venue_url", None)

        if lead.get("phone") and lead.get("website"):
            log_fn(f"  [{i}/{len(target)}] ✅ {lead['business_name']} (complete)")
            continue

        if not venue_url:
            continue

        resp = _safe_get(venue_url, v_session, _get_ua(), log_fn, timeout=12)
        if resp:
            _enrich_from_venue_page(resp.text, lead)

        log_fn(
            f"  [{i}/{len(target)}] "
            + ("✅" if lead.get("phone") or lead.get("website") else "📋")
            + f" {lead['business_name']}"
        )
        time.sleep(random.uniform(2.0, 5.0))

    # Cleanup
    for lead in target:
        lead.pop("_venue_url", None)

    phone_cnt = sum(1 for l in target if l.get("phone"))
    web_cnt   = sum(1 for l in target if l.get("website"))
    log_fn(f"🏁 Foursquare done — {len(target)} leads | {phone_cnt} phones | {web_cnt} websites")
    return target


# ── Public async API ──────────────────────────────────────────────────────────

async def scrape_foursquare(
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
    _log(f"🚀 Foursquare scraper: {niche} in {city} (max {max_leads})")
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
    return await scrape_foursquare(
        niche=niche, city=city, country=country,
        max_leads=max_results, log_callback=log_callback,
    )
