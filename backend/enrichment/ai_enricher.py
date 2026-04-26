"""
ai_enricher.py — AI-powered website content analysis.

Given a lead and their website text, calls Ollama to produce structured
business intelligence: a summary, identified gaps, pain points, and a
personalization hook for more effective outreach messages.
"""
import json
import logging
import re
from typing import Any, Dict

from ..ai_brain import _call_ollama_raw, _ollama_cfg

logger = logging.getLogger(__name__)


def _build_enrichment_prompt(
    lead: Dict[str, Any],
    website_text: str,
    company_dna: str,
) -> str:
    biz   = lead.get("business_name") or "this business"
    niche = lead.get("niche")         or "their industry"
    city  = lead.get("city")          or ""

    return f"""Analyze this business and provide concise sales intelligence.

BUSINESS:
Name: {biz}
Industry: {niche}
Location: {city}

WEBSITE CONTENT (first 2000 chars):
{website_text[:2000]}

YOUR COMPANY:
{company_dna[:300]}

Return ONLY valid JSON — no other text:
{{
  "summary": "2-sentence description of what this business does and who they serve",
  "gaps": "2-3 specific weaknesses or opportunities visible from their site that your company could address",
  "pain_points": "the most likely daily challenge this business faces based on their niche and online presence",
  "personalization_hook": "one specific detail from their website for a genuinely personal outreach angle (not generic)"
}}"""


async def enrich_lead(
    lead: Dict[str, Any],
    website_text: str,
    company_dna: str,
) -> Dict[str, Any]:
    """
    Analyze website text with AI and return enrichment fields.

    Returns dict with: website_summary, business_gaps, pain_points,
    personalization_hook — or empty dict on failure.
    """
    if not website_text or len(website_text.strip()) < 80:
        return {}

    cfg = await _ollama_cfg()
    prompt = _build_enrichment_prompt(lead, website_text, company_dna)

    try:
        raw = await _call_ollama_raw(prompt, cfg, temperature=0.35, num_predict=500)

        json_match = re.search(r"\{[\s\S]+\}", raw)
        if not json_match:
            logger.warning("ai_enricher: no JSON block in response")
            return {}

        data = json.loads(json_match.group(0))

        return {
            "website_summary":      str(data.get("summary",              ""))[:500],
            "business_gaps":        str(data.get("gaps",                 ""))[:500],
            "pain_points":          str(data.get("pain_points",          ""))[:300],
            "personalization_hook": str(data.get("personalization_hook", ""))[:300],
        }

    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("ai_enricher: parse error: %s", exc)
        return {}
    except Exception as exc:
        logger.error("ai_enricher: unexpected error: %s", exc, exc_info=True)
        return {}
