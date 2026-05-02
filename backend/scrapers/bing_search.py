"""
Bing Search HTTP scraper — requests + BeautifulSoup.

Strategy
--------
Phase 1 — Bing Search (4 query variants × up to 5 pages each):
  Collects organic business website URLs from Bing HTML result pages.
  Extracts local-pack cards directly (name, phone, address, website) when
  Bing's map-pack is present — no website visit required for those leads.
  UA rotation + per-request header variance for anti-detection.
  429/403 → 30 s pause → single retry with a fresh UA.
  Soft-block detection via response body ("unusual traffic", "captcha").

Phase 2 — Website contact extraction:
  Visit each URL from the organic results pool; extract business_name,
  email, phone.  Sub-pages (/contact, /about …) checked when homepage
  is incomplete.  Deduplicates at domain level.

Threading model
---------------
All network I/O is synchronous (requests library).  The public async
entry-point runs sync work inside asyncio.to_thread() so FastAPI is
never blocked.  log_callback is a plain sync callable; the caller
bridges it to async via asyncio.run_coroutine_threadsafe when needed.
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

from ..validators import clean_email, clean_phone

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


# ── fake_useragent — graceful degradation ─────────────────────────────────────

try:
    from fake_useragent import UserAgent as _FakeUA
    _UA_GEN   = _FakeUA(
        fallback="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                 "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        browsers=["Chrome", "Firefox", "Edge"],
    )
    _FAKE_UA_OK = True
except Exception:
    _UA_GEN     = None
    _FAKE_UA_OK = False


# ── Constants ─────────────────────────────────────────────────────────────────

_FALLBACK_UAS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# Domains to skip from organic results (directories, social, competitors)
_JUNK_DOMAINS: frozenset = frozenset({
    "bing", "microsoft", "msn", "live", "hotmail", "outlook",
    "google", "youtube", "facebook", "twitter", "x.com", "instagram",
    "linkedin", "wikipedia", "yelp", "tripadvisor", "yellowpages",
    "bbb.org", "amazon", "apple", "yahoo", "pinterest", "reddit",
    "tiktok", "foursquare", "mapquest", "trustpilot", "indeed",
    "glassdoor", "bark.com", "houzz", "thumbtack", "homeadvisor",
    "angi.com", "angieslist", "checkatrade", "ratedpeople",
    "zomato", "justeat", "ubereats", "doordash", "airbnb",
    "booking.com", "expedia", "hotels.com",
})

# Sub-paths for contact discovery when homepage is incomplete
_CONTACT_PATHS: List[str] = [
    "/contact", "/contact-us", "/contacts",
    "/about",   "/about-us",
    "/team",    "/our-team",
    "/reach-us", "/get-in-touch",
]

# Regex patterns
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(
    r"(?<!\d)"
    r"(?:\+?\d{1,3}[\s.\-]?)?"
    r"(?:\(?\d{2,4}\)?[\s.\-]?)?"
    r"\d{3,4}[\s.\-]\d{3,4}"
    r"(?:[\s.\-]\d{1,4})?"
    r"(?!\d)"
)

# 3 request patterns to vary fingerprint across queries
_REQUEST_PATTERNS: List[str] = ["session", "fresh", "referrer"]


# ── UA helper ──────────────────────────────────────────────────────────────────

def _get_ua() -> str:
    if _FAKE_UA_OK and _UA_GEN:
        try:
            return _UA_GEN.random
        except Exception:
            pass
    return random.choice(_FALLBACK_UAS)


# ── URL utilities ──────────────────────────────────────────────────────────────

def _is_junk(url: str) -> bool:
    try:
        domain = urllib.parse.urlparse(url).netloc.lower().lstrip("www.")
        return any(junk in domain for junk in _JUNK_DOMAINS)
    except Exception:
        return True


def _get_domain(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return url


def _base_url(url: str) -> str:
    p = urllib.parse.urlparse(url)
    return f"{p.scheme}://{p.netloc}"


# ── Query builder ──────────────────────────────────────────────────────────────

def _build_queries(niche: str, city: str, country: str) -> List[str]:
    ctx = f"{city} {country}".strip()
    return [
        f"{niche} {city}",
        f"best {niche} in {city}",
        f"{niche} {city} contact phone email",
        f"{niche} near {city} official website",
    ]


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _build_headers(ua: str, referer: Optional[str] = None) -> Dict[str, str]:
    h = {
        "User-Agent":                ua,
        "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language":           "en-US,en;q=0.9",
        "Accept-Encoding":           "gzip, deflate, br",
        "DNT":                       "1",
        "Connection":                "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest":            "document",
        "Sec-Fetch-Mode":            "navigate",
        "Sec-Fetch-Site":            "cross-site" if referer else "none",
        "Sec-Fetch-User":            "?1",
        "Cache-Control":             "max-age=0",
    }
    if referer:
        h["Referer"] = referer
    return h


def _bing_get(
    url:     str,
    session: requests.Session,
    ua:      str,
    pattern: str,
    log_fn:  Callable[[str], None],
) -> Optional[requests.Response]:
    """
    Fetch one Bing Search result page with anti-ban logic.

    pattern "session"  — shared session (cookies accumulate)
    pattern "fresh"    — brand-new Session per request
    pattern "referrer" — shared session + bing.com Referer header
    """
    referer = "https://www.bing.com/" if pattern == "referrer" else None
    headers = _build_headers(ua, referer)

    try:
        sess = requests.Session() if pattern == "fresh" else session
        resp = sess.get(url, headers=headers, timeout=15, verify=False, allow_redirects=True)

        if resp.status_code in (429, 403):
            log_fn(f"⚠️  Bing rate-limit ({resp.status_code}) — waiting 30 s …")
            time.sleep(30)
            fresh_ua   = _get_ua()
            fresh_sess = requests.Session()
            resp = fresh_sess.get(
                url,
                headers=_build_headers(fresh_ua),
                timeout=15, verify=False, allow_redirects=True,
            )

        if resp.status_code != 200:
            log_fn(f"⚠️  Bing returned {resp.status_code}")
            return None

        body = resp.text.lower()
        if "unusual traffic" in body or 'id="captcha"' in body or "verify you are human" in body:
            log_fn("🤖 Bing soft-block detected — waiting 30 s …")
            time.sleep(30)
            return None

        return resp

    except requests.RequestException as exc:
        log_fn(f"⚠️  Bing request error: {exc}")
        return None


def _site_get(
    url:     str,
    session: requests.Session,
    ua:      str,
    timeout: int = 8,
) -> Optional[requests.Response]:
    try:
        resp = session.get(
            url,
            headers=_build_headers(ua),
            timeout=timeout,
            verify=False,
            allow_redirects=True,
        )
        return resp if resp.status_code < 400 else None
    except requests.RequestException:
        return None


# ── Bing result extraction ─────────────────────────────────────────────────────

def _extract_organic_urls(html: str) -> List[str]:
    """
    Extract organic business URLs from a Bing Search result page.

    Bing organic results use direct hrefs (no /url?q= redirect like Google).
    Strategy 1: li.b_algo h2 a — main title link of each result
    Strategy 2: div.b_title a — alternate class used for some result types
    Strategy 3: a[data-bm] inside result containers (Bing's tracking attribute)
    """
    soup  = BeautifulSoup(html, "lxml")
    raw:  Set[str] = set()

    # Strategy 1: canonical organic result title links
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a[href]")
        if a and a.get("href", "").startswith("http"):
            raw.add(a["href"])

    # Strategy 2: b_title variant
    for div in soup.select("div.b_title"):
        a = div.find("a", href=re.compile(r"^https?://"))
        if a:
            raw.add(a["href"])

    # Strategy 3: any external links inside #b_results
    if not raw:
        results = soup.find("ol", id="b_results")
        if results:
            for a in results.find_all("a", href=re.compile(r"^https?://")):
                href = a.get("href", "")
                if href and not href.startswith("https://www.bing.com"):
                    raw.add(href)

    filtered: List[str] = []
    seen:     Set[str]  = set()
    for u in raw:
        if not _is_junk(u) and len(u) > 12 and u not in seen:
            seen.add(u)
            filtered.append(u)

    return filtered


def _extract_local_pack(html: str, niche: str, city: str) -> List[Dict[str, Any]]:
    """
    Extract local-pack business cards directly from Bing Search HTML.

    When Bing returns a map-pack (li.b_lEntry cards), we can get
    name + phone + address + website without visiting each site.
    Returns a list of partial lead dicts (email=None — filled by enrichment).
    """
    soup   = BeautifulSoup(html, "lxml")
    leads: List[Dict[str, Any]] = []

    # ── Strategy 1: JSON-LD LocalBusiness blocks ──────────────────────────────
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        items = data if isinstance(data, list) else [data]
        for item in items:
            if item.get("@type") not in ("LocalBusiness", "Organization", "Store",
                                          "MedicalBusiness", "LegalService",
                                          "HomeAndConstructionBusiness"):
                continue

            name    = (item.get("name") or "").strip()
            phone   = clean_phone(str(item.get("telephone") or ""))
            website = (item.get("url") or "").strip()
            addr    = item.get("address") or {}
            address = (
                addr.get("streetAddress", "") + " " +
                addr.get("addressLocality", "") + " " +
                addr.get("addressRegion", "")
            ).strip() if isinstance(addr, dict) else str(addr)

            if name:
                leads.append({
                    "business_name": name,
                    "phone":         phone   or None,
                    "website":       website or None,
                    "email":         None,
                    "address":       address or None,
                    "niche":         niche,
                    "city":          city,
                    "source":        "BING_SEARCH",
                })

    # ── Strategy 2: li.b_lEntry HTML cards ───────────────────────────────────
    for entry in soup.select("li.b_lEntry, .b_loclist li"):
        name    = ""
        phone   = ""
        website = ""
        address = ""

        # Name
        for sel in ("h2.b_lTitle a", "h3.b_lTitle a", ".b_lTitle a", "h2 a", "h3 a"):
            el = entry.select_one(sel)
            if el:
                name = el.get_text(strip=True)
                break

        if not name:
            continue

        # Phone
        for sel in (".b_phone", "span.b_phone", "[class*='phone']"):
            el = entry.select_one(sel)
            if el:
                phone = clean_phone(el.get_text(strip=True)) or ""
                if phone:
                    break

        # Website
        for sel in (".b_lSite a", ".b_website a", "a[class*='website']"):
            el = entry.select_one(sel)
            if el:
                href = el.get("href", "")
                if href.startswith("http") and not _is_junk(href):
                    website = href
                    break

        # Address
        for sel in ("div.b_address", ".b_address", "[class*='address']"):
            el = entry.select_one(sel)
            if el:
                address = el.get_text(strip=True)
                break

        # Avoid duplicating JSON-LD leads
        if any(l["business_name"].lower() == name.lower() for l in leads):
            continue

        leads.append({
            "business_name": name,
            "phone":         phone   or None,
            "website":       website or None,
            "email":         None,
            "address":       address or None,
            "niche":         niche,
            "city":          city,
            "source":        "BING_SEARCH",
        })

    return leads


# ── Per-website contact extractor ──────────────────────────────────────────────

def _find_email_in_soup(soup: BeautifulSoup) -> Optional[str]:
    for a in soup.find_all("a", href=re.compile(r"^mailto:", re.I)):
        raw   = a["href"].replace("mailto:", "").split("?")[0]
        email = clean_email(raw)
        if email:
            return email
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    for match in _EMAIL_RE.findall(text):
        email = clean_email(match)
        if email:
            return email
    return None


def _find_phone_in_soup(soup: BeautifulSoup) -> Optional[str]:
    for a in soup.find_all("a", href=re.compile(r"^tel:", re.I)):
        raw   = a["href"].replace("tel:", "").strip()
        phone = clean_phone(raw)
        if phone:
            return phone
    text = soup.get_text(separator=" ")
    for match in _PHONE_RE.finditer(text):
        phone = clean_phone(match.group())
        if phone:
            return phone
    return None


def _extract_contact_info(
    url:     str,
    session: requests.Session,
    ua:      str,
) -> Dict[str, Any]:
    """Visit a business website and extract name, email, phone."""
    info: Dict[str, Any] = {
        "business_name": None,
        "email":         None,
        "phone":         None,
    }
    base = _base_url(url)

    resp = _site_get(url, session, ua)
    if not resp:
        return info

    soup = BeautifulSoup(resp.text, "lxml")

    # Name: h1 or <title>
    h1 = soup.find("h1")
    if h1:
        info["business_name"] = h1.get_text(strip=True)[:120]
    else:
        title_tag = soup.find("title")
        if title_tag:
            raw = title_tag.get_text(strip=True)
            info["business_name"] = re.split(r"[\|–—]{1,2}|-{2,}", raw)[0].strip()[:120]

    info["email"] = _find_email_in_soup(soup)
    info["phone"] = _find_phone_in_soup(soup)

    if info["email"] and info["phone"]:
        return info

    # Sub-page scan for missing fields
    for path in _CONTACT_PATHS:
        if info["email"] and info["phone"]:
            break
        sub_resp = _site_get(base + path, session, ua, timeout=6)
        if not sub_resp:
            continue
        sub_soup = BeautifulSoup(sub_resp.text, "lxml")
        if not info["email"]:
            info["email"] = _find_email_in_soup(sub_soup)
        if not info["phone"]:
            info["phone"] = _find_phone_in_soup(sub_soup)
        time.sleep(random.uniform(0.8, 2.0))

    return info


# ── Core sync scraper ──────────────────────────────────────────────────────────

def scrape_sync(
    niche:     str,
    city:      str,
    country:   str,
    max_leads: int,
    log_fn:    Callable[[str], None],
) -> List[Dict[str, Any]]:
    """
    Synchronous Bing Search scraper — runs inside asyncio.to_thread().

    Phase 1: Run 4 query variants, collect organic URLs + extract local-pack
             leads directly from Bing's map-pack cards.
    Phase 2: Visit each organic URL to extract business_name, email, phone.
    """
    queries = _build_queries(niche, city, country)
    log_fn(f"🔵 Bing Search: {len(queries)} query variants for '{niche}' in '{city}'")

    # ── Phase 1: URL pool + local pack ────────────────────────────────────────
    url_pool:    Dict[str, str]       = {}  # domain → first-seen URL
    local_leads: List[Dict[str, Any]] = []
    bing_sess    = requests.Session()

    for q_idx, query in enumerate(queries):
        if len(url_pool) >= max_leads * 3:
            break

        pattern = _REQUEST_PATTERNS[q_idx % len(_REQUEST_PATTERNS)]
        q_enc   = urllib.parse.quote_plus(query)
        log_fn(f"  [{q_idx + 1}/4] \"{query}\" (pattern: {pattern})")

        for page in range(5):
            start      = page * 10
            bing_url   = (
                f"https://www.bing.com/search"
                f"?q={q_enc}&first={start + 1}&count=10&form=HDRSC3&mkt=en-US"
            )

            resp = _bing_get(bing_url, bing_sess, _get_ua(), pattern, log_fn)
            if not resp:
                break

            # Extract local pack (only on first page of first query)
            if q_idx == 0 and page == 0:
                pack = _extract_local_pack(resp.text, niche, city)
                if pack:
                    log_fn(f"     🗺️  Local pack: {len(pack)} businesses extracted directly")
                    local_leads.extend(pack)

            page_urls = _extract_organic_urls(resp.text)
            new_count = 0
            for u in page_urls:
                domain = _get_domain(u)
                if domain and domain not in url_pool:
                    url_pool[domain] = u
                    new_count += 1

            log_fn(f"     Page {page + 1}: {len(page_urls)} results, {new_count} new domains")

            if not page_urls:
                break

            if page < 4:
                time.sleep(random.uniform(3.0, 8.0))

        if q_idx < len(queries) - 1:
            time.sleep(random.uniform(5.0, 12.0))

    log_fn(
        f"🌐 Bing pool: {len(url_pool)} domains + {len(local_leads)} local-pack leads "
        f"→ visiting top {min(max_leads, len(url_pool))} sites"
    )

    # ── Phase 2: contact extraction ───────────────────────────────────────────
    site_leads:  List[Dict[str, Any]] = []
    site_session = requests.Session()
    target_urls  = list(url_pool.values())[:max(0, max_leads - len(local_leads))]

    for i, url in enumerate(target_urls, start=1):
        log_fn(f"🌐 [{i}/{len(target_urls)}] {url}")
        ua   = _get_ua()
        info = _extract_contact_info(url, site_session, ua)

        site_leads.append({
            "business_name": info["business_name"] or _get_domain(url),
            "phone":         info["phone"],
            "website":       url,
            "email":         info["email"],
            "address":       None,
            "niche":         niche,
            "city":          city,
            "country":       country or None,
            "source":        "BING_SEARCH",
            "raw_url":       url,
        })
        log_fn(
            f"  ✅ {site_leads[-1]['business_name']}"
            + (f" | ✉️  {info['email']}"  if info["email"] else "")
            + (f" | 📞 {info['phone']}"   if info["phone"] else "")
        )
        time.sleep(random.uniform(2.0, 5.0))

    all_leads  = local_leads + site_leads
    email_cnt  = sum(1 for l in all_leads if l.get("email"))
    phone_cnt  = sum(1 for l in all_leads if l.get("phone"))
    log_fn(
        f"🏁 Bing Search done — {len(all_leads)} leads | "
        f"{email_cnt} with email | {phone_cnt} with phone"
    )
    return all_leads


# ── Public async API ───────────────────────────────────────────────────────────

async def scrape_bing_search(
    niche:        str,
    city:         str,
    country:      str = "",
    max_leads:    int = 30,
    log_callback: Optional[Callable[[str], None]] = None,
) -> List[Dict[str, Any]]:
    """
    Async entry-point: Bing Search-based lead scraper.

    Parameters
    ----------
    niche        : business category, e.g. "dental clinic"
    city         : target city, e.g. "Dubai"
    country      : target country (context only — no TLD logic needed for Bing)
    max_leads    : max leads to return
    log_callback : sync callable(msg) — for SSE real-time streaming
    """
    def _log(msg: str) -> None:
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass

    _log(f"🚀 Bing Search scraper: {niche} in {city} (max {max_leads})")
    return await asyncio.to_thread(scrape_sync, niche, city, country, max_leads, _log)


# Backward-compat alias for _dispatch_source
async def scrape(
    niche:        str,
    city:         str,
    max_results:  int  = 30,
    cfg:          Optional[Dict[str, Any]] = None,
    log_callback: Optional[Callable[[str], None]] = None,
    country:      str  = "",
) -> List[Dict[str, Any]]:
    return await scrape_bing_search(
        niche=niche, city=city, country=country,
        max_leads=max_results, log_callback=log_callback,
    )
