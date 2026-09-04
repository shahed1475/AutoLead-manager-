---
name: autolead-verification-before-completion
description: Use before claiming any AutoLead change is done, fixed, working, or ready — including bug fixes, new endpoints, scraper changes, AI/agent changes, migrations, and UI changes — and before committing or creating a PR.
---

# AutoLead Verification Before Completion

## Purpose

Defines exactly what "done" means in this repo and the commands that prove it, so a claim of "fixed"/"complete"/"working" is backed by evidence, not by reading the diff and feeling confident.

## When to Use

Before writing "done", "fixed", "complete", "should work now", or any success claim about AutoLead code, and before running `git commit`.

## When NOT to Use

Mid-task exploration, draft code you're still iterating on, or answering a question with no code change.

## Project Context

- Backend tests: `pytest` (pytest-asyncio, `asyncio_mode=auto`), **446 tests passing as of 2026-08-30 baseline** (292 as of 2026-08-24, before the Discovery Planner and Browser Research Agent work landed in the working tree — `tests/discovery/` and `tests/research_agent/` add the rest). Run from repo root with the project venv: `venv/Scripts/python.exe -m pytest -q` (Windows) — **not** `backend/.venv`, which also exists but is not what `start.bat`/CI-equivalent runs use.
- `tests/conftest.py::clean_db` wipes/reinits a throwaway SQLite file per test — if a test looks like it passed but left data behind, the fixture didn't run correctly; investigate before trusting the result.
- **No frontend test framework is configured** (`frontend/package.json` has no `test` script, no vitest/jest). Frontend verification today means: `npm run build` (catches import errors, like the `@dnd-kit/core` failure recorded in `frontend_run.log` from an earlier session) plus manual browser verification. Do not claim a frontend change is "tested" — say "built cleanly and manually verified in browser" instead, which is what's actually true.
- Two backend processes to check when verifying live behavior: the FastAPI server (`venv/Scripts/python.exe run_server.py`, port 8000) and the Vite dev server (`npm run dev` in `frontend/`, port 5173). Logs land in `backend_run.log`/`frontend_run.log` at repo root.

## Rules

1. **Run the actual test suite before claiming a fix works.** A code review of your own diff is not verification.
2. **A new feature needs a new test**, not just "the existing tests still pass." Existing tests passing proves you didn't break something else; it says nothing about whether the new thing works.
3. **A UI change needs a browser check**, not just a clean build. `npm run build` catches import/syntax errors, not broken rendering or a non-functional button.
4. **Never claim a background job, migration, or scraper works from reading the code alone** — these are exactly the categories where AutoLead's existing bugs (silent source-routing failures, a missing `conn.transaction()` method, `queue_worker.py` sitting unused since startup) were only caught by actually running them.
5. **If verification is not possible** (no live LLM available, no real WhatsApp session, no network access to a real scraper target), say so explicitly instead of asserting success. "Not verified — no live Ollama instance in this environment" is an acceptable, honest statement. A silent claim of success is not.

## Architecture Guidance

N/A — this is a process skill, not a subsystem skill.

## Implementation Guidance

Order of verification, cheapest first:
1. `pytest -q` (backend) — always, for any backend change.
2. `npm run build` (frontend, in `frontend/`) — always, for any frontend change.
3. Targeted new test for the specific change, run individually first (`pytest tests/path::test_name -v`) before the full suite.
4. If the change is reachable from the running app, hit it: `curl` the endpoint, or drive the page in a browser (Playwright/claude-in-chrome) and read the actual response/screenshot — don't infer from code.
5. For scraper/browser-automation changes: a real run against a real target when feasible; if not feasible in this environment, say that explicitly rather than skip the caveat.

## Testing Requirements

Full checklist to walk before saying "done" on anything touching lead-generation/outreach:
- [ ] Unit tests for the changed module pass
- [ ] Integration tests for the affected router/endpoint pass
- [ ] Full `pytest -q` run is clean (no new failures, no skipped-and-ignored failures)
- [ ] DB migrations (`_add_col_if_missing` additions, new tables) verified by actually starting the app against a real or throwaway DB file and confirming no startup error
- [ ] Background jobs (APScheduler jobs, `JobQueue` producers) verified by triggering them, not just reading the registration code
- [ ] New/changed API responses checked against what the frontend actually expects (field names, types)
- [ ] Source-adapter changes verified against at least one real or mocked source, confirming failure isolation (one source failing doesn't kill the run)
- [ ] Lead dedup changes verified with an actual duplicate-insertion test, confirming merge behavior not silent data loss
- [ ] Browser Research Agent changes verified with mocked-browser + mocked-LLM tests (`tests/research_agent/` pattern, `_fakes.py`), plus: a budget-exhaustion test (loop terminates at `max_actions`/`max_time`/`max_consecutive_failures`), a no-fabrication test (`validation.validate_no_fabrication` returns clean; a value with no `FOUND` evidence row is a failure), and a per-lead-failure-isolation test (one candidate raising does not abort the session). Never claim it works from a real live run alone — the suite must cover it.
- [ ] Quick Search / research-agent JobQueue paths verified by actually enqueuing and draining a job (queue producers now exist — `discovery/quick_search.py`, `routers/research_agent.py`), not by reading the `enqueue_nowait` call
- [ ] AI/LLM structured-output changes verified with both a valid-response test and a malformed-response test (confirms the retry/repair loop still degrades gracefully)
- [ ] Message generation changes verified against the pain-point-first structural rule and the forbidden-claim regex
- [ ] Approval-path and opt-out behavior re-verified per `autolead-outreach-safety` if touched
- [ ] Export (CSV/Excel) changes verified by actually opening the exported file, not just checking the endpoint returns 200
- [ ] Error handling verified with an actual failure injected (timeout, malformed input), not assumed from a `try/except` existing

## Security Considerations

Verification includes confirming no secret/API key/password is present in a log line, error message, or exported file you just added.

## Performance Considerations

For anything touching scraping or campaigns, verify concurrency limits are respected (rate limits, worker counts) rather than assuming a `Semaphore`/queue is correctly wired — check the actual run, not the code shape.

## Failure Modes

| Excuse | Reality |
|---|---|
| "The logic is straightforward, it'll work" | Straightforward code has broken this exact codebase before (see `conn.transaction()` bug in the production audit). Run it. |
| "The existing tests pass, so I'm done" | Existing tests don't cover new behavior. Add one. |
| "I can't run a browser/LLM/WhatsApp session right now" | Then say so explicitly — don't claim success anyway. |
| "It's just a small UI tweak" | `npm run build` still catches real breakage; run it. |
| "I already manually traced the code path" | Tracing isn't running. Run the test or hit the endpoint. |

## Red Flags — Stop and Verify

- About to write "done"/"fixed"/"should work" without a command having been run this turn
- About to commit without having run `pytest -q` since the last code change
- A UI change with no build or browser check performed
- A claim about scraper/queue/migration behavior based only on reading the code

## Verification Checklist

- [ ] `pytest -q` run this session, output actually read (not assumed)
- [ ] `npm run build` run this session if frontend touched
- [ ] New test added for new behavior
- [ ] Live/manual check performed where automated coverage doesn't exist, or its absence stated explicitly
- [ ] No secrets in new logs/exports

## Related Skills

`autolead-systematic-debugging`, `autolead-outreach-safety`, `autolead-reliability-and-background-jobs`

---
Version: 1.1
Scope: AutoLead-manager
Last reviewed: 2026-08-30 (Discovery Planner + Browser Research Agent additions)
