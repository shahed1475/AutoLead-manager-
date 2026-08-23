"""
followup_agent.py — Follow-up Intelligence Agent (Phase 4).

Generates the body for scheduled follow-up steps (2 = Day-3, 3 = Day-7) from
the same evidence chain marketing_agent.py uses (pain point -> impact ->
solution), never a second identical copy of the initial message and never a
generic ai_brain.generate_all_messages() rewrite (that's the older,
evidence-blind path — see marketing_agent.py's own docstring for why this
codebase treats it as legacy fallback only, not something to build on).

Two guarantees are structural, not LLM-hoped, mirroring every other agent in
this codebase:

1. Step 2 and step 3 always use a different opening angle (a fixed
   step -> angle mapping), so two follow-ups for the same lead never open
   the same way by construction, regardless of what an LLM does with word
   choice.
2. The assembled body is checked against every previous message body
   (initial send + any earlier follow-up) by a normalized-prefix match; if
   it collides, the heuristic (always-available, non-LLM) fragments are
   used instead, which are lexically distinct per step by construction —
   never re-sends the literal previous text.

Reuses marketing_agent.py's fragment builder, renderer, and safety filters
directly rather than re-implementing them, per this codebase's "don't
rebuild, extend" convention for the sales-intelligence agents.
"""
import logging
from typing import Any, Dict, List, Optional

from .base import AgentResult, EvidenceItem
from .marketing_agent import (
    _assemble_email,
    _assemble_whatsapp,
    _build_business_impact,
    _business_benefit_text,
    _generate_fragments_llm,
    _heuristic_fragments,
    _pick_opportunity_for,
    _pick_solution_for,
    _solution_description,
    _strip_period,
)
from .opportunity_agent import _strongest
from .service_knowledge_base import get_service

logger = logging.getLogger(__name__)

# Step -> follow-up angle instruction for the LLM path. Deliberately
# different per step so two follow-ups for the same lead never open the
# same way (structural guarantee #1 above).
_STEP_ANGLE_INSTRUCTION: Dict[int, str] = {
    2: (
        "Open by referencing that you're following up on your earlier note, "
        "then add ONE new, specific observation about the pain point that "
        "was NOT in the first message — a different detail or angle, not a "
        "restatement."
    ),
    3: (
        "Open with a brief, low-pressure final check-in. Reference the "
        "customer-facing impact (not the operational one) of the pain "
        "point, and keep it short — this is the last note in the sequence."
    ),
}

_MAYBE_LATER_ANGLE_INSTRUCTION = (
    "The lead previously said they might be interested later. Open by "
    "acknowledging that gently (no pressure), then add ONE new, specific "
    "observation about the pain point they haven't heard from you before."
)

# Heuristic (non-LLM) opening templates — lexically distinct per step by
# construction, and distinct from marketing_agent's initial-outreach opening
# ("I noticed something worth mentioning about ...").
_STEP_HEURISTIC_OPENING: Dict[int, str] = {
    2: "Following up on my note about {biz} — one more thing I noticed: {pain_point}.",
    3: "Last note from me — {pain_point} still stood out when I checked back on {biz}.",
}
_MAYBE_LATER_HEURISTIC_OPENING = "No rush at all — one more thought on {biz}: {pain_point}."


def _normalize_prefix(text: str, length: int = 60) -> str:
    return "".join((text or "").lower().split())[:length]


def _collides_with_previous(candidate_body: str, previous_bodies: List[str]) -> bool:
    candidate_key = _normalize_prefix(candidate_body)
    if not candidate_key:
        return False
    return any(candidate_key == _normalize_prefix(prev) for prev in previous_bodies if prev)


class FollowUpAgent:
    name = "followup"

    async def run(
        self,
        lead: Dict[str, Any],
        pain_points: List[Dict[str, Any]],
        opportunities: List[Dict[str, Any]],
        solutions: List[Dict[str, Any]],
        step: int,
        previous_bodies: Optional[List[str]] = None,
        latest_reply_intent: Optional[str] = None,
    ) -> AgentResult:
        if not pain_points:
            return AgentResult(status="ok", data={"generated": False}, evidence=[], confidence=0.0)

        previous_bodies = previous_bodies or []
        strongest_pp = _strongest(pain_points)
        opportunity = _pick_opportunity_for(strongest_pp, opportunities or [])
        solution_rec = _pick_solution_for(opportunity, solutions or [])
        service = get_service(solution_rec["service_name"]) if solution_rec else None

        business_name = lead.get("business_name")
        pain_point_text = strongest_pp.get("title") or ""
        evidence_snippet = strongest_pp.get("evidence_snippet") or ""
        business_impact = _build_business_impact(strongest_pp)
        solution_desc = _solution_description(service)
        benefit_text = _business_benefit_text(service)
        service_name = service.name if service else None

        confidence = max(0.0, min(1.0, float(strongest_pp.get("confidence") or 0.0)))

        is_maybe_later = latest_reply_intent == "MAYBE_LATER"
        angle_instruction = _MAYBE_LATER_ANGLE_INSTRUCTION if is_maybe_later else _STEP_ANGLE_INSTRUCTION.get(step, _STEP_ANGLE_INSTRUCTION[2])

        fragments = await _generate_fragments_llm(
            pain_point_text, evidence_snippet, business_impact, solution_desc, benefit_text,
            business_name, angle_instruction, service_name,
        )
        source = "ai_inference"
        email_body: Optional[str] = None

        if fragments is not None:
            candidate = _assemble_email(fragments["opening"], fragments["solution_benefit"], fragments["cta"])
            if _collides_with_previous(candidate, previous_bodies):
                fragments = None
            else:
                email_body = candidate

        if fragments is None:
            fragments = _heuristic_fragments(pain_point_text, business_impact, solution_desc, benefit_text, business_name)
            biz = business_name or "your business"
            if is_maybe_later:
                opening = _MAYBE_LATER_HEURISTIC_OPENING.format(biz=biz, pain_point=_strip_period(pain_point_text))
            else:
                opening_template = _STEP_HEURISTIC_OPENING.get(step, _STEP_HEURISTIC_OPENING[2])
                opening = opening_template.format(biz=biz, pain_point=_strip_period(pain_point_text))
            fragments = {**fragments, "opening": opening}
            email_body = _assemble_email(fragments["opening"], fragments["solution_benefit"], fragments["cta"])
            source = "heuristic"

        whatsapp_body = _assemble_whatsapp(fragments["opening"], fragments["solution_benefit"], fragments["cta"], email_body)
        subject = f"Following up — {business_name}" if business_name else "Following up"

        evidence = [EvidenceItem(
            field_name=f"followup:step{step}",
            source_type=source,
            source_url=strongest_pp.get("source_url"),
            snippet=fragments["opening"][:200],
        )]

        return AgentResult(
            status="ok",
            data={
                "generated": True, "step": step, "subject": subject,
                "email_body": email_body, "whatsapp_body": whatsapp_body,
                "service_name": service_name, "pain_point": pain_point_text,
            },
            evidence=evidence, confidence=confidence,
        )
