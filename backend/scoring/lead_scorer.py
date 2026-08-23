"""
lead_scorer.py — 4-dimension, 0–100 lead scoring engine.

Score breakdown (25 pts each = 100 total):
  D1  Digital Presence  — online footprint (rating, reviews, website, social, SSL)
  D2  Website Quality   — structural audit passthrough from website_analyzer
  D3  Business Potential— niche value, AI growth signal, review volume
  D4  Opportunity       — marketing gaps + missing conversion elements

Classification:
  HOT  ≥ 70  — priority: send within 24 h
  WARM ≥ 40  — send within 48 h
  COLD  < 40 — archive for now

Public API
──────────
  score_lead(lead, enriched, website_scores) -> dict   [async]
  filter_leads_for_outreach()               -> list    [async]
"""
import logging
import re
from typing import Any, Dict, List, Optional

from .. import database as db

logger = logging.getLogger(__name__)

_HOT_THRESHOLD  = 70
_WARM_THRESHOLD = 40

# Case-insensitive substring match against lead.niche
HIGH_VALUE_NICHES: List[str] = [
    "dental", "real estate", "law firm", "accounting", "medical",
    "cosmetic", "restaurant chain", "gym", "hotel", "school",
]

# Statuses that should never be downgraded back to SCORED
_NO_DOWNGRADE_STATUSES = frozenset({"SENT", "REPLIED", "SKIPPED"})


# ── Type coercions ────────────────────────────────────────────────────────────

def _float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return default


def _int(val: Any, default: int = 0) -> int:
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return default


def _coerce_list(val: Any) -> List[str]:
    """Normalise a DB TEXT[] column or AI string into a clean list."""
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str) and val.strip():
        return [s.strip() for s in re.split(r"[;\n]+", val) if s.strip()]
    return []


# ── Domain helpers ────────────────────────────────────────────────────────────

def _in_high_value_niche(niche: str) -> bool:
    if not niche:
        return False
    n = niche.lower()
    return any(kw in n for kw in HIGH_VALUE_NICHES)


def _build_key_problems(m: Dict[str, Any]) -> List[str]:
    """
    Collect the top-3 most impactful problems from structural + AI signals.

    Priority: structural issues → conversion gaps → AI marketing gaps → SEO gaps.
    Deduplication uses the first 40 characters (lowercased) as the key.
    """
    candidates: List[str] = []
    for field in ("issues", "conversion_gaps", "seo_gaps"):
        candidates.extend(_coerce_list(m.get(field) or []))
    candidates.extend(_coerce_list(m.get("marketing_gaps") or []))

    seen: set = set()
    result: List[str] = []
    for item in candidates:
        fingerprint = item.lower()[:40]
        if fingerprint not in seen:
            seen.add(fingerprint)
            result.append(item)
        if len(result) >= 3:
            break

    return result or ["No specific issues identified"]


def _build_opportunity_summary(
    m: Dict[str, Any],
    final_score: float,
    category: str,
) -> str:
    niche  = (m.get("niche") or "business").lower()
    city   = m.get("city") or ""
    growth = (m.get("growth_potential") or "").lower()
    gaps   = _coerce_list(m.get("marketing_gaps") or [])

    growth_adj = {"high": "high-growth", "medium": "growing", "low": "established"}.get(
        growth, "local"
    )
    location = f" in {city}" if city else ""

    if gaps:
        primary = gaps[0].rstrip(".").lower()[:70]
        n       = len(gaps)
        return (
            f"This {growth_adj} {niche}{location} has {n} exploitable "
            f"marketing gap{'s' if n > 1 else ''} — {primary} — "
            f"making it a strong {category} outreach target."
        )

    biz = (m.get("business_summary") or "").strip()
    if biz:
        sentence = biz.split(".")[0].strip()
        return f"{sentence}; scored {int(final_score)}/100 ({category})."

    return (
        f"This {growth_adj} {niche}{location} scores {int(final_score)}/100 "
        f"— prioritised as {category} for outreach."
    )


def _build_pitch_angle(m: Dict[str, Any]) -> str:
    """
    Best pitch comes from structural analysis (specific to website gaps), then
    AI strategy, then personalisation hook, then a generic fallback.
    """
    pitch_angles = _coerce_list(m.get("pitch_angles") or [])
    if pitch_angles:
        return pitch_angles[0]

    best = (m.get("best_pitch_strategy") or "").strip()
    if best:
        return best

    hook = (m.get("personalization_hook") or "").strip()
    if hook:
        return f"Open with a personalised observation: {hook}"

    return "Focus on improving their digital presence to capture more leads organically."


# ── Scoring dimensions ────────────────────────────────────────────────────────

