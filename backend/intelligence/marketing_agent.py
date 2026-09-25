"""
marketing_agent.py — AI Personalized Marketing Agent.

Writes outreach from the real evidence chain Phase 1/2 already built
(pain point -> impact -> opportunity -> solution), never from a generic
niche guess (that's the older ai_brain.generate_messages_v2 +
outreach_domain.py path, which this does not touch or replace).

Two guarantees are structural, not LLM-hoped:

1. Pain point before solution — the agent asks for three ROLE-SCOPED
   fragments (opening / solution_benefit / cta) and code concatenates them
   in a fixed order. The final message is pain-point-first by
   construction, regardless of what the model does with word choice.
2. No irrelevant service mentioned — the model is only ever shown the ONE
   matched solution's plain-language description (a use-case sentence),
   never the other services in the knowledge base or their names, so it
   cannot name one it was never told about. A post-hoc scan for every
   other service name is still run as defense in depth.

Heuristic templates (built directly from stored facts, no LLM) are always
computed first and used whenever the LLM is unavailable, empty, or its
output fails the content-quality / financial-claim / other-service gate —
the agent never blocks on the LLM and never ships an unsupported claim.

Imports `_call_llm_raw`/`_ollama_cfg`/`_validate_message_content` from
ai_brain and `_strongest` from opportunity_agent by name (not module
attribute access) so tests can monkeypatch them directly, same convention
as pain_point_agent.py / opportunity_agent.py.
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional

from ..ai_brain import _call_llm_raw, _ollama_cfg, _validate_message_content
from .base import AgentResult, EvidenceItem
from .opportunity_agent import _strongest
from .service_knowledge_base import SERVICE_KNOWLEDGE_BASE, get_service

logger = logging.getLogger(__name__)

_WHATSAPP_MAX_CHARS = 320
_WHATSAPP_RATIO_OF_EMAIL = 0.6  # hard guarantee: whatsapp is always shorter than email

_VARIANT_LABELS = ["PRIMARY", "ALTERNATIVE_1", "ALTERNATIVE_2"]
_ANGLES = [
    {"key": "observation", "instruction": "Open with a direct, factual observation about the pain point (\"I noticed...\")."},
    {"key": "question",    "instruction": "Open with a genuine, specific question about how they currently handle the situation the pain point describes."},
    {"key": "context",     "instruction": "Open by briefly noting this is a common gap in their industry, then point to their specific situation."},
]

_FORBIDDEN_CLAIM_RE = re.compile(
    r"\$\s?\d|"
    r"\d+\s?%|"
    r"\bguarantee(d|s)?\b|\brisk-free\b|\blimited time\b|\bact now\b|\bROI\b|"
    r"revenue increase|double your|triple your|\bhurry\b|\bdon't miss\b",
    re.IGNORECASE,
)


# ── Fact selection ───────────────────────────────────────────────────────────

def _pick_opportunity_for(pain_point: Dict[str, Any], opportunities: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for opp in opportunities:
        if opp.get("pain_point_id") == pain_point.get("id"):
            return opp
    return opportunities[0] if opportunities else None


def _pick_solution_for(opportunity: Optional[Dict[str, Any]], solutions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if opportunity:
        for sol in solutions:
            if sol.get("business_opportunity_id") == opportunity.get("id"):
                return sol
    return solutions[0] if solutions else None


def _build_business_impact(pain_point: Dict[str, Any]) -> str:
    op = (pain_point.get("operational_impact") or "").strip()
    cust = (pain_point.get("customer_impact") or "").strip()
    parts = [p for p in (op, cust) if p]
    if parts:
        return " ".join(parts)
    return (pain_point.get("description") or "").strip()


def _solution_description(service) -> str:
    """Plain-language description of what would be built — never the branded
    service catalog name, so the message never reads like a service pitch."""
    if service is None or not service.use_cases:
        return "a simple way to reduce the manual work this creates"
    use_case = service.use_cases[0]
    return use_case[0].lower() + use_case[1:] if use_case else "a solution tailored to this"


def _business_benefit_text(service) -> str:
    if service is None or not service.benefits:
        return "making this easier to manage day to day"
    benefit = service.benefits[0]
    return benefit[0].lower() + benefit[1:] if benefit else "making this easier to manage day to day"


def _service_names_excluding(service_name: Optional[str]) -> List[str]:
    return [s.name for s in SERVICE_KNOWLEDGE_BASE if s.name != service_name]


def _mentions_other_service(text: str, service_name: Optional[str]) -> bool:
    lowered = text.lower()
    return any(other.lower() in lowered for other in _service_names_excluding(service_name))


def _has_unsupported_claim(text: str) -> bool:
    return bool(_FORBIDDEN_CLAIM_RE.search(text))


def _strip_period(s: str) -> str:
    return s.rstrip().rstrip(".")


# ── Deterministic (always-available) fragment builder ──────────────────────

def _heuristic_fragments(
    pain_point: str, business_impact: str, solution_desc: str, benefit_text: str, business_name: Optional[str],
) -> Dict[str, str]:
    biz = business_name or "your business"

    opening = f"I noticed something worth mentioning about {biz}: {_strip_period(pain_point)}."
    if business_impact:
        opening += f" {business_impact}"

    solution_benefit = (
        f"We could help by building {_strip_period(solution_desc)}, "
        f"which could go toward {_strip_period(benefit_text)}."
    )

    cta = f"Would you be open to a quick 10-minute demo to see how this could work for {biz}?"

    return {"opening": opening, "solution_benefit": solution_benefit, "cta": cta}


# ── LLM-assisted fragment builder (optional, strictly grounded) ────────────

def _build_fragment_prompt(
    pain_point: str, evidence_snippet: str, business_impact: str,
    solution_desc: str, benefit_text: str, business_name: Optional[str], angle_instruction: str,
) -> str:
    biz = business_name or "this business"
    return f"""You are writing a short, human, non-pushy outreach message for {biz}.

