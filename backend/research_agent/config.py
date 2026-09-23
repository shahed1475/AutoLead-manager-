"""
config.py — Browser Research Agent budgets/limits.

Follows the existing app-wide pattern (see backend/scraper.py::_scraper_cfg,
backend/ai_brain.py::_ollama_cfg): pydantic-settings defaults, overridden at
runtime by the app_settings DB table so limits are editable from Settings
without a restart.
"""
from __future__ import annotations

from typing import Any, Dict

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
    "research_agent_max_decision_makers": 5,
}


async def get_research_config() -> Dict[str, Any]:
    """Read budgets: app_settings DB override > .env/pydantic default > this
    module's hardcoded fallback — the same layering every other config
    module in the app follows (_scraper_cfg, _ollama_cfg)."""
    stored = await db.get_all_settings()
    cfg: Dict[str, Any] = {}
    for key, hardcoded_default in _DEFAULTS.items():
        default = getattr(_env, key, hardcoded_default)
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
    return cfg
