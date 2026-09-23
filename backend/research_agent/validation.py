"""
validation.py — is a lead's research "done", and does its data respect the
missing-information discipline (brief §12: never fabricate, always mark
NOT_FOUND / SECURE_WEB_FORM / UNCONFIRMED explicitly)?
"""
from __future__ import annotations

from typing import List, Tuple

from .models import (
    OPTIONAL_FIELDS,
    RESEARCH_COMPLETE,
    RESEARCH_PARTIAL,
    ResearchLead,
)


def is_research_sufficient(lead: ResearchLead) -> bool:
    """Required fields present is enough to call it done — optional fields
    (email, management contact) are valuable but many real small businesses
    genuinely don't have a public one; budgets, not a wait for every field,
    decide when to stop trying for those."""
    return not lead.missing_required_fields()


def finalize_status(lead: ResearchLead) -> ResearchLead:
    """Called once the loop ends (success, budget exhausted, or explicit
    finish_research). Sets the final research_status and a simple confidence
    score. Never invents a field — only reads what's already on the lead."""
    missing_required = lead.missing_required_fields()
    found_optional = sum(1 for f in OPTIONAL_FIELDS if getattr(lead, f, None))

    if not missing_required:
        lead.research_status = RESEARCH_COMPLETE
        base = 0.7
    elif lead.business_name and (lead.business_phone or lead.business_website):
        lead.research_status = RESEARCH_PARTIAL
        base = 0.4
    else:
        from .models import RESEARCH_FAILED
        lead.research_status = RESEARCH_FAILED
        base = 0.0

    lead.confidence = round(min(1.0, base + 0.05 * found_optional), 2)

    # A short, factual research trail — what was actually found and where.
    # Never a claim; just a summary of the evidence rows already recorded.
    found_fields = sorted({e.field_name for e in lead.evidence if e.status in ("FOUND", "VERIFIED_BY_SOURCE")})
    source_urls = sorted({e.source_url for e in lead.evidence if e.source_url})
    parts = [
        f"{lead.actions_taken} research action(s), {lead.pages_visited} page(s) visited, {lead.searches_taken} search(es).",
    ]
    if found_fields:
        parts.append(f"Fields found: {', '.join(found_fields)}.")
    if missing_required:
        parts.append(f"Missing required: {', '.join(missing_required)}.")
    if lead.decision_makers:
        parts.append("Decision makers: " + "; ".join(f"{d.name} ({d.title})" for d in lead.decision_makers) + ".")
    if not lead.management_contact_name:
        parts.append("No named owner/manager was publicly listed — not inferred.")
    if source_urls:
        parts.append(f"Sources: {'; '.join(source_urls[:6])}.")
    lead.research_notes = " ".join(parts)
    return lead


def validate_no_fabrication(lead: ResearchLead) -> List[str]:
    """Defensive check used by tests and the agent's own post-loop pass:
    every non-null optional contact field must have at least one evidence
    row backing it. Returns a list of violation messages (empty = clean)."""
    violations = []
    contact_fields = (
        "business_phone", "business_email", "management_contact_name",
        "management_title", "management_phone", "management_email",
    )
    for f in contact_fields:
        value = getattr(lead, f, None)
        if value and not any(e.field_name == f and e.status == "FOUND" for e in lead.evidence):
            violations.append(f"{f}={value!r} has no supporting FOUND evidence")
    for d in lead.decision_makers:
        prefix = f"{d.name} — "
        if not any(e.field_name == "decision_maker" and e.status == "FOUND"
                   and (e.snippet or "").startswith(prefix) for e in lead.evidence):
            violations.append(f"decision_maker={d.name!r} has no supporting FOUND evidence")
    return violations


def management_phone_type_is_safe(lead: ResearchLead) -> bool:
    """A management_phone must never be presented without a phone_type —
    otherwise a shared business line could be mistaken for a personal
    direct line (brief §12)."""
    if lead.management_phone and not lead.management_phone_type:
        return False
    return True
