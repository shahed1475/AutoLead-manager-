"""
evidence.py — record a finding on a ResearchLead: sets the field AND appends
the provenance row in one place, so no code path can set a field without
recording where it came from (brief §15: "every extracted important field
should be traceable to a source").
"""
from __future__ import annotations

from typing import Any, Optional

from .models import (
    STATUS_FOUND,
    STATUS_NOT_FOUND,
    STATUS_NOT_FOUND_AFTER_SEARCH,
    STATUS_UNCONFIRMED,
    ResearchEvidence,
    ResearchLead,
)

_EMAIL_STATUS_FIELDS = {"business_email", "management_email"}

# The only fields a finding may ever write. Guards against an LLM save_evidence
# (or any caller) using record_finding to clobber loop counters, research_status,
# confidence, or evidence itself — which would corrupt the run, not enrich it.
_RECORDABLE_FIELDS = frozenset({
    "city", "state", "country",
    "business_name", "business_phone", "business_email", "business_website",
    "management_contact_name", "management_title", "management_phone", "management_email",
})


def record_finding(
    lead: ResearchLead,
    field_name: str,
    value: Any,
    source_type: str,
    source_url: Optional[str] = None,
    snippet: Optional[str] = None,
    confidence: float = 0.7,
    status: str = STATUS_FOUND,
) -> None:
    """Sets `field_name` on the lead (only if the lead doesn't already have
    a higher- or equal-confidence value for it — first good source wins,
    doesn't get silently clobbered by a later weaker one) and appends the
    matching evidence row."""
    if field_name not in _RECORDABLE_FIELDS or not hasattr(lead, field_name):
        return

    existing_evidence = [e for e in lead.evidence if e.field_name == field_name]
    if existing_evidence and getattr(lead, field_name):
        best_existing_confidence = max(e.confidence for e in existing_evidence)
        if confidence <= best_existing_confidence:
            # Still record the corroborating/conflicting evidence, but don't overwrite.
            lead.evidence.append(ResearchEvidence(
                field_name=field_name, source_type=source_type, source_url=source_url,
                snippet=snippet, confidence=confidence, status=status,
            ))
            return

    setattr(lead, field_name, value)
    lead.evidence.append(ResearchEvidence(
        field_name=field_name, source_type=source_type, source_url=source_url,
        snippet=snippet, confidence=confidence, status=status,
    ))

    # Field-specific status companions, kept in sync with the value.
    if field_name in _EMAIL_STATUS_FIELDS:
        status_field = f"{field_name}_status"
        if hasattr(lead, status_field):
            setattr(lead, status_field, status)


def record_not_found(lead: ResearchLead, field_name: str, reason: str = "",
                     after_search: bool = False) -> None:
    """Explicit 'we looked and it isn't there' — never silently absent.

    `after_search=True` records the stronger NOT_FOUND_AFTER_SEARCH: the loop
    actively researched this field (pages visited and/or searches run) and it
    is not publicly available. Idempotent per field — a field already backed
    by a NOT_FOUND-family row is not appended to again.
    """
    status = STATUS_NOT_FOUND_AFTER_SEARCH if after_search else STATUS_NOT_FOUND
    already = any(
        e.field_name == field_name and e.status in (STATUS_NOT_FOUND, STATUS_NOT_FOUND_AFTER_SEARCH)
        for e in lead.evidence
    )
    if not already:
        lead.evidence.append(ResearchEvidence(
            field_name=field_name, source_type="research_loop", source_url=None,
            snippet=reason or None, confidence=0.0, status=status,
        ))
    if field_name in _EMAIL_STATUS_FIELDS:
        status_field = f"{field_name}_status"
        if hasattr(lead, status_field) and getattr(lead, field_name) is None:
            setattr(lead, status_field, status)
