"""
solution_matcher.py — deterministic PopupGenix service matcher.

Deliberately NOT LLM-based: a rule-based keyword/condition match against
service_knowledge_base.py is auditable (every recommendation traces to the
exact keyword that fired) and structurally incapable of "inventing a reason
to sell a service" — the opportunity/pain-point signal has to actually
contain a matching condition, or nothing is recommended at all.

Public API
──────────
  match_solution(opportunity, pain_points, company_profile, evidence) -> dict | None
"""
import re
from typing import Any, Dict, List, Optional

from .service_knowledge_base import SERVICE_KNOWLEDGE_BASE

# Below this many matched keywords, we don't have enough signal to recommend
# anything — returning None (no forced pick) rather than the "least-bad" service.
_MIN_MATCHES_TO_RECOMMEND = 1


def _normalize(text: Any) -> str:
    if text is None:
        return ""
    if isinstance(text, (list, tuple)):
        return " ".join(str(x) for x in text)
    return str(text)


def _build_signal_text(
    opportunity: Dict[str, Any],
    pain_points: List[Dict[str, Any]],
    company_profile: Dict[str, Any],
) -> str:
    parts = [
        _normalize(opportunity.get("opportunity")),
        _normalize(opportunity.get("business_ease")),
        _normalize(opportunity.get("why_it_matters")),
        _normalize((company_profile or {}).get("industry")),
        _normalize((company_profile or {}).get("tech_stack")),
    ]
    for pp in pain_points or []:
        parts.append(_normalize(pp.get("title")))
        parts.append(_normalize(pp.get("description")))
    return " ".join(parts).lower()


def _evidence_ids_for_pain_points(
    pain_points: List[Dict[str, Any]], evidence: List[Dict[str, Any]],
) -> List[int]:
    """Trace back to the exact research_evidence rows behind this opportunity's
    source pain points — pain_point_agent.py tags each with
    field_name="pain_point:<title[:60]>" (see add_research_evidence call in
    orchestrator.run_pain_point_analysis)."""
    titles = {(pp.get("title") or "")[:60] for pp in (pain_points or [])}
    ids: List[int] = []
    for item in evidence or []:
        field_name = item.get("field_name") or ""
        if field_name.startswith("pain_point:") and field_name[len("pain_point:"):] in titles:
            if item.get("id") is not None:
                ids.append(item["id"])
    return ids


def match_solution(
    opportunity: Dict[str, Any],
    pain_points: List[Dict[str, Any]],
    company_profile: Dict[str, Any],
    evidence: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Score every service in the knowledge base against this opportunity's
    signal text; return the single best-matching service, or None if nothing
    matches with enough confidence — never a forced/arbitrary pick.
    """
    signal_text = _build_signal_text(opportunity, pain_points, company_profile)
    if not signal_text.strip():
        return None

    best_service = None
    best_matches: List[str] = []

    for svc in SERVICE_KNOWLEDGE_BASE:
        if any(re.search(re.escape(excl), signal_text) for excl in svc.do_not_recommend_when):
            continue  # explicitly excluded — never recommended regardless of other matches

        matched = [kw for kw in svc.recommend_when if re.search(re.escape(kw), signal_text)]
        if len(matched) < _MIN_MATCHES_TO_RECOMMEND:
            continue

        if best_service is None or len(matched) > len(best_matches):
            best_service = svc
            best_matches = matched
        # Ties keep the first (declaration-order) match — deterministic, no re-pick.

    if best_service is None:
        return None

    opportunity_confidence = float(opportunity.get("confidence") or 0.5)
    match_strength = min(len(best_matches), 5) * 0.1
    confidence = round(min(opportunity_confidence, 0.5 + match_strength), 2)

    return {
        "service": best_service.name,
        "reason": f"{best_matches[0].capitalize()} detected",
        "evidence_ids": _evidence_ids_for_pain_points(pain_points, evidence),
        "confidence": confidence,
    }
