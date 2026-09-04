"""
ai.py — per-lead email preparation for an Email Campaign, on top of the
EXISTING native AI pipeline (backend/ai_brain.py). No second AI system.

  body supplied  -> ai_body = supplied body VERBATIM; ai_brain generates ONLY the subject
  body absent    -> ai_brain generates subject + body

AI output is validated with ai_brain's existing `_validate_message_content`
(unfilled `[Name]` / `[Link]` brackets, raw HTML, hashtags, emoji spam). A
failure sets status = AI_GENERATION_FAILED — the send path skips those leads.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Tuple

from .. import ai_brain

logger = logging.getLogger(__name__)

# extra guard for the templated-placeholder styles the n8n V1.1 spec calls out
_EXTRA_PLACEHOLDER_TOKENS = (
    "{{first_name}}", "{{ first_name }}", "{{company}}", "{{ company }}",
    "{first_name}", "{company}", "[company name]", "[first name]", "[name]",
    "business name", "undefined", "null",
)


def _looks_like_placeholder(text: str) -> bool:
    low = (text or "").lower()
    return any(tok in low for tok in _EXTRA_PLACEHOLDER_TOKENS)


def _lead_for_ai(row: Dict[str, Any]) -> Dict[str, Any]:
    """Map an email_campaign_leads row onto the dict ai_brain expects."""
    return {
        "business_name": row.get("company") or row.get("first_name") or "the business",
        "niche":         (row.get("raw_json_industry") or "general"),
        "city":          row.get("raw_json_location") or "",
        "email":         row.get("email"),
        "first_name":    row.get("first_name") or "",
        "company":       row.get("company") or "",
    }


async def prepare_email(campaign: Dict[str, Any], lead_row: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """
    Returns (updates, status).
    updates: fields to write onto the email_campaign_leads row
      (ai_subject, ai_body, body_source, status_detail).
    status: 'GENERATED' on success, 'AI_GENERATION_FAILED' otherwise.
    """
    ai_enabled = campaign.get("ai_enabled") in (True, 1, "1", "true", "True")
    body_source = lead_row.get("body_source") or "ai"
    provided_body = lead_row.get("provided_body")

    lead = _lead_for_ai(lead_row)

    # ---- supplied body: verbatim body + AI subject only -------------------
    if body_source == "provided" and provided_body is not None:
        subject = ""
        detail = "provided-body"
        try:
            if ai_enabled:
                subject = (await ai_brain.generate_message(lead, "email_subject") or "").strip()
        except Exception as exc:  # noqa: BLE001 — never let AI crash the prep
            logger.warning("prepare_email: subject generation failed for %s: %s",
                           lead_row.get("lead_key"), exc)
            subject = ""
        if not subject or _looks_like_placeholder(subject) or ai_brain._validate_message_content(subject):
            # deterministic fallback subject — first ~8 words of the body
            words = " ".join(str(provided_body).split())[:120].split()
            subject = " ".join(words[:8]) or "Following up"
            detail += "; subject-fallback"
        return (
            {"ai_subject": subject, "ai_body": provided_body, "body_source": "provided",
             "status_detail": detail},
            "GENERATED",
        )

    # ---- no supplied body: AI subject + body via the existing pipeline ----
    if not ai_enabled:
        return (
            {"status_detail": "ai_enabled is false and no body was supplied"},
            "AI_GENERATION_FAILED",
        )
    try:
        dna = ai_brain._load_company_dna()
        msgs = await ai_brain.generate_messages(lead, dna)
        subject = (msgs.get("email_subject") or "").strip()
        body = (msgs.get("email_body") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("prepare_email: generation failed for %s: %s", lead_row.get("lead_key"), exc)
        return ({"status_detail": f"AI request error: {exc}"[:400]}, "AI_GENERATION_FAILED")

    problems = []
    if not subject:
        problems.append("empty subject")
    if not body or len(body) < 40:
        problems.append("empty/too-short body")
    problems += ai_brain._validate_message_content(subject)
    problems += ai_brain._validate_message_content(body)
    if _looks_like_placeholder(subject) or _looks_like_placeholder(body):
        problems.append("unfilled placeholder token")

    if problems:
        return ({"status_detail": "; ".join(problems)[:400]}, "AI_GENERATION_FAILED")

    return ({"ai_subject": subject, "ai_body": body, "body_source": "ai", "status_detail": ""},
            "GENERATED")
