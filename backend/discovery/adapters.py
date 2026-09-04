"""
adapters.py — lightweight SourceAdapter wrapper + SourceRegistry over the 9
existing scrapers. Delegates every call to scrapers._dispatch_source — the
same dispatch function scrapers.run_bulk_scrape already uses for every
Campaign-mode scrape — so per-source call signatures, the built-in
Maps-fallback, and lead-tagging behavior are never duplicated or reimplemented
here. Scraper internals are never touched.

Health/circuit-breaking is run-scoped only (a fresh SourceRegistry per
discovery run) — no persistent cross-run health store in Phase 1.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..scrapers import _dispatch_source, _SOURCE_WEIGHTS

logger = logging.getLogger(__name__)

BROWSER_SOURCES = frozenset({"GOOGLE_MAPS", "BING_SEARCH"})

# Priority order: highest weight (most reliable/highest-volume) = lowest
# priority number = tried first, matching the existing budget-split precedent.
_SOURCE_NAMES = sorted(_SOURCE_WEIGHTS.keys(), key=lambda s: -_SOURCE_WEIGHTS[s])

_CONSECUTIVE_FAILURES_TO_TRIP = 2


@dataclass
class AdapterResult:
    source: str
    leads: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    latency_ms: float = 0.0


@dataclass
class SourceAdapter:
    name: str
    source_type: str
    priority: int
    enabled: bool = True
    rate_limit: Optional[float] = None
    timeout: Optional[int] = None
    health_state: str = "HEALTHY"  # HEALTHY | DEGRADED | UNHEALTHY
    metrics: Dict[str, Any] = field(default_factory=lambda: {
        "calls": 0, "zero_result_count": 0, "exception_count": 0, "last_latency_ms": 0.0,
    })
    _consecutive_failures: int = 0

    async def execute(
        self,
        niche: str, city: str, country: str, budget: int,
        cfg: Dict[str, Any], log_fn: Optional[Callable[[str], None]] = None,
    ) -> AdapterResult:
        t0 = time.monotonic()
        self.metrics["calls"] += 1
        try:
            leads = await _dispatch_source(self.name, niche, city, country, budget, cfg, log_fn or (lambda _m: None))
            latency_ms = (time.monotonic() - t0) * 1000
            self.metrics["last_latency_ms"] = latency_ms
            if not leads:
                self.metrics["zero_result_count"] += 1
                self._register_failure()
            else:
                self._register_success()
            return AdapterResult(source=self.name, leads=leads, latency_ms=latency_ms)
        except Exception as exc:  # defense-in-depth — _dispatch_source shouldn't raise, but never trust that blindly
            latency_ms = (time.monotonic() - t0) * 1000
            self.metrics["exception_count"] += 1
            self.metrics["last_latency_ms"] = latency_ms
            self._register_failure()
            logger.warning("SourceAdapter %s raised: %s", self.name, exc, exc_info=True)
            return AdapterResult(source=self.name, leads=[], error=str(exc), latency_ms=latency_ms)

    def _register_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= _CONSECUTIVE_FAILURES_TO_TRIP:
            self.health_state = "UNHEALTHY"
            self.enabled = False
        elif self._consecutive_failures == 1:
            self.health_state = "DEGRADED"

    def _register_success(self) -> None:
        self._consecutive_failures = 0
        self.health_state = "HEALTHY"


def _build_adapters() -> Dict[str, SourceAdapter]:
    return {
        name: SourceAdapter(
            name=name,
            source_type="BROWSER" if name in BROWSER_SOURCES else "HTTP",
            priority=i,
        )
        for i, name in enumerate(_SOURCE_NAMES)
    }


class SourceRegistry:
    """Construct one fresh instance per discovery run — health/circuit state
    does not persist across runs (Phase 1 scope)."""

    def __init__(self) -> None:
        self._adapters: Dict[str, SourceAdapter] = _build_adapters()

    def get(self, name: str) -> Optional[SourceAdapter]:
        return self._adapters.get((name or "").upper())

    def list_enabled(self) -> List[SourceAdapter]:
        return sorted(
            (a for a in self._adapters.values() if a.enabled),
            key=lambda a: a.priority,
        )

    def all_names(self) -> List[str]:
        return list(self._adapters.keys())

    async def execute(
        self,
        name: str, niche: str, city: str, country: str, budget: int,
        cfg: Dict[str, Any], log_fn: Optional[Callable[[str], None]] = None,
    ) -> AdapterResult:
        adapter = self.get(name)
        if adapter is None:
            return AdapterResult(source=name, leads=[], error=f"Unknown source '{name}'")
        return await adapter.execute(niche, city, country, budget, cfg, log_fn)


def get_registry() -> SourceRegistry:
    """Factory — always returns a fresh, run-scoped registry."""
    return SourceRegistry()
