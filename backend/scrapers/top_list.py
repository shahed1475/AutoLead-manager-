"""
"Top N / Best X in Y" article scraper.

Finds curated "best of" articles via Bing search, parses business names
from numbered/heading lists, then resolves each name to a website and
extracts contact information.

Pipeline
--------
1. Bing search — queries like "best {niche} in {city}", "top {niche} {city}"
   Collect article URLs (Wikipedia, Yelp review pages, directory aggregate
   pages are excluded — only editorial articles are kept).

2. Article parse — for each article URL:
   Extract numbered/bulleted business names:
     • Ordered lists (<ol><li>)
     • Headings: H2/H3 starting with a digit or "N. Name"
     • Bold text following a number
   Clean noise (rankings, ordinal suffixes, common preamble).

3. Name resolution — for each extracted business name:
   Bing search "{name} {city} official website" → take the first non-directory
   result URL as the website homepage.

4. Contact extraction — visit each resolved homepage (+/contact page):
   Extract phone (tel: links, regex) and email (mailto:, meta tags, text).

Anti-ban: UA rotation, 2-5 s delays throughout.
Max article pages: 5; Max names per article: 15.
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

_BING_BASE = "https://www.bing.com"

_FALLBACK_UAS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# Sites that are directories / review aggregators — NOT editorial articles
_EXCLUDED_DOMAINS = frozenset({
    "yelp.com", "tripadvisor.com", "google.com", "facebook.com",
    "yellowpages.com", "manta.com", "mapquest.com", "foursquare.com",
    "bingmaps.com", "wikipedia.org", "wikimedia.org",
    "angi.com", "thumbtack.com", "bark.com", "houzz.com",
    "linkedin.com", "instagram.com", "twitter.com", "x.com",
    "yelp.ca", "yelp.co.uk", "yelp.com.au",
})

_ORDINAL_RE = re.compile(
    r"^\s*(\d+[\.\):]?\s*|first|second|third|[a-z]?st\b|[a-z]?nd\b|[a-z]?rd\b|[a-z]?th\b)",
    re.I,
)
_NOISE_RE = re.compile(
    r"\b(best|top|review|rating|near me|in \w+|award|winner|ranked|"
    r"check out|visit|click|read more|see also|updated|list)\b",
    re.I,
)


def _get_ua() -> str:
    try:
        from fake_useragent import UserAgent
        return UserAgent(browsers=["Chrome", "Firefox"]).random
    except Exception:
        return random.choice(_FALLBACK_UAS)


def _headers(ua: str, referer: str = _BING_BASE) -> Dict[str, str]:
    return {
        "User-Agent":                ua,
        "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language":           "en-US,en;q=0.9",
        "Accept-Encoding":           "gzip, deflate, br",
        "DNT":                       "1",
        "Connection":                "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer":                   referer,
    }


def _safe_get(
    url:     str,
    session: requests.Session,
    ua:      str,
    log_fn:  Callable[[str], None],
    timeout: int = 12,
    referer: str = _BING_BASE,
) -> Optional[requests.Response]:
    for attempt in range(2):
        try:
            resp = session.get(
                url, headers=_headers(ua, referer), timeout=timeout,
                verify=False, allow_redirects=True,
            )
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 503):
                wait = 20 + attempt * 15
                log_fn(f"⚠️  Rate-limit ({resp.status_code}) — waiting {wait}s …")
                time.sleep(wait)
                ua = _get_ua()
                continue
            return None
        except requests.RequestException as exc:
            if attempt == 0:
                time.sleep(4)
            else:
                log_fn(f"⚠️  Request error: {exc}")
    return None


# ── Step 1: Bing search for article URLs ──────────────────────────────────────

def _bing_search_articles(
    niche:   str,
    city:    str,
    session: requests.Session,
    ua:      str,
    log_fn:  Callable[[str], None],
) -> List[str]:
    """Return up to 10 editorial article URLs for the niche+city combo."""
    queries = [
        f"best {niche} in {city}",
        f"top {niche} {city}",
        f"top 10 {niche} {city}",
        f"{city} best {niche} list",
    ]
    seen: Set[str] = set()
    article_urls:  List[str] = []

    for query in queries:
        if len(article_urls) >= 10:
            break

        q_enc = urllib.parse.quote_plus(query)
        url   = f"{_BING_BASE}/search?q={q_enc}&count=10"
        resp  = _safe_get(url, session, ua, log_fn, referer=_BING_BASE)
        if not resp:
            time.sleep(random.uniform(2.0, 4.0))
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        for result in soup.select("li.b_algo"):
            a = result.select_one("h2 a")
            if not a:
                continue
            href = a.get("href", "")
            if not href or not href.startswith("http"):
                continue

            # Extract actual URL from Bing redirect if needed
            if "bing.com/ck/" in href:
                try:
                    from urllib.parse import urlparse, parse_qs
                    qs  = parse_qs(urlparse(href).query)
                    raw = qs.get("url", [href])[0]
                    href = urllib.parse.unquote(raw)
                except Exception:
                    pass

            domain = re.sub(r"^www\.", "", urllib.parse.urlparse(href).netloc.lower())
            if domain in _EXCLUDED_DOMAINS:
                continue
            if href in seen:
                continue

            seen.add(href)
            article_urls.append(href)
            if len(article_urls) >= 10:
                break

        time.sleep(random.uniform(2.0, 4.0))

    log_fn(f"  📰 Found {len(article_urls)} article URLs")
    return article_urls


# ── Step 2: extract business names from an article ────────────────────────────

def _clean_name(raw: str) -> str:
    """Strip ranking numbers, ordinal suffixes, and noise words."""
    name = _ORDINAL_RE.sub("", raw).strip()
    name = re.sub(r"^[\d\.\-\)\s]+", "", name).strip()
    name = re.sub(r"\s{2,}", " ", name)
    # Remove trailing noise like "– review", "- Best for X"
    name = re.split(r"\s+[-–—]\s+", name)[0].strip()
    name = re.split(r"\s*:\s+", name)[0].strip()
    return name[:100]


def _extract_names_from_article(html: str) -> List[str]:
    """Parse business names from an editorial 'best of' article."""
    soup  = BeautifulSoup(html, "lxml")
    names: List[str] = []
    seen:  Set[str]  = set()

    def _add(raw: str) -> None:
        cleaned = _clean_name(raw)
        if len(cleaned) >= 3:
            key = cleaned.lower()
            if key not in seen and not _NOISE_RE.search(cleaned):
                seen.add(key)
                names.append(cleaned)

    # 1. Ordered list items (most reliable for "Top 10" articles)
    for ol in soup.select("ol"):
        for li in ol.select("li"):
            text = li.get_text(separator=" ", strip=True)
            _add(text)

    # 2. H2 / H3 starting with a number or common ranking pattern
    for tag in soup.select("h2, h3"):
        text = tag.get_text(strip=True)
        if re.match(r"^\d+[.\):]?\s+\w", text):
            _add(text)

    # 3. Bold/strong text preceded by a number in the same paragraph
    for p in soup.select("p"):
        text = p.get_text(" ", strip=True)
        # "1. **Business Name** — description"
        for m in re.finditer(r"\d+\.\s+([A-Z][^.\n]{3,60})", text):
            _add(m.group(1))

    # 4. Headings that look like business names (title-cased, 2-6 words)
    if not names:
        for tag in soup.select("h2, h3"):
            text = tag.get_text(strip=True)
            words = text.split()
            if 2 <= len(words) <= 8 and text[0].isupper():
                _add(text)

    return names[:15]


# ── Step 3: resolve business name → website URL ───────────────────────────────

def _resolve_website(
    name:    str,
    city:    str,
    session: requests.Session,
    ua:      str,
    log_fn:  Callable[[str], None],
) -> Optional[str]:
    """Bing search '{name} {city} official website' → first non-directory URL."""
    q    = urllib.parse.quote_plus(f"{name} {city} official website")
    url  = f"{_BING_BASE}/search?q={q}&count=5"
    resp = _safe_get(url, session, ua, log_fn, referer=_BING_BASE)
    if not resp:
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    for result in soup.select("li.b_algo"):
        a = result.select_one("h2 a")
        if not a:
            continue
        href = a.get("href", "")
        if not href or not href.startswith("http"):
            continue
        domain = re.sub(r"^www\.", "", urllib.parse.urlparse(href).netloc.lower())
        if domain not in _EXCLUDED_DOMAINS:
            return href

    return None


# ── Step 4: extract contact info from a business homepage ─────────────────────

def _extract_contact(
    homepage_url: str,
    session:      requests.Session,
    ua:           str,
    log_fn:       Callable[[str], None],
) -> Dict[str, Optional[str]]:
    """Visit homepage and optionally /contact page; return phone + email."""
    result = {"phone": None, "email": None, "address": None}

    def _parse_page(html: str) -> None:
        soup = BeautifulSoup(html, "lxml")

        # Phone from tel: links
        if not result["phone"]:
            for a in soup.select("a[href^='tel:']"):
                raw = (a.get("href") or "").replace("tel:", "")
                p   = clean_phone(raw)
                if p:
                    result["phone"] = p
                    break

        # Phone from text — look for common patterns
        if not result["phone"]:
            text = soup.get_text(" ")
            for m in re.finditer(r"[\+\(]?\d[\d\s\-\.\(\)]{8,14}\d", text):
                p = clean_phone(m.group(0))
                if p:
                    result["phone"] = p
                    break

        # Email from mailto: links
        if not result["email"]:
            for a in soup.select("a[href^='mailto:']"):
                raw = (a.get("href") or "").replace("mailto:", "").split("?")[0].strip()
                e   = clean_email(raw)
                if e:
                    result["email"] = e
                    break

        # Email from text
        if not result["email"]:
            text = soup.get_text(" ")
            for m in re.finditer(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text):
                e = clean_email(m.group(0))
                if e:
                    result["email"] = e
                    break

        # Address from schema.org
        if not result["address"]:
            el = soup.select_one("[itemprop='streetAddress'], .address, address")
            if el:
                result["address"] = el.get_text(strip=True)[:200]

    # Homepage
    resp = _safe_get(homepage_url, session, ua, log_fn, referer=homepage_url)
    if resp:
        _parse_page(resp.text)

    # /contact sub-page if still missing data
    if not result["phone"] or not result["email"]:
        base_url = homepage_url.rstrip("/")
        for path in ("/contact", "/contact-us", "/about"):
            contact_url = base_url + path
            resp = _safe_get(contact_url, session, ua, log_fn,
                             referer=homepage_url, timeout=8)
            if resp and resp.status_code == 200:
                _parse_page(resp.text)
                if result["phone"] and result["email"]:
                    break
            time.sleep(random.uniform(1.0, 2.5))

    return result


# ── Core sync scraper ─────────────────────────────────────────────────────────

def scrape_sync(
    niche:     str,
    city:      str,
    country:   str,
    max_leads: int,
    log_fn:    Callable[[str], None],
) -> List[Dict[str, Any]]:
    log_fn(f"📰 Top-List: 'best {niche}' in '{city}' (target {max_leads})")

    session = requests.Session()
    ua      = _get_ua()

    # ── Step 1: find articles ─────────────────────────────────────────────────
    article_urls = _bing_search_articles(niche, city, session, ua, log_fn)
    if not article_urls:
        log_fn("⚠️  No editorial articles found")
        return []

    # ── Step 2: extract business names ───────────────────────────────────────
    all_names: List[str] = []
    seen_names: Set[str] = set()

    for art_url in article_urls[:5]:
        log_fn(f"  📄 Parsing: {art_url[:80]}")
        resp = _safe_get(art_url, session, ua, log_fn, referer=_BING_BASE)
        if not resp:
            time.sleep(random.uniform(2.0, 4.0))
            continue

        names = _extract_names_from_article(resp.text)
        for n in names:
            key = n.lower()
            if key not in seen_names:
                seen_names.add(key)
                all_names.append(n)

        log_fn(f"    Extracted {len(names)} name(s) ({len(all_names)} total)")
        if len(all_names) >= max_leads * 2:
            break
        time.sleep(random.uniform(2.0, 4.5))

    if not all_names:
        log_fn("⚠️  Could not extract business names from articles")
        return []

    log_fn(f"📋 {len(all_names)} unique names — resolving websites + contacts …")

    # ── Steps 3+4: resolve + extract contacts ────────────────────────────────
    leads: List[Dict] = []

    for name in all_names[:max_leads]:
        if len(leads) >= max_leads:
            break

        log_fn(f"  🔍 '{name}' …")

        website = _resolve_website(name, city, session, ua, log_fn)
        if not website:
            log_fn(f"    ⚠️  No website found for '{name}'")
            time.sleep(random.uniform(1.5, 3.0))
            continue

        contact = _extract_contact(website, session, ua, log_fn)
        log_fn(
            f"    {'✅' if contact['phone'] or contact['email'] else '📋'}"
            f" {name} → {website[:50]}"
        )

        leads.append({
            "business_name": name,
            "phone":         contact["phone"],
            "email":         contact["email"],
            "website":       website,
            "address":       contact["address"],
            "niche":         niche,
            "city":          city,
            "source":        "TOP_LIST",
        })
        time.sleep(random.uniform(2.0, 4.5))

    phone_cnt = sum(1 for l in leads if l.get("phone"))
    web_cnt   = sum(1 for l in leads if l.get("website"))
    email_cnt = sum(1 for l in leads if l.get("email"))
    log_fn(
        f"🏁 Top-List done — {len(leads)} leads"
        f" | {phone_cnt} phones | {email_cnt} emails | {web_cnt} websites"
    )
    return leads


# ── Public async API ──────────────────────────────────────────────────────────

async def scrape_top_list(
    niche:        str,
    city:         str,
    country:      str = "",
    max_leads:    int = 20,
    log_callback: Optional[Callable[[str], None]] = None,
) -> List[Dict[str, Any]]:
    def _log(msg: str) -> None:
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass
    _log(f"🚀 Top-List scraper: {niche} in {city} (max {max_leads})")
    return await asyncio.to_thread(scrape_sync, niche, city, country, max_leads, _log)


async def scrape(
    niche:        str,
    city:         str,
    max_results:  int = 20,
    cfg:          Optional[Dict[str, Any]] = None,
    log_callback: Optional[Callable[[str], None]] = None,
    country:      str = "",
) -> List[Dict[str, Any]]:
    """Orchestrator-compatible dispatch alias."""
    return await scrape_top_list(
        niche=niche, city=city, country=country,
        max_leads=max_results, log_callback=log_callback,
    )
