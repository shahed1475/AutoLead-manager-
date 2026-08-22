"""
pain_point_agent.py — Pain Point Agent.

Turns stored research (company profile + evidence) plus a fresh website read
into evidence-grounded pain points and "business ease" opportunities.

Deliberately NOT a ResearchAgent (base.py's 2-arg Protocol): it needs the
already-persisted company_profile and evidence as additional inputs, and it
is invoked by name from orchestrator.run_pain_point_analysis rather than run
polymorphically alongside QualificationAgent/CompanyResearchAgent.

Critical rule (per product spec): never invent a business problem. Every
pain point returned here is either:
  - "observed"  — derived from a literal, checkable signal in website_data
                  (e.g. has_contact_form is False), confidence fixed by the
                  checklist below, not guessed.
  - "inferred"  — proposed by the LLM, but ONLY kept if the model also
                  supplied a supporting evidence snippet quoting the given
                  content. An inferred item with no evidence is dropped
                  entirely rather than shown as an unfounded claim.
No step here fabricates financial figures — impact text is qualitative only.

Imports `analyze_website`/`_call_llm_raw`/`_ollama_cfg` by name (like
company_research_agent.py) so tests can monkeypatch them directly on this
module.
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional

from ..ai_brain import _call_llm_raw, _ollama_cfg
from ..enrichment.website_analyzer import analyze_website
from .base import AgentResult, EvidenceItem

logger = logging.getLogger(__name__)

_VALID_SEVERITY = frozenset({"low", "medium", "high"})
_DEFAULT_SEVERITY = "medium"

# Niches where an online booking/appointment flow is the expected norm —
# used only to decide whether "no visible booking system" is worth flagging.
_BOOKING_NICHE_KEYWORDS = (
    "dental", "clinic", "medical", "salon", "spa", "barber",
    "restaurant", "cafe", "gym", "fitness", "hotel",
)
_BOOKING_CTA_KEYWORDS = ("book", "appointment", "reserve", "schedule", "order online")

# Industry → the one area PopupGenix could most plausibly make easier.
# Deliberately small and static — this is business *intelligence*, not a
# pitch generator; matched against company_profile.industry then lead.niche.
_EASE_AREA_BY_NICHE: List[tuple] = [
    (("dental", "clinic", "medical", "salon", "spa", "barber", "gym", "fitness"), "Appointment Scheduling"),
    (("restaurant", "cafe", "bar", "catering"), "Ordering & Customer Communication"),
    (("real estate", "realtor", "realty", "property"), "Lead Qualification"),
    (("ecommerce", "e-commerce", "retail", "shop", "store"), "Customer Support"),
    (("agency", "consulting", "marketing"), "Client Management"),
]
_DEFAULT_EASE_AREA = "Customer Communication"


def _ease_area_for(industry: Optional[str], niche: Optional[str]) -> str:
    haystack = f"{industry or ''} {niche or ''}".lower()
    for keywords, area in _EASE_AREA_BY_NICHE:
        if any(kw in haystack for kw in keywords):
            return area
    return _DEFAULT_EASE_AREA


def _clamp_severity(value: Any) -> str:
    if isinstance(value, str) and value.lower() in _VALID_SEVERITY:
        return value.lower()
    return _DEFAULT_SEVERITY


def _clamp_confidence(value: Any, default: float = 0.5) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, f))


# ── Heuristic (observed) checks ────────────────────────────────────────────────
# Each returns a pain-point dict + EvidenceItem, or None if the signal isn't present.

def _check_no_contact_method(website: str, website_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if website_data.get("has_contact_form") or website_data.get("has_phone_on_page") or website_data.get("has_email_on_page"):
        return None
    return {
        "title": "No easy way for customers to reach the business online",
        "description": "The website has no contact form, and no phone number or email address visible on the page.",
        "evidence_snippet": "has_contact_form=False, has_phone_on_page=False, has_email_on_page=False",
        "source_url": website,
        "confidence": 0.9,
        "severity": "high",
        "classification": "observed",
        "operational_impact": "Interested visitors have no direct channel to reach staff from the website.",
        "customer_impact": "Customers may need to search elsewhere (directories, social media) just to make contact.",
    }


def _check_no_booking_system(
    website: str, website_data: Dict[str, Any], industry: Optional[str], niche: Optional[str],
) -> Optional[Dict[str, Any]]:
    haystack = f"{industry or ''} {niche or ''}".lower()
    if not any(kw in haystack for kw in _BOOKING_NICHE_KEYWORDS):
        return None
    cta_text = " ".join(website_data.get("cta_buttons") or []).lower()
    body_text = (website_data.get("body_text") or "").lower()
    if any(kw in cta_text or kw in body_text for kw in _BOOKING_CTA_KEYWORDS):
        return None
    return {
        "title": "No visible online appointment/booking system",
        "description": "No booking, scheduling, or ordering call-to-action was found on the website.",
        "evidence_snippet": f"cta_buttons={website_data.get('cta_buttons') or []}",
        "source_url": website,
        "confidence": 0.85,
        "severity": "high",
        "classification": "observed",
        "operational_impact": "Staff may need to handle routine scheduling/ordering requests manually (phone or walk-in).",
        "customer_impact": "Additional friction - customers must call or visit in person to book or order.",
    }


def _check_no_social_presence(website: str, website_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if website_data.get("social_media_links"):
        return None
    return {
        "title": "No active social media presence found on website",
        "description": "The website does not link to any social media profiles.",
        "evidence_snippet": "social_media_links=[]",
        "source_url": website,
        "confidence": 0.6,
        "severity": "low",
        "classification": "observed",
        "operational_impact": "No visible channel for ongoing, low-cost customer engagement.",
        "customer_impact": "Customers researching the business may find no social proof or recent activity.",
    }


def _check_no_ssl(website: str, website_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if website_data.get("has_ssl") is not False:
        return None
    return {
        "title": "Website is not served over HTTPS",
        "description": "The site does not use SSL/TLS, which browsers flag as \"not secure\".",
        "evidence_snippet": "has_ssl=False",
        "source_url": website,
        "confidence": 0.95,
        "severity": "medium",
        "classification": "observed",
        "operational_impact": "Browsers may show a security warning to visitors.",
        "customer_impact": "Customers may hesitate to enter contact or payment details on the site.",
    }


_HEURISTIC_CHECKS = (
    _check_no_contact_method,
    _check_no_social_presence,
    _check_no_ssl,
)


# ── LLM-inferred pain points ────────────────────────────────────────────────────

def _build_pain_point_prompt(
    lead: Dict[str, Any], company_profile: Dict[str, Any], website_data: Dict[str, Any],
    already_found_titles: List[str],
) -> str:
    biz      = lead.get("business_name") or "this business"
    industry = company_profile.get("industry") or lead.get("niche") or "unknown industry"
    body     = (website_data.get("body_text") or "")[:1500]
    already  = "; ".join(already_found_titles) or "none"

    return f"""You are a B2B business analyst. Based ONLY on the content below, identify
