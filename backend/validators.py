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


def clean_business_name(name: Optional[str]) -> Optional[str]:
    """Trim and collapse internal whitespace. Never rejects — callers decide emptiness."""
    if not name:
        return name
    return re.sub(r"\s+", " ", name.strip())


def normalize_website(url: Optional[str]) -> Optional[str]:
    """
    Normalize a scraped website URL: ensure a scheme, lowercase scheme+host,
    strip a trailing slash. Returns None for empty/unusable input.
    """
    if not url:
        return None
    cleaned = url.strip()
    if not cleaned:
        return None
    if not re.match(r"^https?://", cleaned, re.I):
        cleaned = "https://" + cleaned
    match = re.match(r"^(https?)://([^/]+)(/.*)?$", cleaned, re.I)
    if not match:
        return cleaned
    scheme, host, path = match.group(1).lower(), match.group(2).lower(), match.group(3) or ""
    path = path.rstrip("/")
    return f"{scheme}://{host}{path}"


def clean_country(country: Optional[str]) -> Optional[str]:
    """Trim whitespace and strip stray leading/trailing punctuation from a country string."""
    if not country:
        return None
    cleaned = country.strip().strip(",.;")
    return cleaned or None
