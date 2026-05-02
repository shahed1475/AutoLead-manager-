"""
Configurable generic business-directory scraper.

Supports any site via a SiteConfig descriptor.  Built-in configs cover:
  - manta.com        — general business directory
  - bark.com         — service professionals
  - angi.com         — home services
  - houzz.com        — home improvement / interior design
  - buildzoom.com    — licensed contractors
  - thumbtack.com    — local services

Usage (orchestrator)
--------------------
  from .generic_directory import scrape

  leads = await scrape(niche="dentist", city="Dallas", max_results=30,
                       cfg={"site": "manta"},   # optional
                       log_callback=print)

If cfg["site"] is omitted, the scraper tries all built-in configs and merges
the results, stopping once max_results is reached.

Each SiteConfig defines:
  base_url        — e.g. "https://www.manta.com"
  search_url_fn   — callable(niche, city) -> str
  card_sel        — CSS selector for result cards on search page
  name_sel        — CSS selector for business name within a card
  phone_sel       — CSS selector for phone within a card
  addr_sel        — CSS selector for address within a card
  link_sel        — CSS selector for detail page link within a card
  web_sel         — CSS selector for website link on detail page
  paginate_fn     — callable(base_url, page) -> str  (None = no pagination)
  max_pages       — how many search result pages to crawl

Anti-ban: UA rotation, 3-8 s delays, 429 backoff.
"""

import asyncio
import json
import logging
import random
import re
import time
import urllib.parse
import urllib3
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

import requests
from bs4 import BeautifulSoup

from ..validators import clean_phone, clean_email

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger(__name__)

_FALLBACK_UAS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
]


# ── SiteConfig ────────────────────────────────────────────────────────────────

@dataclass
class SiteConfig:
    name:         str
    base_url:     str
    search_url_fn: Callable[[str, str], str]
    card_sel:     str
    name_sel:     str
    phone_sel:    str
    addr_sel:     str
    link_sel:     str
    web_sel:      str
    paginate_fn:  Optional[Callable[[str, int], str]] = None
    max_pages:    int = 4
    source_tag:   str = "GENERIC_DIR"


def _std_paginate(url: str, page: int) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}page={page}"


# ── Built-in site configs ─────────────────────────────────────────────────────

