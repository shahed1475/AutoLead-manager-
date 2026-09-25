"""
industry_presets.py — ready-made starting points for Company DNA, by the kind
of business that uses HOM (the seller, not its prospects). Labels match the
client onboarding's sector list (frontend/src/portal/auth/Onboarding.jsx).

A preset only fills in *typical* services, customers and problems for the
owner to edit. It makes no claims — "why choose us" is left for the owner,
because the AI must never invent a business's strengths.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

PRESETS: List[Dict[str, Any]] = [
    {"id": "marketing", "label": "Marketing & advertising",
     "services": ["Social media management", "Facebook and Instagram ads", "Google Business Profile optimisation", "Content creation"],
     "customers": ["restaurants", "clinics", "salons", "real estate agencies", "retail shops"],
     "problems": ["Few enquiries from social media", "Ads that spend money without bringing customers", "Hard to find on Google Maps", "Inactive or outdated social pages"]},
    {"id": "web_design", "label": "Web design & development",
     "services": ["Business websites", "Landing pages", "Shopify stores", "Website redesigns"],
     "customers": ["restaurants", "clinics", "law firms", "hotels", "local shops"],
     "problems": ["No website, or one that doesn't work on phones", "No way to book or order online", "Slow or insecure (no HTTPS) website", "Website that doesn't show up on Google"]},
    {"id": "it_software", "label": "IT services & software",
     "services": ["Custom software", "Business automation", "IT support", "Cloud setup"],
     "customers": ["manufacturers", "logistics companies", "schools", "clinics", "retail chains"],
     "problems": ["Manual work in spreadsheets", "Systems that don't talk to each other", "No backups or security", "Slow response to customers"]},
    {"id": "accounting", "label": "Accounting & finance",
     "services": ["Bookkeeping", "Tax filing", "Payroll", "Company registration"],
     "customers": ["small businesses", "startups", "shops", "freelancers", "importers"],
     "problems": ["Late or missed tax filings", "Messy books", "No time for payroll", "Unclear cash flow"]},
    {"id": "real_estate", "label": "Real estate",
     "services": ["Property sales", "Rentals", "Property management", "Valuations"],
     "customers": ["property owners", "developers", "expat families", "companies looking for offices"],
     "problems": ["Empty units", "Slow sales", "Tenants hard to manage", "No reliable local agent"]},
    {"id": "healthcare", "label": "Healthcare & clinics",
     "services": ["Consultations", "Diagnostics", "Health check-up packages", "Home care"],
     "customers": ["families", "companies (staff health)", "schools", "elderly care homes"],
     "problems": ["Long waits for appointments", "No online booking", "No follow-up after visits"]},
    {"id": "education", "label": "Education & training",
     "services": ["Courses", "Coaching", "Corporate training", "Exam preparation"],
     "customers": ["students", "parents", "companies", "job seekers"],
     "problems": ["Skills gap in staff", "Poor exam results", "No flexible online option"]},
    {"id": "hospitality", "label": "Hospitality & restaurants",
     "services": ["Catering", "Event hosting", "Corporate lunches", "Room bookings"],
     "customers": ["companies", "event planners", "wedding couples", "travel agencies"],
     "problems": ["Unreliable catering for events", "Last-minute bookings", "No corporate rate"]},
    {"id": "recruitment", "label": "Recruitment & HR",
     "services": ["Recruitment", "Staffing", "HR outsourcing", "Payroll"],
     "customers": ["growing companies", "factories", "hospitals", "IT firms"],
     "problems": ["Hard-to-fill roles", "Slow hiring", "High staff turnover", "No HR team"]},
    {"id": "construction", "label": "Construction & trades",
     "services": ["Renovation", "Interior design", "Electrical and plumbing", "Maintenance contracts"],
     "customers": ["homeowners", "offices", "restaurants", "property developers"],
     "problems": ["Unreliable contractors", "Projects running late", "No maintenance plan"]},
]

_BY_ID = {p["id"]: p for p in PRESETS}
_BY_LABEL = {p["label"].lower(): p for p in PRESETS}


def get_preset(preset_id: str) -> Optional[Dict[str, Any]]:
    return _BY_ID.get(preset_id)


def preset_for_sector(sector: Optional[str]) -> Optional[Dict[str, Any]]:
    return _BY_LABEL.get((sector or "").strip().lower())


def _bullets(items: List[str]) -> str:
    return "\n".join(f"- {i}" for i in items if i and i.strip())


def build_dna(preset_id: str, business_name: str, city: str = "", services: Optional[List[str]] = None,
              extra: str = "") -> str:
    """A Company DNA draft from a preset plus the owner's own facts. Raises
    ValueError for an unknown preset."""
    p = get_preset(preset_id)
    if not p:
        raise ValueError(f"unknown preset {preset_id!r}")
    name = (business_name or "").strip() or "We"
    where = f", based in {city.strip()}" if (city or "").strip() else ""
    sells = [s.strip() for s in (services or []) if s and s.strip()] or p["services"]
    parts = [
        "## Who we are",
        f"{name} is a {p['label'].lower()} business{where}.",
        "",
        "## What we sell",
        _bullets(sells),
        "",
        "## Ideal customers",
        _bullets(p["customers"]),
        "",
        "## Problems we solve",
        _bullets(p["problems"]),
        "",
        "## Why choose us",
        "",                       # left for the owner: the AI must not invent strengths
    ]
    if (extra or "").strip():
        parts += ["", "## More about us", extra.strip()]
    return "\n".join(parts) + "\n"
