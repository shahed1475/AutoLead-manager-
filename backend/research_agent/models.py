"""
models.py — Browser Research Agent data model.

Dataclasses, matching intelligence/base.py's convention for internal agent
state (Pydantic request models for the API layer live in backend/models.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, Iterable, List, Optional

# ── Field-level evidence status ─────────────────────────────────────────────
STATUS_FOUND = "FOUND"
STATUS_NOT_FOUND = "NOT_FOUND"
# NOT_FOUND_AFTER_SEARCH: the loop actively researched this field (visited pages
# and/or ran searches) and it isn't publicly available — a stronger, auditable
# "we looked" than a bare NOT_FOUND. Never a substitute for a real value.
STATUS_NOT_FOUND_AFTER_SEARCH = "NOT_FOUND_AFTER_SEARCH"
STATUS_SECURE_WEB_FORM = "SECURE_WEB_FORM"
STATUS_UNCONFIRMED = "UNCONFIRMED"
STATUS_VERIFIED_BY_SOURCE = "VERIFIED_BY_SOURCE"

VALID_FIELD_STATUSES = frozenset({
    STATUS_FOUND, STATUS_NOT_FOUND, STATUS_NOT_FOUND_AFTER_SEARCH,
    STATUS_SECURE_WEB_FORM, STATUS_UNCONFIRMED, STATUS_VERIFIED_BY_SOURCE,
})

# ── Lead-level research status ──────────────────────────────────────────────
RESEARCH_PENDING = "PENDING"
RESEARCH_IN_PROGRESS = "IN_PROGRESS"
RESEARCH_COMPLETE = "COMPLETE"
RESEARCH_PARTIAL = "PARTIAL"
RESEARCH_FAILED = "FAILED"

# A lead is "complete enough" once these are known — business_email and every
# management_* field are valuable but never block completion on their own,
# since many small businesses genuinely have no public email or named owner.
REQUIRED_FIELDS = ("business_name", "business_phone", "business_website")
OPTIONAL_FIELDS = (
    "business_email", "management_contact_name", "management_title",
    "management_phone", "management_email",
)

# Roles considered per business type — the agent narrows this list based on
# the niche rather than asking the same titles for every business (brief §13).
DEFAULT_MANAGEMENT_TITLES = ("Owner", "Founder", "General Manager", "Director")
NICHE_MANAGEMENT_TITLES: Dict[str, tuple] = {
    "dental": ("Dentist", "Owner", "Practice Manager", "Office Manager"),
    "medical": ("Doctor", "Practice Owner", "Practice Manager", "Office Manager"),
    "law": ("Partner", "Attorney", "Managing Partner", "Office Manager"),
    "restaurant": ("Owner", "General Manager", "Chef Owner"),
    "hotel": ("General Manager", "Owner", "Director of Operations"),
    "salon": ("Owner", "Salon Manager"),
    "gym": ("Owner", "General Manager"),
    "real estate": ("Broker", "Owner", "Managing Broker"),
    "saas": ("Founder", "CEO", "CTO", "COO"),
    "startup": ("Founder", "CEO", "CTO", "COO"),
    "tech": ("Founder", "CEO", "CTO"),
    "ecommerce": ("Founder", "Owner", "CEO"),
}


def management_titles_for_niche(niche: str) -> tuple:
    n = (niche or "").lower()
    for key, titles in NICHE_MANAGEMENT_TITLES.items():
        if key in n:
            return titles
    return DEFAULT_MANAGEMENT_TITLES


# ── Custom decision-maker titles ────────────────────────────────────────────
# A user-supplied title list ("Head of Marketing", "HR Director") overrides the
# niche defaults above. Titles reach the action-decision prompt, so they are
# sanitised to short plain role phrases — never free-form instructions.
MAX_TARGET_TITLES = 10
_MAX_TITLE_LEN = 60
_TITLE_ALLOWED_RE = re.compile(r"[^A-Za-z0-9 &/.,'+-]")


def sanitize_target_titles(titles: Optional[Iterable[Any]]) -> List[str]:
    """Clean a user-supplied title list: strip odd characters, collapse
    whitespace, drop empties/duplicates (case-insensitive), cap count/length."""
    out: List[str] = []
    seen: set = set()
    for raw in titles or ():
        if not isinstance(raw, str):
            continue
        t = _TITLE_ALLOWED_RE.sub("", raw)
        t = re.sub(r"\s+", " ", t).strip(" ,.-/")[:_MAX_TITLE_LEN].strip()
        if len(t) < 2 or t.lower() in seen:
            continue
        seen.add(t.lower())
        out.append(t)
        if len(out) >= MAX_TARGET_TITLES:
            break
    return out


def resolve_target_titles(niche: str, custom_titles: Optional[Iterable[Any]] = None) -> tuple:
    """The titles the agent hunts for, in priority order: the user's custom
    list when given, else the niche defaults."""
    custom = sanitize_target_titles(custom_titles)
    return tuple(custom) if custom else management_titles_for_niche(niche)


# Common abbreviations, so a target of "CEO" matches "Chief Executive Officer"
# on a page and vice versa.
_TITLE_ALIASES = {
    "ceo": "chief executive officer", "cto": "chief technology officer",
    "coo": "chief operating officer", "cfo": "chief financial officer",
    "cmo": "chief marketing officer", "cio": "chief information officer",
    "cro": "chief revenue officer", "cpo": "chief product officer",
    "vp": "vice president", "gm": "general manager", "md": "managing director",
    "hr": "human resources",
}


def _title_tokens(title: str) -> List[str]:
    t = re.sub(r"[^a-z0-9 ]", " ", (title or "").lower())
    toks: List[str] = []
    for tok in t.split():
        toks.extend(_TITLE_ALIASES.get(tok, tok).split())
    return toks


def match_target_title(title: Optional[str], target_titles: Iterable[str]) -> Optional[str]:
    """The first target title (priority order) whose words all appear in
    `title` — "Owner" matches "Practice Owner", "CEO" matches "Founder &
    Chief Executive Officer". Deterministic; None when nothing matches."""
    have = set(_title_tokens(title or ""))
    if not have:
        return None
    for target in target_titles:
        want = _title_tokens(target)
        if want and all(w in have for w in want):
            return target
    return None


