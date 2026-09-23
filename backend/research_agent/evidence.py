"""
evidence.py — record a finding on a ResearchLead: sets the field AND appends
the provenance row in one place, so no code path can set a field without
recording where it came from (brief §15: "every extracted important field
should be traceable to a source").
"""
from __future__ import annotations

import re
from typing import Any, Optional, Sequence

from .models import (
    STATUS_FOUND,
    DecisionMaker,
    STATUS_NOT_FOUND,
    STATUS_NOT_FOUND_AFTER_SEARCH,
    STATUS_UNCONFIRMED,
    ResearchEvidence,
    ResearchLead,
    match_target_title,
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


DECISION_MAKER_FIELD = "decision_maker"
_NAME_PREFIX_RE = re.compile(r"^(dr|mr|mrs|ms|miss|prof)\.?\s+", re.I)


def _name_tokens(name: str) -> frozenset:
    n = _NAME_PREFIX_RE.sub("", (name or "").strip())
    return frozenset(t for t in re.split(r"[^a-z]+", n.lower()) if t)


def _title_rank(dm: DecisionMaker, target_titles: Sequence[str]) -> int:
    if dm.matched_title and dm.matched_title in target_titles:
        return list(target_titles).index(dm.matched_title)
    return len(target_titles)


def _refresh_primary(lead: ResearchLead, target_titles: Sequence[str], source_type: str,
                     confidence: float) -> None:
    """Re-pick the primary (highest-priority matched title, earliest found
    wins ties) and mirror it into management_contact_name/title through
    record_finding, so those fields keep provenance and existing readers
    keep working."""
    primary = min(
        lead.decision_makers,
        key=lambda d: (_title_rank(d, target_titles), lead.decision_makers.index(d)),
    )
    for d in lead.decision_makers:
        d.is_primary = d is primary
    if lead.management_contact_name == primary.name and lead.management_title == primary.title:
        return
    # The primary choice is made above; record_finding only overwrites on a
    # strictly higher confidence, so step just above whatever backs the
    # current value.
    existing = [e.confidence for e in lead.evidence
                if e.field_name in ("management_contact_name", "management_title")]
    rank_conf = confidence + 0.01 * (len(target_titles) - _title_rank(primary, target_titles))
    mirror_conf = round(min(0.95, max(rank_conf, max(existing, default=0.0) + 0.01)), 3)
    for field_name, value in (("management_contact_name", primary.name),
                              ("management_title", primary.title)):
        record_finding(
            lead, field_name, value, source_type=source_type,
            source_url=primary.source_url, snippet=f"{primary.name} — {primary.title}",
            confidence=mirror_conf,
        )


def record_decision_maker(
    lead: ResearchLead,
    name: str,
    title: str,
    target_titles: Sequence[str],
    source_type: str,
    source_url: Optional[str] = None,
    snippet: Optional[str] = None,
    confidence: float = 0.65,
    max_count: int = 5,
) -> bool:
    """Add one decision maker (a person + role seen on a real page) and its
    evidence row, then re-pick the primary contact. The caller has already
    checked that name and title appear in the page text. Returns True when a
    new person was added.

    The same person is often named twice — "Jonathan" on the homepage,
    "Jonathan Windham" on the About page. A name whose words are a subset of
    a known person's is treated as that person; a fuller version upgrades
    the stored name (and its source) instead of adding a duplicate."""
    name = (name or "").strip()
    title = (title or "").strip()
    tokens = _name_tokens(name)
    if not tokens or not title:
        return False

    evidence_row = ResearchEvidence(
        field_name=DECISION_MAKER_FIELD, source_type=source_type, source_url=source_url,
        snippet=f"{name} — {title}" + (f" | {snippet}" if snippet else ""),
        confidence=confidence, status=STATUS_FOUND,
    )
    for d in lead.decision_makers:
        known = _name_tokens(d.name)
        if tokens <= known:
            lead.evidence.append(evidence_row)  # corroborating source for someone already known
            return False
        if known < tokens:
            d.name, d.source_url = name, source_url
            d.snippet = (snippet or "")[:300] or d.snippet
            lead.evidence.append(evidence_row)
            _refresh_primary(lead, target_titles, source_type, confidence)
            return False
    if len(lead.decision_makers) >= max_count:
        return False

    lead.decision_makers.append(DecisionMaker(
        name=name, title=title, matched_title=match_target_title(title, target_titles),
        source_url=source_url, snippet=(snippet or "")[:300] or None,
        confidence=confidence, status=STATUS_FOUND,
    ))
    lead.evidence.append(evidence_row)
    _refresh_primary(lead, target_titles, source_type, confidence)
    return True
