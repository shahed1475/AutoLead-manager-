"""
_fakes.py — shared test doubles for Browser Research Agent tests. Not a test
file itself (no test_ prefix) — imported by the actual test modules.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Union

from backend.research_agent.actions import ActionResult
from backend.research_agent.models import AgentAction


class _FakePage:
    def __init__(self, url: str = "https://acmedental.test") -> None:
        self.url = url


class ScriptedBrowser:
    """
    Duck-types BrowserController: an .execute(action) -> ActionResult and a
    ._page.url the agent reads for save_evidence provenance. `script` is
    either a dict[action_name] -> list[ActionResult] (consumed in order,
    "no more scripted results" error once exhausted) or a callable
    (action) -> ActionResult for fully dynamic scenarios.
    """

    def __init__(self, script: Union[Dict[str, List[ActionResult]], Callable[[AgentAction], ActionResult]], page_url: str = "https://acmedental.test") -> None:
        self._script = script
        self._page = _FakePage(page_url)
        self.calls: List[str] = []

    async def execute(self, action: AgentAction) -> ActionResult:
        self.calls.append(action.action)
        if callable(self._script):
            return self._script(action)
        queue = self._script.get(action.action, [])
        if queue:
            return queue.pop(0)
        return ActionResult(action=action.action, status="error", error="no more scripted results for this action")


def ok(action: str, **data: Any) -> ActionResult:
    return ActionResult(action=action, status="success", data=data)


def err(action: str, message: str = "failed") -> ActionResult:
    return ActionResult(action=action, status="error", error=message)


class ScriptedPage:
    """Fake Playwright Page for PageReader/PacingController tests.
    `rounds` is a list of {"text": str, "links": [...], "headings": [...],
    "metrics": {...}} dicts — round 0 is the initial read, round N is the
    state after the Nth scroll. The fake advances to the next round each
    time it sees a scrollBy evaluate() call; every other evaluate() reads
    from the current round."""

    def __init__(self, rounds, url: str = "https://clinic.test/team") -> None:
        self._rounds = rounds
        self._round_idx = 0
        self.url = url
        self.scroll_calls = 0
        self.wait_calls: List[float] = []

    def _current(self):
        return self._rounds[min(self._round_idx, len(self._rounds) - 1)]

    async def evaluate(self, js: str):
        if "document.readyState" in js:
            return "complete"
        if "scrollBy" in js:
            self.scroll_calls += 1
            self._round_idx += 1
            return None
        round_data = self._current()
        if "innerText.length" in js:
            return len(round_data["text"])
        if "querySelectorAll('a" in js:
            return round_data["links"]
        if "querySelectorAll('h1" in js:
            return round_data["headings"]
        if "scrollHeight" in js and "querySelectorAll" in js:
            return round_data["metrics"]
        if "innerText" in js:
            return round_data["text"]
        raise AssertionError(f"unscripted evaluate() call: {js[:80]!r}")

    async def wait_for_timeout(self, ms):
        self.wait_calls.append(ms)
