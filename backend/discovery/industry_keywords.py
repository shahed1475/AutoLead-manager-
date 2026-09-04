"""
industry_keywords.py — keyword groups the Discovery Planner uses for its
rule-based (no-LLM) intent classification.

Deliberately small and extendable, not an exhaustive taxonomy — the LLM
fallback (planner.py) handles anything these lists don't match. Kept
consistent with backend/scoring/lead_scorer.py::HIGH_VALUE_NICHES, which
this module extends rather than duplicates independently.
"""
from ..scoring.lead_scorer import HIGH_VALUE_NICHES

# Case-insensitive substring match against the user's query/niche text.
# Seeded from HIGH_VALUE_NICHES plus the additional local-business examples
# given in the Phase 1 brief.
LOCAL_BUSINESS_KEYWORDS: list[str] = sorted(set(HIGH_VALUE_NICHES) | {
    "dentist", "restaurant", "salon", "spa", "clinic", "contractor",
    "medical practice", "plumber", "electrician", "chiropractor",
    "veterinary", "vet clinic", "bakery", "cafe", "barber",
    "auto repair", "car dealership", "florist", "daycare",
})

STARTUP_TECH_KEYWORDS: list[str] = [
    "startup", "saas", "software", "ai", "artificial intelligence",
    "technology", "tech company", "tech startup", "b2b", "developer",
    "platform", "app", "fintech", "biotech", "software company",
    "machine learning", "cloud", "api company",
]
