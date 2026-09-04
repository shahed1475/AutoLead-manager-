"""
handoff.py — send already-discovered leads to the existing Browser Research
Agent. Do NOT rebuild the agent: a handoff creates one lead_research_session
(mode='handoff') seeded with the selected leads' business data, and
run_research_session's existing `seed_businesses` path deep-researches exactly
those (no re-discovery). session.on_lead_complete merges the research result
back onto the lead via the existing create_or_merge_lead.
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, List, Optional

from .. import database as db
from .session import enqueue_session

logger = logging.getLogger(__name__)

_ACTIVE_RESEARCH = ("QUEUED", "RESEARCHING")
# Everything available on the lead that helps the agent research the business
# and its decision-makers. Null fields are omitted — never a fabricated
# placeholder (spec §9).
_SEED_FIELDS = (
    "business_name", "website", "phone", "email", "address",
    "city", "state", "country", "niche", "source", "google_place_id",
    "latitude", "longitude",
)


def _top(leads: List[Dict[str, Any]], key: str) -> str:
    c = Counter((l.get(key) or "").strip() for l in leads if (l.get(key) or "").strip())
    return c.most_common(1)[0][0] if c else ""


def _derive_niche(leads: List[Dict[str, Any]]) -> str:
    return _top(leads, "niche") or "business"


def _derive_location(leads: List[Dict[str, Any]]) -> str:
    city = _top(leads, "city")
    if city:
        state = _top(leads, "state")
        return ", ".join(p for p in (city, state) if p)
    return _top(leads, "state") or _top(leads, "country") or "United States"


async def handoff_leads(
    queue, lead_ids: List[int], *,
    submission_source: str = "manual", priority: Optional[str] = None,
) -> Dict[str, Any]:
    """Queue a research session for the given leads. Returns
    {session_id, queued, skipped}. Idempotent: leads already QUEUED/RESEARCHING
    or flagged excluded_from_research are skipped. `submission_source` records
    how the handoff was triggered (manual / lead_search_automation / …)."""
    ids = [i for i in dict.fromkeys(lead_ids) if i]
    result: Dict[str, Any] = {"session_id": None, "queued": 0, "skipped": []}
    if not ids:
        return result

    leads_map = await db.get_leads_by_ids(ids)
    eligible: List[Dict[str, Any]] = []
    skipped: List[int] = []
    for i in ids:
        lead = leads_map.get(i)
        if lead is None or lead.get("excluded_from_research") \
                or (lead.get("research_status") or "NOT_STARTED") in _ACTIVE_RESEARCH:
            skipped.append(i)
            continue
        eligible.append(dict(lead))
    result["skipped"] = skipped
    if not eligible or queue is None:
        return result

    seeds: List[Dict[str, Any]] = []
    for l in eligible:
        seed = {k: l.get(k) for k in _SEED_FIELDS if l.get(k)}
        seed["_lead_id"] = l["id"]
        if l.get("created_at"):
            seed["discovered_at"] = str(l["created_at"])
        seeds.append(seed)
    seed_lead_ids = [l["id"] for l in eligible]

    session_id = await db.create_research_session({
        "niche": _derive_niche(eligible),
        "location": _derive_location(eligible),
        "country": eligible[0].get("country") or None,
        "target_count": len(seeds),
        "mode": "handoff",
        "submission_source": submission_source,
        "seed_businesses": seeds,
        "seed_lead_ids": seed_lead_ids,
    })
    await db.set_leads_research_status(seed_lead_ids, "QUEUED", session_id=session_id)
    try:
        await db.set_leads_field(seed_lead_ids, "research_submission_source", submission_source)
    except Exception:
        logger.debug("could not record research_submission_source", exc_info=True)

    session_row = await db.get_research_session(session_id)
    if not enqueue_session(queue, session_row, seed_businesses=seeds):
        await db.update_research_session(session_id, {
            "status": "FAILED", "error_message": "Job queue is full",
        })
        await db.set_leads_research_status(seed_lead_ids, "NOT_STARTED", only_from=("QUEUED",))
        return result

    logger.info("[HANDOFF] session %s queued for %d lead(s)", session_id, len(seeds))
    result["session_id"] = session_id
    result["queued"] = len(seeds)
    return result
