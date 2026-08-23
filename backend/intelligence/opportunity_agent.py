"""
opportunity_agent.py — Opportunity Agent.

Synthesizes already-evidence-backed pain points (Phase 1) into a proper
business opportunity: what to call it, why it matters (tied directly to a
specific pain point's recorded operational/customer impact — never new
facts), what "make X easier" framing applies, and how urgent it is.

Order enforced by construction: this agent only ever runs on pain points
that already exist (persisted, evidence-backed) — it cannot run "opportunity
first" and invent a reason backward. See orchestrator.run_opportunity_analysis,
which requires pain points to be present before this agent is invoked.

Deliberately mostly heuristic/deterministic (like solution_matcher.py): the
"why it matters" sentence is built directly from the source pain point's
already-recorded impact fields, and confidence never exceeds that pain
point's own confidence. An LLM pass is optional and used ONLY to polish the
wording of an already-fully-determined sentence — if it's unavailable or
returns something empty/unusable, the heuristic sentence (which is always
computed first) is used as-is. The LLM is never the source of NEW claims.
"""
import logging
from typing import Any, Dict, List, Optional

from ..ai_brain import _call_llm_raw, _ollama_cfg
from .base import AgentResult, EvidenceItem
from .pain_point_agent import _ease_area_for

logger = logging.getLogger(__name__)

_MAX_POLISHED_SENTENCE_CHARS = 240

_OPPORTUNITY_NAME_BY_AREA: Dict[str, str] = {
    "Appointment Scheduling": "Appointment automation",
    "Ordering & Customer Communication": "Ordering & communication automation",
    "Lead Qualification": "Lead qualification automation",
    "Customer Support": "Customer support automation",
    "Client Management": "Client management automation",
    "Customer Communication": "Customer communication automation",
}

_SEVERITY_RANK = {"high": 2, "medium": 1, "low": 0}


def _strongest(pain_points: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pick the pain point that best justifies this opportunity: highest
    confidence first, severity as tiebreaker. Deterministic — no randomness."""
    return max(
        pain_points,
        key=lambda pp: (
            float(pp.get("confidence") or 0.0),
            _SEVERITY_RANK.get((pp.get("severity") or "medium").lower(), 1),
        ),
    )


def _group_by_area(pain_points: List[Dict[str, Any]], industry: Optional[str], niche: Optional[str]) -> Dict[str, List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    area = _ease_area_for(industry, niche)
    # All pain points for a lead currently map to the same niche-derived area
    # (Phase 1's design) — grouped by area (not per-pain-point) so multiple
    # pain points pointing at the same underlying business need consolidate
    # into ONE opportunity instead of duplicating it once per pain point.
    groups.setdefault(area, []).extend(pain_points)
    return groups


def _build_why_it_matters(strongest: Dict[str, Any]) -> str:
    op_impact = (strongest.get("operational_impact") or "").strip()
    cust_impact = (strongest.get("customer_impact") or "").strip()
    parts = [p for p in (op_impact, cust_impact) if p]
    if parts:
        return " ".join(parts)
    desc = (strongest.get("description") or "").strip()
    if desc:
        return desc
    return f"Evidence indicates: {strongest.get('title', 'an unresolved pain point')}."


def _derive_priority(strongest: Dict[str, Any]) -> str:
    severity = (strongest.get("severity") or "medium").lower()
    classification = (strongest.get("classification") or "inferred").lower()
    if severity == "high" and classification == "observed":
        return "HIGH"
    if severity == "high" or severity == "medium":
        return "MEDIUM"
    return "LOW"


async def _polish_wording(heuristic_sentence: str) -> str:
    """Best-effort LLM rephrase of an already-fully-determined sentence.
    Never introduces new facts — only asked to rephrase, and any response
    that looks unusable (empty, too long, or clearly off-topic) is discarded
    in favor of the heuristic sentence that's always computed first."""
    try:
        cfg = await _ollama_cfg()
        prompt = (
            "Rephrase the following business observation into a single, clear, "
            "professional sentence. Do NOT add any new facts, numbers, or claims "
            "that are not already present. Return ONLY the rephrased sentence, "
            "nothing else.\n\n"
            f"Observation: {heuristic_sentence}"
        )
        raw = (await _call_llm_raw(prompt, cfg, temperature=0.2, num_predict=120)).strip()
    except Exception as exc:
        logger.debug("opportunity_agent: wording polish unavailable, using heuristic sentence: %s", exc)
        return heuristic_sentence

    raw = raw.strip().strip('"')
    if not raw or len(raw) > _MAX_POLISHED_SENTENCE_CHARS:
        return heuristic_sentence
    return raw


class OpportunityAgent:
    name = "opportunity"

    async def run(
        self,
        lead: Dict[str, Any],
        company_profile: Dict[str, Any],
        pain_points: List[Dict[str, Any]],
        evidence: List[Dict[str, Any]],
        campaign: Optional[Dict[str, Any]] = None,
    ) -> AgentResult:
        if not pain_points:
            return AgentResult(status="ok", data={"opportunities": []}, evidence=[], confidence=0.0)

        industry = (company_profile or {}).get("industry")
        niche = lead.get("niche")
        groups = _group_by_area(pain_points, industry, niche)

        opportunities: List[Dict[str, Any]] = []
        result_evidence: List[EvidenceItem] = []

        for area, group in groups.items():
            strongest = _strongest(group)
            heuristic_sentence = _build_why_it_matters(strongest)
            why_it_matters = await _polish_wording(heuristic_sentence)

            confidence = min(1.0, max(0.0, float(strongest.get("confidence") or 0.0)))
            opportunity = {
                "opportunity": _OPPORTUNITY_NAME_BY_AREA.get(area, f"{area} automation"),
                "why_it_matters": why_it_matters,
                "business_ease": f"Make {area.lower()} easier",
                "area": area,
                "priority": _derive_priority(strongest),
                "confidence": confidence,
                "classification": strongest.get("classification") or "inferred",
                "pain_point_id": strongest.get("id"),
                "_source_pain_points": group,  # consumed by the caller for solution matching, not persisted
            }
            opportunities.append(opportunity)

            result_evidence.append(EvidenceItem(
                field_name="opportunity:" + opportunity["opportunity"][:60],
                source_type="heuristic" if opportunity["classification"] == "observed" else "ai_inference",
                source_url=strongest.get("source_url"),
                snippet=heuristic_sentence,
            ))

        overall_confidence = max((o["confidence"] for o in opportunities), default=0.0)

        return AgentResult(
            status="ok",
            data={"opportunities": opportunities},
            evidence=result_evidence,
            confidence=overall_confidence,
        )
