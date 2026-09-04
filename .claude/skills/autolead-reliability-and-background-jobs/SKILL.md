---
name: autolead-reliability-and-background-jobs
description: Use when working on campaign execution, scheduling, the job queue, retries, idempotency, pause/resume/cancel behavior, or Docker/deployment config in AutoLead.
---

# AutoLead Reliability & Background Jobs

## Purpose

Documents the two existing campaign-execution engines (a real landmine — see below), the unused-but-ready `JobQueue`, and the idempotency/reliability patterns already established for background work.

## When to Use

Changing campaign execution, scheduling, follow-up dispatch, adding a new background job type, or touching pause/resume/cancel/resumability behavior.

## When NOT to Use

Pure request/response endpoint work with no async job component.

## Project Context

**Two independent, parallel campaign engines exist — the single most important fact in this skill:**
1. `routers/campaigns.py::_run_campaign_task` — UI-driven, launched via `background_tasks.add_task` from `POST /api/campaign/start`. Stages persisted to `campaign_runs.stage`: `QUEUED → STARTING → SCRAPING → ENRICHING → SCORING → WRITING → SENDING → COMPLETED|STOPPED|FAILED`. In-memory `_run_state` dict tracks running/paused/stop_requested/progress. Pause/resume via `POST /api/campaign/pause|resume`, checked at lead/phase boundaries via `_wait_while_paused()`. Delegates scraping to `scrapers.run_bulk_scrape`.
2. `scheduler.py::run_campaign` — APScheduler cron-driven (`_daily_campaign_job`, daily at `schedule_hour` UTC), does scrape→enrich→score→AI→send inline per-lead, uses only `scraper.scrape_google_maps` (not the full 9-source orchestrator), has its own dedup/daily-cap/stop-flag (`request_stop()`/`_stop_flag`, a global bool — not per-run state like path 1).

**A fix or new feature in one does not automatically apply to the other.** Any change to campaign behavior (source selection, new stages, contact validation, etc.) needs an explicit decision: does it apply to both engines, or just the UI-driven one? Don't assume.

