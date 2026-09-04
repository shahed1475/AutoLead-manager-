# Deliberate Page Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Browser Research Agent read pages the way a person does — settle, scroll, read what appears, decide where to go — inside a configurable, bounded budget, instead of a one-shot 20k-char snapshot with a broken scroll stub and zero pacing.

**Architecture:** Two new small units in `backend/research_agent/` — `pacing.py` (`PacingController`: per-operation delay ranges + adaptive `settle()`) and `reader.py` (`PageReader.read_page`: settle → incremental scroll+read loop with 2-round stability detection, bounded by per-page/per-domain/per-session caps). `agent.py`'s existing deterministic R2 rule is wired to call the reader instead of a bare `extract_page_text`; the `ActionResult` shape it returns is unchanged, so `_process_result`/`_process_page_text`/evidence recording need no changes. Crawl breadth widens from a flat `contact|about|team` regex to a scored link map. Four depth-mode presets (`quick|standard|deep|max`) set the budgets; a session-level time budget and a per-domain cap bound the worst case. A settings-gated, off-by-default policy lets the agent read public professional/company profile pages as an evidence source. No scraper, `automation/`, or UI change.

**Tech Stack:** Python 3.11+, `pytest`/`pytest-asyncio` (asyncio_mode=auto), Playwright (already a dependency), stdlib `http.server` for the benchmark's local fixture server.

## Global Constraints

