"""
provider_catalog.py — the descriptive + status layer over search providers
(spec §5-6). The execution layer is still discovery/adapters.py::SourceRegistry;
this module only tells the UI which engines exist and whether each is usable.

Status values (spec §5):
  available            — works now, no configuration needed
  configured           — optional/idle engine that is wired and ready
  not_configured       — needs an API key / instance URL the user hasn't set
  api_required         — only usable via a paid/keyed API (never scraped)
  unsupported          — no ToS-compliant, no-cost integration path
  temporarily_unavailable — implemented but currently blocked (set at runtime)
"""
from __future__ import annotations

from typing import Any, Dict, List

from .. import database as db

# kind: directory | web_search | api | meta | ai
_CATALOG: List[Dict[str, Any]] = [
    # ── Directory sources (business-level, primary) ──────────────────────
    {"id": "GOOGLE_MAPS", "name": "Google Maps", "category": "primary", "kind": "directory", "base": "available"},
    {"id": "YELLOW_PAGES", "name": "Yellow Pages", "category": "primary", "kind": "directory", "base": "available"},
    {"id": "YELP", "name": "Yelp", "category": "primary", "kind": "directory", "base": "configured"},
    {"id": "FOURSQUARE", "name": "Foursquare", "category": "alternative", "kind": "directory", "base": "configured"},
    {"id": "HOTFROG", "name": "Hotfrog", "category": "alternative", "kind": "directory", "base": "configured"},
    {"id": "TOP_LIST", "name": "Top-List articles", "category": "alternative", "kind": "directory", "base": "configured"},
    {"id": "GENERIC_DIR", "name": "Business directories", "category": "alternative", "kind": "directory", "base": "configured"},
    # ── Web search (SERP → lead extraction) ──────────────────────────────
    {"id": "GOOGLE_SEARCH", "name": "Google", "category": "primary", "kind": "web_search", "base": "available"},
    {"id": "BING_SEARCH", "name": "Bing", "category": "primary", "kind": "web_search", "base": "available"},
    {"id": "DUCKDUCKGO", "name": "DuckDuckGo", "category": "primary", "kind": "web_search", "base": "available"},
    {"id": "BRAVE", "name": "Brave Search", "category": "primary", "kind": "web_search", "base": "available"},
    {"id": "STARTPAGE", "name": "Startpage", "category": "alternative", "kind": "web_search", "base": "available"},
    {"id": "ECOSIA", "name": "Ecosia", "category": "alternative", "kind": "web_search", "base": "available"},
    {"id": "MOJEEK", "name": "Mojeek", "category": "alternative", "kind": "web_search", "base": "available"},
    {"id": "MARGINALIA", "name": "Marginalia", "category": "alternative", "kind": "web_search", "base": "available"},
    {"id": "SEARXNG", "name": "SearXNG (self-hosted)", "category": "alternative", "kind": "api",
     "base": "not_configured", "key_setting": "searxng_instance_url"},
    # ── Keyed / paid only ───────────────────────────────────────────────
    {"id": "YANDEX", "name": "Yandex", "category": "alternative", "kind": "api", "base": "api_required"},
    {"id": "BAIDU", "name": "Baidu", "category": "alternative", "kind": "api", "base": "api_required"},
    {"id": "KAGI", "name": "Kagi", "category": "alternative", "kind": "api", "base": "api_required"},
    {"id": "QWANT", "name": "Qwant", "category": "alternative", "kind": "web_search", "base": "unsupported"},
    {"id": "SWISSCOWS", "name": "Swisscows", "category": "alternative", "kind": "web_search", "base": "unsupported"},
    {"id": "YAHOO", "name": "Yahoo", "category": "primary", "kind": "meta", "base": "unsupported"},
    # ── Meta / legacy (no API, resell other indexes) ────────────────────
    *[
        {"id": pid, "name": pname, "category": "meta", "kind": "meta", "base": "unsupported"}
        for pid, pname in [
            ("AOL", "AOL Search"), ("ASK", "Ask.com"), ("EXCITE", "Excite"), ("LYCOS", "Lycos"),
            ("DOGPILE", "Dogpile"), ("WEBCRAWLER", "WebCrawler"), ("GIBIRU", "Gibiru"),
            ("METAGER", "MetaGer"), ("SEARCH_ENCRYPT", "Search Encrypt"), ("PRESEARCH", "Presearch"),
        ]
    ],
    # ── AI search (all paid API or no API) ─────────────────────────────
    *[
        {"id": pid, "name": pname, "category": "ai", "kind": "ai", "base": "api_required"}
        for pid, pname in [
            ("PERPLEXITY", "Perplexity"), ("YOU", "You.com"), ("PHIND", "Phind"),
            ("KOMO", "Komo"), ("ANDI", "Andi"), ("SEARCHGPT", "SearchGPT"),
        ]
    ],
    {"id": "ARC_SEARCH", "name": "Arc Search", "category": "ai", "kind": "ai", "base": "unsupported"},
    {"id": "YEP", "name": "Yep", "category": "alternative", "kind": "api", "base": "api_required"},
]

# Free but needs a URL/key the user supplies — 'not_configured' until then.
_FREE_KEYED: Dict[str, str] = {"SEARXNG": "searxng_instance_url"}
# Paid API only — stays 'api_required' until the user opts in with a key.
_PAID_KEYED: Dict[str, str] = {
    "YEP": "yep_api_key", "KAGI": "kagi_api_key",
    "PERPLEXITY": "perplexity_api_key", "YANDEX": "yandex_api_key",
}


async def get_provider_catalog() -> List[Dict[str, Any]]:
    """The full catalog with per-request status resolved against app_settings."""
    stored = await db.get_all_settings()

    def has(key: str) -> bool:
        return bool((stored.get(key) or "").strip())

    out: List[Dict[str, Any]] = []
    for p in _CATALOG:
        pid = p["id"]
        key_setting = None
        if pid in _FREE_KEYED:
            key_setting = _FREE_KEYED[pid]
            status = "configured" if has(key_setting) else "not_configured"
        elif pid in _PAID_KEYED:
            key_setting = _PAID_KEYED[pid]
            status = "configured" if has(key_setting) else "api_required"
        else:
            status = p["base"]
            key_setting = p.get("key_setting")

        enabled = str(stored.get(f"{pid.lower()}_search_enabled", "true")).strip().lower() != "false"
        out.append({
            "id": pid, "name": p["name"], "category": p["category"], "kind": p["kind"],
            "status": status, "requires_key": bool(key_setting), "key_setting": key_setting,
            "enabled": enabled and status in ("available", "configured"),
        })
    return out
