---
name: autolead-systematic-debugging
description: Use when encountering any bug, test failure, unexpected behavior, or "it should work but doesn't" situation in AutoLead, before proposing or applying a fix.
---

# AutoLead Systematic Debugging

## Purpose

Enforces root-cause fixes over symptom patches, with AutoLead-specific gotchas that have already caused real bugs here.

## When to Use

Any bug report, failing test, unexpected API response, silent failure, or "works on my machine but not in the running app" situation.

## When NOT to Use

Greenfield feature work with no bug yet to chase (use the relevant architecture skill instead).

## Project Context — known landmines

- **Two independent campaign engines exist**: `routers/campaigns.py::_run_campaign_task` (UI-driven, per-run `_run_state` dict, calls `scrapers.run_bulk_scrape`) and `scheduler.py::run_campaign` (APScheduler cron-driven, uses only `scraper.scrape_google_maps`, its own dedup/stop-flag). A fix in one does not apply to the other. If a bug report doesn't specify which path, check both.
- **`backend/scraper.py` (legacy) and `backend/scrapers/__init__.py` (current orchestrator) both exist.** The router calls `scrapers.run_bulk_scrape`, not `scraper.scrape_multi_source`. If you're debugging a live campaign and reading `scraper.py`, you may be reading dead code for that path.
- **Every non-Maps scraper source falls back to Google Maps on failure** (`_run_with_maps_fallback` in `scrapers/__init__.py`), always logged via `_log_source_event` to `campaign_log`. A source that "isn't working" may actually be silently falling back — check `campaign_log`, not just the source's own code, before assuming it's broken.
- **`sales_intelligence_enabled` gates two different enrichment paths.** Off: legacy `analyze_website` → `enrich_lead_with_ai`. On: `run_pending_research`/`run_research_pipeline` (the intelligence agents). A bug that "only happens sometimes" in enrichment may be this flag flipping between environments.
- **`JobQueue` (`queue_worker.py`) now has two producers** (both landed with the Discovery/Research work in the working tree): `discovery/quick_search.py` via `routers/discovery.py` (`"QUICK_SEARCH"`), and `research_agent/session.py` via `routers/research_agent.py` (`"RESEARCH_AGENT"`). The legacy campaign path (`routers/campaigns.py`) and the scheduler still do **not** use the queue. If a queued job "runs but nothing happens," check the handler body persisted a terminal status — both handlers are written to never raise out and to always leave the run/session row in `COMPLETED`/`FAILED`/`CANCELLED`, so a row stuck at `RUNNING` means the worker died, not that the job is slow.
- **Three subsystems now write "discovery run"-shaped rows and they are NOT the same table.** `lead_discovery_runs` (Quick Search + Campaign planner bookkeeping), `lead_research_sessions` (Browser Research Agent), and the legacy global `scraper_router` state (Dashboard's old scrape button) are independent. A "my run isn't showing up" bug is often a query against the wrong one.
- **Browser Research Agent LLM failures are silent by design.** `research_agent/llm.py::decide_next_action` never raises — it returns an `AgentAction(action="_llm_failed", …)` sentinel and `agent.py` falls back to `_fallback_action` (a deterministic backup planner). If a research session "isn't using the LLM," Ollama may simply be unreachable and the run is limping along on the fallback planner — check the logs for `_llm_failed` / "using rule-based fallback" before assuming a prompt bug.
- **Settings have two layers**: `.env`/`config.py` pydantic defaults, overridden at runtime by the `app_settings` DB table (`db.get_all_settings()`). A config value that "isn't taking effect" after an `.env` edit may be shadowed by a stored DB setting — check Settings UI / `app_settings` table before assuming the env var is broken.
- **WhatsApp sending is serialized behind a single process-wide `asyncio.Lock`** because it drives the real desktop via pyautogui. A "WhatsApp send hangs" bug may be lock contention, not the send logic itself.

## Rules

1. **Reproduce before fixing.** Run the failing test, or hit the failing endpoint/UI flow, and see the actual failure yourself before writing a fix.
2. **Identify which code path is actually executing** before editing — this codebase has duplicate/legacy paths (see landmines above) more often than most.
3. **Root cause, not symptom.** A `try/except` that swallows the error is not a fix unless the exception itself is the expected/correct behavior.
4. **After fixing, run the regression suite**, not just the one test you were chasing — `pytest -q` from repo root.
5. **If the bug is in generated AI content** (bad message, wrong score, hallucinated fact), check the deterministic layer first (`lead_scorer.py`, `website_analyzer.py`, `solution_matcher.py`, `pain_point_agent.py`'s heuristic checks) before assuming it's an LLM prompt problem — most of AutoLead's scoring/matching logic is deterministic by design; the bug is more often in the code path feeding the LLM than in the LLM's output.

## Architecture Guidance

Follow: REPRODUCE → OBSERVE (read actual logs: `backend_run.log`, `campaign_log` table, browser console) → ISOLATE (which module/function) → ROOT CAUSE → FIX → TEST → REGRESSION TEST → VERIFY. Do not skip straight from OBSERVE to FIX.

## Implementation Guidance

- Reproduce via the real running app when possible (`venv/Scripts/python.exe run_server.py` + `npm run dev`), not only via a unit test — some AutoLead bugs (dual campaign engines, settings-layer shadowing) only show up when the full app is running.
- Use `campaign_log` (queryable via `GET /api/campaign/history` or direct DB read) as a first-class debugging source for anything scraper/campaign-related — it's an append-only audit trail specifically built for this.
- For AI/agent bugs, check `research_evidence` and the agent's `AgentResult.reason` field before re-prompting — the evidence chain usually shows exactly what the agent saw.

## Testing Requirements

Write a regression test that reproduces the bug before fixing it where practical (mirrors TDD's red-green cycle even outside a formal TDD flow). At minimum, confirm the existing test for the affected module fails before your fix and passes after.

## Security Considerations

If the bug involves a secret appearing somewhere it shouldn't (logs, API responses, exports), treat it as a security bug — see `autolead-security-and-secrets` — not just a functional one.

## Performance Considerations

If the bug is a timeout/hang, check for a missing `timeout=` on an `httpx`/`requests` call or Selenium/Playwright wait before assuming it's a logic bug — most of the scraper layer already has explicit timeouts; a new one you're adding might not.

## Failure Modes

| Mistake | Fix |
|---|---|
| Patching `scheduler.py` when the bug is actually in `campaigns.py`'s path (or vice versa) | Check `_run_state`/log output to confirm which engine is actually running before touching code |
| Adding a broad `try/except: pass` to make an error "go away" | Find why it's raising; only swallow if the exception is genuinely expected |
| Assuming a scraper is broken because results are 0, without checking `campaign_log` for a fallback event | Check the log first — it may have silently fallen back to Maps |
| Re-prompting the LLM to fix a wrong score/pain-point | Check `lead_scorer.py`/`pain_point_agent.py`'s deterministic checks first — most of this logic isn't LLM-driven |

## Verification Checklist

- [ ] Reproduced the actual failure (test run or live request), not just read the code
- [ ] Confirmed which code path (of any duplicate paths) is actually involved
- [ ] Root cause identified and stated, not just a patch applied
- [ ] Regression test added or existing test confirmed to now pass
- [ ] Full `pytest -q` run clean after the fix

## Related Skills

`autolead-verification-before-completion`, `autolead-reliability-and-background-jobs`, `autolead-outreach-safety`

---
Version: 1.1
Scope: AutoLead-manager
Last reviewed: 2026-08-30 (Discovery Planner + Browser Research Agent additions)
