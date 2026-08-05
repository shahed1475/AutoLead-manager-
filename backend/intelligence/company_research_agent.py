"""
company_research_agent.py — Company Research Agent.

Reuses analyze_website()/enrich_lead_with_ai() rather than duplicating them —
enriched_data/scores keep populating exactly as they did before this agent
existed. This agent's job is the additional evidence-tracked structured
profile (company_profiles/research_evidence), not a replacement enrichment
pipeline.

Imports the reused functions by name (not via module attribute access) so
tests can monkeypatch them directly on this module.
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional

from ..ai_brain import _call_llm_raw, _load_company_dna, _ollama_cfg
from ..enrichment.ai_enricher import enrich_lead_with_ai
from ..enrichment.website_analyzer import analyze_website
from .base import AgentResult, EvidenceItem
from .techstack import detect_tech_stack

logger = logging.getLogger(__name__)


def _build_research_prompt(lead: Dict[str, Any], website_data: Dict[str, Any]) -> str:
    biz      = lead.get("business_name") or "this business"
    niche    = lead.get("niche") or "unknown industry"
    body     = (website_data.get("body_text") or "")[:1500]
    headings = " | ".join((website_data.get("all_headings") or [])[:8]) or "none"

    return f"""You are a B2B sales research analyst. Analyse this business's website and
return ONLY a valid JSON object — no markdown, no explanation, no text before or after the JSON.

BUSINESS: {biz}
CLAIMED NICHE: {niche}
WEBSITE TITLE: {website_data.get("page_title") or "unknown"}
HEADINGS: {headings}
CONTENT SAMPLE: {body}

Return ONLY this JSON — every field required, use "unknown" if you cannot determine it:
{{
  "industry": "specific industry category",
  "services": ["service 1", "service 2"],
  "products": ["product 1"],
  "company_description": "2 sentence factual summary of what this business does",
  "company_size_estimate": "solo OR small OR medium OR large OR unknown",
  "maturity_estimate": "startup OR growing OR established OR enterprise OR unknown"
}}"""


def _parse_json_object(raw: str) -> Optional[Dict[str, Any]]:
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()
    match = re.search(r"\{[\s\S]+\}", cleaned)
    if not match:
        return None
    json_str = match.group(0)
    json_str = re.sub(r",\s*([}\]])", r"\1", json_str)
    json_str = re.sub(r"'([^']+)'\s*:", r'"\1":', json_str)
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


_AI_FIELDS = ("industry", "services", "products", "company_description",
              "company_size_estimate", "maturity_estimate")


class CompanyResearchAgent:
    name = "company_research"

    async def run(self, lead: Dict[str, Any], campaign: Optional[Dict[str, Any]] = None) -> AgentResult:
        website = lead.get("website")
        if not website:
            return AgentResult(status="ok", data={}, evidence=[], confidence=0.1)

        try:
            website_data = await analyze_website(website)
        except Exception as exc:
            logger.warning("company_research: analyze_website failed for %s: %s", website, exc)
            return AgentResult(status="failed", reason=str(exc), confidence=0.0)

        if website_data.get("error"):
            return AgentResult(status="ok", data={}, evidence=[], confidence=0.2)

        evidence: List[EvidenceItem] = []
        data: Dict[str, Any] = {}

        tech_stack = detect_tech_stack(website_data.get("raw_html") or "")
        if tech_stack:
            data["tech_stack"] = tech_stack
            evidence.append(EvidenceItem("tech_stack", "heuristic", website, ", ".join(tech_stack)))

        social = website_data.get("social_media_links") or []
        if social:
            data["social_profiles"] = social
            evidence.append(EvidenceItem("social_profiles", "website", website, ", ".join(social)))

        confidence = 0.6
        parsed: Optional[Dict[str, Any]] = None
        try:
            cfg = await _ollama_cfg()
            prompt = _build_research_prompt(lead, website_data)
            raw = await _call_llm_raw(prompt, cfg, temperature=0.2, num_predict=500)
            parsed = _parse_json_object(raw)
        except Exception as exc:
            logger.warning("company_research: LLM synthesis failed for lead %s: %s", lead.get("id"), exc)

        if parsed:
            for field_name in _AI_FIELDS:
                if field_name in parsed:
                    data[field_name] = parsed[field_name]
                    evidence.append(EvidenceItem(field_name, "ai_inference", website, None))
            confidence = 1.0

        # Keep the existing enrichment pipeline populated (enriched_data/scores) —
        # this agent adds structured, evidence-tracked fields alongside it, not instead of it.
        try:
            company_dna = _load_company_dna()
            await enrich_lead_with_ai(lead, website_data, company_dna)
        except Exception as exc:
            logger.warning("company_research: enrich_lead_with_ai failed for lead %s: %s", lead.get("id"), exc)

        return AgentResult(status="ok", data=data, evidence=evidence, confidence=confidence)
