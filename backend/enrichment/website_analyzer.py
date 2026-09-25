"""
website_analyzer.py — Deep website signal extraction and structural scoring.

Two public functions:

  analyze_website(url, timeout) -> dict
      Async. Fetches the page and extracts every signal needed for AI
      enrichment: title, meta, headings, clean body text, phone/email
      presence, CTAs, social links, image count, word count, SSL.

  score_website(website_data) -> dict
      Sync. Consumes the output of analyze_website() and produces a
      0-25 quality score plus four categorised gap lists (issues,
      conversion_gaps, seo_gaps, pitch_angles) used downstream by the
      AI enricher to generate highly targeted outreach.
"""
import logging
import re
import time
from typing import Any, Dict, List, Set

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── In-process TTL cache ──────────────────────────────────────────────────────
# The Redis cache module was removed from this app; without any replacement,
# every enrichment call re-fetched and re-scored the same domain from scratch.
# This lightweight cache avoids that for the common case of re-enriching or
# re-scoring the same lead/domain within a run — it is not shared across
# processes and is intentionally simple (no eviction policy beyond TTL).
_CACHE_TTL_SECONDS = 6 * 3600
_analyze_cache: Dict[str, tuple] = {}  # normalized_url -> (expires_at, result)

# ── Limits ────────────────────────────────────────────────────────────────────

_MAX_TEXT_CHARS = 3_000
_MAX_HEADINGS   = 20
_MAX_CTAS       = 15

# ── Network headers ───────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",   # only what httpx decodes without extra packages (no brotli here)
}

# ── Compiled patterns ─────────────────────────────────────────────────────────

_SOCIAL_RE = re.compile(
    r"(facebook|instagram|linkedin|twitter|x\.com|tiktok|youtube|pinterest)\.com",
    re.I,
)

# Covers: (123) 456-7890 · 123-456-7890 · +1 234 567 8901 · +44 7911 123456
_PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})"
    r"|\+\d{7,15}",
    re.M,
)

_EMAIL_RE = re.compile(
    r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b"
)

# Classes / text patterns that suggest a call-to-action element
_CTA_CLASS_RE = re.compile(
    r"\b(btn|button|cta|call.to.action|get.started|buy|order|shop|"
    r"sign.?up|contact|book|reserve|learn.more|try|start|apply|download|"
    r"quote|schedule|request|join|subscribe)\b",
    re.I,
)

# Text that looks like a CTA label in its own right
_CTA_TEXT_RE = re.compile(
    r"\b(get\s+(?:a\s+)?(?:free\s+)?quote|book\s+(?:a\s+)?(?:free\s+)?|"
    r"contact\s+us|call\s+(?:us\s+)?now|schedule|request|get\s+started|"
    r"start\s+(?:your\s+)?|sign\s+up|learn\s+more|try\s+(?:it\s+)?(?:free)?|"
    r"download|buy\s+now|order\s+now|shop\s+now|subscribe|join\s+(?:us\s+)?(?:now)?)\b",
    re.I,
)

# Words so generic they aren't real CTAs
_GENERIC_TEXT: Set[str] = {
    "submit", "ok", "cancel", "close", "menu", "home", "about", "more",
    "read more", "click here", "here", "link", "continue", "next", "back",
    "send", "go", "yes", "no", "search", "reset", "enter", "loading",
    "click", "log in", "login", "sign in", "register",
}


# ── Private helpers ───────────────────────────────────────────────────────────

def _normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


def _clean_text(soup: BeautifulSoup) -> str:
    """Strip boilerplate, return first _MAX_TEXT_CHARS of readable prose."""
    for tag in soup(["script", "style", "nav", "header", "footer",
                     "noscript", "iframe", "svg", "aside", "figure"]):
        tag.decompose()

    body = (
        soup.find("main")
        or soup.find("article")
        or soup.find(id=re.compile(r"content|main|hero|about|body", re.I))
        or soup.find("body")
        or soup
    )
    lines = [t.strip() for t in body.stripped_strings if len(t.strip()) > 15]
    return " ".join(lines)[:_MAX_TEXT_CHARS]


def _extract_headings(soup: BeautifulSoup) -> List[str]:
    return [
        h.get_text(strip=True)
        for h in soup.find_all(["h1", "h2", "h3"])
        if h.get_text(strip=True)
    ][:_MAX_HEADINGS]


def _has_contact_form(soup: BeautifulSoup) -> bool:
    for form in soup.find_all("form"):
        if form.find(["input", "textarea"],
                     attrs={"type": re.compile(r"^(text|email|tel)$", re.I)}):
            return True
    return False


