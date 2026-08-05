from typing import Any, Dict

from .orchestrator import run_pending_research, run_research_pipeline

__all__ = ["run_pending_research", "run_research_pipeline", "intelligence_enabled"]


def intelligence_enabled(stored_settings: Dict[str, Any]) -> bool:
    """True if the sales_intelligence_enabled app_setting is turned on. Default: off —
    existing campaign behavior is unchanged unless explicitly opted in."""
    return str(stored_settings.get("sales_intelligence_enabled", "false")).lower() == "true"
