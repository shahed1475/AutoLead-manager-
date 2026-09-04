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