additional potential pain points for this business — problems that make running the
business harder than it needs to be. Return ONLY a valid JSON object, no markdown.

BUSINESS: {biz}
INDUSTRY: {industry}
WEBSITE CONTENT SAMPLE: {body}
ALREADY IDENTIFIED (do not repeat these): {already}

Rules:
- Every pain point MUST include a short "evidence" quote taken from the content above.
  If you cannot quote supporting evidence from the content, do not include that pain point.
- Never state or imply a specific financial loss or dollar amount.
- "severity" must be exactly one of: low, medium, high.
- Return at most 3 pain points.

Return ONLY this JSON shape:
{{
  "pain_points": [
    {{
      "title": "short pain point title",
      "description": "1-2 sentence factual description",
      "evidence": "short quote from the content supporting this",
      "severity": "low OR medium OR high",
      "operational_impact": "1 sentence, qualitative only",
      "customer_impact": "1 sentence, qualitative only"
    }}
  ]
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


async def _infer_llm_pain_points(
    lead: Dict[str, Any], company_profile: Dict[str, Any], website_data: Dict[str, Any],
    website: str, already_found_titles: List[str],
) -> List[Dict[str, Any]]:
    try:
        cfg = await _ollama_cfg()
        prompt = _build_pain_point_prompt(lead, company_profile, website_data, already_found_titles)
        raw = await _call_llm_raw(prompt, cfg, temperature=0.3, num_predict=500)
    except Exception as exc:
        logger.warning("pain_point_agent: LLM call failed for lead %s: %s", lead.get("id"), exc)
        return []

    parsed = _parse_json_object(raw)
    if not parsed or not isinstance(parsed.get("pain_points"), list):
        return []

    results: List[Dict[str, Any]] = []
    for item in parsed["pain_points"]:
        if not isinstance(item, dict):
            continue
        evidence = item.get("evidence")
        title = item.get("title")
        # CRITICAL RULE: never present an inference with no supporting evidence.
        if not title or not evidence or not str(evidence).strip():
            continue
        results.append({
            "title": str(title).strip(),
            "description": str(item.get("description") or "").strip() or None,
            "evidence_snippet": str(evidence).strip(),
            "source_url": website,
            "confidence": 0.65,
            "severity": _clamp_severity(item.get("severity")),
            "classification": "inferred",
            "operational_impact": str(item.get("operational_impact") or "").strip() or None,
            "customer_impact": str(item.get("customer_impact") or "").strip() or None,
        })
    return results


# ── Business opportunities derived from pain points ────────────────────────────

def _derive_opportunities(
    pain_points: List[Dict[str, Any]], industry: Optional[str], niche: Optional[str],
) -> List[Dict[str, Any]]:
    if not pain_points:
        return []
    area = _ease_area_for(industry, niche)
    opportunities: List[Dict[str, Any]] = []
    for idx, pp in enumerate(pain_points):
        opportunities.append({
            "pain_point_id": None,  # filled in by the caller once pain points are persisted and have real ids
            "_pain_point_index": idx,
            "area": area,
            "title": f"Make {area.lower()} easier",
            "description": (
                f"Based on \"{pp['title']}\", there may be an opportunity to reduce friction around {area.lower()} "
                f"for this business."
            ),
            "confidence": pp["confidence"],  # never higher-confidence than its source pain point
            "classification": pp["classification"],
        })
    return opportunities


class PainPointAgent:
    name = "pain_point"

    async def run(
        self,
        lead: Dict[str, Any],
        company_profile: Dict[str, Any],
        evidence: List[Dict[str, Any]],
        campaign: Optional[Dict[str, Any]] = None,
    ) -> AgentResult:
        website = lead.get("website")
        if not website:
            return AgentResult(status="ok", data={"pain_points": [], "business_opportunities": []}, evidence=[], confidence=0.0)

        try:
            website_data = await analyze_website(website)
        except Exception as exc:
            logger.warning("pain_point_agent: analyze_website failed for %s: %s", website, exc)
            return AgentResult(status="failed", reason=str(exc), confidence=0.0)

        if website_data.get("error"):
            return AgentResult(status="ok", data={"pain_points": [], "business_opportunities": []}, evidence=[], confidence=0.1)

        industry = company_profile.get("industry")
        niche = lead.get("niche")

        pain_points: List[Dict[str, Any]] = []
        result_evidence: List[EvidenceItem] = []

        for check in _HEURISTIC_CHECKS:
            found = check(website, website_data)
            if found:
                pain_points.append(found)

        booking_pp = _check_no_booking_system(website, website_data, industry, niche)
        if booking_pp:
            pain_points.append(booking_pp)

        already_titles = [pp["title"] for pp in pain_points]
        inferred = await _infer_llm_pain_points(lead, company_profile, website_data, website, already_titles)
        pain_points.extend(inferred)

        for pp in pain_points:
            result_evidence.append(EvidenceItem(
                field_name="pain_point:" + pp["title"][:60],
                source_type="heuristic" if pp["classification"] == "observed" else "ai_inference",
                source_url=pp.get("source_url"),
                snippet=pp.get("evidence_snippet"),
            ))

        opportunities = _derive_opportunities(pain_points, industry, niche)

        confidence = max((pp["confidence"] for pp in pain_points), default=0.0)

        return AgentResult(
            status="ok",
            data={"pain_points": pain_points, "business_opportunities": opportunities},
            evidence=result_evidence,
            confidence=confidence,
        )