@dataclass
class DecisionMaker:
    """One named person with a role at the business. Only ever created from
    text on a real page (name and title both appear verbatim in it) — see
    agent._process_page_text. `matched_title` is the target title it
    satisfies, if any; `is_primary` marks the one mirrored into the lead's
    management_contact_name/title."""
    name: str
    title: str
    matched_title: Optional[str] = None
    source_url: Optional[str] = None
    snippet: Optional[str] = None
    confidence: float = 0.0
    status: str = STATUS_FOUND
    is_primary: bool = False


@dataclass
class ResearchEvidence:
    field_name: str
    source_type: str                       # "google_search" | "website" | "ai_extraction"
    source_url: Optional[str] = None
    snippet: Optional[str] = None
    confidence: float = 0.0
    status: str = STATUS_UNCONFIRMED

    def __post_init__(self) -> None:
        if self.status not in VALID_FIELD_STATUSES:
            self.status = STATUS_UNCONFIRMED
        self.confidence = max(0.0, min(1.0, float(self.confidence or 0.0)))


@dataclass
class ResearchLead:
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None

    business_name: Optional[str] = None
    business_phone: Optional[str] = None
    business_email: Optional[str] = None
    business_website: Optional[str] = None
    business_email_status: str = STATUS_UNCONFIRMED

    management_contact_name: Optional[str] = None
    management_title: Optional[str] = None
    management_phone: Optional[str] = None
    management_phone_type: Optional[str] = None   # "BUSINESS" | "DIRECT"
    management_email: Optional[str] = None
    management_email_status: str = STATUS_UNCONFIRMED

    confidence: float = 0.0
    research_status: str = RESEARCH_PENDING
    research_notes: Optional[str] = None
    evidence: List[ResearchEvidence] = field(default_factory=list)
    decision_makers: List[DecisionMaker] = field(default_factory=list)

    # Internal loop bookkeeping — not exported.
    actions_taken: int = 0
    searches_taken: int = 0
    pages_visited: int = 0
    consecutive_failures: int = 0

    def missing_required_fields(self) -> List[str]:
        return [f for f in REQUIRED_FIELDS if not getattr(self, f)]

    def add_evidence(self, item: ResearchEvidence) -> None:
        self.evidence.append(item)
        if item.status == STATUS_FOUND and hasattr(self, item.field_name):
            pass  # the caller sets the field itself; evidence just records provenance

    def to_export_dict(self) -> Dict[str, Any]:
        return {
            "City": self.city or "",
            "State": self.state or "",
            "Country": self.country or "",
            "Business_Name": self.business_name or "",
            "Business_Phone": self.business_phone or "",
            "Business_Email": self.business_email or "",
            "Business_Website": self.business_website or "",
            "Management_Contact_Name": self.management_contact_name or "",
            "Management_Title": self.management_title or "",
            "Management_Phone": self.management_phone or "",
            "Management_Email": self.management_email or "",
            "Decision_Makers": "; ".join(f"{d.name} ({d.title})" for d in self.decision_makers),
            "Confidence": round(self.confidence, 2),
            "Research_Status": self.research_status,
            "Evidence_URLs": "; ".join(
                sorted({e.source_url for e in self.evidence if e.source_url})
            ),
        }


# ── Agent action (LLM decision) ─────────────────────────────────────────────

VALID_ACTIONS = frozenset({
    "google_search", "open_url", "extract_page_text", "find_links",
    "click", "scroll", "go_back", "open_new_tab", "screenshot",
    "save_evidence", "finish_research",
})


@dataclass
class AgentAction:
    action: str
    params: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    confidence: float = 0.0

    def is_valid(self) -> bool:
        return self.action in VALID_ACTIONS
