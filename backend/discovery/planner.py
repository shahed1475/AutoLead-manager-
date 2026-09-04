"""
planner.py — Discovery Planner: classifies a lead-search request and
recommends sources + bounded query variants.

Rule-based classification first (industry_keywords.py); the LLM
(ai_brain._call_llm_raw) is only called when nothing matches. Never raises —
any LLM failure (timeout, malformed JSON, connection error) degrades to a
safe general-search fallback. See design spec:
docs/superpowers/specs/2026-08-25-lead-discovery-planner-design.md
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..ai_brain import _call_llm_raw, _ollama_cfg
from .. import database as db
from ..config import get_settings
from .industry_keywords import LOCAL_BUSINESS_KEYWORDS, STARTUP_TECH_KEYWORDS

logger = logging.getLogger(__name__)
_env = get_settings()

INTENT_LOCAL_BUSINESS = "LOCAL_BUSINESS"
INTENT_STARTUP_TECH = "STARTUP_TECH_COMPANY"
INTENT_AMBIGUOUS = "AMBIGUOUS"

_VALID_INTENTS = frozenset({INTENT_LOCAL_BUSINESS, INTENT_STARTUP_TECH, INTENT_AMBIGUOUS})

_LOCAL_BUSINESS_SOURCES = ["GOOGLE_MAPS", "YELLOW_PAGES", "DUCKDUCKGO"]
_STARTUP_TECH_SOURCES = ["GOOGLE_SEARCH", "BING_SEARCH", "DUCKDUCKGO"]
_GENERAL_FALLBACK_SOURCES = ["GOOGLE_SEARCH", "DUCKDUCKGO", "GOOGLE_MAPS"]

_MODE_DEFAULT_MAX_VARIANTS = {"QUICK": "discovery_quick_max_variants", "CAMPAIGN": "discovery_campaign_max_variants"}


@dataclass
class DiscoveryPlan:
    intent: str
    confidence: float
    recommended_sources: List[str]
    query_variants: List[str]
    mode: str
    classification_method: str = "rule_based"  # rule_based | llm | llm_fallback

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "recommended_sources": self.recommended_sources,
            "query_variants": self.query_variants,
            "mode": self.mode,
            "classification_method": self.classification_method,
        }


async def _max_variants(mode: str) -> int:
    stored = await db.get_all_settings()
    if mode == "QUICK":
        return int(stored.get("discovery_quick_max_variants") or _env.discovery_quick_max_variants)
    return int(stored.get("discovery_campaign_max_variants") or _env.discovery_campaign_max_variants)


async def _max_sources(mode: str) -> int:
    if mode != "QUICK":
        return 9  # Campaign keeps manual multi-source selection — no cap here
    stored = await db.get_all_settings()
    return int(stored.get("discovery_quick_max_sources") or _env.discovery_quick_max_sources)


def _match_keywords(text: str, keywords: List[str]) -> bool:
    """Word-boundary match, not a bare substring check — a naive `kw in text`
    would match "ai" inside "retail"/"tailor"/"detailing" or "vet" inside
    "advertising"/"convert"."""
    t = text.lower()
    return any(re.search(rf"\b{re.escape(kw)}\b", t) for kw in keywords)


def _rule_based_variants(niche: str, max_variants: int) -> List[str]:
    """Conservative, no-fabrication variant generation: the base phrase plus
    a simple singular/plural companion when it's clearly a different string.
    Never invents a city, business name, or fact not present in the input."""
    base = (niche or "").strip()
    if not base:
        return []
    variants = [base]
    lower = base.lower()
    if lower.endswith("s") and len(base) > 3:
        variants.append(base[:-1])
    elif not lower.endswith("s"):
        variants.append(base + "s")

    seen, out = set(), []
    for v in variants:
        key = v.lower()
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out[:max_variants]


def _classify_rule_based(query: str, niche: str) -> Optional[DiscoveryPlan]:
    text = f"{query or ''} {niche or ''}".strip()
    if not text:
        return None
    if _match_keywords(text, LOCAL_BUSINESS_KEYWORDS):
        return DiscoveryPlan(
            intent=INTENT_LOCAL_BUSINESS, confidence=0.9,
            recommended_sources=list(_LOCAL_BUSINESS_SOURCES),
            query_variants=[], mode="", classification_method="rule_based",
        )
    if _match_keywords(text, STARTUP_TECH_KEYWORDS):
        return DiscoveryPlan(
            intent=INTENT_STARTUP_TECH, confidence=0.9,
            recommended_sources=list(_STARTUP_TECH_SOURCES),
            query_variants=[], mode="", classification_method="rule_based",
        )
    return None


_LLM_REQUIRED = frozenset({"intent", "confidence", "recommended_sources", "query_variants"})


def _parse_planner_json(text: str) -> Optional[Dict[str, Any]]:
    """
    Deliberately NOT ai_brain._extract_json/_map_keys — those coerce every
    field to str(...), which would corrupt confidence (float) and the two
    list fields. Same multi-strategy resilience (raw -> fenced -> brace-span)
    but preserves real JSON types.
    """
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
        if not isinstance(data, dict) or not _LLM_REQUIRED.issubset(data.keys()):
            continue
        intent = str(data.get("intent", "")).strip().upper()
        if intent not in _VALID_INTENTS:
            continue
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        sources = data.get("recommended_sources")
        variants = data.get("query_variants")
        if not isinstance(sources, list) or not isinstance(variants, list):
            continue
        sources = [str(s).strip().upper() for s in sources if str(s).strip()]
        variants = [str(v).strip() for v in variants if str(v).strip()]
        if not sources:
            continue
        return {
            "intent": intent, "confidence": confidence,
            "recommended_sources": sources, "query_variants": variants,
        }
    return None


def _llm_prompt(query: str, niche: str, city: str, country: str) -> str:
    return f"""Classify this lead-generation search request.

