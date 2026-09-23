"""
extraction.py — structured field extraction from page text.

Deterministic first (phone/email via regex — reusing scrapers/email_finder.py's
proven patterns, never asking the LLM to "find" something a regex already
finds reliably), LLM-assisted second (name/title, which genuinely need
language understanding — see llm.py::extract_fields).
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional

from ..scrapers.email_finder import _EMAIL_RE, _PHONE_RE
from ..validators import clean_email, clean_phone, is_valid_email, is_valid_phone

_TEAM_PAGE_HINTS = re.compile(
    r"\b(contact|about|team|staff|leadership|doctors|management|our[- ]team|meet[- ]the[- ]team|location)\b",
    re.I,
)

_ROLE_HINT = re.compile(
    r"\b(owner|founder|ceo|cto|coo|president|managing\s+director|"
    r"principal\s+dentist|lead\s+dentist|dentist|doctor|"
    r"practice\s+owner|practice\s+manager|office\s+manager|clinic\s+manager|"
    r"operations\s+manager|general\s+manager|clinical\s+director|director|"
    r"administrator|managing\s+partner|partner|attorney|broker|chef|"
    r"chief\s+\w+\s+officer|vice\s+president|head\s+of|proprietor|principal)\b",
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


def is_relevant_nav_link(anchor_text: str) -> bool:
    """Whether a link's anchor text looks like it leads to a page worth
    visiting for management/contact info (brief §16 Step 3)."""
    return bool(_TEAM_PAGE_HINTS.search(anchor_text or ""))


def _extra_title_pattern(extra_titles: Iterable[str]) -> Optional[re.Pattern]:
    parts = [re.escape(t.strip()).replace(r"\ ", r"\s+") for t in extra_titles or () if t and t.strip()]
    if not parts:
        return None
    return re.compile(r"\b(" + "|".join(parts) + r")\b", re.I)


def find_role_sentences(text: str, max_sentences: int = 8,
                        extra_titles: Iterable[str] = ()) -> List[str]:
    """Cheap pre-filter: sentences that mention a role-like word, so the LLM's
    field-extraction prompt only needs a handful of candidate lines instead
    of a whole page of text. `extra_titles` (the user's custom target titles)
    extend the built-in role vocabulary. On team pages laid out as cards —
    a name on one line, the role on the next — the short line just above a
    role line is kept with it, so the name isn't lost."""
    if not text:
        return []
    extra = _extra_title_pattern(extra_titles)
    segments = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text)]
    hits: List[str] = []
    for i, seg in enumerate(segments):
        if not seg or len(seg) >= 300:
            continue
        if not (_ROLE_HINT.search(seg) or (extra and extra.search(seg))):
            continue
        prev = segments[i - 1] if i > 0 else ""
        if (prev and len(prev) <= 60 and len(seg) <= 80
                and not _ROLE_HINT.search(prev) and not (extra and extra.search(prev))):
            seg = f"{prev} — {seg}"
        hits.append(seg)
        if len(hits) >= max_sentences:
            break
    return hits


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