def _d1_digital_presence(m: Dict[str, Any]) -> float:
    """D1 — Online footprint (0–25)."""
    pts = 0.0

    # +8  Google rating ≥ 4.0
    if _float(m.get("rating") or m.get("google_rating")) >= 4.0:
        pts += 8

    # +5  Reviews ≥ 20 (established audience)
    reviews = max(_int(m.get("reviews_count")), _int(m.get("review_count")))
    if reviews >= 20:
        pts += 5

    # +5  Has a website (not just a Google listing)
    if m.get("website"):
        pts += 5

    # +4  ≥ 2 social platforms  — prefer actual list over boolean flag
    social_list  = _coerce_list(m.get("social_media_links") or [])
    social_count = len(social_list) if social_list else _int(m.get("has_social_links"))
    if social_count >= 2:
        pts += 4

    # +3  SSL / HTTPS — prefer explicit flag, fall back to URL scheme
    has_ssl = m.get("has_ssl")
    if has_ssl is None:
        has_ssl = (m.get("website") or "").lower().startswith("https")
    if has_ssl:
        pts += 3

    return min(25.0, pts)


def _d2_website_quality(m: Dict[str, Any]) -> float:
    """D2 — Structural quality passthrough from score_website() (0–25)."""
    return min(25.0, max(0.0, _float(m.get("website_quality_score"))))


def _d3_business_potential(m: Dict[str, Any]) -> float:
    """D3 — Niche value + AI growth signal + review volume (0–25)."""
    pts = 0.0

    growth = (m.get("growth_potential") or "").lower()
    if growth == "high":
        pts += 10
    elif growth == "medium":
        pts += 7

    if _in_high_value_niche(m.get("niche") or ""):
        pts += 8

    # +7  ≥ 50 reviews → well-established, has marketing budget
    reviews = max(_int(m.get("reviews_count")), _int(m.get("review_count")))
    if reviews >= 50:
        pts += 7

    return min(25.0, pts)


def _d4_opportunity(m: Dict[str, Any]) -> float:
    """D4 — Marketing gaps + missing conversion elements (0–25)."""
    pts = 0.0

    gaps = _coerce_list(m.get("marketing_gaps") or [])
    pts += min(len(gaps), 3) * 7   # +7 per gap, cap at 3 → max +21

    # +4  No contact form → they need lead capture — our core pitch
    has_form = m.get("has_contact_form")
    if has_form is None:
        # Infer from score_website() output stored in enriched_data
        conv_text = " ".join(_coerce_list(m.get("conversion_gaps") or [])).lower()
        has_form  = "contact form" not in conv_text
    if not has_form:
        pts += 4

    return min(25.0, pts)


# ── Public API ────────────────────────────────────────────────────────────────

