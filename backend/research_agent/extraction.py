"""
extraction.py — structured field extraction from page text.

Deterministic first (phone/email via regex — reusing scrapers/email_finder.py's
proven patterns, never asking the LLM to "find" something a regex already
finds reliably), LLM-assisted second (name/title, which genuinely need
language understanding — see llm.py::extract_fields).
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from ..scrapers.email_finder import _EMAIL_RE, _PHONE_RE
from ..validators import clean_email, clean_phone, is_valid_email, is_valid_phone

# Scored link map — replaces the old flat _TEAM_PAGE_HINTS regex. Weight 3 =
# likely to name a person; weight 2 = an offering/booking page (useful for
# services/tech context, Cycle 2); weight 1 = peripheral but sometimes
# useful; 0 = not worth queuing.
_LINK_HINTS_BY_WEIGHT = (
    (3, re.compile(
        r"\b(contact|about|team|our[- ]team|meet[- ]the[- ]team|staff|leadership|"
        r"management|providers?|doctors?|physicians?|dentists?|attorneys?|"
        r"people|who[- ]we[- ]are)\b", re.I,
    )),
    (2, re.compile(
        r"\b(services?|treatments?|procedures?|what[- ]we[- ]do|specialt(y|ies)|"
        r"pricing|plans?|fees?|book(ing)?|appointments?|schedule|request|"
        r"patient[- ]portal|new[- ]patients?)\b", re.I,
    )),
    (1, re.compile(
        r"\b(locations?|offices?|careers?|jobs?|blog|news|press|insights?)\b", re.I,
    )),
)

_ROLE_HINT = re.compile(
    r"\b(owner|founder|ceo|cto|coo|president|managing\s+director|"
    r"principal\s+dentist|lead\s+dentist|dentist|doctor|"
    r"practice\s+owner|practice\s+manager|office\s+manager|clinic\s+manager|"
    r"operations\s+manager|general\s+manager|clinical\s+director|director|"
    r"administrator|managing\s+partner|partner|attorney|broker|chef)\b",
    re.I,
)


def extract_emails(text: str) -> List[str]:
    if not text:
        return []
    seen, out = set(), []
    for raw in _EMAIL_RE.findall(text):
        email = clean_email(raw)
        if email and email not in seen and is_valid_email(email):
            seen.add(email)
            out.append(email)
    return out


def extract_phones(text: str) -> List[str]:
    if not text:
        return []
    seen, out = set(), []
    for raw in _PHONE_RE.finditer(text):
        phone = clean_phone(raw.group(0))
        if phone and phone not in seen and is_valid_phone(phone):
            seen.add(phone)
            out.append(phone)
    return out


def emails_from_contact_links(links: List[Dict[str, str]]) -> List[str]:
    """Emails carried explicitly in `mailto:` hrefs — a strong, deterministic
    signal (a site author put it there on purpose). `links` are {text, href}
    dicts as returned by BrowserController.find_links / extract_page_text."""
    seen, out = set(), []
    for link in links or []:
        href = (link.get("href") or "").strip()
        if not href.lower().startswith("mailto:"):
            continue
        raw = href[7:].split("?", 1)[0].strip()
        email = clean_email(raw)
        if email and email not in seen and is_valid_email(email):
            seen.add(email)
            out.append(email)
    return out


def phones_from_contact_links(links: List[Dict[str, str]]) -> List[str]:
    """Phones carried explicitly in `tel:` hrefs."""
    seen, out = set(), []
    for link in links or []:
        href = (link.get("href") or "").strip()
        if not href.lower().startswith("tel:"):
            continue
        phone = clean_phone(href[4:].split("?", 1)[0].strip())
        if phone and phone not in seen and is_valid_phone(phone):
            seen.add(phone)
            out.append(phone)
    return out


def link_relevance(href: str, anchor_text: str = "") -> int:
    """0 = not worth queuing; higher = more likely to hold people or
    offering information. Scored from the href path and anchor text
    together, so a link is still recognised even with no visible text."""
    haystack = f"{anchor_text} {href}"
    for weight, pattern in _LINK_HINTS_BY_WEIGHT:
        if pattern.search(haystack):
            return weight
    return 0


def is_relevant_nav_link(anchor_text: str) -> bool:
    """Back-compat shim over link_relevance — True iff the text alone scores
    above 0. Prefer link_relevance directly in new code (it also recognises
    offering/peripheral pages, not just people pages)."""
    return link_relevance(anchor_text, "") > 0


def find_role_sentences(text: str, max_sentences: int = 8) -> List[str]:
    """Cheap pre-filter: sentences that mention a role-like word, so the LLM's
    field-extraction prompt only needs a handful of candidate lines instead
    of a whole page of text."""
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?\n])\s+", text)
    hits = [s.strip() for s in sentences if _ROLE_HINT.search(s) and len(s.strip()) < 300]
    return hits[:max_sentences]


def has_secure_contact_form(text: str, links: Optional[List[Dict[str, str]]] = None) -> bool:
    """True when the page has a contact form but no discoverable email —
    used to set business_email_status/management_email_status to
    SECURE_WEB_FORM instead of NOT_FOUND (brief §12)."""
    t = (text or "").lower()
    if "contact us" in t or "send us a message" in t or "get in touch" in t:
        for link in links or []:
            if "contact" in (link.get("text") or "").lower():
                return True
        if re.search(r"\b(name|email|message)\b.{0,40}\b(name|email|message)\b", t):
            return True
    return False
