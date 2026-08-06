"""
outreach_domain.py — business-domain knowledge for outreach message generation.

Root-cause context: outreach messages were generic and frequently hallucinated
specifics (fake case studies, fake platform references) because the prompt gave
the model nothing real to write from beyond a business name and niche. This
module gives it real, curated domain expertise instead — grounded in the
niches actually present in this app's lead data (restaurants, bakeries, real
estate — see `leads.niche`, which is free-text/typo-prone, hence the fuzzy
keyword matching below rather than an exact enum).

Public API
──────────
  get_domain_profile(niche: str) -> DomainProfile
  build_domain_context(niche: str) -> str   — formatted block for prompt injection
"""
from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class DomainProfile:
    label:            str
    pain_points:      List[str] = field(default_factory=list)
    value_angles:     List[str] = field(default_factory=list)
    tone_notes:       str = ""


_PROFILES: dict = {
    "food_service": DomainProfile(
        label="restaurant / cafe / bakery",
        pain_points=[
            "inconsistent online ordering or delivery-platform dependence eating into margins",
            "low repeat-visit rate — no simple way to bring first-time customers back",
            "menu/hours/photos out of date or inconsistent across Google, Instagram, and their own site",
            "no easy way to capture reviews at the moment customers are happiest",
        ],
        value_angles=[
            "a simple, owned way to reach past customers directly instead of paying delivery-platform commission every time",
            "turning foot traffic into a repeatable channel (SMS/WhatsApp list, loyalty nudge) instead of one-off visits",
            "cleaning up and syncing their online presence so it matches what the food/space actually looks like",
        ],
        tone_notes="Warm, food-loving, specific about the cuisine/setting if known. Never claim a stat or case study you weren't given.",
    ),
    "real_estate": DomainProfile(
        label="real estate agency / agent",
        pain_points=[
            "lead response time — the first agent to reply usually wins the client, and manual follow-up is slow",
            "listings not getting enough qualified inbound interest relative to time spent producing them",
            "no consistent nurture for past clients who could refer or return for their next move",
            "brand/personal presence not reflecting the volume or quality of deals they actually close",
        ],
        value_angles=[
            "faster, more consistent lead response and follow-up without more manual work",
            "turning past clients into a referral engine instead of a one-time transaction",
            "a sharper, more credible online presence that matches their actual track record",
        ],
        tone_notes="Direct, credibility-driven, respects that agents are busy and deal-focused. No fluff, no hype adjectives.",
    ),
    "retail": DomainProfile(
        label="retail / e-commerce",
        pain_points=[
            "foot traffic or site visits not converting to purchases at the rate they'd expect",
            "no simple retargeting or win-back flow for people who almost bought",
            "inventory/promotions not visible enough where their actual customers are looking",
        ],
        value_angles=[
            "capturing near-misses (cart abandons, browsers) instead of losing them silently",
            "a simple, low-effort way to bring past buyers back for the next purchase",
        ],
        tone_notes="Practical, ROI-oriented, concrete.",
    ),
    "professional_services": DomainProfile(
        label="professional services / consulting / agency",
        pain_points=[
            "referrals are the main growth channel but there's no system behind them",
            "expertise isn't visible online in a way that builds trust before the first call",
            "inbound inquiries are inconsistent, making pipeline hard to predict",
        ],
        value_angles=[
            "a more predictable, less referral-dependent way to fill the pipeline",
            "positioning their existing expertise so prospects trust them before the first conversation",
        ],
        tone_notes="Professional, peer-to-peer register — write as if talking to a fellow business owner, not a mass-market consumer.",
    ),
    "health_beauty": DomainProfile(
        label="healthcare / clinic / salon / fitness",
        pain_points=[
            "no-shows and last-minute cancellations with no automated way to fill the gap",
            "new-client acquisition relies heavily on walk-ins or word of mouth",
            "booking/rebooking is manual and easy for clients to forget",
        ],
        value_angles=[
            "automated rebooking/reminder flow that reduces no-shows without extra front-desk work",
            "a simple way to turn one-time visitors into recurring clients",
        ],
        tone_notes="Warm, personal, trust-building — this category is relationship-driven.",
    ),
    "generic": DomainProfile(
        label="local business",
        pain_points=[
            "inconsistent follow-up with interested prospects",
            "online presence not reflecting the actual quality of the business",
        ],
        value_angles=[
            "a simple way to follow up consistently without more manual work",
            "sharpening how the business shows up online to match what it actually delivers",
        ],
        tone_notes="Stay general and honest about not knowing specifics — do not invent detail to sound personalized.",
    ),
}

# Ordered so more specific categories are checked before "generic" catches everything.
_KEYWORDS: List[tuple] = [
    ("food_service",           ["restaurant", "resturent", "cafe", "café", "bakery", "bekari",
                                 "bar", "bistro", "diner", "eatery", "food", "catering"]),
    ("real_estate",            ["real estate", "realty", "realtor", "property", "properties"]),
    ("retail",                 ["retail", "shop", "store", "boutique", "ecommerce", "e-commerce"]),
    ("professional_services",  ["consulting", "consultant", "agency", "law firm", "lawyer",
                                 "accounting", "accountant", "financial", "marketing agency"]),
    ("health_beauty",          ["clinic", "dental", "dentist", "salon", "spa", "gym", "fitness",
                                 "wellness", "beauty", "barber"]),
]


def get_domain_profile(niche: str) -> DomainProfile:
    """Fuzzy-match free-text niche (typos included — see leads.niche) to a domain profile."""
    normalized = (niche or "").strip().lower()
    if not normalized:
        return _PROFILES["generic"]

    for key, keywords in _KEYWORDS:
        if any(kw in normalized for kw in keywords):
            return _PROFILES[key]

    return _PROFILES["generic"]


def build_domain_context(niche: str) -> str:
    """Formatted block for injection into a generation prompt."""
    profile = get_domain_profile(niche)
    pain_points  = "\n".join(f"  - {p}" for p in profile.pain_points)
    value_angles = "\n".join(f"  - {v}" for v in profile.value_angles)
    return f"""DOMAIN EXPERTISE ({profile.label}):
Common real pain points in this category (pick ONE that's plausible for this specific lead — do not claim all of them):
{pain_points}
Value angles that resonate in this category (pick ONE):
{value_angles}
Tone guidance: {profile.tone_notes}"""