async def score_lead(
    lead:           Dict[str, Any],
    enriched:       Optional[Dict[str, Any]] = None,
    website_scores: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Score a lead across four 25-point dimensions (total 0–100).

    Parameters
    ----------
    lead           : lead dict from DB (provides base signals)
    enriched       : output of enrich_lead_with_ai() — AI fields + web_score
    website_scores : output of analyze_website() — raw page signals
                     (has_ssl, has_contact_form, social_media_links list, etc.)

    Side effects
    ────────────
    • upsert_score — persists full breakdown to scores table
    • update_lead  — sets score / score_label / score_category
                     and advances status → SCORED (unless already SENT/REPLIED/SKIPPED)

    Returns
    -------
    Scoring breakdown dict — includes legacy score/score_label/score_category
    so callers can spread it without extra mapping.
    """
    lead_id = lead.get("id")

    # Single lookup dict — later dicts override on collision (website_scores
    # has_contact_form + has_ssl + social_media_links override the bool flags
    # that website_analyzer already stored on the lead row)
    m = {**lead, **(enriched or {}), **(website_scores or {})}

    d1 = _d1_digital_presence(m)
    d2 = _d2_website_quality(m)
    d3 = _d3_business_potential(m)
    d4 = _d4_opportunity(m)

    final_score = min(100.0, max(0.0, d1 + d2 + d3 + d4))
    category    = (
        "HOT"  if final_score >= _HOT_THRESHOLD  else
        "WARM" if final_score >= _WARM_THRESHOLD else
        "COLD"
    )

    key_problems        = _build_key_problems(m)
    opportunity_summary = _build_opportunity_summary(m, final_score, category)
    pitch_angle         = _build_pitch_angle(m)

    result = {
        # New structured breakdown (stored in scores table)
        "digital_score":       d1,
        "website_score":       d2,
        "business_score":      d3,
        "opportunity_score":   d4,
        "final_score":         final_score,
        "category":            category,
        "key_problems":        key_problems,
        "opportunity_summary": opportunity_summary,
        "pitch_angle":         pitch_angle,
        # Legacy aliases (used by LeadTable ScoreBadge + _LEAD_WRITABLE)
        "score":               int(final_score),
        "score_label":         category,
        "score_category":      category,
    }

    if lead_id:
        try:
            await db.upsert_score(lead_id, result)
        except Exception as exc:
            logger.error("score_lead: upsert_score failed for lead %d: %s", lead_id, exc)

        current_status = (lead.get("status") or "").upper()
        lead_update: Dict[str, Any] = {
            "score":          int(final_score),
            "score_label":    category,
            "score_category": category,
        }
        if current_status not in _NO_DOWNGRADE_STATUSES:
            lead_update["status"] = "SCORED"

        try:
            await db.update_lead(lead_id, lead_update)
        except Exception as exc:
            logger.error("score_lead: update_lead failed for lead %d: %s", lead_id, exc)

        logger.info(
            "score_lead: lead %d → %s (%.0f/100)  D1=%.0f D2=%.0f D3=%.0f D4=%.0f",
            lead_id, category, final_score, d1, d2, d3, d4,
        )

    return result


_SEVERITY_WEIGHT = {"high": 20, "medium": 12, "low": 5}
_PRIORITY_WEIGHT = {"HIGH": 22, "MEDIUM": 14, "LOW": 6}


async def score_opportunity_fit(lead_id: int) -> Dict[str, Any]:
    """
    Phase 2 — additive "intelligence fit" score (0–100), parallel to
    score_lead()'s HOT/WARM/COLD final_score. Reflects how strong the
    identified pain points / opportunities / matched solution are for this
    lead. Persisted to scores.intelligence_score / intelligence_category via
    the same upsert_score() call score_lead() already uses.

    This function never reads or writes final_score/category/score_label —
    score_lead()'s existing logic and every HOT/WARM/COLD read path
    (dashboard, LeadTable badges, filter_leads_for_outreach) is unaffected
    whether or not this ever runs for a given lead.
    """
    profile = await db.get_company_profile(lead_id)
    if not profile:
        return {"intelligence_score": 0.0, "intelligence_category": "COLD"}

    pain_points   = await db.get_pain_points(profile["id"])
    opportunities = await db.get_business_opportunities(profile["id"])
    solutions     = await db.get_solution_recommendations(profile["id"])

    # D1 — pain severity: worst identified problem, + breadth bonus for
    # having more than one distinct pain point.
    pain_severity_score = 0.0
    if pain_points:
        pain_severity_score = max(
            _SEVERITY_WEIGHT.get((pp.get("severity") or "medium").lower(), 12) for pp in pain_points
        )
        if len(pain_points) >= 2:
            pain_severity_score += 5
    pain_severity_score = min(25.0, pain_severity_score)

    # D2 — opportunity value: best-priority opportunity, scaled by its own confidence.
    opportunity_value_score = 0.0
    if opportunities:
        best = max(
            opportunities,
            key=lambda o: _PRIORITY_WEIGHT.get((o.get("priority") or "MEDIUM").upper(), 14),
        )
        weight = _PRIORITY_WEIGHT.get((best.get("priority") or "MEDIUM").upper(), 14)
        opportunity_value_score = weight * _clamp01(_float(best.get("confidence")))
    opportunity_value_score = min(25.0, opportunity_value_score)

    # D3 — solution fit: strongest matched service's confidence.
    solution_fit_score = 0.0
    if solutions:
        best_conf = max(_float(s.get("confidence")) for s in solutions)
        solution_fit_score = min(25.0, 25.0 * _clamp01(best_conf))

    # D4 — overall pipeline confidence: how much of this is observed fact vs. inference.
    all_confidences = (
        [_float(pp.get("confidence")) for pp in pain_points]
        + [_float(o.get("confidence")) for o in opportunities]
        + [_float(s.get("confidence")) for s in solutions]
    )
    confidence_score = 0.0
    if all_confidences:
        avg_confidence = sum(all_confidences) / len(all_confidences)
        confidence_score = min(25.0, 25.0 * _clamp01(avg_confidence))

    intelligence_score = min(
        100.0,
        max(0.0, pain_severity_score + opportunity_value_score + solution_fit_score + confidence_score),
    )
    intelligence_category = (
        "HOT"  if intelligence_score >= _HOT_THRESHOLD  else
        "WARM" if intelligence_score >= _WARM_THRESHOLD else
        "COLD"
    )

    result = {
        "pain_severity_score":     pain_severity_score,
        "opportunity_value_score": opportunity_value_score,
        "solution_fit_score":      solution_fit_score,
        "confidence_score":        confidence_score,
        "intelligence_score":      intelligence_score,
        "intelligence_category":   intelligence_category,
    }

    try:
        await db.upsert_score(lead_id, {
            "intelligence_score":    intelligence_score,
            "intelligence_category": intelligence_category,
        })
    except Exception as exc:
        logger.error("score_opportunity_fit: upsert_score failed for lead %d: %s", lead_id, exc)

    return result


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


async def filter_leads_for_outreach() -> List[Dict[str, Any]]:
    """
    Return HOT and WARM leads that are ready for outreach (status=SCORED).

    Ordering: HOT before WARM, then by final_score DESC within each tier.
    Includes the full scores breakdown columns via JOIN.
    """
    async with db.get_db() as conn:
        rows = await conn.fetch(
            """
            SELECT l.*,
                   s.final_score,        s.category,
                   s.digital_score,      s.website_score,
                   s.business_score,     s.opportunity_score,
                   s.key_problems,       s.opportunity_summary,
                   s.pitch_angle
            FROM   leads  l
            JOIN   scores s ON s.lead_id = l.id
            WHERE  l.score_label IN ('HOT', 'WARM')
              AND  l.status = 'SCORED'
            ORDER BY
              CASE l.score_label WHEN 'HOT' THEN 1 ELSE 2 END,
              s.final_score DESC
            """
        )
    return [dict(r) for r in rows]
