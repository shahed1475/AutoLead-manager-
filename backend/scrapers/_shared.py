import logging
import random
import re
import time
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def resolve_delay(
    cfg: Optional[Dict[str, Any]],
    default_min: float,
    default_max: float,
) -> Tuple[float, float]:
    """
    Read delay_min/delay_max from the Settings-UI scraper config, falling
    back to this scraper's own sane defaults when unset. Previously every
    non-Maps scraper accepted `cfg` but ignored it entirely, so the delay
    sliders in Settings only ever affected Google Maps.
    """
    if not cfg:
        return default_min, default_max
    lo = float(cfg.get("delay_min") or default_min)
    hi = float(cfg.get("delay_max") or default_max)
    if hi < lo:
        hi = lo
    return lo, hi

_FALLBACK_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

_JUNK_DOMAINS = frozenset(
    {
        "duckduckgo.com",
        "bing.com",
        "google.",
        "youtube.com",
        "facebook.com",
        "instagram.com",
        "linkedin.com",
        "twitter.com",
        "x.com",
        "wikipedia.org",
        "yelp.com",
        "tripadvisor.",
        "yellowpages.",
        "bbb.org",
        "amazon.",
        "reddit.com",
        "pinterest.",
        "tiktok.com",
        "indeed.com",
        "glassdoor.",
    }
)


def expand_niche(niche: str) -> List[str]:
    base = " ".join((niche or "").strip().split())
    if not base:
        return []

    lower = base.lower()
    variants = [
        base,
        f"{base} business",
        f"{base} company",
        f"{base} services",
        f"{base} near me",
    ]

    synonym_map = {
        "dentist": ["dental clinic", "dental office"],
        "restaurant": ["cafe", "food business"],
        "lawyer": ["law firm", "legal services"],
        "real estate": ["realtor", "real estate agency"],
        "gym": ["fitness center", "personal training"],
        "plumber": ["plumbing company", "plumbing service"],
        "hvac": ["heating cooling company", "air conditioning service"],
        "marketing": ["digital marketing agency", "advertising agency"],
        "web design": ["website design agency", "web development company"],
    }
    for key, synonyms in synonym_map.items():
        if key in lower:
            variants.extend(synonyms)

    seen = set()
    unique = []
    for item in variants:
        norm = item.lower()
        if norm not in seen:
            seen.add(norm)
            unique.append(item)
    return unique


def _headers() -> dict:
    return {
        "User-Agent": random.choice(_FALLBACK_UAS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://duckduckgo.com/",
    }


def _is_junk_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc.lower().lstrip("www.")
        if not parsed.scheme.startswith("http") or not domain:
            return True
        return any(junk in domain for junk in _JUNK_DOMAINS)
    except Exception:
        return True


def _decode_ddg_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    if href.startswith("/l/"):
        query = urllib.parse.urlparse(href).query
        target = urllib.parse.parse_qs(query).get("uddg", [""])[0]
        return urllib.parse.unquote(target)
    if "duckduckgo.com/l/" in href:
        query = urllib.parse.urlparse(href).query
        target = urllib.parse.parse_qs(query).get("uddg", [""])[0]
        return urllib.parse.unquote(target)
    return href


def ddg_collect_urls(
    query: str,
    session: requests.Session,
    log_fn: Callable[[str], None],
    max_urls: int = 20,
) -> List[str]:
    urls: List[str] = []
    seen: Set[str] = set()
    encoded = urllib.parse.quote_plus(query)
    search_urls = [
        f"https://duckduckgo.com/html/?q={encoded}",
        f"https://html.duckduckgo.com/html/?q={encoded}",
    ]

    for search_url in search_urls:
        if len(urls) >= max_urls:
            break
        try:
            resp = session.get(search_url, headers=_headers(), timeout=15)
            if resp.status_code != 200:
                log_fn(f"     DDG returned HTTP {resp.status_code}")
                continue
        except requests.RequestException as exc:
            log_fn(f"     DDG request failed: {exc}")
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        links = soup.select("a.result__a, a.result-link, a[href]")
        for link in links:
            href = _decode_ddg_url(link.get("href", ""))
            if not href.startswith("http"):
                continue
            href = re.sub(r"#.*$", "", href)
            if _is_junk_url(href):
                continue
            domain = urllib.parse.urlparse(href).netloc.lower().lstrip("www.")
            if domain in seen:
                continue
            seen.add(domain)
            urls.append(href)
            if len(urls) >= max_urls:
                break

        if urls:
            break
        time.sleep(random.uniform(0.5, 1.5))

    return urls
