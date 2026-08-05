"""
qualification_agent.py — Lead Qualification Agent.

Fast, free, no LLM — runs for every lead to decide whether the more
expensive Company Research Agent is worth running at all.
"""
import logging
from typing import Any, Dict, List, Optional

import httpx

from ..validators import clean_business_name, is_valid_email, is_valid_phone
from .base import AgentResult, EvidenceItem

logger = logging.getLogger(__name__)

_REACHABILITY_TIMEOUT = 6.0
_CONFIDENCE_THRESHOLD = 0.5

_CHECK_WEIGHTS = {
    "website_reachable": 0.3,
    "has_business_name": 0.25,
    "has_contact_info":  0.25,
    "niche_match":       0.1,
    "location_match":    0.1,
}


async def _check_website_reachable(url: Optional[str]) -> Optional[bool]:
    """None = skipped (no website, neutral — doesn't count against confidence)."""
    if not url:
        return None
    target = url if url.startswith(("http://", "https://")) else f"https://{url}"
    try:
        async with httpx.AsyncClient(timeout=_REACHABILITY_TIMEOUT, follow_redirects=True, verify=False) as client:
            resp = await client.head(target)
            if resp.status_code >= 400:
                resp = await client.get(target)
            return resp.status_code < 400
    except Exception as exc:
        logger.debug("qualification: reachability check failed for %s: %s", url, exc)
        return False


def _niche_matches(lead_niche: Optional[str], campaign_niche: Optional[str]) -> Optional[bool]:
    if not campaign_niche:
        return None
    if not lead_niche:
        return False
    a, b = lead_niche.strip().lower(), campaign_niche.strip().lower()
    return a in b or b in a


def _location_matches(lead: Dict[str, Any], campaign: Dict[str, Any]) -> Optional[bool]:
    campaign_city = campaign.get("city")
    if not campaign_city:
        return None
    lead_city = lead.get("city")
    if not lead_city:
        return False
    return campaign_city.strip().lower() == lead_city.strip().lower()


class QualificationAgent:
    name = "qualification"

    async def run(self, lead: Dict[str, Any], campaign: Optional[Dict[str, Any]] = None) -> AgentResult:
        campaign = campaign or {}
        reasons_failed: List[str] = []
        score = 0.0
        max_score = 0.0

        has_name = bool(clean_business_name(lead.get("business_name")))
        max_score += _CHECK_WEIGHTS["has_business_name"]
        if has_name:
            score += _CHECK_WEIGHTS["has_business_name"]
        else:
            reasons_failed.append("missing business name")

        has_contact = bool(
            (lead.get("email") and is_valid_email(lead["email"]))
            or (lead.get("phone") and is_valid_phone(lead["phone"]))
            or lead.get("website")
        )
        max_score += _CHECK_WEIGHTS["has_contact_info"]
        if has_contact:
            score += _CHECK_WEIGHTS["has_contact_info"]
        else:
            reasons_failed.append("no email, phone, or website")

        reachable = await _check_website_reachable(lead.get("website"))
        if reachable is not None:
            max_score += _CHECK_WEIGHTS["website_reachable"]
            if reachable:
                score += _CHECK_WEIGHTS["website_reachable"]
            else:
                reasons_failed.append("website unreachable")

        niche_ok = _niche_matches(lead.get("niche"), campaign.get("niche"))
        if niche_ok is not None:
            max_score += _CHECK_WEIGHTS["niche_match"]
            if niche_ok:
                score += _CHECK_WEIGHTS["niche_match"]
            else:
                reasons_failed.append("niche mismatch")

        location_ok = _location_matches(lead, campaign)
        if location_ok is not None:
            max_score += _CHECK_WEIGHTS["location_match"]
            if location_ok:
                score += _CHECK_WEIGHTS["location_match"]
            else:
                reasons_failed.append("location mismatch")

        confidence = round((score / max_score) if max_score > 0 else 0.0, 2)
        qualified = confidence >= _CONFIDENCE_THRESHOLD

        evidence = [EvidenceItem(
            field_name="qualification_status",
            source_type="heuristic",
            source_url=lead.get("website"),
            snippet="; ".join(reasons_failed) if reasons_failed else "all checks passed",
        )]

        return AgentResult(
            status="ok" if qualified else "rejected",
            data={
                "qualification_status": "QUALIFIED" if qualified else "REJECTED",
                "qualification_confidence": confidence,
            },
            evidence=evidence,
            confidence=confidence,
            reason="; ".join(reasons_failed) if reasons_failed else None,
        )
