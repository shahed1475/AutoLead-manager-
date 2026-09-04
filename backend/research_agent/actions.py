"""
actions.py — controlled tool registry. The LLM never touches Playwright
directly; it can only request one of these named actions, each with a
validated input schema, a timeout, and structured error handling. Python
executes; the LLM only ever sees the JSON result.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .models import VALID_ACTIONS, AgentAction

logger = logging.getLogger(__name__)


class ActionValidationError(ValueError):
    pass


@dataclass
class ActionResult:
    action: str
    status: str = "success"          # "success" | "error" | "blocked"
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_llm_dict(self, max_chars: int = 2000) -> Dict[str, Any]:
        """Trimmed view fed back to the LLM — full data stays available to
        Python (extraction.py reads the untrimmed page text separately)."""
        out = {"action": self.action, "status": self.status}
        if self.error:
            out["error"] = self.error[:300]
        for k, v in self.data.items():
            if isinstance(v, str) and len(v) > max_chars:
                out[k] = v[:max_chars] + "…"
            else:
                out[k] = v
        return out


def validate_action(action: AgentAction) -> None:
    """Raises ActionValidationError with a specific reason, or returns None."""
    if action.action not in VALID_ACTIONS:
        raise ActionValidationError(f"Unknown action '{action.action}'")

    p = action.params or {}

    if action.action == "google_search":
        if not str(p.get("query", "")).strip():
            raise ActionValidationError("google_search requires a non-empty 'query'")
    elif action.action in ("open_url", "open_new_tab"):
        url = str(p.get("url", "")).strip()
        if not url or not (url.startswith("http://") or url.startswith("https://")):
            raise ActionValidationError(f"{action.action} requires a valid http(s) 'url'")
    elif action.action == "find_links":
        pass  # keyword is optional
    elif action.action == "click":
        if not str(p.get("text", "")).strip() and not str(p.get("selector", "")).strip():
            raise ActionValidationError("click requires 'text' or 'selector'")
    elif action.action == "save_evidence":
        if not str(p.get("field_name", "")).strip():
            raise ActionValidationError("save_evidence requires 'field_name'")
        if p.get("value") in (None, ""):
            raise ActionValidationError("save_evidence requires a non-empty 'value'")
    # extract_page_text, go_back, screenshot, finish_research — no required params
