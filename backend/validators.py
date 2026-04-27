"""
validators.py — Email and phone validation helpers.
Used across scrapers, enrichment, and lead import to clean raw data.
"""
import re
from typing import Optional

_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", re.ASCII
)

_SPAM_LOCALS = frozenset({
    "noreply", "no-reply", "donotreply", "bounce", "postmaster",
    "abuse", "privacy", "legal", "notifications", "newsletter",
    "unsubscribe", "support@2x", "webmaster",
})

_SPAM_DOMAIN_PATTERNS = re.compile(
    r"(example|yourname|yourdomain|domain\.com|wixpress|sentry"
    r"|schema\.org|spamavert|placeholder|email\.com|test\.com)",
    re.I,
)


def is_valid_email(email: Optional[str]) -> bool:
    if not email or len(email) > 254:
        return False
    cleaned = email.strip().lower()
    if not _EMAIL_RE.match(cleaned):
        return False
    local, domain = cleaned.rsplit("@", 1)
    if local in _SPAM_LOCALS:
        return False
    if _SPAM_DOMAIN_PATTERNS.search(domain):
        return False
    return True


def is_valid_phone(phone: Optional[str]) -> bool:
    if not phone:
        return False
    digits = re.sub(r"\D", "", phone)
    return 7 <= len(digits) <= 15


def clean_email(email: Optional[str]) -> Optional[str]:
    """Strip, lowercase, validate — return None if invalid."""
    if not email:
        return None
    cleaned = email.strip().lower().rstrip(".,;)")
    return cleaned if is_valid_email(cleaned) else None


def clean_phone(phone: Optional[str]) -> Optional[str]:
    """Strip formatting — return None if digit count out of range."""
    if not phone:
        return None
    stripped = phone.strip()
    digits = re.sub(r"\D", "", stripped)
    if not (7 <= len(digits) <= 15):
        return None
    return "+" + digits if stripped.startswith("+") else digits
