"""
llm.py — LocalResearchLLM: the two LLM roles the Research Agent uses
(brief §5 in the design spec): decide-next-action and extract-fields.

Wraps ai_brain._call_llm_raw/_ollama_cfg — no second LLM client (brief §4).
Deliberately does NOT reuse ai_brain._extract_json/_map_keys: those coerce
every field to str(...), which is wrong for a typed `confidence: float` and
a `params` dict — same reasoning as discovery/planner.py's own parser.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from ..ai_brain import _call_llm_raw, _ollama_cfg
from .models import VALID_ACTIONS, AgentAction
from .prompts import ACTION_DECISION_PROMPT, ACTION_TOOL_DESCRIPTIONS, FIELD_EXTRACTION_PROMPT

logger = logging.getLogger(__name__)

_NON_PARAM_KEYS = frozenset({"action", "reason", "confidence", "params"})


def _parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Multi-strategy JSON object extraction: raw -> fenced block -> brace span."""
    if not text:
        return None
    candidates = [text.strip()]
    for pattern in (r"```json\s*([\s\S]+?)\s*```", r"```\s*([\s\S]+?)\s*```"):
        for m in re.finditer(pattern, text, re.DOTALL):
            candidates.append(m.group(1).strip())
    for m in re.finditer(r"\{[\s\S]+?\}", text, re.DOTALL):
        candidates.append(m.group(0))
    for cand in candidates:
        try:
            data = json.loads(cand)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


async def decide_next_action(state_summary: str, cfg: Optional[Dict[str, Any]] = None) -> AgentAction:
    """
    Never raises. On any failure (Ollama unavailable, malformed JSON, unknown
    action, timeout), returns AgentAction(action="_llm_failed", ...) — a
    sentinel agent.py's rule-based fallback planner reacts to. The research
    loop must never halt because the LLM had a bad turn.
    """
    try:
        resolved_cfg = cfg or await _ollama_cfg()
        prompt = ACTION_DECISION_PROMPT.format(state_summary=state_summary, tools=ACTION_TOOL_DESCRIPTIONS)
        raw = await _call_llm_raw(prompt, resolved_cfg, temperature=0.2, num_predict=250)
    except Exception as exc:
        logger.warning("decide_next_action: LLM call failed: %s", exc)
        return AgentAction(action="_llm_failed", reason=str(exc)[:200])

    data = _parse_json_object(raw)
    if not data or "action" not in data:
        return AgentAction(action="_llm_failed", reason="unparseable LLM output")

    action = str(data.get("action", "")).strip()
    if action not in VALID_ACTIONS:
        return AgentAction(action="_llm_failed", reason=f"invalid action '{action}'")

    # Params can arrive nested under "params" or flattened as sibling keys
    # (the brief's own example is flat: {"action":..., "query":..., "reason":...}).
    params = data.get("params")
    if not isinstance(params, dict):
        params = {k: v for k, v in data.items() if k not in _NON_PARAM_KEYS}

    try:
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5

    return AgentAction(
        action=action, params=params, reason=str(data.get("reason", ""))[:300],
        confidence=max(0.0, min(1.0, confidence)),
    )


async def extract_fields(
    text: str,
    missing_fields: List[str],
    business_name: Optional[str] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """
    Given page text and which fields are still missing, ask the LLM to pull
    out ONLY what's explicitly present. Returns {} on any failure or if
    there's nothing to extract — deterministic regex extraction
    (extraction.py) is the real floor for phone/email; this only handles
    the genuinely language-dependent fields (name/title).
    """
    if not text or not missing_fields:
        return {}
    try:
        resolved_cfg = cfg or await _ollama_cfg()
        prompt = FIELD_EXTRACTION_PROMPT.format(
            business_name=business_name or "(unknown)",
            missing_fields=", ".join(missing_fields),
            text=text[:4000],
        )
        raw = await _call_llm_raw(prompt, resolved_cfg, temperature=0.1, num_predict=300)
    except Exception as exc:
        logger.warning("extract_fields: LLM call failed: %s", exc)
        return {}

    data = _parse_json_object(raw)
    if not isinstance(data, dict):
        return {}
    junk = {"unknown", "n/a", "null", "none", ""}
    return {
        k: str(v).strip()
        for k, v in data.items()
        if v is not None and str(v).strip().lower() not in junk
    }
