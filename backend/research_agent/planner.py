"""
planner.py — geographic expansion (brief §10): a single city needs no
expansion; a state/country/"worldwide" request expands into a bounded list
of representative cities (LLM-proposed, with a small deterministic
fallback), never an exhaustive enumeration.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..ai_brain import _call_llm_raw, _ollama_cfg
from .llm import _parse_json_object

logger = logging.getLogger(__name__)

_BROAD_SCOPE_HINTS = re.compile(
    r"\b(worldwide|global|nationwide|all\s+cities|entire\s+country)\b", re.I,
)

# Recognized broad-region names (case-insensitive exact match on the FIRST
# comma segment). Needed because a bare comma is not a reliable signal:
# "Abbeville, USA" and "California, USA" have the exact same shape, but one
# is a specific city and the other needs expansion — the real distinguisher
# is whether the location itself IS a known state/country, not punctuation.
_US_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa",
    "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan",
    "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island",
    "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont",
    "virginia", "washington", "west virginia", "wisconsin", "wyoming",
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il", "in",
    "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv",
    "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn",
    "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy",
}
_COUNTRIES = {
    "usa", "us", "united states", "united states of america", "uk", "united kingdom",
    "canada", "australia", "germany", "france", "spain", "italy", "mexico", "brazil",
    "india", "china", "japan", "south korea", "netherlands", "sweden", "norway",
    "denmark", "ireland", "new zealand", "south africa", "singapore", "uae",
    "united arab emirates", "saudi arabia", "egypt", "nigeria", "kenya", "argentina",
    "chile", "colombia", "portugal", "poland", "switzerland", "austria", "belgium",
}
_KNOWN_BROAD_REGIONS = _US_STATES | _COUNTRIES | {"worldwide", "global"}

_FALLBACK_CITIES: Dict[str, List[str]] = {
    "usa": ["New York, NY", "Los Angeles, CA", "Chicago, IL", "Houston, TX", "Phoenix, AZ"],
    "united states": ["New York, NY", "Los Angeles, CA", "Chicago, IL", "Houston, TX", "Phoenix, AZ"],
    "worldwide": ["New York, USA", "London, UK", "Toronto, Canada", "Sydney, Australia", "Berlin, Germany"],
    "uk": ["London", "Manchester", "Birmingham", "Leeds", "Glasgow"],
    "united kingdom": ["London", "Manchester", "Birmingham", "Leeds", "Glasgow"],
    "canada": ["Toronto, ON", "Vancouver, BC", "Montreal, QC", "Calgary, AB", "Ottawa, ON"],
    "australia": ["Sydney", "Melbourne", "Brisbane", "Perth", "Adelaide"],
}


@dataclass
class GeoTask:
    city: Optional[str]
    state: Optional[str]
    country: Optional[str]
    raw_location: str
    target_count: int


def _looks_like_single_city(location: str) -> bool:
    """True unless the location's first segment IS itself a recognized
    broad region (a US state, a country, or 'worldwide'/'global') — e.g.
    'Abbeville', 'Abbeville, USA', and 'Abbeville, LA, USA' are all a single
    city; 'California' and 'California, USA' are not, regardless of comma
    presence in either case (a bare comma is not a reliable signal — see
    _KNOWN_BROAD_REGIONS above)."""
    loc = (location or "").strip()
    if not loc:
        return False
    if _BROAD_SCOPE_HINTS.search(loc):
        return False
    first_segment = loc.split(",")[0].strip().lower()
    return first_segment not in _KNOWN_BROAD_REGIONS


def _parse_city_state_country(city_str: str, original_location: str) -> Dict[str, Optional[str]]:
    parts = [p.strip() for p in city_str.split(",") if p.strip()]
    if len(parts) >= 3:
        return {"city": parts[0], "state": parts[1], "country": parts[2]}
    if len(parts) == 2:
        # Could be "City, State" or "City, Country" — state.py has no reliable
        # way to tell without a lookup table; keep the second part as state
        # when the original location scope was a country (typical for
        # domestic expansion), else as country.
        return {"city": parts[0], "state": parts[1], "country": None}
    return {"city": parts[0] if parts else city_str, "state": None, "country": None}


async def expand_geography(
    location: str, target_count: int, max_units: int = 5, cfg: Optional[Dict[str, Any]] = None,
) -> List[GeoTask]:
    """Returns 1+ GeoTask, each with a target_count summing to (at most) the
    original target_count. Never fabricates a city — the LLM path is asked
    for real, well-known cities; the fallback path uses a small fixed list."""
    location = (location or "").strip()
    if not location:
        return []

    if _looks_like_single_city(location):
        parsed = _parse_city_state_country(location, location)
        return [GeoTask(city=parsed["city"], state=parsed["state"], country=parsed["country"],
                         raw_location=location, target_count=target_count)]

    cities = await _propose_cities(location, max_units, cfg)
    if not cities:
        # Single unexpanded task using the raw location as-is — better than
        # fabricating cities we're not confident about.
        return [GeoTask(city=None, state=None, country=location, raw_location=location, target_count=target_count)]

    # If the region being expanded is itself a known state or country, that's
    # a reliable fallback for any proposed city that came back bare (no
    # comma) — e.g. the fallback lists for "UK"/"Australia" are just city
    # names. Determined once from the *known* region set, not guessed per
    # city, so state and country are never both set to the same string.
    location_key = location.strip().lower()
    location_is_state = location_key in _US_STATES
    location_is_country = location_key in _COUNTRIES or location_key in {"worldwide", "global"}

    n = len(cities)
    per_city = max(1, target_count // n)
    tasks: List[GeoTask] = []
    for i, city_str in enumerate(cities):
        is_last = i == n - 1
        count = target_count - per_city * (n - 1) if is_last else per_city
        parsed = _parse_city_state_country(city_str, location)

        state = parsed["state"]
        country = parsed["country"]
        if not state and not country:
            if location_is_state:
                state = location
            elif location_is_country:
                country = location

        tasks.append(GeoTask(
            city=parsed["city"], state=state, country=country,
            raw_location=location, target_count=max(1, count),
        ))
    return tasks


async def _propose_cities(location: str, max_units: int, cfg: Optional[Dict[str, Any]]) -> List[str]:
    key = location.strip().lower()
    try:
        resolved_cfg = cfg or await _ollama_cfg()
        prompt = (
            f"List up to {max_units} well-known, real cities to research businesses in, "
            f"for the region \"{location}\". Only include real, well-known cities you are "
            f"confident exist in this region — do not invent city names.\n"
            f'Return ONLY strict JSON: {{"cities": ["City, State/Country", ...]}}'
        )
        raw = await _call_llm_raw(prompt, resolved_cfg, temperature=0.2, num_predict=200)
        data = _parse_json_object(raw)
        if isinstance(data, dict) and isinstance(data.get("cities"), list):
            cities = [str(c).strip() for c in data["cities"] if str(c).strip()]
            if cities:
                return cities[:max_units]
    except Exception as exc:
        logger.info("Geo expansion LLM call failed for '%s': %s — using fallback list", location, exc)

    return _FALLBACK_CITIES.get(key, [])[:max_units]