BUILT_IN_CONFIGS: Dict[str, SiteConfig] = {

    "manta": SiteConfig(
        name         = "Manta",
        base_url     = "https://www.manta.com",
        search_url_fn= lambda n, c: (
            f"https://www.manta.com/mb?search={urllib.parse.quote_plus(n)}"
            f"&location={urllib.parse.quote_plus(c)}"
        ),
        card_sel     = ".search-result-item, .company-results article, li.result",
        name_sel     = ".company-name a, h2 a, .listing-name a",
        phone_sel    = ".phone, .tel, a[href^='tel:'], [class*='phone']",
        addr_sel     = ".address, .location, [itemprop='address']",
        link_sel     = ".company-name a, h2 a",
        web_sel      = "a.website, a[href*='http'][rel='nofollow'], a[class*='website']",
        paginate_fn  = _std_paginate,
        source_tag   = "GENERIC_DIR",
    ),

    "bark": SiteConfig(
        name         = "Bark",
        base_url     = "https://www.bark.com",
        search_url_fn= lambda n, c: (
            f"https://www.bark.com/find/local/{urllib.parse.quote(n.replace(' ', '-'))}"
            f"/{urllib.parse.quote(c.replace(' ', '-'))}/"
        ),
        card_sel     = ".pro-card, .bark-card, .provider-card, [class*='pro-listing']",
        name_sel     = ".pro-name, .bark-name, h2 a, h3 a, [class*='name']",
        phone_sel    = "a[href^='tel:'], .phone, [class*='phone']",
        addr_sel     = ".location, .address, [class*='location']",
        link_sel     = "a[href*='/find/'], h2 a, h3 a",
        web_sel      = "a[href*='http'][rel*='nofollow'], a[class*='website']",
        paginate_fn  = _std_paginate,
        source_tag   = "GENERIC_DIR",
    ),

    "angi": SiteConfig(
        name         = "Angi",
        base_url     = "https://www.angi.com",
        search_url_fn= lambda n, c: (
            f"https://www.angi.com/companylist/"
            f"{urllib.parse.quote(c.replace(' ', '-').lower())}/"
            f"{urllib.parse.quote(n.replace(' ', '-').lower())}.htm"
        ),
        card_sel     = ".company-profile, .pro-listing, [class*='provider']",
        name_sel     = ".business-name, h2, h3, [class*='name']",
        phone_sel    = "a[href^='tel:'], .phone, [class*='phone']",
        addr_sel     = ".address, .location, [class*='address']",
        link_sel     = "h2 a, .business-name a, [class*='name'] a",
        web_sel      = "a[href*='http'][rel*='nofollow'], a[class*='website'], a.visit-site",
        paginate_fn  = None,
        max_pages    = 2,
        source_tag   = "GENERIC_DIR",
    ),

    "houzz": SiteConfig(
        name         = "Houzz",
        base_url     = "https://www.houzz.com",
        search_url_fn= lambda n, c: (
            f"https://www.houzz.com/professionals/"
            f"{urllib.parse.quote(n.replace(' ', '-').lower())}/"
            f"{urllib.parse.quote(c.replace(' ', '-').lower())}"
        ),
        card_sel     = ".hz-pro-search-results-card, .pro-card, [class*='result-card']",
        name_sel     = "[class*='business-name'], h2, h3, [class*='displayName']",
        phone_sel    = "a[href^='tel:'], [class*='phone']",
        addr_sel     = "[class*='location'], [class*='city'], address",
        link_sel     = "a[href*='/pro/'], a[class*='profile'], h2 a",
        web_sel      = "a[href*='http'][rel*='nofollow'], a[class*='website']",
        paginate_fn  = _std_paginate,
        max_pages    = 3,
        source_tag   = "GENERIC_DIR",
    ),

    "buildzoom": SiteConfig(
        name         = "BuildZoom",
        base_url     = "https://www.buildzoom.com",
        search_url_fn= lambda n, c: (
            f"https://www.buildzoom.com/contractors/"
            f"{urllib.parse.quote(c.replace(' ', '-').lower())}"
            f"?q={urllib.parse.quote_plus(n)}"
        ),
        card_sel     = ".contractor-card, .contractor-result, [class*='contractor']",
        name_sel     = ".contractor-name, h2, h3, [class*='name']",
        phone_sel    = "a[href^='tel:'], .phone",
        addr_sel     = ".address, .location",
        link_sel     = "h2 a, h3 a, .contractor-name a",
        web_sel      = "a[href*='http'][rel*='nofollow'], a.website",
        paginate_fn  = _std_paginate,
        max_pages    = 4,
        source_tag   = "GENERIC_DIR",
    ),

    "thumbtack": SiteConfig(
        name         = "Thumbtack",
        base_url     = "https://www.thumbtack.com",
        search_url_fn= lambda n, c: (
            f"https://www.thumbtack.com/k/{urllib.parse.quote(n.replace(' ', '-').lower())}"
            f"/{urllib.parse.quote(c.replace(' ', '-').lower())}/"
        ),
        card_sel     = "[class*='professional-card'], [data-testid*='pro'], [class*='pro-card']",
        name_sel     = "[class*='business-name'], h2, h3",
        phone_sel    = "a[href^='tel:'], [class*='phone']",
        addr_sel     = "[class*='location'], [class*='city']",
        link_sel     = "a[href*='/k/'], a[href*='/pro/']",
        web_sel      = "a[href*='http'][rel*='nofollow']",
        paginate_fn  = None,
        max_pages    = 2,
        source_tag   = "GENERIC_DIR",
    ),
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_ua() -> str:
    try:
        from fake_useragent import UserAgent
        return UserAgent(browsers=["Chrome", "Firefox", "Edge"]).random
    except Exception:
        return random.choice(_FALLBACK_UAS)


def _headers(ua: str, base: str) -> Dict[str, str]:
    return {
        "User-Agent":                ua,
        "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language":           "en-US,en;q=0.9",
        "Accept-Encoding":           "gzip, deflate, br",
        "DNT":                       "1",
        "Connection":                "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer":                   base,
    }


def _safe_get(
    url:     str,
    session: requests.Session,
    ua:      str,
    base:    str,
    log_fn:  Callable[[str], None],
    timeout: int = 12,
) -> Optional[requests.Response]:
    for attempt in range(2):
        try:
            resp = session.get(
                url, headers=_headers(ua, base), timeout=timeout,
                verify=False, allow_redirects=True,
            )
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 503):
                wait = 25 + attempt * 20
                log_fn(f"⚠️  Rate-limit ({resp.status_code}) on {base} — waiting {wait}s …")
                time.sleep(wait)
                ua = _get_ua()
                continue
            if resp.status_code in (403, 404, 400, 410):
                return None
            log_fn(f"⚠️  HTTP {resp.status_code} from {url[:60]}")
            return None
        except requests.RequestException as exc:
            if attempt == 0:
                time.sleep(5)
            else:
                log_fn(f"⚠️  Request error: {exc}")
    return None