- **Subsystem boundary:** every change lives in `backend/research_agent/` (+ its tests). No edit to `backend/scrapers/`, `backend/discovery/`, `backend/automation/`, any router, or any frontend file.
- **No DB schema change.** No `_add_col_if_missing`, no new table, no new column on `lead_research_sessions`/`lead_research_results`/`lead_research_evidence`.
- **No fabrication regression.** `evidence.record_finding` stays the sole field-setting chokepoint. Nothing in this plan sets a `ResearchLead` field outside it.
- **Never raises into the loop.** Every new function that touches the browser (`pacing.settle`, `reader.read_page`) catches its own exceptions and returns a normal value, matching `browser.py`'s existing contract.
- **No hard-coded sleep outside `pacing.py`.** After Task 7, the only `asyncio.sleep` / `page.wait_for_timeout` calls left in the subsystem are inside `pacing.py` and Playwright's own navigation/selector timeouts in `browser.py` (untouched).
- **TDD every task:** write the failing test, watch it fail, write minimal code, watch it pass, run the full `tests/research_agent/` suite, commit.
- **Baseline:** `pytest -q` is 827 passing on this branch before Task 1. Frontend is untouched — no `npm run build` gate for this plan.
- **Reuse, don't duplicate:** `PageReader` calls the raw Playwright `page` object (`browser._page`) directly for its own scroll/measure steps — it does **not** add new public methods to `BrowserController`, and `BrowserController` itself is not modified in this plan. `_process_page_text`, `_apply_deterministic_extraction`, `evidence.py`, `validation.py`, `llm.py`'s prompts (except the tool-description removal in Task 6) are unchanged.
- **Config layering unchanged in shape:** `app_settings` DB row > depth preset > `.env`/pydantic default > hardcoded fallback — same precedence pattern as every other config module in this codebase, with one new layer (the depth preset) inserted between DB and `.env`.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/research_agent/pacing.py` (new) | `PacingProfile`, `PACING_PROFILES` (fast/standard/deliberate), `resolve_profile`, `PacingController` (`wait`, `settle`), `INSTANT_PROFILE` + `instant_controller()` for pacing-agnostic callers (tests) |
| `backend/research_agent/reader.py` (new) | `PageContent` dataclass, `read_page(page, pacing, *, max_scrolls, page_time_cap_s)` — the careful-read algorithm |
| `backend/research_agent/config.py` (modify) | `DEPTH_PRESETS`, `resolve_depth`, 7 new `_DEFAULTS` keys, `get_research_config(depth=None)` |
| `backend/research_agent/extraction.py` (modify) | `link_relevance(href, anchor_text)` scored map; `is_relevant_nav_link` becomes a back-compat shim over it |
| `backend/research_agent/models.py` (modify) | `VALID_ACTIONS` drops `scroll`/`screenshot` |
| `backend/research_agent/actions.py` (modify) | `validate_action` drops the `scroll`-specific branch (now unreachable — `scroll` fails the `VALID_ACTIONS` check first, same as any retired action) |
| `backend/research_agent/prompts.py` (modify) | `ACTION_TOOL_DESCRIPTIONS` drops the `scroll`/`screenshot` lines |
| `backend/research_agent/agent.py` (modify) | `ResearchAgent.__init__` gains `pacing`; `research_business` routes `extract_page_text` through the reader, paces navigation/domain changes, tracks per-domain time, loosens R3 for deep/max, gates professional-profile URLs; `_collect_relevant_links` uses `link_relevance` + a configurable cap; `run_research_session` builds the session's `PacingController`, enforces the time budget, paces between leads |
| `backend/research_agent/session.py` (modify) | `run_research_session_persisted` gains an optional `research_depth` param threaded into `get_research_config(depth=...)` |
| `tests/research_agent/_fakes.py` (modify) | add `ScriptedPage` — a fake Playwright page for `reader`/`pacing` tests |
| `tests/research_agent/test_pacing.py` (new) | pacing tests |
| `tests/research_agent/test_reader.py` (new) | reader tests |
| `tests/research_agent/test_config.py` (new) | depth-preset/config tests |
| `tests/research_agent/test_extraction.py` (modify) | add `link_relevance` tests, keep existing green |
| `tests/research_agent/test_actions.py` (modify) | drop scroll cases, add the "retired action" case |
| `tests/research_agent/test_agent_loop.py` (modify) | add reader-wiring, pacing, domain-cap, professional-profile, R3-loosening tests |
| `tests/research_agent/test_session.py` (modify) | add the `research_depth` threading test |
| `tests/research_agent/fixtures/{clinic,thin,broken}/*.html` (new) | static fixture sites for the benchmark |
| `tests/research_agent/benchmark/server.py` (new) | serves the fixtures over local HTTP |
| `tests/research_agent/benchmark/runner.py` (new) | runs the agent against the fixtures, writes `results_<label>.json` |
| `tests/research_agent/benchmark/results_before.json`, `results_after.json` (generated) | the BEFORE/AFTER evidence |
| `docs/superpowers/plans/2026-09-04-deliberate-page-research.md` | this file — checkboxes updated as tasks land |

---

### Task 1: Benchmark harness + fixture sites + BEFORE baseline

**Files:**
- Create: `tests/research_agent/fixtures/clinic/index.html`
- Create: `tests/research_agent/fixtures/clinic/team.html`
- Create: `tests/research_agent/fixtures/clinic/services.html`
- Create: `tests/research_agent/fixtures/thin/index.html`
- Create: `tests/research_agent/fixtures/broken/index.html`
- Create: `tests/research_agent/benchmark/__init__.py` (empty)
- Create: `tests/research_agent/benchmark/server.py`
- Create: `tests/research_agent/benchmark/runner.py`
- Create: `tests/research_agent/benchmark/README.md`

**Interfaces:**
- Produces: `serve_fixtures(directory: Path) -> tuple[int, Callable[[], None]]` (port, stop-fn); `run_benchmark(label: str) -> dict` — later tasks don't call these (they're a standalone harness, not imported by production or unit-test code).

This task has no "failing unit test" in the usual sense — its deliverable is a working, runnable harness plus a checked-in BEFORE snapshot. Steps still follow write → run → verify → commit.

- [ ] **Step 1: Write the fixture sites**

`tests/research_agent/fixtures/clinic/index.html`:
```html
<!doctype html>
<html><head><title>Sunny Dental Clinic</title></head>
<body>
<h1>Sunny Dental Clinic</h1>
<p>Quality dental care in Sunnyvale. Call us at (555) 010-1000 or email info@sunnydental.test.</p>
<nav>
  <a href="team.html">Meet Our Team</a> |
  <a href="services.html">Services</a>
</nav>
</body></html>
```

`tests/research_agent/fixtures/clinic/team.html` (the lazy-loaded-card fixture — this is what a one-shot snapshot misses):
```html
<!doctype html>
<html><head><title>Our Team - Sunny Dental Clinic</title></head>
<body>
<h1>Meet Our Team</h1>
<div id="cards">
  <div class="card">Amanda Reyes &mdash; Lead Dentist and Practice Owner</div>
</div>
<div style="height:1400px"></div>
<script>
  var more = [
    {text: "Marcus Bell — Practice Manager"},
    {text: "Dana Whitfield — Office Manager"}
  ];
  var revealed = 0;
  window.addEventListener('scroll', function () {
    var nearBottom = (window.scrollY + window.innerHeight) > (document.body.scrollHeight - 500);
    if (nearBottom && revealed < more.length) {
      var div = document.createElement('div');
      div.className = 'card';
      div.textContent = more[revealed].text;
      document.getElementById('cards').appendChild(div);
      revealed += 1;
    }
  });
</script>
</body></html>
```

`tests/research_agent/fixtures/clinic/services.html`:
```html
<!doctype html>
<html><head><title>Services - Sunny Dental Clinic</title></head>
<body>
<h1>Our Services</h1>
<p>General dentistry, cosmetic dentistry, orthodontics.</p>
</body></html>
```

`tests/research_agent/fixtures/thin/index.html`:
```html
<!doctype html>
<html><head><title>Thin Consulting Co</title></head>
<body>
<h1>Thin Consulting Co</h1>
<p>We provide consulting services. Contact us at hello@thinconsulting.test or (555) 010-2000.</p>
</body></html>
```

`tests/research_agent/fixtures/broken/index.html` (near-empty page whose only content comes from a script that throws — simulates a broken/JS-error site):
```html
<!doctype html>
<html><head><title>Broken Widgets Inc</title></head>
<body>
<script>throw new Error("simulated JS crash — this page never renders real content");</script>
</body></html>
```

- [ ] **Step 2: Write the local fixture server**

`tests/research_agent/benchmark/__init__.py`: empty file (package marker).

`tests/research_agent/benchmark/server.py`:
```python
"""
server.py — serves tests/research_agent/fixtures/ over plain localhost HTTP
for the benchmark runner. Never imported by the pytest suite or by
production code — a manual/CI-optional tool only.
"""
from __future__ import annotations

import functools
import http.server
import socket
import threading
from pathlib import Path
from typing import Callable, Tuple


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve_fixtures(directory: Path) -> Tuple[int, Callable[[], None]]:
    """Starts a background HTTP server rooted at `directory`. Returns
    (port, stop_fn) — call stop_fn() when done."""
    port = _free_port()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    def stop() -> None:
        httpd.shutdown()
        httpd.server_close()

    return port, stop
```

- [ ] **Step 3: Write the benchmark runner**

`tests/research_agent/benchmark/runner.py`:
```python
"""
runner.py — benchmark harness for the Research Agent's page-reading
behaviour. NOT part of `pytest -q` (drives a real headless Playwright
browser against local fixtures; takes roughly a minute). Run manually from
the repo root:

    venv/Scripts/python.exe -m tests.research_agent.benchmark.runner before
    venv/Scripts/python.exe -m tests.research_agent.benchmark.runner after

Writes tests/research_agent/benchmark/results_<label>.json and prints a
summary. Task 1 of the plan runs this against unmodified agent.py (label
"before"); the final task re-runs it once every other task has landed
(label "after").

The LLM is replaced with a deterministic "always finish" stub and a tiny
regex stand-in for name/title extraction, so this runs with no live Ollama
and no network access beyond the local fixture server — repeatable and
fast. This means the deterministic rules in agent.py (_forced_action's
R1/R2/R3) do essentially all of the work, which is exactly the code path
this plan changes.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root on sys.path

from backend.research_agent import agent as agent_mod  # noqa: E402
from backend.research_agent import llm as llm_mod  # noqa: E402
from backend.research_agent.browser import BrowserController  # noqa: E402
from backend.research_agent.config import get_research_config  # noqa: E402

from .server import serve_fixtures  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
BUSINESSES = [
    {"business_name": "Sunny Dental Clinic", "path": "clinic/index.html"},
    {"business_name": "Thin Consulting Co", "path": "thin/index.html"},
    {"business_name": "Broken Widgets Inc", "path": "broken/index.html"},
]

_NAME_ROLE_RE = re.compile(r"([A-Z][a-z]+ [A-Z][a-z]+)\s*[—,-]\s*([A-Za-z ]+)")


async def _fake_extract_fields(text, missing_fields, business_name=None, cfg=None):
    """Deterministic stand-in for the LLM name/title extractor — the
    benchmark must not depend on a live Ollama instance."""
    out: Dict[str, str] = {}
    m = _NAME_ROLE_RE.search(text)
    if m:
        if "management_contact_name" in missing_fields:
            out["management_contact_name"] = m.group(1)
        if "management_title" in missing_fields:
            out["management_title"] = m.group(2).strip()
    return out


async def _finish_only(state_summary, cfg=None):
    return agent_mod.AgentAction(action="finish_research", reason="benchmark: deterministic rules only")


async def run_benchmark(label: str) -> Dict[str, Any]:
    port, stop_server = serve_fixtures(FIXTURES_DIR)
    base_url = f"http://127.0.0.1:{port}"

    orig_decide = llm_mod.decide_next_action
    orig_extract = llm_mod.extract_fields
    llm_mod.decide_next_action = _finish_only
    llm_mod.extract_fields = _fake_extract_fields

    results: List[Dict[str, Any]] = []
    try:
        cfg = await get_research_config()
        cfg = {**cfg, "research_agent_headless": True}
        async with BrowserController(
            headless=True, page_timeout_ms=cfg["research_agent_page_timeout_ms"],
        ) as browser:
            agent = agent_mod.ResearchAgent(browser, cfg, "benchmark")
            for biz in BUSINESSES:
                hint = {"business_name": biz["business_name"], "website": f"{base_url}/{biz['path']}"}
                started = time.monotonic()
                lead = await agent.research_business(hint)
                elapsed = time.monotonic() - started
                results.append({
                    "business": biz["business_name"],
                    "pages_visited": lead.pages_visited,
                    "useful_pages": sum(1 for e in lead.evidence if e.status == "FOUND"),
                    "fields_found": sum(
                        1 for f in ("business_email", "business_phone",
                                    "management_contact_name", "management_title")
                        if getattr(lead, f)
                    ),
                    "decision_makers_found": 1 if lead.management_contact_name else 0,
                    "wall_time_s": round(elapsed, 2),
                    "research_status": lead.research_status,
                })
    finally:
        llm_mod.decide_next_action = orig_decide
        llm_mod.extract_fields = orig_extract
        stop_server()

    summary = {
        "label": label,
        "businesses": results,
        "totals": {
            "pages_visited": sum(r["pages_visited"] for r in results),
            "useful_pages": sum(r["useful_pages"] for r in results),
            "fields_found": sum(r["fields_found"] for r in results),
            "decision_makers_found": sum(r["decision_makers_found"] for r in results),
            "wall_time_s": round(sum(r["wall_time_s"] for r in results), 2),
        },
    }
    out_path = Path(__file__).parent / f"results_{label}.json"
    out_path.write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else "before"
    asyncio.run(run_benchmark(label))
```

`tests/research_agent/benchmark/README.md`:
```markdown
# Research Agent benchmark

Not part of `pytest -q`. Drives a real headless Playwright browser against
the local fixture sites in `tests/research_agent/fixtures/` — no live
Ollama, no network access beyond localhost.

    venv/Scripts/python.exe -m tests.research_agent.benchmark.runner before
    venv/Scripts/python.exe -m tests.research_agent.benchmark.runner after

`results_before.json` was captured against the unmodified agent (before
Task 2 of docs/superpowers/plans/2026-09-04-deliberate-page-research.md).
`results_after.json` was captured once every other task in that plan
landed. See the plan's final task for the comparison table.
```

- [ ] **Step 4: Run it and capture the BEFORE numbers**

Run: `venv/Scripts/python.exe -m tests.research_agent.benchmark.runner before`
Expected: prints a JSON summary and writes `tests/research_agent/benchmark/results_before.json`. On the "clinic" business, `decision_makers_found` is expected to be `0` and `pages_visited` around `2` (index + team) — the lazily-revealed "Marcus Bell" card is invisible to today's one-shot `extract_page_text`, so the name/role regex only ever sees "Amanda Reyes" on the team page, if it fires at all before the loop's other budgets end it. Record whatever the actual numbers are — they're the baseline, not an assertion to force.

- [ ] **Step 5: Commit**

```bash
git add tests/research_agent/fixtures tests/research_agent/benchmark
git commit -m "test(research-agent): benchmark harness + fixture sites + BEFORE baseline"
```

---

### Task 2: `PacingController` (`pacing.py`)

**Files:**
- Create: `backend/research_agent/pacing.py`
- Test: `tests/research_agent/test_pacing.py`

**Interfaces:**
- Produces: `PacingProfile` (dataclass: `name, ranges, settle_cap_s, retry_attempts, retry_backoff`), `PACING_PROFILES: Dict[str, PacingProfile]` (`"fast"|"standard"|"deliberate"`), `resolve_profile(name: Optional[str]) -> PacingProfile`, `PacingController(profile, *, sleep_fn=None)` with `async def wait(op: str) -> float` and `async def settle(page, *, cap_s: Optional[float] = None) -> str` (`"settled"|"timeout"`), `INSTANT_PROFILE: PacingProfile`, `instant_controller() -> PacingController`.

- [ ] **Step 1: Write the failing tests**

`tests/research_agent/test_pacing.py`:
```python
import asyncio

import pytest

from backend.research_agent.pacing import (
    PACING_PROFILES,
    PacingController,
    instant_controller,
    resolve_profile,
)

pytestmark = pytest.mark.asyncio


class _RecordingSleep:
    def __init__(self):
        self.calls = []

    async def __call__(self, seconds):
        self.calls.append(seconds)


def test_resolve_profile_known_names():
    assert resolve_profile("fast").name == "fast"
    assert resolve_profile("standard").name == "standard"
    assert resolve_profile("deliberate").name == "deliberate"


def test_resolve_profile_unknown_falls_back_to_deliberate():
    assert resolve_profile("bogus").name == "deliberate"
    assert resolve_profile(None).name == "deliberate"
    assert resolve_profile("DELIBERATE").name == "deliberate"  # case-insensitive


async def test_wait_samples_within_the_profiles_range():
    sleep = _RecordingSleep()
    ctl = PacingController(PACING_PROFILES["standard"], sleep_fn=sleep)
    lo, hi = PACING_PROFILES["standard"].range_for("after_scroll")
    duration = await ctl.wait("after_scroll")
    assert lo <= duration <= hi
    assert sleep.calls == [duration] or (duration == 0.0 and sleep.calls == [])


async def test_wait_unknown_op_is_a_safe_noop():
    sleep = _RecordingSleep()
    ctl = PacingController(PACING_PROFILES["deliberate"], sleep_fn=sleep)
    duration = await ctl.wait("not_a_real_op")
    assert duration == 0.0
    assert sleep.calls == []


async def test_settle_returns_settled_once_content_stabilises():
    class _Page:
        def __init__(self):
            self._n = 0

        async def evaluate(self, js):
            if "readyState" in js:
                return "complete"
            self._n += 1
            return 100  # constant text length -> stable immediately

    ctl = PacingController(PACING_PROFILES["fast"], sleep_fn=_RecordingSleep())
    result = await ctl.settle(_Page(), cap_s=1.0)
    assert result == "settled"


async def test_settle_times_out_when_content_never_stabilises():
    class _GrowingPage:
        def __init__(self):
            self._len = 0

        async def evaluate(self, js):
            if "readyState" in js:
                return "complete"
            self._len += 50
            return self._len  # always growing -> never stable

    ctl = PacingController(PACING_PROFILES["fast"], sleep_fn=_RecordingSleep())
    result = await ctl.settle(_GrowingPage(), cap_s=0.05)
    assert result == "timeout"


async def test_settle_handles_evaluate_errors_gracefully():
    class _BrokenPage:
        async def evaluate(self, js):
            raise RuntimeError("page closed")

    ctl = PacingController(PACING_PROFILES["fast"], sleep_fn=_RecordingSleep())
    result = await ctl.settle(_BrokenPage(), cap_s=0.2)
    assert result == "timeout"


async def test_instant_controller_never_really_sleeps():
    ctl = instant_controller()
    # Even the deliberate-sized upper bound would be seconds; instant_controller
    # must return near-immediately regardless of profile because its sleep_fn
    # is a no-op.
    import time
    started = time.monotonic()
    await ctl.wait("between_leads")
    assert time.monotonic() - started < 0.05
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/Scripts/python.exe -m pytest tests/research_agent/test_pacing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.research_agent.pacing'`.

- [ ] **Step 3: Write `pacing.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/research_agent/test_pacing.py -v`
Expected: PASS, all 8 tests, in well under a second (no real sleeping — `_RecordingSleep` and `instant_controller`'s `_noop_sleep` never block; the two `settle()` tests use tiny `cap_s` values).

- [ ] **Step 5: Commit**

```bash
git add backend/research_agent/pacing.py tests/research_agent/test_pacing.py
git commit -m "feat(research-agent): PacingController — centralized, profiled delays"
```

---

### Task 3: `PageReader` (`reader.py`)

**Files:**
- Create: `backend/research_agent/reader.py`
- Modify: `tests/research_agent/_fakes.py` — add `ScriptedPage`
- Test: `tests/research_agent/test_reader.py`

**Interfaces:**
- Consumes: `pacing.PacingController` (Task 2).
- Produces: `PageContent` (dataclass: `url, text, links, headings, scroll_rounds, stopped_reason`), `async def read_page(page, pacing, *, initial: Dict[str, Any], max_scrolls: int, page_time_cap_s: float) -> PageContent`.

**Design note (why `initial` is a parameter, not fetched by `read_page` itself):** the reader's *first* snapshot is fetched by the caller through the normal `browser.execute(AgentAction(action="extract_page_text"))` path (Task 7) — the exact same call every existing test already scripts — and handed in as `initial`. `read_page` then tries to **scroll for more** using the raw `page` object; if that fails (no real scroll capability — a test double, a closed page), it falls back to returning `initial` unchanged with `stopped_reason="bottom"`, never an exception. This keeps every pre-existing `ScriptedBrowser`-based test working unchanged (they script `extract_page_text` once and get exactly that content back) while a real Playwright page gets the full scroll-and-accumulate treatment.

- [ ] **Step 1: Add `ScriptedPage` to `_fakes.py`**

Append to `tests/research_agent/_fakes.py`:
```python
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
```

- [ ] **Step 2: Write the failing tests**

`tests/research_agent/test_reader.py`:
```python
import pytest

from backend.research_agent.pacing import instant_controller
from backend.research_agent.reader import read_page

from ._fakes import ScriptedPage

pytestmark = pytest.mark.asyncio

INITIAL = {"url": "https://clinic.test/team", "text": "Amanda Reyes — Lead Dentist", "links": [], "headings": []}


def _round(text, links=None, headings=None, scroll_height=2000, scroll_y=0, inner_height=900, card_count=1):
    return {
        "text": text, "links": links or [], "headings": headings or [],
        "metrics": {"scrollHeight": scroll_height, "scrollY": scroll_y,
                    "innerHeight": inner_height, "cardCount": card_count},
    }


async def test_short_page_stops_immediately_at_bottom_no_scrolling():
    # round 0's metrics say scrollY(0) + innerHeight(900) >= scrollHeight(900) -> already at bottom
    page = ScriptedPage([_round("(unused — initial wins)", scroll_height=900, scroll_y=0, inner_height=900)])
    initial = {"url": "https://thin.test", "text": "Thin Consulting Co. Contact us.", "links": [], "headings": []}
    content = await read_page(page, instant_controller(), initial=initial, max_scrolls=6, page_time_cap_s=5)
    assert content.stopped_reason == "bottom"
    assert content.scroll_rounds == 0
    assert content.text == "Thin Consulting Co. Contact us."   # exactly the initial snapshot, unmodified


async def test_reveals_lazy_loaded_content_on_scroll():
    rounds = [
        _round("Amanda Reyes — Lead Dentist", scroll_height=2000, scroll_y=0, card_count=1),         # round 0: not yet at bottom
        _round("Amanda Reyes — Lead Dentist\nMarcus Bell — Practice Manager",
               scroll_height=2000, scroll_y=900, card_count=2),                                        # round 1: still not at bottom
        _round("Amanda Reyes — Lead Dentist\nMarcus Bell — Practice Manager\nDana Whitfield — Office Manager",
               scroll_height=2000, scroll_y=1800, card_count=3),                                       # round 2: now at bottom
    ]
    page = ScriptedPage(rounds)
    content = await read_page(page, instant_controller(), initial=INITIAL, max_scrolls=10, page_time_cap_s=5)
    assert "Marcus Bell" in content.text          # invisible to a one-shot snapshot
    assert "Dana Whitfield" in content.text
    assert content.stopped_reason == "bottom"
    assert content.scroll_rounds == 2


async def test_stops_after_two_stable_rounds_with_no_new_content():
    round0 = _round("Static content that never changes.", scroll_height=3000, scroll_y=0, card_count=1)
    same_after_scroll = _round("Static content that never changes.", scroll_height=3000, scroll_y=0, card_count=1)
    page = ScriptedPage([round0, same_after_scroll, same_after_scroll, same_after_scroll])
    content = await read_page(page, instant_controller(), initial=INITIAL, max_scrolls=10, page_time_cap_s=5)
    assert content.stopped_reason == "stable"
    assert content.scroll_rounds == 2               # two unchanged rounds after the first scroll


async def test_stops_at_max_scrolls():
    growing = [
        _round(f"content round {i}", scroll_height=5000, scroll_y=i * 100, card_count=i + 1)
        for i in range(10)
    ]
    page = ScriptedPage(growing)
    content = await read_page(page, instant_controller(), initial=INITIAL, max_scrolls=3, page_time_cap_s=30)
    assert content.stopped_reason == "max_scrolls"
    assert content.scroll_rounds == 3


async def test_empty_initial_and_no_scroll_growth_reports_empty():
    page = ScriptedPage([_round("", links=[], scroll_height=900, scroll_y=0, inner_height=900)])
    empty_initial = {"url": "https://empty.test", "text": "", "links": [], "headings": []}
    content = await read_page(page, instant_controller(), initial=empty_initial, max_scrolls=6, page_time_cap_s=5)
    assert content.stopped_reason == "empty"
    assert content.text == ""


async def test_no_scroll_capability_falls_back_to_the_initial_snapshot():
    """A page double with no working evaluate() (today's ScriptedBrowser
    fake, or a real page that's already closed) must not raise — read_page
    returns exactly the initial snapshot, unmodified, stopped_reason
    'bottom'. This is what keeps every pre-existing scripted-browser test
    working once agent.py routes extract_page_text through the reader."""
    class _NoEvaluatePage:
        url = "https://acmedental.test"

    content = await read_page(
        _NoEvaluatePage(), instant_controller(), initial=INITIAL, max_scrolls=6, page_time_cap_s=5,
    )
    assert content.stopped_reason == "bottom"
    assert content.scroll_rounds == 0
    assert content.text == INITIAL["text"]


async def test_evaluate_error_mid_scroll_returns_error_content_never_raises():
    class _BreaksDuringScroll:
        """Answers settle()'s readyState/text-length checks and the first
        metrics probe normally, succeeds on the scroll step itself, then
        fails on the re-fetch afterward — simulating a page that navigates
        away or closes mid-scroll."""
        url = "https://broken.test"

        def __init__(self):
            self._scrolled = False

        async def evaluate(self, js):
            if "document.readyState" in js:
                return "complete"
            if "innerText.length" in js:
                return 42  # constant -> settle() stabilises quickly
            if "scrollBy" in js:
                self._scrolled = True
                return None
            if self._scrolled:
                raise RuntimeError("navigation interrupted")
            if "scrollHeight" in js:
                return {"scrollHeight": 5000, "scrollY": 0, "innerHeight": 900, "cardCount": 1}  # not at bottom -> triggers a scroll
            raise RuntimeError("unexpected evaluate call")

    content = await read_page(
        _BreaksDuringScroll(), instant_controller(), initial=INITIAL, max_scrolls=6, page_time_cap_s=5,
    )
    assert content.stopped_reason == "error"
    assert content.url == "https://broken.test"


async def test_page_time_cap_stops_a_slow_growing_page():
    # Never stabilises and never reaches bottom — only the time cap can stop it.
    growing = [
        _round(f"content {i}", scroll_height=100000, scroll_y=i * 10, card_count=i + 1)
        for i in range(50)
    ]
    page = ScriptedPage(growing)
    content = await read_page(page, instant_controller(), initial=INITIAL, max_scrolls=1000, page_time_cap_s=0.05)
    assert content.stopped_reason == "page_time_cap"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `venv/Scripts/python.exe -m pytest tests/research_agent/test_reader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.research_agent.reader'`.

- [ ] **Step 4: Write `reader.py`**

```python
"""
reader.py — PageReader: reads the page currently open in the browser the
way a person would. The caller (agent.py) fetches the first snapshot
through the normal extract_page_text action and hands it in as `initial`;
read_page's job is to keep scrolling and accumulate more, stopping when
nothing new shows up (or a cap is hit) — and to fall back to `initial`
unchanged when the page object can't actually be scrolled (no working
evaluate(), e.g. a test double). Never raises — every exit path returns a
PageContent.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .pacing import PacingController

_MAX_TEXT_CHARS = 40000
_MAX_LINKS = 120
_MAX_HEADINGS = 40

_JS_TEXT = "() => document.body ? document.body.innerText : ''"
_JS_LINKS = """
() => Array.from(document.querySelectorAll('a[href]')).map(a => ({
    text: (a.innerText || '').trim().slice(0, 80),
    href: a.href || a.getAttribute('href') || '',
})).filter(l => l.href)
"""
_JS_METRICS = """
() => ({
    scrollHeight: document.body ? document.body.scrollHeight : 0,
    scrollY: window.scrollY,
    innerHeight: window.innerHeight,
    cardCount: document.querySelectorAll(
        "article, li, .card, [class*='card'], [class*='team'], [class*='member']"
    ).length,
})
"""
_JS_SCROLL_STEP = "() => window.scrollBy(0, Math.round(window.innerHeight * 0.8))"


@dataclass
class PageContent:
    url: str = ""
    text: str = ""
    links: List[Dict[str, str]] = field(default_factory=list)
    headings: List[str] = field(default_factory=list)
    scroll_rounds: int = 0
    stopped_reason: str = "bottom"  # "stable" | "max_scrolls" | "page_time_cap" | "bottom" | "empty" | "error"


def _dedup_links(links: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen, out = set(), []
    for l in links or []:
        href = l.get("href", "")
        if href and href not in seen:
            seen.add(href)
            out.append(l)
    return out[:_MAX_LINKS]


def _from_initial(url: str, initial: Dict[str, Any], scroll_rounds: int, stopped_reason: str) -> PageContent:
    text = initial.get("text") or ""
    links = _dedup_links(initial.get("links") or [])
    headings = (initial.get("headings") or [])[:_MAX_HEADINGS]
    if not text.strip() and not links:
        return PageContent(url=url, links=[], headings=headings,
                            scroll_rounds=scroll_rounds, stopped_reason="empty")
    return PageContent(url=url, text=text[:_MAX_TEXT_CHARS], links=links, headings=headings,
                        scroll_rounds=scroll_rounds, stopped_reason=stopped_reason)


async def read_page(
    page: Any, pacing: PacingController, *, initial: Dict[str, Any],
    max_scrolls: int, page_time_cap_s: float,
) -> PageContent:
    start = time.monotonic()
    url = initial.get("url") or getattr(page, "url", "") or ""

    try:
        await pacing.settle(page)
    except Exception:
        pass  # best-effort — proceed regardless

    try:
        metrics = (await page.evaluate(_JS_METRICS)) or {}
    except Exception:
        # No real scroll capability (a test double, or a page that's already
        # closed/navigated away) — the initial snapshot is all there is.
        return _from_initial(url, initial, scroll_rounds=0, stopped_reason="bottom")

    text = initial.get("text") or ""
    links = initial.get("links") or []
    headings = (initial.get("headings") or [])[:_MAX_HEADINGS]
    scroll_rounds = 0
    stable_rounds = 0
    stopped_reason = "bottom"
    last_scroll_height = metrics.get("scrollHeight", 0)
    last_text_len = len(text)
    last_card_count = metrics.get("cardCount", 0)

    while True:
        at_bottom = metrics.get("scrollY", 0) + metrics.get("innerHeight", 0) >= last_scroll_height
        if at_bottom:
            stopped_reason = "bottom"
            break
        if scroll_rounds >= max_scrolls:
            stopped_reason = "max_scrolls"
            break
        if time.monotonic() - start >= page_time_cap_s:
            stopped_reason = "page_time_cap"
            break

        try:
            await page.evaluate(_JS_SCROLL_STEP)
        except Exception:
            stopped_reason = "error"
            break
        scroll_rounds += 1
        await pacing.wait("after_scroll")

        try:
            new_text = (await page.evaluate(_JS_TEXT)) or ""
            new_links = (await page.evaluate(_JS_LINKS)) or []
            metrics = (await page.evaluate(_JS_METRICS)) or {}
        except Exception:
            stopped_reason = "error"
            break

        grew = (
            len(new_text) > last_text_len
            or metrics.get("scrollHeight", 0) > last_scroll_height
            or metrics.get("cardCount", 0) > last_card_count
        )
        text, links = new_text, new_links
        last_text_len = len(new_text)
        last_scroll_height = metrics.get("scrollHeight", last_scroll_height)
        last_card_count = metrics.get("cardCount", last_card_count)

        if grew:
            stable_rounds = 0
        else:
            stable_rounds += 1
            if stable_rounds >= 2:
                stopped_reason = "stable"
                break

    if stopped_reason == "error":
        return PageContent(url=url, stopped_reason="error")

    links = _dedup_links(links)
    if not text.strip() and not links:
        return PageContent(url=url, text="", links=[], headings=headings,
                            scroll_rounds=scroll_rounds, stopped_reason="empty")
    return PageContent(
        url=url, text=text[:_MAX_TEXT_CHARS], links=links, headings=headings,
        scroll_rounds=scroll_rounds, stopped_reason=stopped_reason,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/research_agent/test_reader.py tests/research_agent/test_pacing.py -v`
Expected: PASS, all tests, sub-second (`instant_controller()` never really sleeps).

- [ ] **Step 6: Commit**

```bash
git add backend/research_agent/reader.py tests/research_agent/_fakes.py tests/research_agent/test_reader.py
git commit -m "feat(research-agent): PageReader — settle, incremental scroll, stability-based stop"
```

---

### Task 4: Depth-mode presets (`config.py`)

**Files:**
- Modify: `backend/research_agent/config.py`
- Test: `tests/research_agent/test_config.py` (new)

**Interfaces:**
- Produces: `DEPTH_PRESETS: Dict[str, Dict[str, Any]]` (`quick|standard|deep|max`), `resolve_depth(depth: Optional[str]) -> str`, `get_research_config(depth: Optional[str] = None) -> Dict[str, Any]` (signature change — new optional param, fully backward compatible with every existing `await get_research_config()` call site).

- [ ] **Step 1: Write the failing tests**

`tests/research_agent/test_config.py`:
```python
import pytest

from backend.research_agent import config as racfg

pytestmark = pytest.mark.asyncio


def test_resolve_depth_known_names():
    assert racfg.resolve_depth("quick") == "quick"
    assert racfg.resolve_depth("Standard") == "standard"
    assert racfg.resolve_depth("DEEP") == "deep"
    assert racfg.resolve_depth("max") == "max"


def test_resolve_depth_unknown_or_none_falls_back_to_standard():
    assert racfg.resolve_depth("bogus") == "standard"
    assert racfg.resolve_depth(None) == "standard"
    assert racfg.resolve_depth("") == "standard"


async def test_default_config_matches_standard_depth(clean_db):
    default_cfg = await racfg.get_research_config()
    standard_cfg = await racfg.get_research_config(depth="standard")
    assert default_cfg["research_agent_max_pages_per_lead"] == standard_cfg["research_agent_max_pages_per_lead"]
    assert default_cfg["research_agent_pacing_profile"] == "standard"


async def test_deep_preset_widens_budgets(clean_db):
    cfg = await racfg.get_research_config(depth="deep")
    assert cfg["research_agent_pacing_profile"] == "deliberate"
    assert cfg["research_agent_max_pages_per_lead"] == 9
    assert cfg["research_agent_max_scrolls_per_page"] == 12
    assert cfg["research_agent_max_searches_per_lead"] == 6
    assert cfg["research_agent_max_actions_per_lead"] == 24
    assert cfg["research_agent_max_time_per_lead_seconds"] == 420
    assert cfg["research_agent_max_domain_seconds"] == 240
    assert cfg["research_agent_page_time_cap_seconds"] == 75


async def test_quick_preset_is_lean(clean_db):
    cfg = await racfg.get_research_config(depth="quick")
    assert cfg["research_agent_pacing_profile"] == "fast"
    assert cfg["research_agent_max_pages_per_lead"] == 2
    assert cfg["research_agent_max_actions_per_lead"] == 8


async def test_db_setting_overrides_the_preset(clean_db):
    db = clean_db
    await db.upsert_setting("research_agent_max_pages_per_lead", "3")
    cfg = await racfg.get_research_config(depth="deep")
    assert cfg["research_agent_max_pages_per_lead"] == 3   # DB wins over deep's preset value of 9


async def test_unset_global_depth_setting_used_when_no_depth_passed(clean_db):
    db = clean_db
    await db.upsert_setting("research_agent_research_depth", "max")
    cfg = await racfg.get_research_config()
    assert cfg["research_agent_research_depth"] == "max"
    assert cfg["research_agent_max_pages_per_lead"] == 14


async def test_explicit_depth_wins_over_stored_global_depth(clean_db):
    db = clean_db
    await db.upsert_setting("research_agent_research_depth", "max")
    cfg = await racfg.get_research_config(depth="quick")
    assert cfg["research_agent_research_depth"] == "quick"


async def test_new_keys_present_and_typed(clean_db):
    cfg = await racfg.get_research_config()
    assert isinstance(cfg["research_agent_allow_professional_profiles"], bool)
    assert cfg["research_agent_allow_professional_profiles"] is False
    assert isinstance(cfg["research_agent_time_budget_seconds"], int)
    assert isinstance(cfg["research_agent_max_queued_links"], int)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/Scripts/python.exe -m pytest tests/research_agent/test_config.py -v`
Expected: FAIL — `AttributeError: module 'backend.research_agent.config' has no attribute 'resolve_depth'` (and `DEPTH_PRESETS`).

- [ ] **Step 3: Modify `config.py`**

Replace the whole file:
```python
"""
config.py — Browser Research Agent budgets/limits.

Layering: app_settings DB override > active depth preset > .env/pydantic
default > this module's hardcoded fallback. The depth preset is the one new
layer (Cycle 1 of the deliberate-page-research work) — everything else
follows the existing app-wide pattern (see backend/scraper.py::_scraper_cfg,
backend/ai_brain.py::_ollama_cfg).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .. import database as db
from ..config import get_settings

_env = get_settings()

_DEFAULTS: Dict[str, Any] = {
    "research_agent_headless": False,          # visible Chrome by default in development
    "research_agent_max_actions_per_lead": 12,
    "research_agent_max_searches_per_lead": 4,
    "research_agent_max_pages_per_lead": 5,
    "research_agent_max_time_per_lead_seconds": 180,
    "research_agent_max_total_leads": 20,
    "research_agent_max_consecutive_failures": 3,
    "research_agent_max_geographic_units": 5,
    "research_agent_page_timeout_ms": 20000,
    "research_agent_save_to_leads": True,
    # Deliberate-page-research (Cycle 1) additions — these are the
    # "standard"-depth values; DEPTH_PRESETS overrides them per depth mode.
    "research_agent_pacing_profile": "standard",
    "research_agent_max_scrolls_per_page": 6,
    "research_agent_max_domain_seconds": 120,
    "research_agent_page_time_cap_seconds": 40,
    "research_agent_max_queued_links": 6,
    "research_agent_time_budget_seconds": 0,           # 0 = no session-level time budget
    "research_agent_allow_professional_profiles": False,
}

# One preset per research_depth value — see
# docs/superpowers/specs/2026-09-04-deliberate-page-research-design.md §7.
DEPTH_PRESETS: Dict[str, Dict[str, Any]] = {
    "quick": {
        "research_agent_pacing_profile": "fast",
        "research_agent_max_pages_per_lead": 2,
        "research_agent_max_scrolls_per_page": 3,
        "research_agent_max_searches_per_lead": 2,
        "research_agent_max_actions_per_lead": 8,
        "research_agent_max_time_per_lead_seconds": 90,
        "research_agent_max_domain_seconds": 45,
        "research_agent_page_time_cap_seconds": 20,
    },
    "standard": {
        "research_agent_pacing_profile": "standard",
        "research_agent_max_pages_per_lead": 5,
        "research_agent_max_scrolls_per_page": 6,
        "research_agent_max_searches_per_lead": 4,
        "research_agent_max_actions_per_lead": 14,
        "research_agent_max_time_per_lead_seconds": 200,
        "research_agent_max_domain_seconds": 120,
        "research_agent_page_time_cap_seconds": 40,
    },
    "deep": {
        "research_agent_pacing_profile": "deliberate",
        "research_agent_max_pages_per_lead": 9,
        "research_agent_max_scrolls_per_page": 12,
        "research_agent_max_searches_per_lead": 6,
        "research_agent_max_actions_per_lead": 24,
        "research_agent_max_time_per_lead_seconds": 420,
        "research_agent_max_domain_seconds": 240,
        "research_agent_page_time_cap_seconds": 75,
    },
    "max": {
        "research_agent_pacing_profile": "deliberate",
        "research_agent_max_pages_per_lead": 14,
        "research_agent_max_scrolls_per_page": 20,
        "research_agent_max_searches_per_lead": 8,
        "research_agent_max_actions_per_lead": 36,
        "research_agent_max_time_per_lead_seconds": 900,
        "research_agent_max_domain_seconds": 480,
        "research_agent_page_time_cap_seconds": 120,
    },
}
_DEFAULT_DEPTH = "standard"


def resolve_depth(depth: Optional[str]) -> str:
    d = (depth or _DEFAULT_DEPTH).strip().lower()
    return d if d in DEPTH_PRESETS else _DEFAULT_DEPTH


async def get_research_config(depth: Optional[str] = None) -> Dict[str, Any]:
    """Read budgets: app_settings DB override > active depth preset >
    .env/pydantic default > this module's hardcoded fallback.

    `depth` picks the preset. If not given, the stored
    `research_agent_research_depth` app_setting is used (so an operator can
    change every session's default depth from Settings without a deploy);
    if that isn't set either, "standard"."""
    stored = await db.get_all_settings()
    if depth is None:
        depth = stored.get("research_agent_research_depth")
    resolved_depth = resolve_depth(depth)
    preset = DEPTH_PRESETS[resolved_depth]

    cfg: Dict[str, Any] = {}
    for key, hardcoded_default in _DEFAULTS.items():
        default = preset.get(key, getattr(_env, key, hardcoded_default))
        raw = stored.get(key)
        if raw is None:
            cfg[key] = default
        elif isinstance(default, bool):
            cfg[key] = str(raw).lower() == "true"
        elif isinstance(default, int):
            try:
                cfg[key] = int(raw)
            except (TypeError, ValueError):
                cfg[key] = default
        else:
            cfg[key] = raw
    cfg["research_agent_research_depth"] = resolved_depth
    return cfg
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/research_agent/test_config.py -v`
Expected: PASS, all 9 tests.

- [ ] **Step 5: Run the existing research_agent suite to confirm no regression**

Run: `venv/Scripts/python.exe -m pytest tests/research_agent/ -q`
Expected: PASS — every existing call site (`agent.py`, `session.py`) calls `get_research_config()` with zero or one positional/keyword arg it already used; the new `depth` param is optional and additive.

- [ ] **Step 6: Commit**

```bash
git add backend/research_agent/config.py tests/research_agent/test_config.py
git commit -m "feat(research-agent): depth-mode presets (quick/standard/deep/max)"
```

---

### Task 5: Crawl breadth — scored link relevance (`extraction.py`)

**Files:**
- Modify: `backend/research_agent/extraction.py`
- Modify: `tests/research_agent/test_extraction.py`

**Interfaces:**
- Produces: `link_relevance(href: str, anchor_text: str = "") -> int` (0/1/2/3). `is_relevant_nav_link(anchor_text: str) -> bool` keeps its exact existing signature and behavior for current callers (becomes a thin wrapper).

- [ ] **Step 1: Write the failing tests**

Append to `tests/research_agent/test_extraction.py`:
```python
def test_link_relevance_people_pages_score_highest():
    assert extraction.link_relevance("https://x.test/team", "Meet Our Team") == 3
    assert extraction.link_relevance("https://x.test/about", "About") == 3
    assert extraction.link_relevance("https://x.test/providers", "Our Providers") == 3
    assert extraction.link_relevance("https://x.test/contact", "Contact Us") == 3


def test_link_relevance_offering_pages_score_medium():
    assert extraction.link_relevance("https://x.test/services", "Services") == 2
    assert extraction.link_relevance("https://x.test/book-appointment", "Book Now") == 2
    assert extraction.link_relevance("https://x.test/pricing", "Pricing") == 2


def test_link_relevance_peripheral_pages_score_low():
    assert extraction.link_relevance("https://x.test/careers", "Careers") == 1
    assert extraction.link_relevance("https://x.test/blog/post-1", "Blog") == 1


def test_link_relevance_irrelevant_pages_score_zero():
    assert extraction.link_relevance("https://x.test/shop", "Shop Now") == 0
    assert extraction.link_relevance("https://x.test/cart", "Cart") == 0


def test_link_relevance_checks_href_even_with_no_anchor_text():
    assert extraction.link_relevance("https://x.test/our-team/", "") == 3


def test_is_relevant_nav_link_still_works_as_a_shim():
    assert extraction.is_relevant_nav_link("Meet the Team")
    assert extraction.is_relevant_nav_link("Contact")
    assert not extraction.is_relevant_nav_link("Shop Now")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/Scripts/python.exe -m pytest tests/research_agent/test_extraction.py -v`
Expected: FAIL — `AttributeError: module 'backend.research_agent.extraction' has no attribute 'link_relevance'`.

- [ ] **Step 3: Modify `extraction.py`**

Replace the `_TEAM_PAGE_HINTS` constant and `is_relevant_nav_link` function with:
```python
# Scored link map — replaces the old flat _TEAM_PAGE_HINTS regex. Weight 3 =
# likely to name a person; weight 2 = an offering/booking page (useful for
# services/tech context, Cycle 2); weight 1 = peripheral but sometimes
# useful; 0 = not worth queuing.
_LINK_HINTS_BY_WEIGHT = (
    (3, re.compile(
        r"\b(contact|about|team|our[- ]team|meet[- ]the[- ]team|staff|leadership|"
        r"management|providers?|doctors?|physicians?|dentists?|attorneys?|"
        r"people|who[- ]we[- ]are)\b", re.I,
    )),
    (2, re.compile(
        r"\b(services?|treatments?|procedures?|what[- ]we[- ]do|specialt(y|ies)|"
        r"pricing|plans?|fees?|book(ing)?|appointments?|schedule|request|"
        r"patient[- ]portal|new[- ]patients?)\b", re.I,
    )),
    (1, re.compile(
        r"\b(locations?|offices?|careers?|jobs?|blog|news|press|insights?)\b", re.I,
    )),
)


def link_relevance(href: str, anchor_text: str = "") -> int:
    """0 = not worth queuing; higher = more likely to hold people or
    offering information. Scored from the href path and anchor text
    together, so a link is still recognised even with no visible text."""
    haystack = f"{anchor_text} {href}"
    for weight, pattern in _LINK_HINTS_BY_WEIGHT:
        if pattern.search(haystack):
            return weight
    return 0


def is_relevant_nav_link(anchor_text: str) -> bool:
    """Back-compat shim over link_relevance — True iff the text alone scores
    above 0. Prefer link_relevance directly in new code (it also recognises
    offering/peripheral pages, not just people pages)."""
    return link_relevance(anchor_text, "") > 0
```

Leave everything else in the file (`extract_emails`, `extract_phones`, `find_role_sentences`, `has_secure_contact_form`, the contact-link helpers) unchanged. Remove the now-unused `_TEAM_PAGE_HINTS` name entirely (nothing else references it).

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/research_agent/test_extraction.py -v`
Expected: PASS, all tests (the pre-existing `test_is_relevant_nav_link` at the top of the file and the new ones).

- [ ] **Step 5: Commit**

```bash
git add backend/research_agent/extraction.py tests/research_agent/test_extraction.py
git commit -m "feat(research-agent): scored link relevance — widen crawl breadth beyond contact/about/team"
```

---

### Task 6: Retire the broken `scroll`/`screenshot` actions from the LLM vocabulary

**Files:**
- Modify: `backend/research_agent/models.py`
- Modify: `backend/research_agent/actions.py`
- Modify: `backend/research_agent/prompts.py`
- Modify: `tests/research_agent/test_actions.py`

**Interfaces:**
- Produces: `models.VALID_ACTIONS` without `scroll`/`screenshot`. `actions.validate_action` unchanged in structure — the `scroll` branch is simply removed (an action named `"scroll"` now fails the `VALID_ACTIONS` membership check at the top of the function, exactly like any other retired/unknown action name).

This task does not touch `browser.py` — `BrowserController.scroll`/`.screenshot` and their entries in `execute()`'s handler dict are left as-is (dead code, harmless, reachable only if something bypassed `validate_action`, which nothing does). `reader.py` (Task 3) never calls them; it evaluates its own scroll JS directly on `browser._page`.

- [ ] **Step 1: Write the failing test**

In `tests/research_agent/test_actions.py`, replace the `VALID_EXAMPLES` list (remove the two `scroll` rows and the `screenshot` row) and replace `test_scroll_requires_valid_direction` with a retirement test:

```python
VALID_EXAMPLES = [
    AgentAction(action="google_search", params={"query": "dental clinics Abbeville"}),
    AgentAction(action="open_url", params={"url": "https://example-dental.test"}),
    AgentAction(action="extract_page_text", params={}),
    AgentAction(action="find_links", params={}),
    AgentAction(action="find_links", params={"keyword": "contact"}),
    AgentAction(action="click", params={"text": "Contact Us"}),
    AgentAction(action="click", params={"selector": "#contact-link"}),
    AgentAction(action="go_back", params={}),
    AgentAction(action="open_new_tab", params={"url": "https://example-dental.test"}),
    AgentAction(action="save_evidence", params={"field_name": "business_phone", "value": "5551234567", "confidence": 0.8, "status": "FOUND"}),
    AgentAction(action="finish_research", params={"reason": "done"}),
]


@pytest.mark.parametrize("action", VALID_EXAMPLES, ids=[a.action for a in VALID_EXAMPLES])
def test_valid_actions_accepted(action):
    validate_action(action)  # must not raise


def test_scroll_and_screenshot_are_retired_actions():
    """Scrolling and screenshot capture are now owned entirely by
    PageReader (reader.py) — the LLM can no longer request them as
    standalone actions."""
    for retired in ("scroll", "screenshot"):
        with pytest.raises(ActionValidationError, match="Unknown action"):
            validate_action(AgentAction(action=retired, params={"direction": "down"}))
```

(Leave every other test in the file unchanged.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/Scripts/python.exe -m pytest tests/research_agent/test_actions.py -v`
Expected: FAIL — `test_scroll_and_screenshot_are_retired_actions` fails because `scroll`/`screenshot` are still in `VALID_ACTIONS` (no `ActionValidationError` raised).

- [ ] **Step 3: Modify `models.py`**

In `backend/research_agent/models.py`, change:
```python
VALID_ACTIONS = frozenset({
    "google_search", "open_url", "extract_page_text", "find_links",
    "click", "scroll", "go_back", "open_new_tab", "screenshot",
    "save_evidence", "finish_research",
})
```
to:
```python
VALID_ACTIONS = frozenset({
    "google_search", "open_url", "extract_page_text", "find_links",
    "click", "go_back", "open_new_tab",
    "save_evidence", "finish_research",
})
```

- [ ] **Step 4: Modify `actions.py`**

In `backend/research_agent/actions.py::validate_action`, remove the `scroll` branch:
```python
    elif action.action == "scroll":
        if p.get("direction") not in ("up", "down"):
            raise ActionValidationError("scroll requires direction 'up' or 'down'")
```
(The `if action.action not in VALID_ACTIONS: raise ActionValidationError(...)` guard at the top of the function already rejects `"scroll"` and `"screenshot"` now that they're out of `VALID_ACTIONS` — no replacement branch needed.)

- [ ] **Step 5: Modify `prompts.py`**

In `backend/research_agent/prompts.py`, remove these two lines from `ACTION_TOOL_DESCRIPTIONS`:
```python
- scroll: {"action": "scroll", "direction": "down"} — scroll the current page
```
```python
- screenshot: {"action": "screenshot"} — capture a screenshot for evidence
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/research_agent/test_actions.py tests/research_agent/test_llm.py -v`
Expected: PASS. (`test_llm.py` is included because it may assert on prompt content — read its current assertions if any fail and adjust only the ones that literally string-match the removed lines; do not change its other assertions.)

- [ ] **Step 7: Run the full research_agent suite**

Run: `venv/Scripts/python.exe -m pytest tests/research_agent/ -q`
Expected: PASS. If any test elsewhere constructs `AgentAction(action="scroll", ...)` or `action="screenshot"` and asserts success, fix that test the same way Step 1 did (it's now a retired action, not a valid one).

- [ ] **Step 8: Commit**

```bash
git add backend/research_agent/models.py backend/research_agent/actions.py backend/research_agent/prompts.py tests/research_agent/test_actions.py
git commit -m "refactor(research-agent): retire the broken scroll/screenshot LLM actions"
```

---

### Task 7: Wire pacing + `PageReader` into `agent.py`

**Files:**
- Modify: `backend/research_agent/agent.py`
- Modify: `tests/research_agent/test_agent_loop.py`

**Interfaces:**
- Consumes: `pacing.PacingController`, `pacing.instant_controller`, `pacing.resolve_profile` (Task 2); `reader.read_page(page, pacing, *, initial, max_scrolls, page_time_cap_s)`, `reader.PageContent` (Task 3); `extraction.link_relevance` (Task 5).
- Produces: `ResearchAgent.__init__(self, browser, cfg, niche, pacing=None)` — `pacing` optional, defaults to `pacing_mod.instant_controller()` so every existing 3-positional-arg call site in the test suite keeps working unchanged. `run_research_session(..., pacing=None)` — one new optional keyword-only-in-practice parameter appended after `discovery_fallback`; every existing call site is unaffected, and it's how a test injects a spy `PacingController` without needing a real or scripted browser at the session level.

- [ ] **Step 1: Write the failing tests**

Append to `tests/research_agent/test_agent_loop.py` (reuse the file's existing `_cfg`, `_scripted_llm`, `ScriptedBrowser`, `ok`, `err` helpers already imported at the top):

```python
from backend.research_agent.models import ResearchLead
from backend.research_agent.pacing import PACING_PROFILES, PacingController


class _RecordingSleep:
    def __init__(self):
        self.calls = []

    async def __call__(self, seconds):
        self.calls.append(seconds)


TALL_TEAM_PAGE_ROUND_1 = "Acme Family Dental\nContact us at info@acmefamilydental.test."
TALL_TEAM_PAGE_ROUND_2 = TALL_TEAM_PAGE_ROUND_1 + "\nEmma Papp — Office Manager, has been with the practice for 10 years."


async def test_extract_page_text_is_routed_through_the_reader(monkeypatch):
    """R2 (page just opened, not yet read) must now produce content via
    PageReader — proven here by content that only appears after a scroll,
    which the OLD bare extract_page_text action could never see. The first
    snapshot still comes from the normal scripted extract_page_text result
    (round 1's text); browser._page is swapped for a fake that supports
    scrolling so the reader can fetch round 2's content on top of it."""
    scrolled = {"n": 0}

    async def fake_evaluate(js):
        if "document.readyState" in js:
            return "complete"
        if "innerText.length" in js:
            return len(TALL_TEAM_PAGE_ROUND_1)  # settle() sees this before any scroll
        if "scrollBy" in js:
            scrolled["n"] += 1
            return None
        if "scrollHeight" in js and "querySelectorAll" in js:
            # Not at bottom before the scroll; at bottom after it.
            return {"scrollHeight": 2000, "scrollY": 2000 if scrolled["n"] else 0,
                     "innerHeight": 900, "cardCount": 2 if scrolled["n"] else 1}
        if "querySelectorAll('a" in js:
            return []
        if "innerText" in js:
            return TALL_TEAM_PAGE_ROUND_2  # only asked for AFTER the scroll succeeds
        raise AssertionError(js)

    class _FakePageForReader:
        url = "https://acmefamilydental.test"
        evaluate = staticmethod(fake_evaluate)

        async def wait_for_timeout(self, ms):
            return None

    browser = ScriptedBrowser({
        "open_url": [ok("open_url", url="https://acmefamilydental.test", title="Acme Family Dental")],
        "extract_page_text": [ok("extract_page_text", url="https://acmefamilydental.test", text=TALL_TEAM_PAGE_ROUND_1)],
    })
    browser._page = _FakePageForReader()

    actions = [
        AgentAction(action="open_url", params={"url": "https://acmefamilydental.test"}),
        AgentAction(action="finish_research", params={"reason": "done"}),
    ]
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", _scripted_llm(actions))
    monkeypatch.setattr(agent_mod.llm_mod, "extract_fields", _no_management_extraction)

    a = agent_mod.ResearchAgent(browser, _cfg(), "dental clinics")
    lead = await a.research_business({"business_name": "Acme Family Dental", "website": "https://acmefamilydental.test"})

    assert lead.business_email == "info@acmefamilydental.test"
    # Emma Papp only appears in round 2's text, reachable only via scrolling.
    assert scrolled["n"] >= 1


async def test_pacing_wait_called_between_leads(monkeypatch):
    """Session-level pacing (between_leads) is exercised even without a real
    or scripted browser — follows this file's existing _FakeBrowserCtx +
    ResearchAgent.research_business monkeypatch pattern (see
    test_one_failed_lead_does_not_stop_the_batch above) and injects a spy
    PacingController via run_research_session's new `pacing=` parameter."""
    seen_ops = []

    class _SpyPacing(PacingController):
        async def wait(self, op):
            seen_ops.append(op)
            return await super().wait(op)

    spy = _SpyPacing(PACING_PROFILES["standard"], sleep_fn=_RecordingSleep())

    async def fake_research_business(self, hint, **kwargs):
        return ResearchLead(business_name=hint["business_name"], business_phone="555",
                            business_website=hint.get("website"))

    monkeypatch.setattr(agent_mod.ResearchAgent, "research_business", fake_research_business)

    class _FakeBrowserCtx:
        async def __aenter__(self):
            return ScriptedBrowser({})
        async def __aexit__(self, *exc):
            return False
    monkeypatch.setattr(agent_mod, "BrowserController", lambda **kw: _FakeBrowserCtx())

    result = await agent_mod.run_research_session(
        niche="dental clinics", location="Abbeville, LA", target_count=2,
        seed_businesses=[{"business_name": "A Inc", "website": "https://a.test", "city": "Abbeville"},
                          {"business_name": "B Inc", "website": "https://b.test", "city": "Abbeville"}],
        cfg=_cfg(), pacing=spy,
    )
    assert len(result["leads"]) == 2
    assert seen_ops.count("between_leads") >= 1


async def test_domain_time_cap_finalises_a_slow_candidate(monkeypatch):
    """A candidate that spends longer than max_domain_seconds on one domain
    is finalised with whatever was found — the session moves on rather than
    hanging on one slow site."""
    browser = ScriptedBrowser({
        "open_url": [ok("open_url", url="https://slow.test", title="Slow Co")],
        "extract_page_text": [ok("extract_page_text", url="https://slow.test", text="Slow Co. No contact info here.")],
        "find_links": [ok("find_links", links=[])] * 20,
    })

    async def _always_read_more(state_summary, cfg=None):
        # Never says finish_research on its own — only the domain cap should end the loop.
        return AgentAction(action="find_links", params={})

    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", _always_read_more)
    monkeypatch.setattr(agent_mod.llm_mod, "extract_fields", _no_management_extraction)

    cfg = _cfg(max_domain_seconds=0, max_actions_per_lead=50, max_time_per_lead_seconds=60)
    a = agent_mod.ResearchAgent(browser, cfg, "dental clinics")
    lead = await a.research_business({"business_name": "Slow Co", "website": "https://slow.test"})
    assert lead.actions_taken < 50   # stopped well before the action budget, via the domain cap
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/Scripts/python.exe -m pytest tests/research_agent/test_agent_loop.py -k "reader or pacing_wait or domain_time" -v`
Expected: FAIL — `test_extract_page_text_is_routed_through_the_reader` fails because `extract_page_text` still calls `browser.execute` directly (no reader involved, so `fake_evaluate`'s `scrollBy` branch is never hit); `test_domain_time_cap_finalises_a_slow_candidate` fails because there's no `max_domain_seconds` budget check yet (the loop runs to `max_actions_per_lead`=50).

- [ ] **Step 3: Modify `agent.py`**

At the top of the file, add imports:
```python
from . import extraction
from . import pacing as pacing_mod
from . import reader as reader_mod
```
(`extraction` is already imported — leave it; add the two new ones alongside it.)

Change `ResearchAgent.__init__`:
```python
class ResearchAgent:
    """One instance per research session. Owns the BrowserController."""

    def __init__(
        self, browser: BrowserController, cfg: Dict[str, Any], niche: str,
        pacing: Optional[pacing_mod.PacingController] = None,
    ) -> None:
        self.browser = browser
        self.cfg = cfg
        self.niche = niche
        # Callers that build a ResearchAgent directly without going through
        # run_research_session's depth/profile resolution (today: tests) get
        # a zero-delay controller — production sessions always pass a real
        # one built from the resolved pacing profile.
        self.pacing = pacing or pacing_mod.instant_controller()
```

In `research_business`, add domain-tracking state next to the existing bookkeeping (right after `last_opened_url = ...` / `just_opened = False`):
```python
        current_domain: Optional[str] = None
        domain_entered_at: float = 0.0
        max_domain_seconds = self.cfg.get("research_agent_max_domain_seconds", 120)
        max_scrolls_per_page = self.cfg.get("research_agent_max_scrolls_per_page", 6)
        page_time_cap_s = self.cfg.get("research_agent_page_time_cap_seconds", 40)
```

Add a domain-budget check to the guard block at the top of the `while True:` loop, immediately after the existing `max_failures` check:
```python
            if current_domain and time.monotonic() - domain_entered_at > max_domain_seconds:
                logger.info("[RESEARCH] Lead '%s': max_domain_seconds reached on %s", lead.business_name, current_domain)
                break
```

Replace the action-dispatch block (`logger.info("[LLM] Action: %s ..."` through `result = await self.browser.execute(action)`) with:
```python
            logger.info("[LLM] Action: %s — %s", action.action, action.reason or "")
            if action.action in ("open_url", "open_new_tab"):
                target_domain = _domain(action.params.get("url", ""))
                if target_domain and target_domain != current_domain:
                    await self.pacing.wait("between_domains" if current_domain else "navigation")
                    current_domain, domain_entered_at = target_domain, time.monotonic()
                else:
                    await self.pacing.wait("navigation")
                result = await self.browser.execute(action)
            elif action.action == "extract_page_text":
                result = await self._read_current_page(max_scrolls_per_page, page_time_cap_s)
            elif action.action == "click":
                await self.pacing.wait("link_selection")
                result = await self.browser.execute(action)
            else:
                result = await self.browser.execute(action)
            logger.info("[BROWSER] %s -> %s", action.action, result.status)
```

Add a new method (near `_forced_action`). It fetches the first snapshot exactly the way the loop always has — `self.browser.execute(...)` — then hands that to the reader as `initial` for scroll-continuation. A page double with no real scroll capability (every existing `ScriptedBrowser`-based test) makes `reader.read_page` fall back to returning that first snapshot unchanged (Task 3's `test_no_scroll_capability_falls_back_to_the_initial_snapshot`), so this is a pure addition — no existing test's scripted `extract_page_text` result stops being used:
```python
    async def _read_current_page(self, max_scrolls: int, page_time_cap_s: float) -> ActionResult:
        first = await self.browser.execute(AgentAction(action="extract_page_text", params={}))
        if first.status != "success":
            return first
        content = await reader_mod.read_page(
            self.browser._page, self.pacing, initial=first.data,
            max_scrolls=max_scrolls, page_time_cap_s=page_time_cap_s,
        )
        status = "error" if content.stopped_reason == "error" else "success"
        return ActionResult(action="extract_page_text", status=status, data={
            "url": content.url, "text": content.text, "links": content.links,
            "headings": content.headings, "scroll_rounds": content.scroll_rounds,
            "stopped_reason": content.stopped_reason,
        })
```

Update `_collect_relevant_links` to use scored relevance and a configurable cap (it stays a `@staticmethod`, `max_queued` is an added parameter with a default so any direct caller not yet updated keeps working):
```python
    @staticmethod
    def _collect_relevant_links(links: List[Dict[str, str]], opened_urls: Optional[set],
                                relevant_links: Optional[List[str]], max_queued: int = 6) -> None:
        """Queue useful page URLs the loop hasn't opened yet (people pages
        score highest, then offering pages, then peripheral pages), so R3 in
        _forced_action can follow one when it's still worth visiting."""
        if relevant_links is None:
            return
        opened = opened_urls or set()
        for link in links or []:
            href = (link.get("href") or "").strip()
            text = link.get("text") or ""
            if not href.startswith("http"):
                continue
            if href in opened or href in relevant_links:
                continue
            if extraction.link_relevance(href, text) > 0:
                relevant_links.append(href)
        relevant_links.sort(key=lambda u: -extraction.link_relevance(u, ""))
        del relevant_links[max_queued:]
```

Update the one call site (in `_process_result`'s `extract_page_text` branch) to pass the configured cap:
```python
        elif action.action == "extract_page_text":
            self._collect_relevant_links(
                data.get("links", []), opened_urls, relevant_links,
                max_queued=self.cfg.get("research_agent_max_queued_links", 6),
            )
            await self._process_page_text(lead, data)
```
(Also update the `find_links` branch's call the same way, for consistency: `self._collect_relevant_links(data.get("links", []), opened_urls, relevant_links, max_queued=self.cfg.get("research_agent_max_queued_links", 6))`.)

Finally, in `run_research_session`, build a real `PacingController` from the resolved config and pass it to `ResearchAgent`, and enforce the session time budget. Change:
```python
    async with BrowserController(
        headless=resolved_cfg["research_agent_headless"],
        page_timeout_ms=resolved_cfg["research_agent_page_timeout_ms"],
    ) as browser:
        agent = ResearchAgent(browser, resolved_cfg, niche)
```
to:
```python
    session_pacing = pacing or pacing_mod.PacingController(
        pacing_mod.resolve_profile(resolved_cfg.get("research_agent_pacing_profile"))
    )
    time_budget_s = float(resolved_cfg.get("research_agent_time_budget_seconds") or 0)
    session_started = time.monotonic()

    async with BrowserController(
        headless=resolved_cfg["research_agent_headless"],
        page_timeout_ms=resolved_cfg["research_agent_page_timeout_ms"],
    ) as browser:
        agent = ResearchAgent(browser, resolved_cfg, niche, session_pacing)
```

Also add `pacing: Optional[pacing_mod.PacingController] = None` to `run_research_session`'s parameter list (alongside the existing `discovery_fallback: Optional[Any] = None,` line — add it right after, as the last parameter). This is purely additive: every existing caller (`session.py`, and every test that doesn't pass it) is unaffected, and it's what lets a test inject a spy controller to observe pacing without needing a real or scripted browser at the `run_research_session` level (see the `test_pacing_wait_called_between_leads` test below, which follows this file's existing `_FakeBrowserCtx` + `ResearchAgent.research_business` monkeypatch pattern rather than trying to drive a real `BrowserController`).

And inside the `for hint in candidates:` loop, right after the existing `if len(leads) >= target_count: break` line, add the time-budget check, and right before `lead = await agent.research_business(...)` add the between-leads pacing:
```python
                if time_budget_s and time.monotonic() - session_started > time_budget_s:
                    logger.info(
                        "[AGENT] Session time budget (%.0fs) reached — stopping with partial results",
                        time_budget_s,
                    )
                    return {"leads": leads, "failed_count": failed_count, "skipped_count": skipped_count,
                            "geo_tasks": geo_tasks, "processed_keys": processed_keys}
```
(placed alongside the existing budget/cancellation checks in that loop) and:
```python
                await session_pacing.wait("between_leads")
                try:
                    lead = await agent.research_business(hint, on_action=on_action, is_cancelled=is_cancelled)
```
(the `await session_pacing.wait("between_leads")` line goes immediately before the existing `try:` block that calls `agent.research_business`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/research_agent/test_agent_loop.py -v`
Expected: PASS, all tests including the three new ones and every pre-existing one (the `pacing=None` default keeps every `ResearchAgent(browser, cfg, niche)` 3-arg call site working; `instant_controller()` adds zero wall-clock time).

- [ ] **Step 5: Run the full research_agent suite**

Run: `venv/Scripts/python.exe -m pytest tests/research_agent/ -q`
Expected: PASS — no regression in `test_session.py`, `test_handoff.py`, `test_no_fabrication_guards.py`, `test_validation.py`, `test_sessions_list.py`, `test_research_agent_router.py`.

- [ ] **Step 6: Run the full backend suite**

Run: `venv/Scripts/python.exe -m pytest -q`
Expected: 827+ passed, 0 failed (baseline plus this task's new tests).

- [ ] **Step 7: Commit**

```bash
git add backend/research_agent/agent.py tests/research_agent/test_agent_loop.py
git commit -m "feat(research-agent): wire PageReader + PacingController into the research loop"
```

---

### Task 8: Thread depth into `session.py`

**Files:**
- Modify: `backend/research_agent/session.py`
- Modify: `tests/research_agent/test_session.py`

**Interfaces:**
- Consumes: `config.get_research_config(depth=...)` (Task 4).
- Produces: `run_research_session_persisted(session_id, niche, location, target_count, seed_businesses=None, research_depth=None)` — one new optional keyword param.

No DB column is added — `research_depth` is not persisted per-session (see the design doc's explicit "no schema change" constraint for Cycle 1). Every session today gets its depth from the global `research_agent_research_depth` app_setting (default `"standard"`) via `get_research_config()`'s existing fallback (Task 4), unless a caller passes `research_depth` explicitly. `enqueue_session`'s `_handler` closure is deliberately **not** changed to thread a value from `session_row` — there is no column to read it from yet; a future cycle that adds per-session depth also adds the column and updates this call site.

- [ ] **Step 1: Write the failing test**

Add to `tests/research_agent/test_session.py` — it already does `from backend.research_agent import session as session_mod` at the top (confirmed), so these use that same alias:
```python
async def test_research_depth_param_is_threaded_into_get_research_config(clean_db, monkeypatch):
    db = clean_db
    sid = await db.create_research_session({"niche": "dental clinic", "location": "Abbeville, LA", "target_count": 1})

    captured = {}

    async def fake_get_research_config(depth=None):
        captured["depth"] = depth
        return {
            "research_agent_save_to_leads": False, "research_agent_headless": True,
            "research_agent_page_timeout_ms": 20000, "research_agent_pacing_profile": "fast",
            "research_agent_max_geographic_units": 1,
        }

    async def fake_run_research_session(**kwargs):
        return {"leads": [], "failed_count": 0, "skipped_count": 0, "geo_tasks": [], "processed_keys": []}

    monkeypatch.setattr(session_mod, "get_research_config", fake_get_research_config)
    monkeypatch.setattr(session_mod, "run_research_session", fake_run_research_session)

    await session_mod.run_research_session_persisted(
        sid, "dental clinic", "Abbeville, LA", 1, research_depth="deep",
    )
    assert captured["depth"] == "deep"


async def test_research_depth_defaults_to_none_when_not_passed(clean_db, monkeypatch):
    db = clean_db
    sid = await db.create_research_session({"niche": "dental clinic", "location": "Abbeville, LA", "target_count": 1})

    captured = {}

    async def fake_get_research_config(depth=None):
        captured["depth"] = depth
        return {
            "research_agent_save_to_leads": False, "research_agent_headless": True,
            "research_agent_page_timeout_ms": 20000, "research_agent_pacing_profile": "fast",
            "research_agent_max_geographic_units": 1,
        }

    async def fake_run_research_session(**kwargs):
        return {"leads": [], "failed_count": 0, "skipped_count": 0, "geo_tasks": [], "processed_keys": []}

    monkeypatch.setattr(session_mod, "get_research_config", fake_get_research_config)
    monkeypatch.setattr(session_mod, "run_research_session", fake_run_research_session)

    await session_mod.run_research_session_persisted(sid, "dental clinic", "Abbeville, LA", 1)
    assert captured["depth"] is None   # get_research_config resolves the global setting itself
```

(If the file doesn't already `import backend.research_agent.session as session_mod` at the top, add that import rather than repeating the module path inline — match whatever import alias the file already uses; check before writing.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/Scripts/python.exe -m pytest tests/research_agent/test_session.py -k research_depth -v`
Expected: FAIL — `run_research_session_persisted()` raises `TypeError: run_research_session_persisted() got an unexpected keyword argument 'research_depth'`.

- [ ] **Step 3: Modify `session.py`**

Change the signature and the one call site:
```python
async def run_research_session_persisted(
    session_id: int,
    niche: str,
    location: str,
    target_count: int,
    seed_businesses: Optional[List[Dict[str, Any]]] = None,
    research_depth: Optional[str] = None,
) -> None:
    """JobQueue handler body. Never raises out — any unhandled exception is
    caught and persisted as status=FAILED so the session row is always left
    in a terminal, queryable state (matches Phase 1's quick_search.py
    precedent for the same failure-visibility reason).

    `research_depth` selects the quick/standard/deep/max preset for this
    run (see config.py::DEPTH_PRESETS); when not given, get_research_config
    falls back to the stored research_agent_research_depth app_setting."""
    cfg = await get_research_config(depth=research_depth)
    save_to_leads = cfg["research_agent_save_to_leads"]
```
(Only the `def` line and the `cfg = await get_research_config()` line change — everything else in the function is untouched.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/research_agent/test_session.py -v`
Expected: PASS, all tests (new and pre-existing — no other call site passes `research_depth`, so nothing else changes behavior).

- [ ] **Step 5: Commit**

```bash
git add backend/research_agent/session.py tests/research_agent/test_session.py
git commit -m "feat(research-agent): thread an optional research_depth into session persistence"
```

---

### Task 9: Professional-profile source policy (settings-gated)

**Files:**
- Modify: `backend/research_agent/agent.py`
- Modify: `tests/research_agent/test_agent_loop.py`

**Interfaces:**
- Consumes: `cfg["research_agent_allow_professional_profiles"]` (Task 4), `browser._check_blocked` (existing, unchanged).
- Produces: no new public function — a small addition inside `research_business`'s R3-equivalent link-following logic and `_collect_relevant_links`.

**Design (recap from the spec):** off by default. When on, a link the agent discovered on the business's own site pointing at a recognizably public company/professional-profile domain (a small, explicit allow-list of known professional-profile hosts — not a general "any external domain") may be opened as a read-only evidence source, capped at 2 opens per candidate. An auth wall / CAPTCHA is handled by the existing `browser._check_blocked` path exactly like any other blocked page — recorded, abandoned, never bypassed.

- [ ] **Step 1: Write the failing tests**

Append to `tests/research_agent/test_agent_loop.py`:
```python
PROFILE_LINK_TEXT = "Find us on LinkedIn"
PROFILE_LINK_HREF = "https://www.linkedin.com/company/acme-family-dental"


async def test_professional_profile_links_ignored_when_setting_is_off(monkeypatch):
    browser = ScriptedBrowser({
        "open_url": [ok("open_url", url="https://acmefamilydental.test", title="Acme Family Dental")],
        "extract_page_text": [ok(
            "extract_page_text", url="https://acmefamilydental.test",
            text="Acme Family Dental. Contact us at info@acmefamilydental.test.",
            links=[{"text": PROFILE_LINK_TEXT, "href": PROFILE_LINK_HREF}],
        )],
    })
    actions = [
        AgentAction(action="open_url", params={"url": "https://acmefamilydental.test"}),
        AgentAction(action="finish_research", params={"reason": "done"}),
    ]
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", _scripted_llm(actions))
    monkeypatch.setattr(agent_mod.llm_mod, "extract_fields", _no_management_extraction)

    cfg = _cfg(allow_professional_profiles=False)
    a = agent_mod.ResearchAgent(browser, cfg, "dental clinics")
    await a.research_business({"business_name": "Acme Family Dental", "website": "https://acmefamilydental.test"})
    # Only the business site was opened — the LinkedIn link was discovered
    # but never queued (setting is off), so nothing else to follow.
    assert browser.calls.count("open_url") == 1


async def test_professional_profile_link_visited_read_only_when_setting_is_on(monkeypatch):
    browser = ScriptedBrowser({
        "open_url": [
            ok("open_url", url="https://acmefamilydental.test", title="Acme Family Dental"),
            ok("open_url", url=PROFILE_LINK_HREF, title="Acme Family Dental | LinkedIn"),
        ],
        "extract_page_text": [
            ok("extract_page_text", url="https://acmefamilydental.test",
               text="Acme Family Dental.", links=[{"text": PROFILE_LINK_TEXT, "href": PROFILE_LINK_HREF}]),
            ok("extract_page_text", url=PROFILE_LINK_HREF,
               text="Acme Family Dental. Jordan Pike — Owner."),
        ],
    })
    actions_after_pages = [AgentAction(action="finish_research", reason="done")]
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action", _scripted_llm(actions_after_pages))
    monkeypatch.setattr(agent_mod.llm_mod, "extract_fields", _no_management_extraction)

    cfg = _cfg(allow_professional_profiles=True)
    a = agent_mod.ResearchAgent(browser, cfg, "dental clinics")
    await a.research_business({"business_name": "Acme Family Dental", "website": "https://acmefamilydental.test"})
    assert browser.calls.count("open_url") == 2   # business site + the profile page


async def test_professional_profile_auth_wall_is_recorded_and_skipped(monkeypatch):
    browser = ScriptedBrowser({
        "open_url": [
            ok("open_url", url="https://acmefamilydental.test", title="Acme Family Dental"),
            ActionResult(action="open_url", status="blocked", data={"url": PROFILE_LINK_HREF}),
        ],
        "extract_page_text": [
            ok("extract_page_text", url="https://acmefamilydental.test",
               text="Acme Family Dental.", links=[{"text": PROFILE_LINK_TEXT, "href": PROFILE_LINK_HREF}]),
        ],
    })
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action",
                        _scripted_llm([AgentAction(action="finish_research", reason="done")]))
    monkeypatch.setattr(agent_mod.llm_mod, "extract_fields", _no_management_extraction)

    cfg = _cfg(allow_professional_profiles=True)
    a = agent_mod.ResearchAgent(browser, cfg, "dental clinics")
    lead = await a.research_business({"business_name": "Acme Family Dental", "website": "https://acmefamilydental.test"})
    assert browser.calls.count("open_url") == 2   # tried it, got blocked, moved on — never raised
    assert lead.research_status in ("COMPLETE", "PARTIAL", "FAILED")
```

(`ActionResult` is already imported at the top of `test_agent_loop.py` via `from backend.research_agent.actions import ...` — check the existing import line and add `ActionResult` to it if it isn't already there.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/Scripts/python.exe -m pytest tests/research_agent/test_agent_loop.py -k professional_profile -v`
Expected: FAIL — with the setting on, the profile link is never queued today (no host allow-list exists), so `browser.calls.count("open_url")` is `1`, not `2`, in the second and third tests.

- [ ] **Step 3: Modify `agent.py`**

Add a module-level allow-list near the other module constants (next to `_DIRECTORY_DOMAINS`):
```python
# Recognized public company/professional-profile hosts — NOT a general
# "visit any external link" allow-list. Only consulted when
# research_agent_allow_professional_profiles is true. See the design doc
# §8: read-only, company/public pages only, capped, auth-wall -> skip.
_PROFESSIONAL_PROFILE_DOMAINS = frozenset({"linkedin.com"})
_MAX_PROFESSIONAL_PROFILE_OPENS_PER_CANDIDATE = 2
```

In `research_business`, add tracking state alongside the other per-candidate counters:
```python
        professional_profile_opens = 0
```

In `_collect_relevant_links`, queue a professional-profile link only when the setting is on (this requires passing the flag through — add a parameter with a default so the method's other callers/tests are unaffected):
```python
    @staticmethod
    def _collect_relevant_links(links: List[Dict[str, str]], opened_urls: Optional[set],
                                relevant_links: Optional[List[str]], max_queued: int = 6,
                                allow_professional_profiles: bool = False) -> None:
        if relevant_links is None:
            return
        opened = opened_urls or set()
        for link in links or []:
            href = (link.get("href") or "").strip()
            text = link.get("text") or ""
            if not href.startswith("http"):
                continue
            if href in opened or href in relevant_links:
                continue
            domain = _domain(href)
            is_professional_profile = domain in _PROFESSIONAL_PROFILE_DOMAINS
            if is_professional_profile and not allow_professional_profiles:
                continue
            if is_professional_profile or extraction.link_relevance(href, text) > 0:
                relevant_links.append(href)
        relevant_links.sort(key=lambda u: -extraction.link_relevance(u, ""))
        del relevant_links[max_queued:]
```

Update both call sites in `_process_result` to pass the flag:
```python
        elif action.action == "find_links":
            self._collect_relevant_links(
                data.get("links", []), opened_urls, relevant_links,
                max_queued=self.cfg.get("research_agent_max_queued_links", 6),
                allow_professional_profiles=self.cfg.get("research_agent_allow_professional_profiles", False),
            )

        elif action.action == "extract_page_text":
            self._collect_relevant_links(
                data.get("links", []), opened_urls, relevant_links,
                max_queued=self.cfg.get("research_agent_max_queued_links", 6),
                allow_professional_profiles=self.cfg.get("research_agent_allow_professional_profiles", False),
            )
            await self._process_page_text(lead, data)
```

Finally, cap how many professional-profile pages a candidate may open. In `_forced_action`'s R3 (the follow-a-queued-link rule), a professional-profile URL popped from the queue must respect the per-candidate cap; since `_forced_action` doesn't currently see the counter, do the cap check in `research_business` right where R3's chosen action is about to run — the simplest correct place is where the URL is popped, i.e. inside `_forced_action` itself, which needs the counter passed in. Change `_forced_action`'s signature and call site:
```python
    def _forced_action(
        self, lead: ResearchLead, *, just_opened: bool, last_opened_url: Optional[str],
        pages_read: set, relevant_links: List[str], max_pages: int,
        professional_profile_opens: int = 0,
    ) -> Optional[AgentAction]:
        ...
        if (needs_more and relevant_links and lead.pages_visited < max_pages
                and last_opened_url in pages_read):
            nxt = relevant_links[0]
            if (_domain(nxt) in _PROFESSIONAL_PROFILE_DOMAINS
                    and professional_profile_opens >= _MAX_PROFESSIONAL_PROFILE_OPENS_PER_CANDIDATE):
                relevant_links.pop(0)   # drop it — cap reached, don't offer it again
                return None
            relevant_links.pop(0)
            return AgentAction(action="open_url", params={"url": nxt},
                               reason="deterministic: follow a Contact/About/Team link")
        return None
```
And update the call site in `research_business`'s main loop:
```python
            action = self._forced_action(
                lead, just_opened=just_opened, last_opened_url=last_opened_url,
                pages_read=pages_read, relevant_links=relevant_links, max_pages=max_pages,
                professional_profile_opens=professional_profile_opens,
            )
```
And increment the counter where `open_url`/`open_new_tab` results are processed (next to `lead.pages_visited += 1`):
```python
            if action.action in ("open_url", "open_new_tab"):
                lead.pages_visited += 1
                if _domain(action.params.get("url", "")) in _PROFESSIONAL_PROFILE_DOMAINS:
                    professional_profile_opens += 1
                if result.status == "success":
                    ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/research_agent/test_agent_loop.py -v`
Expected: PASS, all tests.

- [ ] **Step 5: Run the full research_agent suite, then the full backend suite**

Run: `venv/Scripts/python.exe -m pytest tests/research_agent/ -q` then `venv/Scripts/python.exe -m pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add backend/research_agent/agent.py tests/research_agent/test_agent_loop.py
git commit -m "feat(research-agent): settings-gated, capped, read-only professional-profile source"
```

---

### Task 10: R3 loosening for deep/max, full regression, AFTER benchmark, report

**Files:**
- Modify: `backend/research_agent/agent.py`
- Modify: `tests/research_agent/_fakes.py` — `ScriptedBrowser` gains `consumed_urls` tracking
- Modify: `tests/research_agent/test_agent_loop.py`
- Regenerate: `tests/research_agent/benchmark/results_after.json`

**Interfaces:** `ScriptedBrowser.consumed_urls: List[str]` (new attribute) — this task finishes wiring §6 of the design doc (deep/max may follow one people/offering link even once required fields are satisfied) and closes out the cycle.

- [ ] **Step 1: Add read-tracking to `ScriptedBrowser`**

In `tests/research_agent/_fakes.py`, add a tracking list so a test can assert a URL was actually *read* (its `extract_page_text` result consumed), not just opened. In `ScriptedBrowser.__init__`, add `self.consumed_urls: List[str] = []` alongside `self.calls`. In `ScriptedBrowser.execute`, after the existing dict-branch resolves `result` for a non-callable script, add:
```python
        if action.action == "extract_page_text" and result.status == "success":
            self.consumed_urls.append(result.data.get("url", ""))
```
(Insert this right before the final `return result` in the dict-script branch of `execute`, so it fires for both the dict-based and any future path — but the callable-script branch already `return`s earlier, so this line only needs to sit once, after queue-population, before the function's `return result`.)

- [ ] **Step 2: Write the failing test**

Append to `tests/research_agent/test_agent_loop.py`:
```python
async def test_deep_mode_follows_one_extra_offering_link_when_fields_already_complete(monkeypatch):
    browser = ScriptedBrowser({
        "open_url": [
            ok("open_url", url="https://acmefamilydental.test", title="Acme Family Dental"),
            ok("open_url", url="https://acmefamilydental.test/services", title="Services"),
        ],
        "extract_page_text": [
            ok("extract_page_text", url="https://acmefamilydental.test",
               text="Acme Family Dental. Call (337) 555-0142. Contact: info@acmefamilydental.test. Jordan Pike is the Owner.",
               links=[{"text": "Services", "href": "https://acmefamilydental.test/services"}]),
            ok("extract_page_text", url="https://acmefamilydental.test/services",
               text="General dentistry, cosmetic dentistry, orthodontics."),
        ],
    })
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action",
                        _scripted_llm([AgentAction(action="finish_research", reason="done")]))
    monkeypatch.setattr(agent_mod.llm_mod, "extract_fields", _finds_owner_extraction)

    cfg = _cfg(research_depth="deep")
    a = agent_mod.ResearchAgent(browser, cfg, "dental clinics")
    lead = await a.research_business({"business_name": "Acme Family Dental", "website": "https://acmefamilydental.test"})
    # Every field — required AND optional — is already found after page 1
    # (management_contact_name included, via the stubbed extractor below).
    # Deep mode still visits the queued offering page once.
    assert lead.management_contact_name == "Jordan Pike"
    assert browser.calls.count("open_url") == 2
    assert "https://acmefamilydental.test/services" in browser.consumed_urls  # actually read, not just opened


async def test_standard_mode_does_not_follow_extra_offering_links_once_fields_complete(monkeypatch):
    browser = ScriptedBrowser({
        "open_url": [ok("open_url", url="https://acmefamilydental.test", title="Acme Family Dental")],
        "extract_page_text": [ok(
            "extract_page_text", url="https://acmefamilydental.test",
            text="Acme Family Dental. Call (337) 555-0142. Contact: info@acmefamilydental.test. Jordan Pike is the Owner.",
            links=[{"text": "Services", "href": "https://acmefamilydental.test/services"}],
        )],
    })
    monkeypatch.setattr(agent_mod.llm_mod, "decide_next_action",
                        _scripted_llm([AgentAction(action="finish_research", reason="done")]))
    monkeypatch.setattr(agent_mod.llm_mod, "extract_fields", _finds_owner_extraction)

    cfg = _cfg(research_depth="standard")
    a = agent_mod.ResearchAgent(browser, cfg, "dental clinics")
    lead = await a.research_business({"business_name": "Acme Family Dental", "website": "https://acmefamilydental.test"})
    assert lead.management_contact_name == "Jordan Pike"
    assert browser.calls.count("open_url") == 1   # standard mode stops once every field is found — no extra visit
```

`_finds_owner_extraction` is a module-level test helper (add it near `_no_management_extraction` at the top of the file):
```python
async def _finds_owner_extraction(text, missing_fields, business_name=None, cfg=None):
    out = {}
    if "management_contact_name" in missing_fields:
        out["management_contact_name"] = "Jordan Pike"
    if "management_title" in missing_fields:
        out["management_title"] = "Owner"
    return out
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `venv/Scripts/python.exe -m pytest tests/research_agent/test_agent_loop.py -k "deep_mode_follows or standard_mode_does_not" -v`
Expected: FAIL — `test_deep_mode_follows_one_extra_offering_link...` fails because R3 today only follows a link while `management_contact_name` or `business_email` is missing, and both are already found after page 1 (management via the stubbed extractor), so `open_url` is called only once regardless of depth.

- [ ] **Step 4: Modify `agent.py`**

`_forced_action`'s R3 needs the resolved depth to decide whether to loosen. Add a per-candidate flag `followed_extra_link = False` in `research_business`'s init block:
```python
        followed_extra_link = False
        research_depth = self.cfg.get("research_agent_research_depth", "standard")
        allow_extra_link = research_depth in ("deep", "max")
```

Change R3 in `_forced_action` to accept and use these two new pieces of state:
```python
    def _forced_action(
        self, lead: ResearchLead, *, just_opened: bool, last_opened_url: Optional[str],
        pages_read: set, relevant_links: List[str], max_pages: int,
        professional_profile_opens: int = 0, allow_extra_link: bool = False,
        followed_extra_link: bool = False,
    ) -> Optional[AgentAction]:
        ...
        # R3 — current page read, management / business email still missing,
        # and a queued page is known: follow it. In deep/max modes, once per
        # candidate, also follow a queued page even when both are already
        # found — this is how the agent reaches a services/providers page
        # for context beyond the two required contact fields.
        needs_more = (not lead.management_contact_name) or (
            not lead.business_email and lead.business_email_status != STATUS_SECURE_WEB_FORM
        )
        may_follow_extra = allow_extra_link and not followed_extra_link
        if ((needs_more or may_follow_extra) and relevant_links
                and lead.pages_visited < max_pages and last_opened_url in pages_read):
            nxt = relevant_links[0]
            if (_domain(nxt) in _PROFESSIONAL_PROFILE_DOMAINS
                    and professional_profile_opens >= _MAX_PROFESSIONAL_PROFILE_OPENS_PER_CANDIDATE):
                relevant_links.pop(0)
                return None
            relevant_links.pop(0)
            reason = "deterministic: follow a Contact/About/Team link" if needs_more else \
                     "deterministic: deep-mode extra page (fields already complete)"
            return AgentAction(action="open_url", params={"url": nxt}, reason=reason)
        return None
```

Update the call site in `research_business`'s loop to pass the two new args, and set `followed_extra_link = True` whenever the extra-link branch actually fires (check `needs_more` was false at call time — simplest: recompute it once per iteration before calling `_forced_action`, matching what `_forced_action` itself computes):
```python
            needs_more_now = (not lead.management_contact_name) or (
                not lead.business_email and lead.business_email_status != STATUS_SECURE_WEB_FORM
            )
            action = self._forced_action(
                lead, just_opened=just_opened, last_opened_url=last_opened_url,
                pages_read=pages_read, relevant_links=relevant_links, max_pages=max_pages,
                professional_profile_opens=professional_profile_opens,
                allow_extra_link=allow_extra_link, followed_extra_link=followed_extra_link,
            )
            if action is not None and action.action == "open_url" and not needs_more_now:
                followed_extra_link = True
```
(Place this right before the existing `if action is None:` LLM-fallback branch, replacing the plain `action = self._forced_action(...)` call that's there today.)

Also gate the top-of-loop early-completion check (`if validation.is_research_sufficient(lead) and lead.actions_taken > 0:`) so deep/max don't finish before they've had a chance to *read* the extra link, not just open it — "pending" must stay true from the moment a link is queued through to the moment the page it led to has actually been read, or the loop breaks right after opening it and R2 never gets to run:
```python
                if lead.management_contact_name or management_search_attempted or paths_exhausted:
                    break
```
to
```python
                extra_link_not_yet_taken = allow_extra_link and not followed_extra_link and bool(relevant_links)
                extra_link_taken_not_yet_read = bool(
                    followed_extra_link and last_opened_url and last_opened_url not in pages_read
                )
                extra_link_pending = extra_link_not_yet_taken or extra_link_taken_not_yet_read
                if (lead.management_contact_name or management_search_attempted or paths_exhausted) and not extra_link_pending:
                    break
```
(`extra_link_not_yet_taken` covers "deep/max, haven't used the extra link yet, one is queued — don't finish before offering it"; `extra_link_taken_not_yet_read` covers "just opened the extra link — don't finish before R2 reads it." Once the extra page has been read, both go false and the loop is free to break exactly as it always did.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/research_agent/test_agent_loop.py -v`
Expected: PASS, all tests (the full file, including every test from Tasks 7 and 9 — `standard` depth's default (`allow_extra_link=False`) preserves the exact old R3 behavior for every pre-existing test).

- [ ] **Step 6: Full regression**

Run: `venv/Scripts/python.exe -m pytest -q`
Expected: all tests pass (827 baseline + this plan's new tests across `test_pacing.py`, `test_reader.py`, `test_config.py`, `test_extraction.py`, `test_actions.py`, `test_agent_loop.py`, `test_session.py`). Record the final count.

- [ ] **Step 7: Re-run the benchmark — AFTER numbers**

Run: `venv/Scripts/python.exe -m tests.research_agent.benchmark.runner after`
Expected: writes `tests/research_agent/benchmark/results_after.json`. On the "clinic" business specifically: `decision_makers_found` should now include "Marcus Bell" (revealed only by scrolling — invisible to the BEFORE run), and/or `useful_pages`/`fields_found` should be at or above the BEFORE numbers on every fixture, never below. If a number regresses versus BEFORE, that is a bug to fix before closing this task, not a result to report as-is.

- [ ] **Step 8: Write the comparison into the benchmark README**

Append a `## Results` section to `tests/research_agent/benchmark/README.md` with a table built from `results_before.json` vs `results_after.json`'s `totals` (pages_visited, useful_pages, fields_found, decision_makers_found, wall_time_s) — copy the actual numbers from both JSON files, not placeholders.

- [ ] **Step 9: Update the plan's checkboxes and commit**

```bash
git add backend/research_agent/agent.py tests/research_agent/_fakes.py tests/research_agent/test_agent_loop.py tests/research_agent/benchmark/results_after.json tests/research_agent/benchmark/README.md docs/superpowers/plans/2026-09-04-deliberate-page-research.md
git commit -m "feat(research-agent): deep/max follow one extra page past required fields; Cycle 1 complete"
```

---

## Completion report (fill in after Task 10, before declaring the cycle done)

Per the brief's §27: do not report "complete" because tests pass or a browser opened — report the BEFORE/AFTER comparison from Task 10 Step 7, plus:
- Files changed (diff stat)
- Architecture changes (one paragraph, matching §0 of the design doc)
- Tests added (count per new/modified test file) and the final `pytest -q` result
- Any remaining limitation (e.g., per-session depth needs a DB column — deferred to Cycle 3; the professional-profile allow-list is currently just `linkedin.com` — expand only on explicit request)
- Commit hashes for this plan's commits
