"""
ai_enricher.py — AI-powered business intelligence from website content.

Public API
──────────
  enrich_lead_with_ai(lead, website_data, company_dna) -> dict

    1. Calls score_website() to get structural scoring + gap lists.
    2. Builds a structured prompt (lead profile + website signals + gaps
       + your company DNA).
    3. Calls Ollama; extracts and validates the JSON response.
       Retries once if the response is malformed.
    4. Persists everything (AI fields + structural score) to the
       enriched_data table in a single upsert.
    5. Updates the lead row: legacy text columns + status → ENRICHED.
    6. Returns the combined enrichment dict (AI + structural).

The function never raises — errors are logged and an empty dict is
returned so the caller can degrade gracefully to plain scoring.
"""
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .website_analyzer import score_website
from ..ai_brain import _call_llm_raw, _ollama_cfg
from .. import database as db

logger = logging.getLogger(__name__)

# Retry the Ollama call this many times before giving up
_MAX_RETRIES = 2

# Exact keys the model must return
_REQUIRED_AI_KEYS = frozenset({
    "business_summary", "target_audience", "service_level",
    "brand_positioning", "marketing_gaps", "growth_potential",
    "best_pitch_strategy", "personalization_hook",
})

_VALID_SERVICE_LEVELS   = {"budget", "mid-range", "premium"}
_VALID_GROWTH_POTENTIAL = {"low", "medium", "high"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_list(val: Any, max_items: int = 5) -> List[str]:
    """Normalise AI output to a clean, bounded list of strings."""
    if isinstance(val, list):
        items = [str(x).strip() for x in val if str(x).strip()]
    elif isinstance(val, str):
        items = [s.strip() for s in re.split(r"[;\n]+|,\s(?=[A-Z])", val) if s.strip()]
    else:
        items = []
    return items[:max_items]


def _parse_ai_response(raw: str) -> Optional[Dict[str, Any]]:
    """
    Extract and parse the JSON object from a raw Ollama response.

    Handles the three most common LLM output mistakes:
      • Markdown fences  ```json ... ```
      • Trailing commas  {"a": 1,}
      • Single-quoted keys  {'key': 'value'}
    Returns None if no valid JSON object can be extracted.
    """
    # 1. Strip markdown fences
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()

    # 2. Find the first complete {...} block
    match = re.search(r"\{[\s\S]+\}", cleaned)
    if not match:
        return None
    json_str = match.group(0)

    # 3. Fix common syntax errors
    json_str = re.sub(r",\s*([}\]])", r"\1", json_str)      # trailing commas
    json_str = re.sub(r"'([^']+)'\s*:", r'"\1":', json_str)  # single-quoted keys

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as exc:
        logger.debug("_parse_ai_response: JSONDecodeError after cleanup: %s", exc)
        return None

    return data if isinstance(data, dict) else None


def _validate_and_clean(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate AI output against the expected schema and coerce types.

    All fields are guaranteed present in the returned dict.
    service_level and growth_potential are clamped to allowed values.
    """
    sl = str(data.get("service_level", "")).lower().strip()
    gp = str(data.get("growth_potential", "")).lower().strip()

    return {
        "business_summary":    str(data.get("business_summary",    "")).strip()[:500],
        "target_audience":     str(data.get("target_audience",     "")).strip()[:300],
        "service_level":       sl if sl in _VALID_SERVICE_LEVELS   else "mid-range",
        "brand_positioning":   str(data.get("brand_positioning",   "")).strip()[:300],
        "marketing_gaps":      _coerce_list(data.get("marketing_gaps", []), max_items=5),
        "growth_potential":    gp if gp in _VALID_GROWTH_POTENTIAL else "medium",
        "best_pitch_strategy": str(data.get("best_pitch_strategy", "")).strip()[:500],
        "personalization_hook": str(data.get("personalization_hook","")).strip()[:300],
    }


# ── Prompt ────────────────────────────────────────────────────────────────────

def _build_prompt(
    lead: Dict[str, Any],
    website_data: Dict[str, Any],
    web_score: Dict[str, Any],
    company_dna: str,
) -> str:
    biz   = lead.get("business_name") or "this business"
    niche = lead.get("niche")         or "their industry"
    city  = lead.get("city")          or ""

    headings  = " | ".join((website_data.get("all_headings") or [])[:8]) or "none"
    ctas      = ", ".join((website_data.get("cta_buttons") or [])[:5])   or "none"
    social    = ", ".join(website_data.get("social_media_links") or [])  or "none"
    body      = (website_data.get("body_text") or "")[:1_500]
    meta      = website_data.get("meta_description") or "missing"
    issues    = "; ".join((web_score.get("issues") or [])[:3])           or "none"
    conv_gaps = "; ".join((web_score.get("conversion_gaps") or [])[:3])  or "none"
    seo_gaps  = "; ".join((web_score.get("seo_gaps") or [])[:3])         or "none"
    score_str = str(int(web_score.get("website_quality_score", 0)))

    return f"""You are a senior marketing analyst specialising in digital presence audits.
Analyse this business and return ONLY a valid JSON object — no markdown, no explanation, no text before or after the JSON.

BUSINESS PROFILE:
  Name:     {biz}
  Industry: {niche}
  Location: {city}

WEBSITE AUDIT RESULTS (quality score: {score_str}/25):
  URL:             {website_data.get("url", "unknown")}
  HTTPS:           {"yes" if website_data.get("has_ssl") else "no"}
  Title:           {website_data.get("page_title") or "not found"}
  Meta desc:       {meta}
  Headings:        {headings}
  CTAs found:      {ctas}
  Social:          {social}
  Has contact form:{"yes" if website_data.get("has_contact_form") else "no"}
  Phone on page:   {"yes" if website_data.get("has_phone_on_page") else "no"}
  Word count:      {website_data.get("word_count", 0)}
  Issues:          {issues}
  Conversion gaps: {conv_gaps}
  SEO gaps:        {seo_gaps}

WEBSITE CONTENT SAMPLE:
{body}

YOUR COMPANY (what you offer):
{company_dna[:400]}

Return ONLY this JSON — every field is required, no field may be null or empty:
{{
  "business_summary": "2 sentences: what {biz} does and who they serve",
  "target_audience": "specific description of their typical customer (1 sentence)",
  "service_level": "budget OR mid-range OR premium",
  "brand_positioning": "how they present themselves online (1 sentence)",
  "marketing_gaps": ["specific gap 1 for this business", "specific gap 2", "specific gap 3"],
  "growth_potential": "low OR medium OR high",
  "best_pitch_strategy": "actionable, specific approach to pitch YOUR services to THIS exact business (2 sentences)",
  "personalization_hook": "one specific detail from their website that proves you actually visited — not generic"
}}"""


# ── Public API ────────────────────────────────────────────────────────────────

async def enrich_lead_with_ai(
    lead:        Dict[str, Any],
    website_data: Dict[str, Any],
    company_dna: str,
) -> Dict[str, Any]:
    """
    Generate AI business intelligence, persist to DB, update lead status.

    Parameters
    ----------
    lead         : lead dict from database (must contain 'id')
    website_data : output of analyze_website()
    company_dna  : contents of company_dna.txt

    Returns
    -------
    Combined dict: AI-enrichment fields + structural scoring fields.
    Returns empty dict on complete failure (errors are logged).

    Side effects
    ────────────
    • upsert_enriched_data — persists all fields to enriched_data table
    • update_lead          — sets legacy text columns + status = ENRICHED
    """
    lead_id = lead.get("id")
    biz     = lead.get("business_name") or "unknown"

    # ── Step 1: Structural scoring (sync, instant) ─────────────────────────────
    web_score = score_website(website_data)

    # ── Step 2: Skip AI if no usable website content ───────────────────────────
    body_text = (website_data.get("body_text") or "").strip()
    if not body_text and website_data.get("error"):
        logger.info(
            "enrich_lead_with_ai: no website content for '%s' (error=%s) — structural score only",
            biz, website_data["error"],
        )
        if lead_id:
            await _persist(lead_id, {}, web_score, website_data)
        return web_score

    # ── Step 3: Ollama AI analysis ─────────────────────────────────────────────
    cfg    = await _ollama_cfg()
    prompt = _build_prompt(lead, website_data, web_score, company_dna)

    ai_enrichment: Optional[Dict[str, Any]] = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            raw    = await _call_llm_raw(prompt, cfg, temperature=0.30, num_predict=700)
            parsed = _parse_ai_response(raw)

            if parsed is None:
                logger.warning(
                    "enrich_lead_with_ai: attempt %d/%d — no JSON block in response for '%s' "
                    "(first 150 chars: %s…)",
                    attempt, _MAX_RETRIES, biz, raw[:150],
                )
                continue

            ai_enrichment = _validate_and_clean(parsed)

            # Validate that key fields are non-empty
            if ai_enrichment["business_summary"] and ai_enrichment["personalization_hook"]:
                break

            logger.warning(
                "enrich_lead_with_ai: attempt %d/%d — empty required fields for '%s'",
                attempt, _MAX_RETRIES, biz,
            )
            ai_enrichment = None

        except Exception as exc:
            logger.warning(
                "enrich_lead_with_ai: attempt %d/%d failed for '%s': %s",
                attempt, _MAX_RETRIES, biz, exc,
            )
            if attempt == _MAX_RETRIES:
                # Still persist the structural score
                if lead_id:
                    await _persist(lead_id, {}, web_score, website_data)
                return web_score

    if ai_enrichment is None:
        logger.error(
            "enrich_lead_with_ai: all %d attempts failed for '%s' — structural score only",
            _MAX_RETRIES, biz,
        )
        if lead_id:
            await _persist(lead_id, {}, web_score, website_data)
        return web_score

    # ── Step 4: Persist everything in one shot ─────────────────────────────────
    if lead_id:
        await _persist(lead_id, ai_enrichment, web_score, website_data)

    logger.info(
        "enrich_lead_with_ai: enriched '%s' → service=%s growth=%s score=%.0f/25",
        biz,
        ai_enrichment["service_level"],
        ai_enrichment["growth_potential"],
        web_score["website_quality_score"],
    )

    return {**ai_enrichment, **web_score}


# ── Persistence helper ────────────────────────────────────────────────────────

async def _persist(
    lead_id:      int,
    ai_data:      Dict[str, Any],
    web_score:    Dict[str, Any],
    website_data: Dict[str, Any],
) -> None:
    """Single upsert to enriched_data + update to leads table."""
    enriched_row: Dict[str, Any] = {
        # Structural website scoring
        "website_quality_score": web_score.get("website_quality_score", 0.0),
        "issues":                web_score.get("issues",          []),
        "conversion_gaps":       web_score.get("conversion_gaps", []),
        "seo_gaps":              web_score.get("seo_gaps",        []),
        "pitch_angles":          web_score.get("pitch_angles",    []),
        # Raw website text (for future re-enrichment without re-fetching)
        "website_text": (website_data.get("body_text") or "")[:2_000],
    }

    # Merge AI fields if present
    if ai_data:
        enriched_row.update({
            "business_summary":    ai_data.get("business_summary",    ""),
            "target_audience":     ai_data.get("target_audience",     ""),
            "service_level":       ai_data.get("service_level",       "mid-range"),
            "brand_positioning":   ai_data.get("brand_positioning",   ""),
            "marketing_gaps":      ai_data.get("marketing_gaps",      []),
            "growth_potential":    ai_data.get("growth_potential",    "medium"),
            "best_pitch_strategy": ai_data.get("best_pitch_strategy", ""),
            "personalization_hook":ai_data.get("personalization_hook",""),
        })

    try:
        await db.upsert_enriched_data(lead_id, enriched_row)
    except Exception as exc:
        logger.error("enrich_lead_with_ai: enriched_data upsert failed for lead %d: %s", lead_id, exc)

    # ── Update leads table: legacy columns + enrichment timestamp + status ─────
    lead_update: Dict[str, Any] = {
        "has_social_links": 1 if website_data.get("has_social_links") else 0,
        "enriched_at":      _now_iso(),
        "status":           "ENRICHED",
    }
    if ai_data:
        lead_update.update({
            "website_summary":      ai_data.get("business_summary",    ""),
            "business_gaps":        "; ".join(ai_data.get("marketing_gaps", [])),
            "pain_points":          ai_data.get("best_pitch_strategy", "")[:300],
            "personalization_hook": ai_data.get("personalization_hook",""),
        })

    try:
        await db.update_lead(lead_id, lead_update)
    except Exception as exc:
        logger.error("enrich_lead_with_ai: lead update failed for lead %d: %s", lead_id, exc)