FACTS YOU MUST USE — do not add any fact, number, or claim not listed here:
- Pain point observed: {pain_point}
- Evidence: {evidence_snippet or "N/A"}
- Business impact: {business_impact or "N/A"}
- Solution we could offer: {solution_desc}
- Business benefit: {benefit_text}

STYLE RULES:
- {angle_instruction}
- Never introduce yourself as a company or list services/products.
- Never mention a dollar amount, percentage, guaranteed result, or urgency claim.
- Never use emojis, hashtags, or HTML.
- Sound like a researcher who understands their business, not a salesperson.

Return ONLY this JSON, no markdown, no text before or after:
{{
  "opening": "1-2 sentences referencing the pain point and its impact",
  "solution_benefit": "1-2 sentences describing the solution and benefit — do not name any brand/service, describe what it does",
  "cta": "1 short, low-pressure question inviting a demo"
}}"""


def _parse_json_object(raw: str) -> Optional[Dict[str, Any]]:
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()
    match = re.search(r"\{[\s\S]+\}", cleaned)
    if not match:
        return None
    json_str = match.group(0)
    json_str = re.sub(r",\s*([}\]])", r"\1", json_str)
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def _generate_fragments_llm(
    pain_point: str, evidence_snippet: str, business_impact: str,
    solution_desc: str, benefit_text: str, business_name: Optional[str],
    angle_instruction: str, service_name: Optional[str],
) -> Optional[Dict[str, str]]:
    try:
        cfg = await _ollama_cfg()
        prompt = _build_fragment_prompt(
            pain_point, evidence_snippet, business_impact, solution_desc, benefit_text, business_name, angle_instruction,
        )
        raw = await _call_llm_raw(prompt, cfg, temperature=0.5, num_predict=300)
    except Exception as exc:
        logger.debug("marketing_agent: LLM fragment generation unavailable: %s", exc)
        return None

    parsed = _parse_json_object(raw)
    if not parsed:
        return None

    opening = str(parsed.get("opening") or "").strip()
    solution_benefit = str(parsed.get("solution_benefit") or "").strip()
    cta = str(parsed.get("cta") or "").strip()
    if not opening or not solution_benefit or not cta:
        return None

    combined = f"{opening} {solution_benefit} {cta}"
    if _validate_message_content(combined):
        return None
    if _has_unsupported_claim(combined):
        return None
    if _mentions_other_service(combined, service_name):
        return None

    return {"opening": opening, "solution_benefit": solution_benefit, "cta": cta}


# ── Channel rendering ────────────────────────────────────────────────────────

def _assemble_email(opening: str, solution_benefit: str, cta: str) -> str:
    return f"{opening}\n\n{solution_benefit}\n\n{cta}"


def _assemble_whatsapp(opening: str, solution_benefit: str, cta: str, email_text: str) -> str:
    """Compact rendering, hard-capped to always be shorter than the email
    rendering for the same fragments (ratio-based cap, not hope)."""
    cap = min(_WHATSAPP_MAX_CHARS, max(40, int(len(email_text) * _WHATSAPP_RATIO_OF_EMAIL)))
    text = f"{opening} {solution_benefit} {cta}"
    if len(text) > cap:
        text = text[: max(0, cap - 1)].rstrip() + "…"
    return text


def _build_subject(business_name: Optional[str]) -> str:
    biz = business_name or "your business"
    return f"Quick thought on {biz}"


def _build_strategy(pain_point: str, service_name: Optional[str], classification: Optional[str]) -> str:
    basis = "an observed" if classification == "observed" else "an inferred"
    service_note = f"; position the solution as {service_name.lower()}-style automation" if service_name else ""
    return f"Lead with {basis} pain point ('{pain_point}'){service_note}; end with a low-friction demo CTA."


# ── Agent ─────────────────────────────────────────────────────────────────────

class MarketingAgent:
    name = "marketing"

    async def run(
        self,
        lead: Dict[str, Any],
        company_profile: Dict[str, Any],
        pain_points: List[Dict[str, Any]],
        opportunities: List[Dict[str, Any]],
        solutions: List[Dict[str, Any]],
        evidence: List[Dict[str, Any]],
        campaign: Optional[Dict[str, Any]] = None,
    ) -> AgentResult:
        if not pain_points:
            return AgentResult(status="ok", data={"messages": []}, evidence=[], confidence=0.0)

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

        base_confidence = float(strongest_pp.get("confidence") or 0.0)
        if solution_rec:
            base_confidence = min(base_confidence, float(solution_rec.get("confidence") or base_confidence))
        base_confidence = max(0.0, min(1.0, base_confidence))

        strategy = _build_strategy(pain_point_text, service_name, strongest_pp.get("classification"))

        messages: List[Dict[str, Any]] = []
        result_evidence: List[EvidenceItem] = []

        for label, angle in zip(_VARIANT_LABELS, _ANGLES):
            fragments = await _generate_fragments_llm(
                pain_point_text, evidence_snippet, business_impact, solution_desc, benefit_text,
                business_name, angle["instruction"], service_name,
            )
            source = "ai_inference"
            if fragments is None:
                fragments = _heuristic_fragments(pain_point_text, business_impact, solution_desc, benefit_text, business_name)
                source = "heuristic"

            email_body = _assemble_email(fragments["opening"], fragments["solution_benefit"], fragments["cta"])
            whatsapp_body = _assemble_whatsapp(fragments["opening"], fragments["solution_benefit"], fragments["cta"], email_body)
            subject = _build_subject(business_name)

            common = dict(
                strategy=strategy, pain_point=pain_point_text, evidence=evidence_snippet,
                business_impact=business_impact, solution=solution_desc,
                business_benefit=benefit_text, cta=fragments["cta"],
                confidence=base_confidence, variant=label, service_name=service_name,
                # Proof shown to the reviewer next to the draft.
                evidence_url=strongest_pp.get("source_url") or None,
                evidence_checked_at=strongest_pp.get("created_at") or None,
                evidence_kind="observed" if strongest_pp.get("classification") == "observed" else "inferred",
            )
            messages.append({**common, "channel": "EMAIL", "subject": subject, "message": email_body})
            messages.append({**common, "channel": "WHATSAPP", "subject": None, "message": whatsapp_body})

            result_evidence.append(EvidenceItem(
                field_name=f"marketing:{label}",
                source_type=source,
                source_url=strongest_pp.get("source_url"),
                snippet=fragments["opening"][:200],
            ))

        return AgentResult(status="ok", data={"messages": messages}, evidence=result_evidence, confidence=base_confidence)