def _first_text(root: Any, selector: str) -> str:
    el = root.select_one(selector) if selector else None
    if not el:
        return ""
    return el.get_text(strip=True)[:200]


def _extract_json_ld_contacts(html: str) -> Dict[str, Optional[str]]:
    """Try to extract phone/website/email from JSON-LD on a detail page."""
    soup   = BeautifulSoup(html, "lxml")
    result = {"phone": None, "website": None, "email": None}
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
            if not any(k in t for k in ("LocalBusiness", "Organization",
                                         "Service", "Store", "Establishment")):
                continue
            if not result["phone"] and block.get("telephone"):
                result["phone"] = clean_phone(block["telephone"]) or None
            if not result["website"] and block.get("url"):
                url = block["url"].strip()
                # Keep only external URLs
                if url and not any(d in url for d in ("manta.com", "bark.com",
                                                       "angi.com", "houzz.com",
                                                       "buildzoom.com", "thumbtack.com")):
                    result["website"] = url
            if not result["email"] and block.get("email"):
                result["email"] = clean_email(block["email"]) or None
    return result


# ── Per-site scrape function ──────────────────────────────────────────────────

def _scrape_one_site(
    cfg:       SiteConfig,
    niche:     str,
    city:      str,
    max_leads: int,
    log_fn:    Callable[[str], None],
    seen:      Set[str],
) -> List[Dict]:
    session = requests.Session()
    ua      = _get_ua()
    pool:   List[Dict] = []
    base    = cfg.base_url

    search_url = cfg.search_url_fn(niche, city)
    log_fn(f"  🌐 {cfg.name}: {search_url}")

    for page in range(1, cfg.max_pages + 1):
        if len(pool) + len(seen) >= max_leads * 2:
            break

        url  = search_url if page == 1 else (
            cfg.paginate_fn(search_url, page) if cfg.paginate_fn else None
        )
        if not url:
            break

        resp = _safe_get(url, session, ua, base, log_fn)
        if not resp:
            break

        soup  = BeautifulSoup(resp.text, "lxml")
        cards = soup.select(cfg.card_sel)

        # Broader fallback
        if not cards:
            cards = soup.select(".result, .listing, article, li.item")

        if not cards:
            if page == 1:
                log_fn(f"  ⚠️  {cfg.name}: no cards on page 1 — skipping site")
            break

        new_count = 0
        for card in cards:
            # Name
            name = _first_text(card, cfg.name_sel)
            if not name or len(name) < 2:
                continue
            key = name.lower().strip()
            if key in seen:
                continue

            # Phone
            phone = ""
            if cfg.phone_sel:
                el = card.select_one(cfg.phone_sel)
                if el:
                    raw = (el.get("href") or "").replace("tel:", "") or el.get_text(strip=True)
                    phone = clean_phone(raw) or ""

            # Address
            address = _first_text(card, cfg.addr_sel) if cfg.addr_sel else ""

            # Website (try detail link on card)
            website = ""
            if cfg.web_sel:
                el = card.select_one(cfg.web_sel)
                if el:
                    href = el.get("href", "")
                    if href and href.startswith("http") and base not in href:
                        website = href

            # Detail URL
            detail_url = ""
            if cfg.link_sel:
                el = card.select_one(cfg.link_sel)
                if el and el.name == "a":
                    href = el.get("href", "")
                    if href:
                        detail_url = href if href.startswith("http") else base + href

            seen.add(key)
            pool.append({
                "business_name": name,
                "phone":         phone   or None,
                "website":       website or None,
                "email":         None,
                "address":       address or None,
                "niche":         niche,
                "city":          city,
                "source":        cfg.source_tag,
                "_detail_url":   detail_url or None,
                "_site":         cfg.name,
            })
            new_count += 1

        log_fn(f"  {cfg.name} page {page}: {new_count} new ({len(pool)} total)")
        if new_count == 0:
            break
        if page < cfg.max_pages:
            time.sleep(random.uniform(3.0, 7.0))

    return pool


