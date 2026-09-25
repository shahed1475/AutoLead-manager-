"""
audit/lead_audit.py — a one-page, verifiable audit of a lead's online presence.

Every check is deterministic (a field on the lead, or a fact read from the
business's own homepage) and carries its source and the time it was checked.
When the data isn't there, the check is 'unknown' — the audit never reports a
problem it didn't see. No AI, no guessing.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .. import database as db
from ..enrichment.website_analyzer import analyze_website

PASS, ISSUE, UNKNOWN = "pass", "issue", "unknown"
GOOD_RATING = 4.0
ENOUGH_REVIEWS = 20

_VIEWPORT = re.compile(r"<meta[^>]+name=[\"']viewport[\"']", re.I)
_WHATSAPP = re.compile(r"wa\.me/|api\.whatsapp\.com|whatsapp://|chat\.whatsapp\.com", re.I)
_BOOKING = re.compile(r"\b(book(ing)?|appointment|reserv(e|ation)|schedule)\b|calendly\.com|setmore\.com|simplybook", re.I)


def _check(key: str, label: str, status: str, detail: str, source: Optional[str],
           checked_at: str, tip: str = "") -> Dict[str, Any]:
    return {"key": key, "label": label, "status": status, "detail": detail,
            "source": source, "checked_at": checked_at, "tip": tip if status == ISSUE else ""}


def _listing_source(lead: Dict[str, Any]) -> str:
    return f"Business listing ({lead['source']})" if lead.get("source") else "Business listing"


def run_checks(lead: Dict[str, Any], site: Optional[Dict[str, Any]], checked_at: str) -> List[Dict[str, Any]]:
    """The audit's checks for one lead. `site` is analyze_website()'s result
    (None when the lead has no website)."""
    listing = _listing_source(lead)
    website = (lead.get("website") or "").strip()
    out: List[Dict[str, Any]] = []

    out.append(_check("has_website", "Has a website", PASS if website else ISSUE,
                      website or "No website on the business listing", listing, checked_at,
                      "A simple website lets customers find opening hours, prices and a way to contact you."))

    reachable = bool(site) and not site.get("error")
    src = (site or {}).get("url") or website or None
    if website:
        out.append(_check("site_reachable", "Website opens", PASS if reachable else ISSUE,
                          "Homepage loaded" if reachable else f"Homepage did not load ({(site or {}).get('error') or 'no response'})",
                          src, checked_at, "Customers who can't open the site go to a competitor."))

    def site_check(key: str, label: str, ok: bool, yes: str, no: str, tip: str) -> None:
        if not reachable:
            out.append(_check(key, label, UNKNOWN, "Couldn't check — the website didn't load" if website
                              else "No website to check", src, checked_at))
        else:
            out.append(_check(key, label, PASS if ok else ISSUE, yes if ok else no, src, checked_at, tip))

    html = (site or {}).get("raw_html") or ""
    ctas = " ".join((site or {}).get("cta_buttons") or [])
    site_check("https", "Secure connection (HTTPS)", bool((site or {}).get("has_ssl")),
               "Site uses HTTPS", "Site doesn't use HTTPS — browsers show it as 'Not secure'",
               "Browsers warn visitors about sites without HTTPS.")
    site_check("mobile_ready", "Works on phones", bool(_VIEWPORT.search(html)),
               "Page is set up for mobile screens", "Page isn't set up for mobile screens",
               "Most visitors come from phones; a mobile layout keeps them on the page.")
    site_check("page_title", "Page title", bool((site or {}).get("page_title")),
               f"“{(site or {}).get('page_title', '')}”", "Homepage has no title",
               "The title is what Google shows in search results.")
    site_check("meta_description", "Search description", bool((site or {}).get("meta_description")),
               "Has a description for search results", "No description for search results",
               "A short description improves how the site appears on Google.")
    site_check("contact_form", "Contact form", bool((site or {}).get("has_contact_form")),
               "Has a contact form", "No contact form on the homepage",
               "A form captures enquiries outside opening hours.")
    site_check("phone_on_site", "Phone number on site", bool((site or {}).get("has_phone_on_page")),
               "Phone number is shown", "No phone number on the homepage",
               "Customers ready to buy want to call straight away.")
    site_check("whatsapp_link", "WhatsApp chat link", bool(_WHATSAPP.search(html)),
               "Has a WhatsApp chat link", "No WhatsApp chat link",
               "A click-to-chat WhatsApp button turns visitors into conversations.")
    site_check("online_booking", "Online booking", bool(_BOOKING.search(ctas) or re.search(r"calendly\.com|setmore\.com|simplybook", html, re.I)),
               "Visitors can book online", "No way to book online",
               "Online booking saves staff time and captures customers who don't call.")
    social = (site or {}).get("social_media_links") or []
    site_check("social_links", "Social media links", bool(social),
               f"Links to {len(social)} social profile(s)", "No links to social media",
               "Social links show the business is active and build trust.")

    rating = lead.get("rating")
    if rating is None:
        out.append(_check("rating", "Google rating", UNKNOWN, "No rating on the listing", listing, checked_at))
    else:
        r = float(rating)
        out.append(_check("rating", "Google rating", PASS if r >= GOOD_RATING else ISSUE, f"{r:.1f} stars",
                          listing, checked_at, "Asking happy customers for reviews lifts the rating."))

    reviews = lead.get("reviews_count") if lead.get("reviews_count") is not None else lead.get("review_count")
    if reviews is None:
        out.append(_check("reviews", "Number of reviews", UNKNOWN, "No review count on the listing", listing, checked_at))
    else:
        n = int(reviews)
        out.append(_check("reviews", "Number of reviews", PASS if n >= ENOUGH_REVIEWS else ISSUE, f"{n} reviews",
                          listing, checked_at, "An automatic review request after each visit builds reviews steadily."))
    return out


def summarize(checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    passed = sum(1 for c in checks if c["status"] == PASS)
    issues = sum(1 for c in checks if c["status"] == ISSUE)
    unknown = sum(1 for c in checks if c["status"] == UNKNOWN)
    known = passed + issues
    return {"score": round(100 * passed / known) if known else None,
            "passed": passed, "issues": issues, "unknown": unknown}


def _view(row: Dict[str, Any], lead: Dict[str, Any]) -> Dict[str, Any]:
    checks = json.loads(row["checks_json"] or "[]")
    return {"id": row["id"], "lead_id": row["lead_id"], "created_at": row["created_at"],
            "business_name": lead.get("business_name"), "website": lead.get("website"),
            "city": lead.get("city"), "niche": lead.get("niche"),
            "checks": checks, **summarize(checks)}


async def build_audit(lead_id: int) -> Optional[Dict[str, Any]]:
    """Run the audit now and save it. None when the lead doesn't exist."""
    lead = await db.get_lead_by_id(lead_id)
    if not lead:
        return None
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    site = await analyze_website(lead["website"]) if (lead.get("website") or "").strip() else None
    checks = run_checks(lead, site, checked_at)
    row = await db.save_lead_audit(lead_id, summarize(checks)["score"], json.dumps(checks))
    return _view(row, lead)


async def latest_audit(lead_id: int) -> Optional[Dict[str, Any]]:
    lead = await db.get_lead_by_id(lead_id)
    row = await db.get_latest_lead_audit(lead_id) if lead else None
    return _view(row, lead) if row else None