Query: {query or niche}
Niche: {niche}
City: {city}
Country: {country or "(not specified)"}

Return ONLY strict JSON, no prose, no markdown fences:
{{
  "intent": "LOCAL_BUSINESS" | "STARTUP_TECH_COMPANY" | "AMBIGUOUS",
  "confidence": 0.0-1.0,
  "recommended_sources": ["GOOGLE_MAPS" | "GOOGLE_SEARCH" | "BING_SEARCH" | "YELLOW_PAGES" | "YELP"],
  "query_variants": ["up to 4 short category-phrasing variants of the niche — do NOT invent a specific city, business name, or fact not given above"]
}}

LOCAL_BUSINESS = a physical/location-based business (clinics, restaurants, salons, law firms, real estate, etc.) — best found via Google Maps.
STARTUP_TECH_COMPANY = a startup, SaaS, software, or technology company — best found via Google/Bing search.
Use AMBIGUOUS only if genuinely neither fits."""


async def _classify_via_llm(query: str, niche: str, city: str, country: str) -> DiscoveryPlan:
    fallback = DiscoveryPlan(
        intent=INTENT_STARTUP_TECH, confidence=0.0,
        recommended_sources=list(_GENERAL_FALLBACK_SOURCES),
        query_variants=[q for q in [niche or query] if q],
        mode="", classification_method="llm_fallback",
    )
    try:
        cfg = await _ollama_cfg()
        raw = await _call_llm_raw(
            _llm_prompt(query, niche, city, country), cfg, temperature=0.1, num_predict=300,
        )
        parsed = _parse_planner_json(raw)
        if not parsed:
            logger.info("Discovery planner: LLM response unparseable — using general fallback")
            return fallback
        return DiscoveryPlan(
            intent=parsed["intent"], confidence=parsed["confidence"],
            recommended_sources=parsed["recommended_sources"] or list(_GENERAL_FALLBACK_SOURCES),
            query_variants=parsed["query_variants"], mode="", classification_method="llm",
        )
    except Exception as exc:
        logger.warning("Discovery planner: LLM classification failed (%s) — using general fallback", exc)
        return fallback


class DiscoveryPlanner:
    """DiscoveryPlanner().plan(query, niche, city, country, mode) -> DiscoveryPlan"""

    async def plan(
        self,
        query: str,
        niche: str,
        city: str,
        country: str = "",
        mode: str = "QUICK",
    ) -> DiscoveryPlan:
        mode = (mode or "QUICK").upper()
        effective_niche = (niche or query or "").strip()

        result = _classify_rule_based(query, effective_niche)
        if result is None:
            result = await _classify_via_llm(query, effective_niche, city, country)

        result.mode = mode
        max_sources = await _max_sources(mode)
        result.recommended_sources = result.recommended_sources[:max_sources] or list(_GENERAL_FALLBACK_SOURCES)[:max_sources]

        max_variants = await _max_variants(mode)
        if not result.query_variants:
            result.query_variants = _rule_based_variants(effective_niche, max_variants)
        else:
            result.query_variants = result.query_variants[:max_variants]
        if not result.query_variants and effective_niche:
            result.query_variants = [effective_niche]

        return result
