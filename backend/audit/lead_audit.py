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
    site_check("whatsapp_link", "WhatsApp chat link", bool((site or {}).get("has_whatsapp_link") or _WHATSAPP.search(html)),
               "Has a WhatsApp chat link or chat button", "No WhatsApp chat link",
               "A click-to-chat WhatsApp button turns visitors into conversations.")
    site_check("online_booking", "Online booking", bool((site or {}).get("has_booking_link") or _BOOKING.search(ctas)),
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


# Research statuses (research_agent/evidence.py) in the words a person reads.
_EMAIL_NOTES = {
    "SECURE_WEB_FORM": "Uses a contact form on their website; no public email address.",
    "NOT_FOUND_AFTER_SEARCH": "Not published: searched their website and listings.",
    "NOT_FOUND": "Not found.",
    "UNCONFIRMED": "Found but not confirmed yet.",
}


def _email_note(value: Optional[str], status: Optional[str]) -> Optional[str]:
    if value:
        return None if (status or "FOUND") == "FOUND" else _EMAIL_NOTES.get(status or "")
    return _EMAIL_NOTES.get(status or "", "Not published.")


def _details(site: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """What the audit read off the homepage, kept with the audit."""
    site = site or {}
    return {
        "final_url": site.get("url"),
        "page_title": site.get("page_title") or None,
        "meta_description": site.get("meta_description") or None,
        "word_count": site.get("word_count") or 0,
        "image_count": site.get("image_count") or 0,
        "headings": (site.get("all_headings") or [])[:8],
        "technology": site.get("technology") or [],
        "social_profiles": site.get("social_profiles") or [],
        "emails_on_page": site.get("emails_on_page") or [],
        "phones_on_page": site.get("phones_on_page") or [],
    }


def _business(lead: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("business_name", "niche", "address", "city", "country", "phone", "email", "website",
            "rating", "source", "created_at")
    out = {k: lead.get(k) for k in keys}
    out["reviews"] = lead.get("reviews_count") if lead.get("reviews_count") is not None else lead.get("review_count")
    return out


def _contacts(bundle: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not bundle:
        return {"researched": False, "people": [], "primary": None,
                "business_email": None, "business_email_note": None}
    r = bundle["result"]
    people = [{"name": p["name"], "title": p.get("title"), "source_url": p.get("source_url"),
               "is_primary": bool(p.get("is_primary"))} for p in bundle["people"]]
    primary = None
    if r.get("management_contact_name"):
        phone = r.get("management_phone")
        primary = {
            "name": r["management_contact_name"], "title": r.get("management_title"),
            "phone": phone,
            "phone_is_business_line": bool(phone) and (r.get("management_phone_type") == "BUSINESS"
                                                       or phone == r.get("business_phone")),
            "email": r.get("management_email"),
            "email_note": _email_note(r.get("management_email"), r.get("management_email_status")),
        }
    return {"researched": True, "people": people, "primary": primary,
            "business_email": r.get("business_email"),
            "business_email_note": _email_note(r.get("business_email"), r.get("business_email_status")),
            "business_phone": r.get("business_phone"), "researched_at": r.get("created_at")}


_SUMMARY_KEYS = ("business_summary", "target_audience", "service_level", "brand_positioning", "growth_potential")


async def _report(row: Dict[str, Any], lead: Dict[str, Any]) -> Dict[str, Any]:
    checks = json.loads(row["checks_json"] or "[]")
    try:
        details = json.loads(row.get("details_json") or "null") or _details(None)
    except ValueError:
        details = _details(None)
    enriched = await db.get_enriched_data(lead["id"]) or {}
    summary = {k: enriched.get(k) for k in _SUMMARY_KEYS if enriched.get(k)}
    return {"id": row["id"], "lead_id": row["lead_id"], "created_at": row["created_at"],
            "business_name": lead.get("business_name"), "website": lead.get("website"),
            "city": lead.get("city"), "niche": lead.get("niche"),
            "business": _business(lead), "contacts": _contacts(await db.get_lead_research_bundle(lead["id"])),
            "details": details, "summary": summary or None,       # summary is AI-written: the page labels it
            "checks": checks, **summarize(checks)}


async def build_audit(lead_id: int) -> Optional[Dict[str, Any]]:
    """Run the audit now and save it. None when the lead doesn't exist."""
    lead = await db.get_lead_by_id(lead_id)
    if not lead:
        return None
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    site = await analyze_website(lead["website"]) if (lead.get("website") or "").strip() else None
    checks = run_checks(lead, site, checked_at)
    row = await db.save_lead_audit(lead_id, summarize(checks)["score"], json.dumps(checks),
                                   json.dumps(_details(site)))
    return await _report(row, lead)


async def latest_audit(lead_id: int) -> Optional[Dict[str, Any]]:
    lead = await db.get_lead_by_id(lead_id)
    row = await db.get_latest_lead_audit(lead_id) if lead else None
    return await _report(row, lead) if row else None


# ── Audit many leads (Leads table "Audit" button) ─────────────────────────
MAX_BATCH = 100
_batch = {"running": False, "done": 0, "total": 0, "failed": 0}


def batch_status() -> Dict[str, Any]:
    return dict(_batch)


async def run_batch(lead_ids: List[int]) -> None:
    """Audit the leads one after another (each fetches one homepage)."""
    ids = list(dict.fromkeys(int(i) for i in lead_ids))[:MAX_BATCH]
    _batch.update(running=True, done=0, total=len(ids), failed=0)
    try:
        for lid in ids:
            try:
                await build_audit(lid)
            except Exception:  # noqa: BLE001 — one bad site mustn't stop the batch
                _batch["failed"] += 1
            _batch["done"] += 1
    finally:
        _batch["running"] = False
