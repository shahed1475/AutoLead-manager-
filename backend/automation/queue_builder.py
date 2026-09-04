"""
queue_builder.py — turn parsed locations + niches into an ordered search queue.
Niche-outer, location-inner (spec §2): all locations for niche #1, then all
locations for niche #2, and so on.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_queue(
    locations: List[Dict[str, Optional[str]]], niches: List[str],
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    pos = 0
    for niche in niches:
        for loc in locations:
            items.append({
                "position": pos,
                "niche": niche,
                "city": loc.get("city"),
                "state": loc.get("state"),
            })
            pos += 1
    return items
