"""
lead_scorer.py — Deterministic HOT / WARM / COLD lead scoring.

Score 0–100 based on data completeness and quality signals.
No AI or external calls — runs instantly on any lead dict.

Thresholds:
  HOT  ≥ 70  (high quality, reachable, enriched)
  WARM ≥ 40  (moderate data, worth sending)
  COLD  < 40 (incomplete or low-signal)
"""
from typing import Any, Dict, Tuple

_HOT_THRESHOLD  = 70
_WARM_THRESHOLD = 40

_WEIGHTS: Dict[str, int] = {
    "has_email":        20,   # primary outreach channel
    "has_phone":        15,   # WhatsApp reachability
    "has_website":      15,   # business legitimacy signal
    "verified_email":   10,   # passed format + domain validation
    "has_good_rating":  10,   # rating >= 4.0 → established business
    "has_reviews":      10,   # review_count >= 20 → active business
    "enriched":         10,   # AI website analysis completed
    "has_address":       5,   # physical location known
    "has_social_links":  5,   # visible online presence (from enrichment)
}


def calculate_score(lead: Dict[str, Any]) -> Tuple[int, str]:
    """
    Score a lead and classify it.

    Parameters
    ----------
    lead : dict — any lead dict (from DB or raw scrape)

    Returns
    -------
    (score: int 0–100, label: "HOT" | "WARM" | "COLD")
    """
    score = 0

    if lead.get("email"):
        score += _WEIGHTS["has_email"]

    if lead.get("phone"):
        score += _WEIGHTS["has_phone"]

    if lead.get("website"):
        score += _WEIGHTS["has_website"]

    if lead.get("verified_email"):
        score += _WEIGHTS["verified_email"]

    try:
        if float(lead.get("rating") or 0) >= 4.0:
            score += _WEIGHTS["has_good_rating"]
    except (TypeError, ValueError):
        pass

    try:
        if int(lead.get("review_count") or 0) >= 20:
            score += _WEIGHTS["has_reviews"]
    except (TypeError, ValueError):
        pass

    if lead.get("website_summary"):
        score += _WEIGHTS["enriched"]

    if lead.get("address"):
        score += _WEIGHTS["has_address"]

    # Social signal comes from website analysis (stored as text flag)
    if lead.get("has_social_links"):
        score += _WEIGHTS["has_social_links"]

    score = min(100, max(0, score))
    label = "HOT" if score >= _HOT_THRESHOLD else ("WARM" if score >= _WARM_THRESHOLD else "COLD")

    return score, label


def score_lead(lead: Dict[str, Any]) -> Dict[str, Any]:
    """Convenience wrapper — returns {score, score_label} dict."""
    score, label = calculate_score(lead)
    return {"score": score, "score_label": label}
