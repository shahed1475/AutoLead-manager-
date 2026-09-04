"""
config.py — Browser Research Agent budgets/limits.

Layering: app_settings DB override > active depth preset > .env/pydantic
default > this module's hardcoded fallback. The depth preset is the one new
layer (Cycle 1 of the deliberate-page-research work) — everything else
follows the existing app-wide pattern (see backend/scraper.py::_scraper_cfg,
backend/ai_brain.py::_ollama_cfg).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .. import database as db
from ..config import get_settings

_env = get_settings()

_DEFAULTS: Dict[str, Any] = {
    "research_agent_headless": False,          # visible Chrome by default in development
    "research_agent_max_actions_per_lead": 12,
    "research_agent_max_searches_per_lead": 4,
    "research_agent_max_pages_per_lead": 5,
    "research_agent_max_time_per_lead_seconds": 180,
    "research_agent_max_total_leads": 20,
    "research_agent_max_consecutive_failures": 3,
    "research_agent_max_geographic_units": 5,
    "research_agent_page_timeout_ms": 20000,
    "research_agent_save_to_leads": True,
    # Deliberate-page-research (Cycle 1) additions — these are the
    # "standard"-depth values; DEPTH_PRESETS overrides them per depth mode.
    "research_agent_pacing_profile": "standard",
    "research_agent_max_scrolls_per_page": 6,
    "research_agent_max_domain_seconds": 120,
    "research_agent_page_time_cap_seconds": 40,
    "research_agent_max_queued_links": 6,
    "research_agent_time_budget_seconds": 0,           # 0 = no session-level time budget
    "research_agent_allow_professional_profiles": False,
}

# One preset per research_depth value — see
# docs/superpowers/specs/2026-09-04-deliberate-page-research-design.md §7.
DEPTH_PRESETS: Dict[str, Dict[str, Any]] = {
    "quick": {
        "research_agent_pacing_profile": "fast",
        "research_agent_max_pages_per_lead": 2,
        "research_agent_max_scrolls_per_page": 3,
        "research_agent_max_searches_per_lead": 2,
        "research_agent_max_actions_per_lead": 8,
        "research_agent_max_time_per_lead_seconds": 90,
        "research_agent_max_domain_seconds": 45,
        "research_agent_page_time_cap_seconds": 20,
    },
    "standard": {
        "research_agent_pacing_profile": "standard",
        "research_agent_max_pages_per_lead": 5,
        "research_agent_max_scrolls_per_page": 6,
        "research_agent_max_searches_per_lead": 4,
        "research_agent_max_actions_per_lead": 14,
        "research_agent_max_time_per_lead_seconds": 200,
        "research_agent_max_domain_seconds": 120,
        "research_agent_page_time_cap_seconds": 40,
    },
    "deep": {
        "research_agent_pacing_profile": "deliberate",
        "research_agent_max_pages_per_lead": 9,
        "research_agent_max_scrolls_per_page": 12,
        "research_agent_max_searches_per_lead": 6,
        "research_agent_max_actions_per_lead": 24,
        "research_agent_max_time_per_lead_seconds": 420,
        "research_agent_max_domain_seconds": 240,
        "research_agent_page_time_cap_seconds": 75,
    },
    "max": {
        "research_agent_pacing_profile": "deliberate",
        "research_agent_max_pages_per_lead": 14,
        "research_agent_max_scrolls_per_page": 20,
        "research_agent_max_searches_per_lead": 8,
        "research_agent_max_actions_per_lead": 36,
        "research_agent_max_time_per_lead_seconds": 900,
        "research_agent_max_domain_seconds": 480,
        "research_agent_page_time_cap_seconds": 120,
    },
}
_DEFAULT_DEPTH = "standard"


def resolve_depth(depth: Optional[str]) -> str:
    d = (depth or _DEFAULT_DEPTH).strip().lower()
    return d if d in DEPTH_PRESETS else _DEFAULT_DEPTH


async def get_research_config(depth: Optional[str] = None) -> Dict[str, Any]:
    """Read budgets: app_settings DB override > active depth preset >
    .env/pydantic default > this module's hardcoded fallback.

    `depth` picks the preset. If not given, the stored
    `research_agent_research_depth` app_setting is used (so an operator can
    change every session's default depth from Settings without a deploy);
    if that isn't set either, "standard"."""
    stored = await db.get_all_settings()
    if depth is None:
        depth = stored.get("research_agent_research_depth")
    resolved_depth = resolve_depth(depth)
    preset = DEPTH_PRESETS[resolved_depth]

    cfg: Dict[str, Any] = {}
    for key, hardcoded_default in _DEFAULTS.items():
        default = preset.get(key, getattr(_env, key, hardcoded_default))
        raw = stored.get(key)
        if raw is None:
            cfg[key] = default
        elif isinstance(default, bool):
            cfg[key] = str(raw).lower() == "true"
        elif isinstance(default, int):
            try:
                cfg[key] = int(raw)
            except (TypeError, ValueError):
                cfg[key] = default
        else:
            cfg[key] = raw
    cfg["research_agent_research_depth"] = resolved_depth
    return cfg
