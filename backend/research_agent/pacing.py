"""
pacing.py — PacingController: centralizes every deliberate delay the
Research Agent takes. After this lands, no other module in
backend/research_agent/ calls asyncio.sleep or page.wait_for_timeout for
pacing purposes (Playwright's own navigation/selector timeouts in
browser.py are failure timeouts, not pacing, and stay untouched).
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

PACING_OPS = (
    "search_page_load", "result_review", "click", "page_load", "initial_read",
    "scroll", "after_scroll", "link_selection", "navigation", "between_domains",
    "between_leads",
)


@dataclass
class PacingProfile:
    name: str
    ranges: Dict[str, Tuple[float, float]]
    settle_cap_s: float
    retry_attempts: int = 2
    retry_backoff: Tuple[float, float] = (1.0, 3.0)

    def range_for(self, op: str) -> Tuple[float, float]:
        return self.ranges.get(op, (0.0, 0.0))


PACING_PROFILES: Dict[str, PacingProfile] = {
    "fast": PacingProfile(
        name="fast", settle_cap_s=3.0,
        ranges={op: (0.0, 0.3) for op in PACING_OPS},
    ),
    "standard": PacingProfile(
        name="standard", settle_cap_s=6.0, retry_attempts=2, retry_backoff=(1.5, 3.5),
        ranges={
            "search_page_load": (0.8, 1.8), "result_review": (1.0, 2.5), "click": (0.4, 1.0),
            "page_load": (0.8, 2.0), "initial_read": (0.5, 1.2), "scroll": (0.3, 0.7),
            "after_scroll": (0.6, 1.4), "link_selection": (0.5, 1.2), "navigation": (0.5, 1.2),
            "between_domains": (2.0, 4.0), "between_leads": (2.0, 5.0),
        },
    ),
    "deliberate": PacingProfile(
        name="deliberate", settle_cap_s=10.0, retry_attempts=3, retry_backoff=(2.0, 5.0),
        ranges={
            "search_page_load": (1.5, 3.0), "result_review": (2.0, 4.5), "click": (0.8, 1.8),
            "page_load": (1.5, 3.5), "initial_read": (1.0, 2.2), "scroll": (0.6, 1.2),
            "after_scroll": (1.2, 2.8), "link_selection": (0.8, 2.0), "navigation": (1.0, 2.5),
            "between_domains": (3.0, 7.0), "between_leads": (4.0, 9.0),
        },
    ),
}
_DEFAULT_PROFILE = "deliberate"

# A zero-delay profile — NOT one of the three public profiles above (never
# selected by a depth preset). Used only as the fallback for a caller that
# builds a ResearchAgent without explicitly constructing a PacingController
# (today: direct-construction tests). run_research_session always builds and
# passes a real PacingController from the resolved depth/profile config.
INSTANT_PROFILE = PacingProfile(
    name="instant", settle_cap_s=0.2,
    ranges={op: (0.0, 0.0) for op in PACING_OPS},
)


def resolve_profile(name: Optional[str]) -> PacingProfile:
    key = (name or _DEFAULT_PROFILE).strip().lower()
    return PACING_PROFILES.get(key, PACING_PROFILES[_DEFAULT_PROFILE])


async def _noop_sleep(_seconds: float) -> None:
    return None


class PacingController:
    """One instance per research session (mirrors BrowserController's
    one-per-session lifetime)."""

    def __init__(self, profile: PacingProfile, *, sleep_fn: Optional[Callable[[float], Any]] = None) -> None:
        self.profile = profile
        self._sleep = sleep_fn or asyncio.sleep

    async def wait(self, op: str) -> float:
        lo, hi = self.profile.range_for(op)
        duration = random.uniform(lo, hi) if hi > lo else lo
        if duration > 0:
            await self._sleep(duration)
        return duration

    async def settle(self, page: Any, *, cap_s: Optional[float] = None) -> str:
        """Poll until the page looks done: readyState complete AND body text
        length unchanged across two consecutive samples, or cap_s elapses.
        Never raises — an evaluate() failure (closed page, navigation mid-poll)
        is treated as "can't confirm settled" and returns "timeout"."""
        cap = self.profile.settle_cap_s if cap_s is None else cap_s
        start = time.monotonic()
        last_len = -1
        stable_rounds = 0
        while time.monotonic() - start < cap:
            try:
                ready = await page.evaluate("() => document.readyState")
                text_len = await page.evaluate(
                    "() => (document.body ? document.body.innerText.length : 0)"
                )
            except Exception:
                return "timeout"
            if ready == "complete" and text_len == last_len:
                stable_rounds += 1
                if stable_rounds >= 2:
                    return "settled"
            else:
                stable_rounds = 0
            last_len = text_len
            await self._sleep(0.3)
        return "timeout"


def instant_controller() -> PacingController:
    """A zero-wall-clock controller — for callers that don't care about
    pacing timing (see the docstring on INSTANT_PROFILE)."""
    return PacingController(INSTANT_PROFILE, sleep_fn=_noop_sleep)
