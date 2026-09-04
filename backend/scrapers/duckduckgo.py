"""
duckduckgo.py — DuckDuckGo web-search lead scraper.

The one free, no-key, no-JS web-search engine that tolerates light automated
use. Phase 1 collects business URLs from the DDG HTML endpoint (reusing
_shared.ddg_collect_urls — junk-domain filtered, domain-deduped). Phase 2
reuses google_search._extract_business_info to pull name / email / phone from
each site. No CAPTCHA solving: a blocked/empty response just yields fewer
leads and the orchestrator's Maps fallback covers the shortfall.
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Callable, Dict, List, Optional

import requests

from ._shared import ddg_collect_urls, expand_niche, resolve_delay
from .google_search import _extract_business_info, _get_domain, _get_ua

_BOILERPLATE_TITLES = frozenset({
    "javascript is disabled", "just a moment...", "access denied",
    "attention required! | cloudflare", "are you a robot?", "page not found",
    "403 forbidden", "site not found", "untitled",
})


def _scrape_sync(
    niche: str, city: str, country: str, max_results: int,
    log_fn: Callable[[str], None], cfg: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    delay_min, delay_max = resolve_delay(cfg, 2.0, 5.0)
    variants = expand_niche(niche)[:4] or [niche]
    loc = " ".join(p for p in (city, country) if p)

    search_sess = requests.Session()
    url_pool: Dict[str, str] = {}
    for i, variant in enumerate(variants):
        if len(url_pool) >= max_results * 2:
            break
        query = f"{variant} {loc}".strip()
        log_fn(f"  🦆 DDG [{i + 1}/{len(variants)}]: \"{query}\"")
        for u in ddg_collect_urls(query, search_sess, log_fn, max_urls=max_results * 2):
            d = _get_domain(u)
            if d and d not in url_pool:
                url_pool[d] = u
        if i < len(variants) - 1:
            time.sleep(random.uniform(delay_min, delay_max))

    targets = list(url_pool.values())[:max_results]
    log_fn(f"  🦆 DDG: {len(url_pool)} domains → visiting {len(targets)}")

    leads: List[Dict[str, Any]] = []
    site_sess = requests.Session()
    for i, url in enumerate(targets, 1):
        info = _extract_business_info(url, site_sess, _get_ua(), log_fn)
        name = (info.get("business_name") or "").strip()
        if not name or name.lower() in _BOILERPLATE_TITLES or len(name) > 90:
            name = _get_domain(url)
        leads.append({
            "business_name": name,
            "phone":   info.get("phone"),
            "email":   info.get("email"),
            "website": url,
            "address": info.get("address"),
            "niche":   niche,
            "city":    city,
            "country": country or None,
            "source":  "DUCKDUCKGO",
            "raw_url":  url,
        })
        time.sleep(random.uniform(delay_min, delay_max))
    return leads


async def scrape(
    niche: str, city: str, country: str = "", max_results: int = 20,
    cfg: Optional[Dict[str, Any]] = None,
    log_callback: Optional[Callable[[str], None]] = None,
) -> List[Dict[str, Any]]:
    def _log(msg: str) -> None:
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass

    _log(f"🦆 DuckDuckGo web search: {niche} / {city} (max {max_results})")
    try:
        return await asyncio.to_thread(_scrape_sync, niche, city, country, max_results, _log, cfg)
    except Exception as exc:  # never raises — orchestrator handles empties
        _log(f"  ⚠️  DuckDuckGo failed: {exc}")
        return []