def _has_phone(text: str, soup: BeautifulSoup) -> bool:
    if _PHONE_RE.search(text):
        return True
    return bool(soup.find("a", href=re.compile(r"^tel:", re.I)))


def _has_email_on_page(text: str, soup: BeautifulSoup) -> bool:
    if _EMAIL_RE.search(text):
        return True
    return bool(soup.find("a", href=re.compile(r"^mailto:", re.I)))


_WHATSAPP_RE = re.compile(
    r"wa\.me/|api\.whatsapp\.com|whatsapp://|chat\.whatsapp\.com|wa\.link/"
    r"|plugins/(?:wp-whatsapp|click-to-chat|creame-whatsapp-me|wp-whatsapp-chat|whatsapp-chat)"
    r"|njt-whatsapp|ht-ctc|joinchat",
    re.I,
)
_BOOKING_SITE_RE = re.compile(r"calendly\.com|setmore\.com|simplybook|acuityscheduling|zocdoc|practo\.com|fresha\.com|booksy\.com", re.I)
_BOOKING_CTA_RE = re.compile(r"\b(book(ing)?|appointment|reserv(e|ation)|schedule)\b", re.I)


def _social_profile_urls(soup: BeautifulSoup, limit: int = 8) -> List[str]:
    """Profile links (not share buttons), first seen first."""
    out: List[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if _SOCIAL_RE.search(href) and not re.search(r"/sharer|/share\?|intent/tweet|/shareArticle", href, re.I):
            if href not in out:
                out.append(href)
        if len(out) >= limit:
            break
    return out


def _link_values(soup: BeautifulSoup, scheme: str, limit: int = 5) -> List[str]:
    """The addresses/numbers in mailto:/tel: links, de-duplicated."""
    out: List[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith(scheme):
            v = href[len(scheme):].split("?")[0].strip()
            v = re.sub(r"[^\d+]", "", v) if scheme == "tel:" else v.lower()
            if v and v not in out:
                out.append(v)
        if len(out) >= limit:
            break
    return out


def _has_whatsapp(html: str) -> bool:
    """A click-to-chat link, or a WhatsApp chat widget whose button is built by
    JavaScript (its plugin assets are in the page even though the link isn't)."""
    return bool(_WHATSAPP_RE.search(html or ""))


def _has_booking(html: str, ctas: List[str]) -> bool:
    return bool(_BOOKING_CTA_RE.search(" ".join(ctas or [])) or _BOOKING_SITE_RE.search(html or ""))


def _extract_ctas(soup: BeautifulSoup) -> List[str]:
    """Return unique, meaningful CTA labels (buttons, submit inputs, CTA links)."""
    candidates: List[str] = []

    # <button> elements
    for el in soup.find_all("button"):
        t = el.get_text(strip=True)
        if t and t.lower() not in _GENERIC_TEXT and 3 <= len(t) <= 80:
            candidates.append(t)

    # <input type="submit|button">
    for el in soup.find_all("input", attrs={"type": re.compile(r"^(submit|button)$", re.I)}):
        t = (el.get("value") or "").strip()
        if t and t.lower() not in _GENERIC_TEXT and 3 <= len(t) <= 80:
            candidates.append(t)

    # <a> links that look like CTAs (by class or text)
    for el in soup.find_all("a"):
        cls  = " ".join(el.get("class", []))
        t    = el.get_text(strip=True)
        if not t or len(t) < 3 or len(t) > 80 or t.lower() in _GENERIC_TEXT:
            continue
        if _CTA_CLASS_RE.search(cls) or _CTA_TEXT_RE.search(t):
            candidates.append(t)

    # Deduplicate (case-insensitive), preserve order
    seen: Set[str] = set()
    result: List[str] = []
    for c in candidates:
        lk = c.lower()
        if lk not in seen:
            seen.add(lk)
            result.append(c[:80])
        if len(result) >= _MAX_CTAS:
            break

    return result


def _extract_social_links(soup: BeautifulSoup) -> List[str]:
    """Return sorted list of social platform names found in any <a href>."""
    found: Set[str] = set()
    for a in soup.find_all("a", href=True):
        m = _SOCIAL_RE.search(a["href"])
        if m:
            platform = m.group(1).lower()
            if platform == "x.com":
                platform = "twitter"
            found.add(platform)
    return sorted(found)


# ── Public: fetch + extract ───────────────────────────────────────────────────

async def analyze_website(url: str, timeout: int = 10) -> Dict[str, Any]:
    """
    Fetch a business website and return a comprehensive signal dict.

    All keys are always present; ``error`` is None on success.
    ``has_social_links`` and ``page_text`` are backward-compat aliases.
    """
    norm    = _normalize_url(url)
    has_ssl = norm.startswith("https://")

    cached = _analyze_cache.get(norm)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    result: Dict[str, Any] = {
        # Network
        "url":               norm,
        "has_ssl":           has_ssl,
        "error":             None,
        "raw_html":          "",
        # Content
        "page_title":        "",
        "meta_description":  "",
        "all_headings":      [],
        "body_text":         "",
        "word_count":        0,
        "image_count":       0,
        # Contact / conversion
        "has_contact_form":  False,
        "has_phone_on_page": False,
        "has_email_on_page": False,
        "cta_buttons":       [],
        "social_profiles":   [],           # the profile URLs themselves (max 8)
        "emails_on_page":    [],           # mailto: addresses (max 5)
        "phones_on_page":    [],           # tel: numbers (max 5)
        "technology":        [],           # enrichment/technology.py — read from the full page
        "has_whatsapp_link": False,        # click-to-chat link or chat-widget plugin, anywhere on the page
        "has_booking_link":  False,        # booking CTA or a booking-service link
        # Social / trust
        "social_media_links": [],
        # Backward-compat aliases consumed by score_lead() + older callers
        "has_social_links":  False,
        "page_text":         "",
    }

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            verify=False,
            headers=_HEADERS,
        ) as client:
            resp = await client.get(norm)
            resp.raise_for_status()
            html = resp.text
    except httpx.HTTPStatusError as exc:
        result["error"] = f"HTTP {exc.response.status_code}"
        return result
    except httpx.TimeoutException:
        result["error"] = "timeout"
        return result
    except Exception as exc:
        result["error"] = str(exc)[:120]
        return result

    soup = BeautifulSoup(html, "lxml")
    # Capped small on purpose: tech-stack signatures (WordPress, Shopify, etc.)
    # all match in <head>/early <script>/<link> tags, so 40,000 chars is ample —
    # keeps the in-process _analyze_cache footprint bounded even though this
    # runs on every enrichment call regardless of the sales-intelligence toggle.
    result["raw_html"] = html[:40_000]  # used only for in-request tech-stack detection, never persisted

    # ── Title ──────────────────────────────────────────────────────────────────
    tag = soup.find("title")
    result["page_title"] = tag.get_text(strip=True)[:120] if tag else ""

    # ── Meta description ───────────────────────────────────────────────────────
    meta = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    result["meta_description"] = meta.get("content", "")[:250] if meta else ""

    # ── Headings ───────────────────────────────────────────────────────────────
    result["all_headings"] = _extract_headings(soup)

    # ── Body text ──────────────────────────────────────────────────────────────
    # _clean_text deletes header/footer/nav/scripts, so it gets its own copy —
    # the contact, social and CTA checks below need the whole page.
    body_text = _clean_text(BeautifulSoup(html, "lxml"))
    result["body_text"]  = body_text
    result["page_text"]  = body_text                  # backward-compat
    result["word_count"] = len(body_text.split())
    result["image_count"] = len(soup.find_all("img"))

    # ── Contact / conversion signals ───────────────────────────────────────────
    result["has_contact_form"]   = _has_contact_form(soup)
    result["has_phone_on_page"]  = _has_phone(body_text, soup)
    result["has_email_on_page"]  = _has_email_on_page(body_text, soup)
    result["cta_buttons"]        = _extract_ctas(soup)
    result["social_profiles"]    = _social_profile_urls(soup)
    result["emails_on_page"]     = _link_values(soup, "mailto:")
    result["phones_on_page"]     = _link_values(soup, "tel:")
    from .technology import detect as _detect_technology
    result["technology"]         = _detect_technology(html)
    result["has_whatsapp_link"]  = _has_whatsapp(html)
    result["has_booking_link"]   = _has_booking(html, result["cta_buttons"])

    # ── Social / trust signals ─────────────────────────────────────────────────
    social                        = _extract_social_links(soup)
    result["social_media_links"]  = social
    result["has_social_links"]    = bool(social)      # backward-compat

    logger.debug(
        "analyze_website: %s → wc=%d headings=%d ctas=%d social=%s ssl=%s",
        norm, result["word_count"], len(result["all_headings"]),
        len(result["cta_buttons"]), social, has_ssl,
    )
    _analyze_cache[norm] = (time.monotonic() + _CACHE_TTL_SECONDS, result)
    return result


# ── Public: structural scoring ────────────────────────────────────────────────

def score_website(website_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Produce a 0-25 website quality score and four categorised gap lists.

    Scoring rubric (max 25 pts):
      +5  SSL / HTTPS
      +5  contact form present
      +3  phone number on page
      +3  meta description > 50 chars
      +3  ≥ 2 social media links
      +3  CTA buttons found (non-generic)
      +3  word count > 300

    Returns
    -------
    dict with:
      website_quality_score : float  0-25
      issues                : list[str]  detected problems
      conversion_gaps       : list[str]  missing conversion elements
      seo_gaps              : list[str]  missing SEO elements
      pitch_angles          : list[str]  actionable sales opportunities
    """
    if website_data.get("error"):
        return {
            "website_quality_score": 0.0,
            "issues":          ["Website could not be fetched — may be down or block crawlers"],
            "conversion_gaps": ["Unable to verify contact options or conversion path"],
            "seo_gaps":        ["Website accessibility and indexability unknown"],
            "pitch_angles":    [
                "Help them establish a reliable, crawlable online presence",
                "Fix technical issues preventing customers from finding them online",
            ],
        }

    score:           float     = 0.0
    issues:          List[str] = []
    conversion_gaps: List[str] = []
    seo_gaps:        List[str] = []
    pitch_angles:    List[str] = []

    # ── +5 SSL ─────────────────────────────────────────────────────────────────
    if website_data.get("has_ssl"):
        score += 5
    else:
        issues.append("No HTTPS — site served over plain HTTP")
        seo_gaps.append("Missing HTTPS: Google penalises non-SSL sites and browsers show 'Not Secure'")
        pitch_angles.append(
            "Migrate them to HTTPS — quick win that boosts trust, SEO, and removes scary browser warnings"
        )

    # ── +5 contact form ────────────────────────────────────────────────────────
    if website_data.get("has_contact_form"):
        score += 5
    else:
        conversion_gaps.append("No contact form — visitors have no self-service way to reach them")
        pitch_angles.append(
            "Add a simple quote/contact form so leads can enquire 24/7 without calling"
        )

    # ── +3 phone on page ──────────────────────────────────────────────────────
    if website_data.get("has_phone_on_page"):
        score += 3
    else:
        issues.append("Phone number not visible in page content")
        conversion_gaps.append("No visible phone number — removes a key trust and conversion signal")

    # ── +3 meta description > 50 chars ────────────────────────────────────────
    meta = website_data.get("meta_description", "")
    if meta and len(meta) > 50:
        score += 3
    else:
        seo_gaps.append(
            "Missing or very short meta description — Google auto-generates an unhelpful snippet"
        )
        pitch_angles.append(
            "Write compelling meta descriptions for every page to dramatically improve click-through from search"
        )

    # ── +3 ≥2 social media links ──────────────────────────────────────────────
    social = website_data.get("social_media_links", [])
    if len(social) >= 2:
        score += 3
    elif len(social) == 1:
        issues.append(f"Only one social platform linked ({social[0]}) — limited social proof")
        pitch_angles.append(
            f"Expand their social presence beyond {social[0]} to build a stronger brand footprint"
        )
    else:
        issues.append("No social media links on website")
        conversion_gaps.append("No social proof — visitors can't verify legitimacy via social media")
        pitch_angles.append(
            "Set up and link their social profiles to build trust and create additional customer touchpoints"
        )

    # ── +3 non-generic CTA buttons ────────────────────────────────────────────
    ctas = website_data.get("cta_buttons", [])
    if ctas:
        score += 3
    else:
        conversion_gaps.append("No clear call-to-action buttons — visitors don't know what to do next")
        pitch_angles.append(
            "Add specific CTA buttons ('Get a Free Quote', 'Book a Consultation') to guide visitors to convert"
        )

    # ── +3 word count > 300 ───────────────────────────────────────────────────
    wc = website_data.get("word_count", 0)
    if wc > 300:
        score += 3
    elif wc > 100:
        issues.append(f"Thin content ({wc} words) — insufficient for SEO or visitor confidence")
        seo_gaps.append("Too little readable content for Google to understand and rank the page")
        pitch_angles.append("Build out their content with service pages, FAQs, and case studies")
    else:
        issues.append(f"Near-empty page detected ({wc} words) — possibly a JavaScript SPA or parked domain")
        seo_gaps.append("Search engines can't index content they can't read — site effectively invisible")
        pitch_angles.append(
            "Rebuild their site with substantive content — they're essentially invisible to search engines"
        )

    score = min(25.0, max(0.0, score))

    return {
        "website_quality_score": score,
        "issues":          issues          or ["No major structural issues detected"],
        "conversion_gaps": conversion_gaps or ["No obvious conversion gaps detected"],
        "seo_gaps":        seo_gaps        or ["No obvious SEO gaps detected"],
        "pitch_angles":    pitch_angles    or ["Site appears well-optimised — focus on personalisation"],
    }
