"""
config.py — Lead Search Automation settings.

Layering: app_settings DB override > .env/pydantic default > this module's
fallback — identical to backend/research_agent/config.py and _scraper_cfg.
"""
from __future__ import annotations

from typing import Any, Dict

from .. import database as db
from ..config import get_settings

_env = get_settings()

# A Selenium Google Maps scrape floors at ~3-8s per business, so a per-item
# target far above this cannot finish inside the automation's per-source time
# budget. The read-clamps below make an out-of-range stored value (e.g. a legacy
# 500, or a hand-edited negative) harmless without a DB write — every consumer
# reads through get_automation_settings(), so this is the single chokepoint.
_MAX_PER_ITEM_TARGET = 40
_MAX_DAILY_LIMIT = 100_000
_MAX_DURATION_HOURS = 24
_MAX_RETRIES = 5

# key -> (min, max), inclusive. Applied after type coercion below.
_INT_BOUNDS: Dict[str, tuple] = {
    "automation_daily_limit":     (1, _MAX_DAILY_LIMIT),
    "automation_duration_hours":  (0, _MAX_DURATION_HOURS),   # 0 = no daily time limit
    "automation_per_item_target": (1, _MAX_PER_ITEM_TARGET),
    "automation_max_retries":     (0, _MAX_RETRIES),
}

_DEFAULTS: Dict[str, Any] = {
    "automation_enabled":         False,
    "automation_daily_limit":     500,
    "automation_start_time":      "07:00",
    "automation_timezone":        "America/New_York",
    "automation_duration_hours":  4,
    "automation_per_item_target": 40,
    "automation_max_retries":     2,
}


async def get_automation_settings() -> Dict[str, Any]:
    stored = await db.get_all_settings()
    cfg: Dict[str, Any] = {}
    for key, hardcoded in _DEFAULTS.items():
        default = getattr(_env, key, hardcoded)
        raw = stored.get(key)
        if raw is None:
            cfg[key] = default
        elif isinstance(default, bool):
            cfg[key] = str(raw).strip().lower() == "true"
        elif isinstance(default, int):
            try:
                cfg[key] = int(raw)
            except (TypeError, ValueError):
                cfg[key] = default
        else:
            cfg[key] = str(raw)

    for key, (lo, hi) in _INT_BOUNDS.items():
        try:
            cfg[key] = max(lo, min(int(cfg[key]), hi))
        except (TypeError, ValueError):
            cfg[key] = _DEFAULTS[key]
    return cfg
