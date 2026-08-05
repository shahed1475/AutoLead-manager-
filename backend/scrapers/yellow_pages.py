"""
Yellow Pages scraper — requests + BeautifulSoup (HTTP-only, no Selenium).

Strategy
--------
Phase 1 — Search pages (up to 5 pages × ~30 results):
  URL: https://www.yellowpages.com/search?search_terms={niche}&geo_location_terms={city}
  Primary:  JSON-LD ItemList / LocalBusiness blocks.
  Fallback: CSS selectors on .organic.result cards.
  Collects detail page URLs for Phase 2.

Phase 2 — Detail page visits:
  For cards missing phone or website, visit the business profile page.
  Profile pages reliably contain JSON-LD and schema.org markup.

Anti-ban: UA rotation, 3-6 s delays, 429 → 30 s backoff.
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
from ._shared import resolve_delay

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger(__name__)

_BASE = "https://www.yellowpages.com"

_FALLBACK_UAS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
]

# ── CSS selectors (Yellow Pages has stable class names) ────────────────────────
_CARD_SEL   = ".organic.result"
_NAME_SELS  = [".business-name a", ".business-name span", "h2.n a", "h2 a", ".listing-name a"]
_PHONE_SELS = [".phones.phone.primary", ".phone", ".contact .phone", "a[href^='tel:']"]
_ADDR_SELS  = [".adr", ".street-address", ".locality", "[itemprop='address']"]
_WEB_SELS   = ["a.track-visit-website", "a[class*='website']", "a[href*='http'][rel*='nofollow']"]
_LINK_SELS  = ["a.business-name", "h2.n a", ".listing-name a"]


def _get_ua() -> str:
    try:
        from fake_useragent import UserAgent
        return UserAgent(browsers=["Chrome", "Firefox", "Edge"]).random
    except Exception:
        return random.choice(_FALLBACK_UAS)


def _headers(ua: str) -> Dict[str, str]:
    return {
        "User-Agent":                ua,
        "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language":           "en-US,en;q=0.9",
        "Accept-Encoding":           "gzip, deflate, br",
        "DNT":                       "1",
        "Connection":                "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer":                   "https://www.yellowpages.com/",
        "Sec-Fetch-Dest":            "document",
        "Sec-Fetch-Mode":            "navigate",
        "Sec-Fetch-Site":            "same-origin",
        "Sec-Fetch-User":            "?1",
        "Cache-Control":             "max-age=0",
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
            resp = session.get(
                url, headers=_headers(ua), timeout=timeout,
                verify=False, allow_redirects=True,
            )
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 503):
                wait = 30 + attempt * 20
                log_fn(f"⚠️  YP rate-limit ({resp.status_code}) — waiting {wait}s …")
                time.sleep(wait)
                ua = _get_ua()
                continue
            if resp.status_code in (403, 404, 400, 410):
                return None
            log_fn(f"⚠️  YP HTTP {resp.status_code}")
            return None
        except requests.RequestException as exc:
            if attempt == 0:
                time.sleep(5)
            else:
                log_fn(f"⚠️  YP request error: {exc}")
    return None


# ── JSON-LD extraction ─────────────────────────────────────────────────────────

def _extract_ld_leads(html: str, niche: str, city: str) -> List[Dict]:
    soup   = BeautifulSoup(html, "lxml")
    blocks = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
            if isinstance(data, list):
                blocks.extend(data)
            elif isinstance(data, dict):
                # Unwrap ItemList
                if data.get("@type") == "ItemList":
                    for item in data.get("itemListElement", []):
                        if isinstance(item, dict) and item.get("item"):
                            blocks.append(item["item"])
                        elif isinstance(item, dict):
                            blocks.append(item)
                else:
                    blocks.append(data)
        except Exception:
            pass

    leads = []
    for block in blocks:
        t = block.get("@type", "")
        if isinstance(t, list):
            t = " ".join(t)
        biz_types = ("LocalBusiness", "Restaurant", "Organization",
                     "MedicalBusiness", "Store", "Service", "Establishment",
                     "Plumber", "Electrician", "Dentist", "Attorney")
        if not any(k in t for k in biz_types):
            continue

        name = (block.get("name") or "").strip()
        if not name:
            continue

        phone   = clean_phone(block.get("telephone", "") or "") or None
        website = (block.get("url") or "").strip() or None
        if website and "yellowpages.com" in (website or "").lower():
            website = None

        addr_obj = block.get("address", {}) or {}
        if isinstance(addr_obj, str):
            address = addr_obj
        else:
            parts   = [
                addr_obj.get("streetAddress", ""),
                addr_obj.get("addressLocality", ""),
                addr_obj.get("addressRegion", ""),
                addr_obj.get("postalCode", ""),
            ]
            address = ", ".join(p for p in parts if p) or None

        agg = block.get("aggregateRating", {}) or {}
        try:
            rating = float(agg.get("ratingValue", 0) or 0) or None
        except (TypeError, ValueError):
            rating = None
        try:
            reviews = int(agg.get("reviewCount", 0) or 0) or None
        except (TypeError, ValueError):
            reviews = None

        leads.append({
            "business_name": name[:200],
            "phone":         phone,
            "website":       website,
            "email":         None,
            "address":       address,
            "niche":         niche,
            "city":          city,
            "source":        "YELLOW_PAGES",
            "rating":        rating,
            "reviews_count": reviews,
        })

    return leads


# ── CSS card extraction ────────────────────────────────────────────────────────

def _first_text(root: Any, selectors: List[str]) -> str:
    for sel in selectors:
        el = root.select_one(sel)
        if el:
            text = el.get_text(strip=True)
            if text:
                return text[:200]
    return ""


def _extract_cards_css(html: str, niche: str, city: str) -> List[Dict]:
    soup  = BeautifulSoup(html, "lxml")
    cards = soup.select(_CARD_SEL)

    if not cards:
        # Broader fallback
        cards = soup.select(".result, .business-listing, li.listing, [class*='result']")

    leads = []
    for card in cards:
        # Name
        name = _first_text(card, _NAME_SELS)
        if not name:
            continue

        # Phone
        phone = ""
        for sel in _PHONE_SELS:
            el = card.select_one(sel)
            if el:
                raw = (el.get("href") or "").replace("tel:", "") or el.get_text(strip=True)
                phone = clean_phone(raw) or ""
                if phone:
                    break

        # Address
        address = _first_text(card, _ADDR_SELS)

        # Website (external link on result cards)
        website = ""
        for sel in _WEB_SELS:
            el = card.select_one(sel)
            if el:
                href = el.get("href", "")
                if href and "yellowpages" not in href.lower() and href.startswith("http"):
                    website = href
                    break

        # Detail URL
        detail_url = ""
        for sel in _LINK_SELS:
            el = card.select_one(sel)
            if el and el.name == "a":
                href = el.get("href", "")
                if href:
                    detail_url = href if href.startswith("http") else _BASE + href
                    break

        # Rating
        rating_el = card.select_one(".rating, [class*='rating']")
        rating = None
        if rating_el:
            try:
                rating = float(rating_el.get("data-rating") or
                               rating_el.get_text(strip=True).split()[0])
            except (TypeError, ValueError, IndexError):
                pass

        leads.append({
            "business_name": name,
            "phone":         phone    or None,
            "website":       website  or None,
            "email":         None,
            "address":       address  or None,
            "niche":         niche,
            "city":          city,
            "source":        "YELLOW_PAGES",
            "rating":        rating,
            "reviews_count": None,
            "_detail_url":   detail_url or None,
        })

    return leads


# ── Profile enrichment ─────────────────────────────────────────────────────────

def _enrich_from_detail(html: str, lead: Dict) -> None:
    """Mutate lead in-place from a YP business detail page."""
    soup = BeautifulSoup(html, "lxml")

    if not lead.get("phone"):
        el = soup.select_one(".phones.phone.primary, .phone, [itemprop='telephone']")
        if el:
            raw = (el.get("href") or "").replace("tel:", "") or el.get_text(strip=True)
            lead["phone"] = clean_phone(raw) or None

    if not lead.get("website"):
        el = soup.select_one("a.track-visit-website, a[class*='website'], [itemprop='url']")
        if el:
            href = el.get("href", "") or el.get("content", "")
            if href and "yellowpages" not in href.lower():
                lead["website"] = href if href.startswith("http") else None

    if not lead.get("email"):
        # Try JSON-LD on detail page
        for block in _extract_ld_leads(html, lead["niche"], lead["city"]):
            e = block.get("email")
            if e:
                lead["email"] = clean_email(e) or None
                break

    if not lead.get("address"):
        el = soup.select_one(".adr, [itemprop='address'], .street-address")
        if el:
            lead["address"] = el.get_text(strip=True)[:200] or None


# ── Core sync scraper ─────────────────────────────────────────────────────────

def scrape_sync(
    niche:     str,
    city:      str,
    country:   str,
    max_leads: int,
    log_fn:    Callable[[str], None],
    cfg:       Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    log_fn(f"📒 Yellow Pages: '{niche}' in '{city}' (target {max_leads})")
    delay_min, delay_max = resolve_delay(cfg, 3.0, 6.0)

    session    = requests.Session()
    ua         = _get_ua()
    pool:      List[Dict] = []
    seen_names: Set[str] = set()

    n_enc = urllib.parse.quote_plus(niche)
    c_enc = urllib.parse.quote_plus(f"{city}{', ' + country if country else ''}")

    # ── Phase 1: search pages ─────────────────────────────────────────────────
    for page in range(1, 6):
        if len(pool) >= max_leads * 2:
            break

        url = (
            f"{_BASE}/search?search_terms={n_enc}&geo_location_terms={c_enc}"
            + (f"&page={page}" if page > 1 else "")
        )
        log_fn(f"  🌐 YP page {page}: {url}")

        resp = _safe_get(url, session, ua, log_fn)
        if not resp:
            break

        # JSON-LD first; fall back to CSS
        page_leads = _extract_ld_leads(resp.text, niche, city)
        if not page_leads:
            page_leads = _extract_cards_css(resp.text, niche, city)

        if not page_leads:
            log_fn(f"  ⚠️  YP page {page}: no cards found — stopping")
            break

        new_count = 0
        for lead in page_leads:
            key = (lead.get("business_name") or "").lower().strip()
            if key and key not in seen_names:
                seen_names.add(key)
                pool.append(lead)
                new_count += 1

        log_fn(f"  Page {page}: {new_count} new leads ({len(pool)} total)")
        if new_count == 0:
            break
        if page < 5:
            time.sleep(random.uniform(delay_min, delay_max))

    if not pool:
        log_fn("⚠️  Yellow Pages returned no results")
        return []

    log_fn(f"📋 Pool: {len(pool)} — enriching detail pages …")

    # ── Phase 2: detail page enrichment ──────────────────────────────────────
    target    = pool[:max_leads]
    d_session = requests.Session()

    for i, lead in enumerate(target, 1):
        detail_url = lead.pop("_detail_url", None)

        if lead.get("phone") and lead.get("website"):
            log_fn(f"  [{i}/{len(target)}] ✅ {lead['business_name']} (complete)")
            continue

        if not detail_url:
            continue

        resp = _safe_get(detail_url, d_session, _get_ua(), log_fn, timeout=12)
        if resp:
            _enrich_from_detail(resp.text, lead)

        log_fn(
            f"  [{i}/{len(target)}] "
            + ("✅" if lead.get("phone") or lead.get("website") else "📋")
            + f" {lead['business_name']}"
        )
        time.sleep(random.uniform(2.0, 4.5))

    # Cleanup internal keys
    for lead in target:
        lead.pop("_detail_url", None)

    phone_cnt = sum(1 for l in target if l.get("phone"))
    web_cnt   = sum(1 for l in target if l.get("website"))
    log_fn(f"🏁 Yellow Pages done — {len(target)} leads | {phone_cnt} phones | {web_cnt} websites")
    return target


# ── Public async API ──────────────────────────────────────────────────────────

async def scrape_yellow_pages(
    niche:        str,
    city:         str,
    country:      str = "",
    max_leads:    int = 30,
    log_callback: Optional[Callable[[str], None]] = None,
    cfg:          Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    def _log(msg: str) -> None:
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass
    _log(f"🚀 Yellow Pages scraper: {niche} in {city} (max {max_leads})")
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
    return await scrape_yellow_pages(
        niche=niche, city=city, country=country,
        max_leads=max_results, log_callback=log_callback, cfg=cfg,
    )