- **`queue_worker.py::JobQueue`** — generic bounded async FIFO queue, N concurrent coroutine workers, instantiated at app startup (`init_queue(n_workers=4)` in `main.py` lifespan). It now has **two producers** (both landed in the working tree with the 2026-08-30 discovery work): `routers/discovery.py` → `discovery/quick_search.py::run_quick_search` (`"QUICK_SEARCH"`), and `routers/research_agent.py` → `research_agent/session.py::run_research_session_persisted` (`"RESEARCH_AGENT"`). Both use `enqueue_nowait` and return 503 if the queue is unavailable/full. The two campaign engines below still do **not** use the queue. It remains the right carrier for any further new background job type — follow the two existing handlers' shape (never raise out; always leave the run/session row in a terminal status).
- **Follow-up scheduling is idempotent by design:** `followup_engine.schedule_followups_for_lead` no-ops if `count_messages_for_lead(lead_id) > 0`. New scheduling logic should follow this check-before-insert pattern.
- **Existing retry/backoff patterns:** scrapers back off and retry on rate-limit signals (e.g., Google Search's 30s backoff + fresh UA on 429/403); `ai_brain._generate_phase` retries structured-output failures up to `MAX_RETRIES=3`. Reuse these shapes for new retry logic rather than inventing a new backoff scheme.
- **Docker:** `docker-compose.yml` is SQLite-only (no Postgres/Redis — deliberately removed in an earlier hardening pass), profile-gated (`--profile full`), backend + nginx-served frontend. No CI/CD pipeline exists (no `.github/workflows`) — verification is manual (`pytest -q`, `npm run build`) as documented in `autolead-verification-before-completion`.

## Rules

1. **Before changing campaign behavior, determine which engine(s) the change applies to** — check both `routers/campaigns.py` and `scheduler.py` explicitly, don't assume parity.
2. **New background job types should use the existing `JobQueue`**, not a new bespoke `background_tasks.add_task` call, unless there's a specific reason the queue's bounded-concurrency model doesn't fit.
3. **Any new scheduled/repeatable operation must be idempotent** — check-before-insert or check-before-send, matching `schedule_followups_for_lead`'s pattern, so a retry or a duplicate trigger doesn't double-send or double-create.
4. **Pause/resume/cancel must be checked at natural boundaries** (per-lead, per-phase), not just at job start — matching `_wait_while_paused()`'s placement.
5. **A single source/lead/step failure must never abort an entire campaign run** — isolate and log, continue with the rest (same principle as `autolead-discovery-and-source-adapters`'s source-isolation rule, extended to the whole campaign engine).
6. **Don't add Postgres/Redis/a new external service without an explicit decision to do so** — the current architecture deliberately runs SQLite-only, single-process; reintroducing infra dependencies is a real architectural decision, not a casual addition.

## Architecture Guidance

New long-running lead-gen work is modeled as a `JobQueue` job with progress persisted to a DB row (`campaign_runs`, `lead_discovery_runs`, `lead_research_sessions` are all this pattern) so status survives a server restart and is pollable to the frontend. Cancellation is **cooperative**: the handler polls a DB-status flag at natural boundaries (`quick_search._is_cancelled`, the research agent's `is_cancelled` callback threaded through the loop), rather than killing the worker. New handlers follow that.

## Implementation Guidance

When adding a new stage to the campaign stage machine, add the new stage string, update both the persisted `campaign_runs.stage` writes and the frontend's `STAGE_TO_STEP`/`PIPELINE_STEPS` mapping (`Campaign.jsx`) in the same change — a backend-only stage addition will silently fail to render in the UI.

## Testing Requirements

- New job-queue producers: test enqueue/dequeue/worker-execution in isolation, plus a failure-in-one-job-doesn't-stop-the-queue test.
- New campaign stages: test the stage transitions in sequence, and test that pause/stop are honored at the new boundary.
- Idempotency: test that triggering the same scheduling/dispatch operation twice doesn't double-create/double-send.
- If a change applies to only one of the two campaign engines, add a test note (or an actual test) confirming the other engine's behavior is unaffected.

## Security Considerations

Background jobs that touch outreach must still pass through all DO_NOT_CONTACT checkpoints — see `autolead-outreach-safety`; a queue/scheduling refactor is a common place to accidentally drop a check.

## Performance Considerations

`JobQueue`'s worker count (`queue_workers` config, default 4) bounds concurrency — new producers should respect this rather than spawning unbounded concurrent work. WhatsApp sends are additionally serialized behind their own lock regardless of queue concurrency.

## Failure Modes

| Mistake | Fix |
|---|---|
| Fixing a bug only in `campaigns.py`, missing the same bug in `scheduler.py` | Check both engines explicitly before calling a campaign-behavior fix complete |
| New background feature uses another ad-hoc `background_tasks.add_task` | Use the existing `JobQueue` instead |
| New scheduled operation re-sends on every trigger | Add a check-before-act idempotency guard |
| New campaign stage added to backend only | Update `Campaign.jsx`'s stage-mapping in the same change |
| One failed lead/source aborts the whole campaign | Isolate per-item failures, log, continue |

## Verification Checklist

- [ ] Determined and stated which campaign engine(s) this change affects
- [ ] New background work uses `JobQueue` or has a stated reason not to
- [ ] Idempotency verified for any new scheduled/repeatable action
- [ ] Pause/stop/cancel checked at the new boundary if applicable
- [ ] Backend stage changes mirrored in frontend stage-mapping if applicable
- [ ] Single-item failure doesn't abort the whole run (tested)

## Related Skills

`autolead-discovery-and-source-adapters`, `autolead-browser-research-agent`, `autolead-outreach-safety`, `autolead-systematic-debugging`, `autolead-verification-before-completion`

---
Version: 1.1
Scope: AutoLead-manager
Last reviewed: 2026-08-30 (JobQueue now has two producers: Quick Search, Browser Research Agent)
