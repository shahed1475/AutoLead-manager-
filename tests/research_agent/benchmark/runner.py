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

from backend.database import init_db  # noqa: E402
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
    await init_db()
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
