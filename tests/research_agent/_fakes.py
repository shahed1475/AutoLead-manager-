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
