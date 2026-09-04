"""
merge_dedup.py — basic validation + merge-aware save for discovery
candidates. The actual dedup/merge logic lives in
database.py::create_or_merge_lead (it needs direct DB access to be
race-safe against concurrent writes); this module is the Basic Validation
step plus the batch-orchestration layer that calls it per candidate.

Validation mirrors scrapers._validate_and_clean's existing rule: reject only
when business_name is missing, or when there's no email/phone/website left
after clearing invalid-format fields — never reject solely for a bad email.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import database as db
from ..validators import is_valid_email, is_valid_phone


def clean_contact_fields(candidate: Dict[str, Any]) -> Dict[str, Any]:
    c = dict(candidate)
    if c.get("email") and not is_valid_email(c["email"]):
        c["email"] = None
    if c.get("phone") and not is_valid_phone(c["phone"]):
        c["phone"] = None
    return c


# A candidate is kept as long as it has a name AND at least one locating /
# identifying signal — a business with only a name + city + niche is a valid
# research candidate (spec §8, §25), not junk. Bare-name-only rows (usually a
# parse error) are still dropped.
_IDENTITY_ANCHORS = ("email", "phone", "website", "address", "city", "niche", "raw_url", "source_url")
_CONTACT_FIELDS = ("email", "phone", "website")


def basic_validate(candidate: Dict[str, Any]) -> bool:
    if not (candidate.get("business_name") or "").strip():
        return False
    if not any(candidate.get(k) for k in _IDENTITY_ANCHORS):
        return False
    return True


def discovery_status_for(candidate: Dict[str, Any]) -> str:
    """FULL = reachable (has a contact channel); MINIMAL = name + context only."""
    return "FULL" if any(candidate.get(k) for k in _CONTACT_FIELDS) else "MINIMAL"


async def merge_and_save(
    candidates: List[Dict[str, Any]],
    run_id: Optional[int] = None,
    default_source: str = "UNKNOWN",
) -> Dict[str, Any]:
    """
    Validates and saves a batch of raw candidate dicts (each already tagged
    with its own 'source' by the scraper dispatch layer). Returns
    {saved_ids, new_count, merged_count, rejected_count, merge_reasons}.
    """
    saved_ids: List[int] = []
    new_count = 0
    merged_count = 0
    rejected_count = 0
    merge_reasons: Dict[str, int] = {}

    for raw in candidates:
        candidate = clean_contact_fields(raw)
        if not basic_validate(candidate):
            rejected_count += 1
            continue

        status = discovery_status_for(candidate)
        candidate.setdefault("discovery_status", status)

        source = candidate.get("source") or default_source
        source_identifier = (
            candidate.get("website") or candidate.get("phone")
            or candidate.get("email") or candidate.get("business_name")
        )
        lead_id, is_new, merge_reason = await db.create_or_merge_lead(
            candidate, source=source, source_identifier=source_identifier, run_id=run_id,
        )
        saved_ids.append(lead_id)
        if is_new:
            new_count += 1
        else:
            merged_count += 1
            if merge_reason:
                merge_reasons[merge_reason] = merge_reasons.get(merge_reason, 0) + 1
            # A later source that carries a contact channel upgrades a
            # previously name-only lead. create_or_merge_lead only fills NULLs,
            # so this explicit bump is needed.
            if status == "FULL":
                existing = await db.get_lead_by_id(lead_id)
                if existing and existing.get("discovery_status") == "MINIMAL":
                    await db.update_lead(lead_id, {"discovery_status": "FULL"})

    return {
        "saved_ids": saved_ids,
        "unique_saved_ids": list(dict.fromkeys(saved_ids)),
        "new_count": new_count,
        "merged_count": merged_count,
        "rejected_count": rejected_count,
        "merge_reasons": merge_reasons,
    }
