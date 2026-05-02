"""
Google Search HTTP scraper — requests + fake_useragent + BeautifulSoup.

Strategy
--------
Phase 1 — Google Search (4 query variants × up to 5 pages each):
  Collects organic business website URLs from Google HTML.
  3 rotating request patterns + per-request UA rotation for anti-ban.
  429/403 hard-block detection → 30 s pause → single retry with fresh UA.
  Soft-block detection via response body ("unusual traffic", "captcha").

Phase 2 — Website contact extraction:
  Visit each collected URL; extract business_name, email, phone.
  Scan homepage internal links to find /contact, /about, /team sub-pages.
  Check sub-pages when homepage yields no email or phone.
  Deduplicates leads at domain level to prevent same company twice.

Threading model
---------------
All network I/O is synchronous (requests library).  The public async
entry-points run sync work inside asyncio.to_thread() so FastAPI never
blocks.  log_callback is a plain sync callable; the caller bridges it to
async via asyncio.run_coroutine_threadsafe when SSE streaming is needed.
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

from ..validators import clean_email, clean_phone

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


# ── fake_useragent — graceful degradation ────────────────────────────────────

try:
    from fake_useragent import UserAgent as _FakeUA

    _UA_GEN = _FakeUA(
        fallback=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        browsers=["Chrome", "Firefox", "Edge"],
    )
    _FAKE_UA_OK = True
except Exception:
    _UA_GEN     = None
    _FAKE_UA_OK = False


# ── Constants ─────────────────────────────────────────────────────────────────

# 10-UA hardcoded fallback when fake_useragent is unavailable
_FALLBACK_UAS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

# Domains to skip when parsing Google results — directories, platforms, socials
_JUNK_DOMAINS: frozenset = frozenset({
    "google", "googleapis", "gstatic", "googletagmanager", "goo.gl",
    "youtube", "facebook", "twitter", "x.com", "instagram", "linkedin",
    "wikipedia", "yelp", "tripadvisor", "yellowpages", "bbb.org",
    "amazon", "apple", "microsoft", "bing", "yahoo",
    "pinterest", "reddit", "tiktok", "snapchat", "whatsapp",
    "foursquare", "mapquest", "angieslist", "houzz", "thumbtack",
    "homeadvisor", "nextdoor", "glassdoor", "indeed", "trustpilot",
    "bark.com", "checkatrade", "ratedpeople", "mybuilder",
    "businessdirectory", "businesslist", "chamberofcommerce",
    "zomato", "justeat", "ubereats", "deliveroo", "doordash",
    "airbnb", "booking.com", "expedia", "hotels.com",
})

# Sub-paths checked for contact info when homepage is incomplete
_CONTACT_PATHS: List[str] = [
    "/contact",      "/contact-us",     "/contacts",
    "/about",        "/about-us",
    "/team",         "/our-team",
    "/reach-us",     "/get-in-touch",
    "/connect",      "/support",
]

# Country → TLD for query variant 4
_COUNTRY_TLD: Dict[str, str] = {
    "UAE":                  "ae",
    "United Arab Emirates": "ae",
    "Saudi Arabia":         "sa",
    "Qatar":                "qa",
    "Kuwait":               "kw",
    "Bahrain":              "bh",
    "Oman":                 "om",
    "Egypt":                "eg",
    "Jordan":               "jo",
    "Lebanon":              "lb",
    "UK":                   "co.uk",
    "United Kingdom":       "co.uk",
    "Australia":            "com.au",
    "Canada":               "ca",
    "India":                "in",
    "Pakistan":             "pk",
    "Germany":              "de",
    "France":               "fr",
    "Spain":                "es",
    "Italy":                "it",
    "Netherlands":          "nl",
    "Brazil":               "com.br",
    "Mexico":               "com.mx",
    "Singapore":            "sg",
    "Malaysia":             "com.my",
}

# 3 request patterns cycled across queries
_REQUEST_PATTERNS: List[str] = ["session", "fresh", "referrer"]

# Regex: broad email for scraping (validation happens via clean_email)
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Regex: phone numbers in multiple formats
_PHONE_RE = re.compile(
    r"(?<!\d)"
    r"(\+?\d{1,3}[\s.\-]?)?"          # optional country code
    r"(?:\(?\d{2,4}\)?[\s.\-]?)?"     # optional area code
    r"\d{3,4}[\s.\-]\d{3,4}"          # main body (requires separator)
    r"(?:[\s.\-]\d{1,4})?"            # optional extension
    r"(?!\d)"
)


# ── User-Agent helper ─────────────────────────────────────────────────────────

def _get_ua() -> str:
    if _FAKE_UA_OK and _UA_GEN:
        try:
            return _UA_GEN.random
        except Exception:
            pass
    return random.choice(_FALLBACK_UAS)


# ── URL utilities ─────────────────────────────────────────────────────────────

def _is_junk_url(url: str) -> bool:
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


def _country_tld(country: str) -> str:
    if not country:
        return "com"
    return _COUNTRY_TLD.get(country.strip(), _COUNTRY_TLD.get(country.strip().title(), "com"))


# ── Query variants builder ────────────────────────────────────────────────────

def _build_queries(niche: str, city: str, country: str) -> List[str]:
    ctx = f"{city} {country}".strip()
    return [
        f"top {niche} in {city}",
        f"{niche} services {ctx}",
        f"{niche} company {city} email contact",
        f"best {niche} near {city}",
    ]


# ── HTTP helpers ──────────────────────────────────────────────────────────────

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


def _google_get(
    url:     str,
    session: requests.Session,
    ua:      str,
    pattern: str,
    log_fn:  Callable[[str], None],
) -> Optional[requests.Response]:
    """
    Fetch one Google Search result page with anti-ban logic.

    pattern "session"  — shared session (Google cookies accumulate like a real browser)
    pattern "fresh"    — brand-new Session per request (no history)
    pattern "referrer" — shared session + adds google.com as Referer
    """
    referer = "https://www.google.com/search?q=business" if pattern == "referrer" else None
    headers = _build_headers(ua, referer)

    def _attempt(sess: requests.Session) -> requests.Response:
        return sess.get(url, headers=headers, timeout=15, verify=False, allow_redirects=True)

    try:
        sess = requests.Session() if pattern == "fresh" else session
        resp = _attempt(sess)

        # Hard rate-limit / forbidden → wait 30 s and retry once with a fresh UA
        if resp.status_code in (429, 403):
            log_fn(f"⚠️  Google rate-limit ({resp.status_code}) — waiting 30 s, retrying …")
            time.sleep(30)
            retry_ua      = _get_ua()
            retry_headers = _build_headers(retry_ua)
            fresh_sess    = requests.Session()
            fresh_sess.headers.update(retry_headers)
            resp = fresh_sess.get(url, timeout=15, verify=False, allow_redirects=True)

        if resp.status_code != 200:
            log_fn(f"⚠️  Google returned {resp.status_code}")
            return None

        # Soft block — Google returns 200 but shows a CAPTCHA / bot challenge
        body = resp.text.lower()
        if "unusual traffic" in body or 'id="captcha"' in body or "verify you are human" in body:
            log_fn("🤖 Google soft-block detected — waiting 30 s …")
            time.sleep(30)
            return None

        return resp

    except requests.RequestException as exc:
        log_fn(f"⚠️  Google request error: {exc}")
        return None


def _site_get(
    url:     str,
    session: requests.Session,
    ua:      str,
    timeout: int = 8,
) -> Optional[requests.Response]:
    """Fetch a business website page — tolerant of SSL errors and slow servers."""
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


# ── Google result URL extraction ──────────────────────────────────────────────

def _extract_result_urls(html: str) -> List[str]:
    """
    4-strategy extraction of organic business URLs from a Google Search HTML page.

    Strategy 1 (most reliable): /url?q=ENCODED_URL redirect links
    Strategy 2: jsname="UWckNb" anchor tags (modern Google)
    Strategy 3: Direct https:// hrefs inside known result containers (div.g, etc.)
    Strategy 4: Fallback — all external https:// hrefs inside #search / #rso div
    """
    soup  = BeautifulSoup(html, "lxml")
    raw: Set[str] = set()

    # Strategy 1: /url?q= encoded redirect links
    for a in soup.find_all("a", href=re.compile(r"^/url\?q=https?", re.I)):
        try:
            qs  = urllib.parse.parse_qs(urllib.parse.urlparse(a["href"]).query)
            url = urllib.parse.unquote(qs.get("q", [""])[0])
            if url.startswith("http"):
                raw.add(url)
        except Exception:
            continue

    # Strategy 2: jsname="UWckNb" anchors
    for a in soup.find_all("a", attrs={"jsname": "UWckNb"}):
        href = a.get("href", "")
        if href.startswith("http"):
            raw.add(href)

    # Strategy 3: Direct hrefs inside known result container divs
    for container in soup.select(
        "div.g, div.yuRUbf, div[class*='yuRUbf'], li.g, div[data-hveid]"
    ):
        a = container.find("a", href=re.compile(r"^https?://"))
        if a:
            raw.add(a["href"])

    # Strategy 4: Fallback — scan the full #search or #rso div
    if not raw:
        results_div = soup.find("div", {"id": ["search", "rso"]})
        if results_div:
            for a in results_div.find_all("a", href=re.compile(r"^https?://")):
                raw.add(a["href"])

    # Remove junk and very short URLs, return in stable (insertion) order
    filtered: List[str] = []
    seen:     Set[str]  = set()
    for u in raw:
        if not _is_junk_url(u) and len(u) > 12 and u not in seen:
            seen.add(u)
            filtered.append(u)

    return filtered


# ── Email / phone extraction ──────────────────────────────────────────────────

def _find_email_in_soup(soup: BeautifulSoup) -> Optional[str]:
    # Method 1: mailto: links — highest confidence
    for a in soup.find_all("a", href=re.compile(r"^mailto:", re.I)):
        raw   = a["href"].replace("mailto:", "").split("?")[0]
        email = clean_email(raw)
        if email:
            return email

    # Method 2: regex over visible text (strip script/style noise first)
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    for match in _EMAIL_RE.findall(text):
        email = clean_email(match)
        if email:
            return email

    return None


def _find_phone_in_soup(soup: BeautifulSoup) -> Optional[str]:
    # Method 1: tel: href links
    for a in soup.find_all("a", href=re.compile(r"^tel:", re.I)):
        raw   = a["href"].replace("tel:", "").strip()
        phone = clean_phone(raw)
        if phone:
            return phone

    # Method 2: regex over page text
    text = soup.get_text(separator=" ")
    for match in _PHONE_RE.finditer(text):
        phone = clean_phone(match.group())
        if phone:
            return phone

    return None


def _find_contact_subpages(soup: BeautifulSoup, base: str) -> List[str]:
    """
    Collect candidate URLs for sub-page contact discovery.

    First pass: scan all internal links whose path or link-text contains a
    contact/about keyword.  Second pass: append the fixed _CONTACT_PATHS so
    we always attempt them even if they are not linked on the homepage.
    """
    contact_kw = {"contact", "about", "team", "reach", "connect", "support", "get-in-touch"}
    found:      List[str] = []
    seen_paths: Set[str]  = set()
    base_domain = _get_domain(base)

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = (a.get_text() or "").lower()

        # Normalise to absolute URL
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = base + href
        elif href.startswith("./"):
            href = base + "/" + href[2:]
        elif not href.startswith("http"):
            continue

        # Internal links only
        if _get_domain(href) != base_domain:
            continue

        path = urllib.parse.urlparse(href).path.lower().rstrip("/")
        if path in seen_paths:
            continue

        if any(kw in path or kw in text for kw in contact_kw):
            seen_paths.add(path)
            found.append(href)

    # Append fixed paths not already in the list
    for cp in _CONTACT_PATHS:
        if cp.rstrip("/") not in seen_paths:
            found.append(base + cp)

    return found[:8]   # cap at 8 to avoid excessive per-site requests


# ── Per-website contact extractor ─────────────────────────────────────────────

def _extract_business_info(
    url:     str,
    session: requests.Session,
    ua:      str,
    log_fn:  Callable[[str], None],
) -> Dict[str, Any]:
    """
    Visit a business website and extract name, email, and phone.

    Order of operations:
    1. Homepage — extract from title/h1, mailto links, regex
    2. Internal contact/about/team sub-pages — visited only when homepage
       is missing either email or phone
    """
    info: Dict[str, Any] = {"business_name": None, "email": None, "phone": None, "address": None}
    base = _base_url(url)

    resp = _site_get(url, session, ua)
    if not resp:
        return info

    soup = BeautifulSoup(resp.text, "lxml")

    # ── Business name ─────────────────────────────────────────────────────────
    h1 = soup.find("h1")
    if h1:
        info["business_name"] = h1.get_text(strip=True)[:120]
    else:
        title_tag = soup.find("title")
        if title_tag:
            raw_title = title_tag.get_text(strip=True)
            # "Company | Tagline | City" → take first segment
            info["business_name"] = re.split(r"[\|–—]{1,2}|-{2,}", raw_title)[0].strip()[:120]

    # ── Email & phone from homepage ───────────────────────────────────────────
    info["email"] = _find_email_in_soup(soup)
    info["phone"] = _find_phone_in_soup(soup)

    if info["email"] and info["phone"]:
        return info

    # ── Sub-page scan for missing fields ─────────────────────────────────────
    sub_pages = _find_contact_subpages(soup, base)

    for sp_url in sub_pages:
        if info["email"] and info["phone"]:
            break

        sub_resp = _site_get(sp_url, session, ua, timeout=6)
        if not sub_resp:
            continue

        sub_soup = BeautifulSoup(sub_resp.text, "lxml")

        if not info["email"]:
            info["email"] = _find_email_in_soup(sub_soup)
        if not info["phone"]:
            info["phone"] = _find_phone_in_soup(sub_soup)

        time.sleep(random.uniform(0.8, 2.0))   # polite between sub-pages

    return info


# ── Core sync scraper ─────────────────────────────────────────────────────────

def _scrape_sync(
    niche:     str,
    city:      str,
    country:   str,
    max_leads: int,
    log_fn:    Callable[[str], None],
) -> List[Dict[str, Any]]:
    """
    Synchronous implementation — runs inside asyncio.to_thread().

    Phase 1: Run 4 query variants across up to 5 result pages each.
             Collect organic business URLs; deduplicate at domain level.
    Phase 2: Visit each URL; extract business_name, email, phone.
             Check contact/about sub-pages when homepage is incomplete.
    """
    queries = _build_queries(niche, city, country)
    log_fn(f"🔍 Running {len(queries)} query variants …")

    # ── Phase 1: URL pool collection ─────────────────────────────────────────
    url_pool:    Dict[str, str] = {}   # domain → first-seen URL
    google_sess: requests.Session = requests.Session()

    for q_idx, query in enumerate(queries):
        if len(url_pool) >= max_leads * 3:  # 3× over-collect for domain-level dedup
            break

        pattern = _REQUEST_PATTERNS[q_idx % len(_REQUEST_PATTERNS)]
        q_enc   = urllib.parse.quote_plus(query)
        log_fn(f"  [{q_idx + 1}/4] \"{query}\" (pattern: {pattern})")

        for page in range(5):                  # pages 1–5 (start 0, 10, 20, 30, 40)
            start      = page * 10
            google_url = (
                f"https://www.google.com/search"
                f"?q={q_enc}&start={start}&num=10&hl=en&gl=en"
            )

            resp = _google_get(google_url, google_sess, _get_ua(), pattern, log_fn)
            if not resp:
                break   # blocked or error — skip remaining pages for this query

            page_urls = _extract_result_urls(resp.text)
            new_count = 0

            for u in page_urls:
                domain = _get_domain(u)
                if domain and domain not in url_pool:
                    url_pool[domain] = u
                    new_count += 1

            log_fn(f"     Page {page + 1}: {len(page_urls)} results, {new_count} new domains")

            if not page_urls:
                break   # Google has no more organic results for this query

            if page < 4:
                time.sleep(random.uniform(4.0, 10.0))  # anti-ban delay between pages

        # Anti-ban delay between queries
        if q_idx < len(queries) - 1:
            time.sleep(random.uniform(6.0, 14.0))

    log_fn(
        f"🌐 URL pool: {len(url_pool)} unique domains "
        f"→ visiting top {min(max_leads, len(url_pool))}"
    )

    # ── Phase 2: contact extraction ───────────────────────────────────────────
    leads:       List[Dict[str, Any]] = []
    site_session = requests.Session()
    target_urls  = list(url_pool.values())[:max_leads]

    for i, url in enumerate(target_urls, start=1):
        log_fn(f"🌐 [{i}/{len(target_urls)}] {url}")
        ua   = _get_ua()
        info = _extract_business_info(url, site_session, ua, log_fn)

        leads.append({
            "business_name": info["business_name"] or _get_domain(url),
            "phone":         info["phone"],
            "website":       url,
            "address":       None,
            "email":         info["email"],
            "rating":        None,
            "reviews_count": None,
            "niche":         niche,
            "city":          city,
            "country":       country or None,
            "source":        "GOOGLE_SEARCH",
            "raw_url":       url,
        })

        log_fn(
            f"  ✅ {leads[-1]['business_name']}"
            + (f" | ✉️  {info['email']}"  if info["email"] else "")
            + (f" | 📞 {info['phone']}"   if info["phone"] else "")
        )

        time.sleep(random.uniform(2.0, 5.0))   # polite delay between site visits

    email_count = sum(1 for l in leads if l["email"])
    phone_count = sum(1 for l in leads if l["phone"])
    log_fn(
        f"🏁 Done — {len(leads)} leads | "
        f"{email_count} with email | {phone_count} with phone"
    )
    return leads


# ── Public async API ──────────────────────────────────────────────────────────

async def scrape_google_search(
    niche:        str,
    city:         str,
    country:      str = "",
    max_leads:    int = 30,
    log_callback: Optional[Callable[[str], None]] = None,
) -> List[Dict[str, Any]]:
    """
    Async entry-point: Google Search-based lead scraper.

    Parameters
    ----------
    niche        : business category, e.g. "web design agency"
    city         : target city, e.g. "Dubai"
    country      : target country, e.g. "UAE" (drives TLD variant + query context)
    max_leads    : max leads to return (URLs collected 3× this, then trimmed)
    log_callback : sync callable(msg) — for SSE real-time streaming
    """
    def _log(msg: str) -> None:
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass

    _log(f"🚀 Google Search scraper: {niche} in {city} (max {max_leads})")
    return await asyncio.to_thread(_scrape_sync, niche, city, country, max_leads, _log)


async def search_businesses(
    niche:        str,
    city:         str,
    max_results:  int = 20,
    log_callback: Optional[Callable[[str], None]] = None,
) -> List[Dict[str, Any]]:
    """Backward-compatible wrapper — called by scrape_multi_source for GOOGLE_SEARCH source."""
    return await scrape_google_search(
        niche=niche,
        city=city,
        country="",
        max_leads=max_results,
        log_callback=log_callback,
    )