def _enrich_detail_pages(
    leads:   List[Dict],
    base:    str,
    log_fn:  Callable[[str], None],
) -> None:
    """Visit detail pages for leads missing phone or website (in-place)."""
    session = requests.Session()
    ua      = _get_ua()

    for i, lead in enumerate(leads, 1):
        detail_url = lead.pop("_detail_url", None)
        lead.pop("_site", None)

        if lead.get("phone") and lead.get("website"):
            continue
        if not detail_url:
            continue

        resp = _safe_get(detail_url, session, ua, base, log_fn, timeout=12)
        if not resp:
            continue

        soup = BeautifulSoup(resp.text, "lxml")

        # Phone from tel: links
        if not lead.get("phone"):
            el = soup.select_one("a[href^='tel:'], .phone, [itemprop='telephone']")
            if el:
                raw = (el.get("href") or "").replace("tel:", "") or el.get_text(strip=True)
                lead["phone"] = clean_phone(raw) or None

        # Website from external links
        if not lead.get("website"):
            for a in soup.select("a[href*='http']"):
                href = a.get("href", "")
                if href and base not in href and href.startswith("http"):
                    lead["website"] = href
                    break

        # Email from mailto: links
        if not lead.get("email"):
            el = soup.select_one("a[href^='mailto:']")
            if el:
                raw = (el.get("href") or "").replace("mailto:", "").split("?")[0]
                lead["email"] = clean_email(raw) or None

        # JSON-LD as last resort
        if not lead.get("phone") or not lead.get("website"):
            contacts = _extract_json_ld_contacts(resp.text)
            if not lead.get("phone"):
                lead["phone"] = contacts["phone"]
            if not lead.get("website"):
                lead["website"] = contacts["website"]
            if not lead.get("email"):
                lead["email"] = contacts["email"]

        time.sleep(random.uniform(2.0, 4.5))


# ── Core sync scraper ─────────────────────────────────────────────────────────

def scrape_sync(
    niche:     str,
    city:      str,
    country:   str,
    max_leads: int,
    log_fn:    Callable[[str], None],
    site:      Optional[str] = None,
) -> List[Dict[str, Any]]:
    log_fn(
        f"📂 Generic Dir: '{niche}' in '{city}'"
        + (f" via {site}" if site else " (all sites)")
        + f" (target {max_leads})"
    )

    if site:
        cfg = BUILT_IN_CONFIGS.get(site.lower())
        if not cfg:
            log_fn(f"⚠️  Unknown site '{site}' — available: {list(BUILT_IN_CONFIGS)}")
            return []
        sites = [cfg]
    else:
        sites = list(BUILT_IN_CONFIGS.values())

    pool: List[Dict]  = []
    seen: Set[str]    = set()

    for cfg in sites:
        if len(pool) >= max_leads:
            break
        remaining = max_leads - len(pool)
        site_leads = _scrape_one_site(cfg, niche, city, remaining, log_fn, seen)
        pool.extend(site_leads)

    if not pool:
        log_fn("⚠️  Generic directories returned no results")
        return []

    log_fn(f"📋 Pool: {len(pool)} — enriching detail pages …")

    # Group by base domain for the detail-page session
    _enrich_detail_pages(pool[:max_leads], "", log_fn)

    target    = pool[:max_leads]
    phone_cnt = sum(1 for l in target if l.get("phone"))
    web_cnt   = sum(1 for l in target if l.get("website"))
    log_fn(f"🏁 Generic Dir done — {len(target)} leads | {phone_cnt} phones | {web_cnt} websites")
    return target


# ── Public async API ──────────────────────────────────────────────────────────

async def scrape_generic_directory(
    niche:        str,
    city:         str,
    country:      str = "",
    max_leads:    int = 30,
    log_callback: Optional[Callable[[str], None]] = None,
    site:         Optional[str] = None,
) -> List[Dict[str, Any]]:
    def _log(msg: str) -> None:
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass
    _log(f"🚀 Generic Directory scraper: {niche} in {city} (max {max_leads})")
    return await asyncio.to_thread(scrape_sync, niche, city, country, max_leads, _log, site)


async def scrape(
    niche:        str,
    city:         str,
    max_results:  int = 30,
    cfg:          Optional[Dict[str, Any]] = None,
    log_callback: Optional[Callable[[str], None]] = None,
    country:      str = "",
) -> List[Dict[str, Any]]:
    """Orchestrator-compatible dispatch alias."""
    site = (cfg or {}).get("site")
    return await scrape_generic_directory(
        niche=niche, city=city, country=country,
        max_leads=max_results, log_callback=log_callback, site=site,
    )
