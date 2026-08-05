"""
techstack.py — zero-extra-network-call heuristic tech-stack detection.

Operates on HTML already fetched by analyze_website() — never fetches
anything itself. Pure regex signature matching, easy to extend.
"""
import re
from typing import Dict, List, Optional

_SIGNATURES: Dict[str, List[str]] = {
    "WordPress":        [r"wp-content", r"wp-includes", r'name="generator"\s+content="WordPress'],
    "Shopify":           [r"cdn\.shopify\.com", r"Shopify\.theme"],
    "Wix":                [r"static\.wixstatic\.com", r"\bwix\.com\b"],
    "Squarespace":        [r"squarespace\.com", r"static1\.squarespace\.com"],
    "Webflow":            [r"webflow\.com", r"\bwf-"],
    "Next.js/React":      [r"__NEXT_DATA__", r"data-reactroot", r"_next/static"],
    "Google Analytics":   [r"google-analytics\.com", r"gtag\("],
}


def detect_tech_stack(html: Optional[str]) -> List[str]:
    if not html:
        return []
    found: List[str] = []
    for name, patterns in _SIGNATURES.items():
        if any(re.search(p, html, re.I) for p in patterns):
            found.append(name)
    return found
