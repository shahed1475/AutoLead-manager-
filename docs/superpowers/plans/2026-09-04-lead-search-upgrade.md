# Lead Search Architecture Upgrade — Automation + Manual + Research Handoff

**Branch:** `feat/lead-search-upgrade` (off `feat/lead-search-campaign` line; carries the uncommitted
Stage 0/1 automation fix).
**Baseline:** 735 backend tests pass; `npm run build` clean; automation verified working
(46 VT dental leads, parity with manual).

## Context

The `/lead-search` page currently stacks two tools: **Lead Search Automation** (`backend/automation/`,
scheduled niche×city queue) and **Manual "Search Leads"** (`backend/discovery/quick_search.py`,
one-shot). Both run the same engine: `DiscoveryPlanner → SourceRegistry → merge_and_save →
create_or_merge_lead`. Neither enriches or scores; automation output is only visible in the Leads
list; there is no way to hand discovered leads to the existing **Research Agent** (`backend/research_agent/`).

This upgrade delivers the spec in `Downloads/Lead Search Architecture Upgrade …md`: two clearly
separated modes, a visible lead list for each, enrichment + scoring in the pipeline, and manual +
persistent-automatic handoff to the **existing** Research Agent (not rebuilt).

## Decisions (from user, 2026-09-04)

- **Paradigm:** add the web-search-provider layer the spec describes (`SearchProvider` → `{title,url,snippet}`
  → lead-extraction), **additively** alongside the existing business-directory `SourceAdapter` layer —
  not a rewrite. Existing Google Maps / Yellow Pages adapters untouched.
- **Providers:** wire every free, no-API-key engine reachable by driving an installed browser
  (Chrome/Firefox/Brave). Best-effort + graceful skip on CAPTCHA/consent walls (project rule: no
  evasion, no stealth, no proxy rotation — `CLAUDE.md`). Engines behind a paid API or hard anti-bot →
  status chip only (`api_required` / `unsupported`), never a fake adapter.
- **Manual UX:** keep auto-save-all (every valid business from a manual search is persisted).
- **Scope:** all 12 phases, checkpointed one at a time.

## What already exists (reuse — do NOT duplicate)

| Spec need | Component |
|---|---|
| Orchestrator / provider abstraction | `backend/discovery/adapters.py` (`SourceRegistry`, `SourceAdapter`, circuit breaker, metrics) |
| Query builder | `backend/discovery/planner.py` (`DiscoveryPlanner`) |
| Normalization | `database._normalize_lead_fields`, `backend/validators.py` |
| Dedup + multi-source retention | `database.create_or_merge_lead`, `find_duplicate_lead_fuzzy`, `lead_sources` table |
| Unified lead DB | `leads` table — both modes already write it |
| Search-job persistence | `lead_discovery_runs` (QUICK / CAMPAIGN / AUTOMATION), `POST /api/discovery/search/{id}/cancel` |
| Email Finder | `backend/scrapers/email_finder.py` `find_emails_from_website()` |
| Lead Scorer | `backend/scoring/lead_scorer.py` (4-dim, 100-pt, HOT/WARM/COLD) |
| Research Agent | `backend/research_agent/`, `/api/research-agent/*`, JobQueue `"RESEARCH_AGENT"`, `lead_research_sessions/results/evidence` |
| **Research handoff seed** | `research_agent.agent.run_research_session(seed_businesses=[…])` **already** deep-researches a caller-supplied list; `session.run_research_session_persisted` already has a `seed_businesses` param; `on_lead_complete` already merges the result back via `create_or_merge_lead` |
| Settings pattern | `app_settings` table + `backend/config.py` layering (`sales_intelligence_enabled` precedent) |
| Secret redaction | `settings_router._redact_settings` (masks `*_api_key`/`*_password`/`*_secret`/`*_token`) |
| Migrations | `database._add_col_if_missing` + `CREATE TABLE IF NOT EXISTS` in `_run_migrations` |
| Background jobs | `backend/queue_worker.py` `JobQueue` (4 workers) |
| Auth | `backend/auth.py` single-user session (applied in `main.py`) |

## Migration (all additive — `_add_col_if_missing`; no drops, no type changes)

- `leads`: `source_type TEXT`, `research_status TEXT DEFAULT 'NOT_STARTED'`, `email_status TEXT`,
  `last_research_session_id INTEGER`, `latitude REAL`, `longitude REAL`, `google_place_id TEXT`,
  `excluded_from_research INTEGER DEFAULT 0`
- `lead_discovery_runs`: `emails_found INTEGER DEFAULT 0`, `leads_scored INTEGER DEFAULT 0`,
  `research_queued INTEGER DEFAULT 0`, `per_source_counts TEXT`
- `app_settings` rows (data): `research_handoff_mode` (`manual`|`automatic`, default `manual`),
  `research_handoff_min_score` (default `60`), `discovery_enrichment_enabled` (default `true`),
  plus per-provider `*_search_enabled` flags and any `*_api_key` the user supplies.
- New table `search_providers` is NOT needed — provider status is derived from the registry +
  settings at request time.

## Phases

| # | Deliverable | Files | Verify |
|---|---|---|---|
| P1 | Audit + this plan | `docs/superpowers/plans/2026-09-04-lead-search-upgrade.md` | — |
| P2 ✅ | Leads metadata + list filters: migration (`source_type`, `research_status`, `email_status`, `last_research_session_id`, `excluded_from_research`, `latitude`, `longitude`, `google_place_id`); back-fill from `lead_discovery_runs.mode` + `lead_research_results`; `GET /api/leads` gains `source`/`source_type`/`research_status` params; `Leads.jsx` filter dropdowns + `LeadTable` inline source_type/research badges | `database.py`, `routers/leads.py`, `models.py`, `Leads.jsx`, `LeadTable.jsx`, `lib/badges.js`, `tests/test_database_lead_search_meta.py` + `tests/test_leads_router_filters.py` (8 tests) | 743 pass; build clean; live: 235 automation / 447 manual / 54 researched |
| P3 ✅ | Enrichment in the discovery path: new `backend/discovery/enrichment.py::enrich_and_score(lead_ids)` — (1) `email_finder` for website-but-no-email leads (never overwrites a verified/present email; sets `email_status` FOUND/NOT_FOUND), (2) **light** path `analyze_website` + `enrich_lead_with_ai` (NOT `run_pending_research` — discovery volume is high; deep intelligence stays campaign/per-lead triggered), (3) `lead_scorer`. Setting-gated (`discovery_enrichment_enabled`, default true). Called inline from `quick_search.py` and `automation/lead_search_service.py` (never raises; leads already saved). `SearchResult.emails_found` + runner log line surface the count. | `config.py`, `discovery/enrichment.py` (new), `quick_search.py`, `automation/lead_search_service.py`, `automation/runner.py`, `tests/discovery/test_enrichment.py` (new, 6 tests) | 124 discovery+automation tests pass; live run verify |
| P4 ✅ | Research handoff (manual): new `research_agent/handoff.py::handoff_leads(queue, lead_ids)` → one `lead_research_session` (`mode='handoff'`, `seed_businesses`/`seed_lead_ids` JSON on the row, resume-safe); `agent.py` synthetic single geo-task for seeded sessions (no geo expansion); `session.enqueue_session(seed_businesses=...)`; `run_research_session_persisted` flips seed leads `QUEUED`→`RESEARCHING`→`COMPLETED`/`FAILED` (`db.set_leads_research_status` with `only_from` guard); idempotency (skip `QUEUED`/`RESEARCHING`/`excluded_from_research`); `POST /api/leads/research`, `POST /api/leads/{id}/research`, `POST /api/leads/{id}/research-exclude` | `database.py`, `research_agent/handoff.py` (new), `research_agent/session.py`, `research_agent/agent.py`, `routers/leads.py`, `tests/research_agent/test_handoff.py` + `tests/test_leads_research_endpoints.py` (13 tests) | 762 pass; live: 3 leads → session 40 `mode=handoff` RUNNING, leads → RESEARCHING |
| P5 ✅ | Persistent auto-handoff: `research_handoff_mode` (`manual`/`automatic`, validated in `settings_router`), `research_handoff_min_score` (60), `research_handoff_max_per_batch` (25). `discovery/enrichment.py::maybe_auto_handoff` runs as step 4 of `enrich_and_score`: when `automatic`, queues eligible leads (`score >= min_score`, `research_status NOT_STARTED`, not excluded/queued/done) up to the per-batch cap. Never raises. `_handoff_leads` lazy-imports the browser-heavy package. Settings UI toggle deferred to P6. | `config.py`, `settings_router.py`, `discovery/enrichment.py`, `tests/discovery/test_auto_research.py` (4 tests) | 766 pass; live: bad mode → 422; **end-to-end P4 verified — session 40 COMPLETED, all 3 leads researched, Progressive Dental Care got `frontdesk@…` email merged back from the Research Agent** |
| P6 ✅ | Dashboard split: `LeadSearch.jsx` → landing with two cards ("Open Automation"/"Open Manual Search"); `LeadSearchAutomation.jsx` + `LeadSearchManual.jsx` (extracted, behaviour preserved); routes `/lead-search{,/automation,/manual}`. `Leads.jsx`: per-row "Send to Research Agent" (Bot icon, disabled while QUEUED/RESEARCHING), bulk "Send to Research Agent", `leadsApi.research/researchOne/researchExclude`. `Settings.jsx`: "Lead Search & Research" card — enrichment toggle, auto-handoff toggle, min-score field. | `App.jsx`, `LeadSearch.jsx` (rewritten), `LeadSearchAutomation.jsx` + `LeadSearchManual.jsx` (new), `Leads.jsx`, `LeadTable.jsx`, `api/client.js`, `Settings.jsx` | `npm run build` clean; browser: all 4 routes render, 0 console errors, filters + cards present |
| P7 ✅ | Provider catalog: `discovery/provider_catalog.py` — 33-engine descriptor list with runtime status (`available`/`configured`/`not_configured`/`api_required`/`unsupported`/`temporarily_unavailable`), free-keyed (SEARXNG) vs paid-keyed (KAGI/PERPLEXITY/YEP/YANDEX) resolution, per-provider `<id>_search_enabled` flag. `GET /api/lead-search/providers` (new `routers/lead_search.py`, registered in `main.py`). `Settings.jsx` → `SearchProvidersSection` (status chips grouped by kind). Descriptive layer only — execution stays on `SourceRegistry`. | `discovery/provider_catalog.py` (new), `routers/lead_search.py` (new), `main.py`, `api/client.js`, `Settings.jsx`, `components/settings/SearchProvidersSection.jsx` (new), `tests/test_lead_search_providers.py` (5 tests) | 771 pass; build clean |
| P8 ✅ | Web-search provider: `scrapers/duckduckgo.py` — the one free, no-key, no-JS web-search engine that tolerates light automation. Phase 1 reuses `_shared.ddg_collect_urls` (junk-domain filtered, domain-deduped), Phase 2 reuses `google_search._extract_business_info` (name/email/phone per site), boilerplate-title guard, never raises. Wired into `scrapers._dispatch_source` + `_SOURCE_WEIGHTS` + planner (`_LOCAL_BUSINESS`/`_STARTUP_TECH`/`_GENERAL_FALLBACK`); `discovery_quick_max_sources` 2→3. Other engines: catalog-listed with honest status (`api_required`/`unsupported`) — no fake adapters (§41). | `scrapers/duckduckgo.py` (new), `scrapers/__init__.py`, `discovery/planner.py`, `config.py`, `tests/discovery/test_duckduckgo_source.py` + planner/adapter test updates | 774 pass; live DDG scrape → 6 real VT dental leads (DDG rate-limits with 202s — degrades to Maps fallback, as designed) |
| P9 ~ | Search Orchestrator: the role is **already fulfilled** by `discovery/quick_search.py` (manual) + `automation/lead_search_service.py` (automation) — both do planner-driven provider selection, the fallback-to-next-source loop, per-source isolation, dedup, progress, cancellation. A formal extraction would refactor two working paths for cosmetic gain — **deferred** (documented, not built). |
| P10 ✅ | Automation dashboard counters: `lead_discovery_runs` gains `emails_found` / `leads_scored` / `research_queued` (populated by `lead_search_service._close_discovery_run`). `db.get_discovery_run_totals` + `get_recent_discovery_runs`. `GET /api/automation/status` → `pipeline_totals`; new `GET /api/automation/runs`. `AutomationDashboard.jsx` "Pipeline (all searches)" 6-tile row — real data. | `database.py`, `automation/lead_search_service.py`, `routers/automation.py`, `AutomationDashboard.jsx`, `tests/automation/test_dashboard_counters.py` (3 tests) | 778 pass; live `pipeline_totals` on `/status` |
| P11 ✅ | End-to-end test: real wiring, mocked externals — `run_quick_search` → dedup → `enrich_and_score` (email found + status + score) → `maybe_auto_handoff` (only score ≥ threshold) → research session + `research_status=QUEUED`. Asserts data at every stage. | `tests/test_lead_search_e2e.py` (new) | passes |
| P12 ✅ | Full regression **778 passed** · `npm run build` clean · live smoke: migrations clean, providers endpoint, pipeline_totals, leads filters, research handoff (end-to-end, session 40 completed + email merged back) | — | green |

## Follow-up: Lead → Research Agent handoff completion (2026-09-04, approved)

| Phase | Deliverable | Status |
|---|---|---|
| A | Preserve incomplete businesses: `leads.discovery_status` (FULL/MINIMAL); `basic_validate` keeps name + any anchor (city/niche/address/…); campaign-path `_validate_and_clean` mirrors; MINIMAL excluded from `filter_leads_for_outreach`; back-fill | ✅ 9 tests |
| B | Submission-source tracking: `lead_research_sessions.submission_source` + `leads.research_submission_source`; `handoff_leads(submission_source=…)`; `POST /leads/research` param; `db.set_leads_field` | ✅ |
| C | Automation activity-log events: `maybe_auto_handoff(log_fn=…)` emits one structured `automation_log` line per item (queued / already-queued / failed / queue-unavailable) | ✅ |
| D | Richer research payload: `_SEED_FIELDS` += address, niche, source, google_place_id, lat/long; `discovered_at` from `created_at`; nulls omitted (no placeholders) | ✅ |
| E | Manual Search results → "Send to Research Agent (N)" button + research-status pill (`LeadSearchManual.jsx`) | ✅ |
| F | Automation page "Research Agent" section: auto-send toggle + queue summary (`/status.research_queue`) + recent-discovered-leads table w/ select + send; `GET /automation/runs`; `db.get_research_status_counts` | ✅ |
| G | Status labels — `RESEARCH_LABEL` map (P2) already aligns display (`NOT_STARTED→"Not researched"`, `COMPLETED→"Researched"`, …); no rename | ✅ |
| H | Retry FAILED research — Bot button re-sends (idempotency allows FAILED→re-queue); title/colour for FAILED | ✅ |
| I | Full regression **791 passed** · `npm run build` clean · live: MINIMAL lead sent to research OK, `submission_source` recorded, automation Research section renders (0 console errors) | ✅ |

## Follow-up: Connect the handoff buttons to the Research Agent page (2026-09-04, approved)

**Goal (from `Screenshot_532.png`):** after sending leads, you can open the Research Agent
page and *see* them (session list), get there in one click (nav + stable `?session=` route),
and tell where each session came from (Discovery vs Handoff).

**Architecture map (single service, no second path):**
```
Lead Search (manual / automation) ─┐
Leads page  "Send to Research Agent"├─► research_agent.handoff.handoff_leads(queue, lead_ids,
Manual Search results  "     "      │      *, submission_source, priority)
Automation page  "     "           ─┘        │  one lead_research_sessions row (mode='handoff')
                                             │  seed_lead_ids = [stable lead PKs]
maybe_auto_handoff (persistent automatic) ───┘  idempotent: skips QUEUED/RESEARCHING/EXCLUDED
                                             ▼
                    JobQueue "RESEARCH_AGENT" → session.run_research_session_persisted
                                             ▼
     Research Agent page: GET /api/research-agent/sessions?mode=handoff  (Sessions list)
                          GET /api/research-agent/{id}  →  ?session=<id> deep link + origin badge
```
Manual entry = `POST /api/leads/research` → `routers/leads._handoff` → `handoff_leads`.
Automatic entry = `discovery/enrichment.maybe_auto_handoff` → `_handoff_leads` → `handoff_leads`.
**Both converge on the identical function and the identical table** — verified by
`tests/test_lead_to_research_handoff_integration.py::test_manual_and_automatic_converge_on_one_session_table`.

| Phase | Deliverable | Status |
|---|---|---|
| A | `db.list_research_sessions(limit, offset, mode)` + `GET /api/research-agent/sessions` (`{sessions, has_more}`, `mode` ∈ discovery/handoff, 422 on bad mode, limit clamped ≤100); `researchAgentApi.sessions()` | ✅ 7 tests (`test_sessions_list.py`) |
| B | `ResearchAgent.jsx`: `<SessionsPanel>` (filter chips All / From Lead Search / Started here; poll only while a session is active); `useSearchParams` `?session=` wins precedence; `selectSession()` writes the param (`replace`); session stats block gets a Handoff/Discovery origin badge; "Searching:" line suppressed for handoff | ✅ build clean |
| C | Shared `lib/researchToast.jsx` — success toast with "View in Research Agent →" → `navigate('/research-agent?session=' + id)`. Wired into `Leads.jsx` (row + bulk), `LeadSearchManual.jsx`, `AutomationResearch.jsx`; all invalidate `['research-sessions']` | ✅ build clean |
| D | Whole-feature integration test (real `handoff_leads`, real DB, real `/sessions` — only the JobQueue faked): manual path visible + keyed by stable PK + leads→QUEUED; automatic path same service + same table + `submission_source='lead_search_automation'`; both converge; idempotent across runs; edges — invalid id (200, skipped, no session), empty list (422), duplicate (skipped), bad submission_source (→manual), queue unavailable (503 manual / safe no-op automatic), deleted lead (skipped), unknown `?mode=` (422); regression — discovery session not shown as handoff | ✅ 14 tests (`test_lead_to_research_handoff_integration.py`) |
| — | Full regression **812 passed** · `npm run build` clean · **live end-to-end**: Manual Search → select 2 → "Send to Research Agent (2)" → toast + "View in Research Agent →" → `/research-agent?session=43` opens with "Handoff · 2 leads · manual send" badge + row in Sessions list under the "From Lead Search" filter → session ran to COMPLETED, 2/2 researched, results linked back to lead PKs 1069 & 1070 | ✅ |

Auth boundaries intentionally not tested: single-tenant, SQLite-only, no RBAC (CLAUDE.md);
`/api/research-agent` has no auth dependency.

## Follow-up: Automation Settings hardening (2026-09-04)

Audit of the "Automation Settings" panel (`AutomationSettings.jsx` + `PUT /api/automation/settings`
+ `automation/config.py`). Found: only `start_time` / `timezone` / `per_item_target` were validated;
`daily_limit`, `duration_hours`, `max_retries` accepted **any** value including negatives, which
soft-brick the runner (`daily_limit ≤ 0` → instant `LIMIT_REACHED` + `POST /start` 409;
`max_retries < 0` → `range(0)` → `result` stays `None` → `AttributeError` every slice, item stuck
`SEARCHING`). Frontend sent `max_retries` with no input to set it, and `Number(x) || default`
silently rewrote `0`.

| Fix | Change | Status |
|---|---|---|
| Config clamp (single chokepoint) | `automation/config.py::_INT_BOUNDS` — `daily_limit` (1–100 000), `duration_hours` (0–24), `per_item_target` (1–40), `max_retries` (0–5); clamped in `get_automation_settings()` after coercion, so every consumer (runner, scheduler_hooks, status body) is safe from any stored legacy/hand-edited value with no DB write | ✅ 3 tests (`test_config.py`) |
| API validation | `routers/automation.py::update_settings` loops `_INT_BOUNDS` → 422 `"<key> must be an integer between <lo> and <hi>"` (rejects bool, non-int, out-of-range) | ✅ 3 tests (`test_automation_router.py`) |
| Config default align | `backend/config.py::automation_per_item_target` 100 → 40 (matched `_DEFAULTS`; `_env` was winning so the 40 was dead) | ✅ |
| Frontend | `AutomationSettings.jsx`: added "Retries per failed search" input; `clampField()` on blur + in the save payload (matches backend bounds); form re-seeds from the server's canonical `settings` after save; per-field hint text; amber line when `enabled` but `status ∈ {STOPPED, PAUSED}` ("won't run until you press Start Now / Resume") | ✅ build clean |
| — | Full regression **818 passed** · `npm run build` clean · live: `PUT` negative/oversized `daily_limit`·`duration_hours`·`max_retries` → 422; valid saves persist; browser: blur-clamp `-99→1`, save round-trips, STOPPED warning renders | ✅ |

### Automation Settings upgrade — Option C (approved 2026-09-04)

| Part | Change | Status |
|---|---|---|
| Enable toggle authoritative | `update_settings` → `_reconcile_state_with_settings`: saving `automation_enabled=true` from `IDLE/STOPPED/COMPLETED/LIMIT_REACHED` flips `status→SCHEDULED` and clears `today_date` (so it may run today); saving `false` from `SCHEDULED/RUNNING` → `STOPPED`; `PAUSED` left alone. One activity-log line each. | ✅ 3 tests |
| Truthful next-run | `_compute_next_run(cfg, state)` → `_status_body` returns real `next_run_at` (ISO w/ tz) **or** `null` + `next_run_reason` ("Stopped — press Start Now", "Daily limit reached — resumes tomorrow", "Search list finished", "Due now — starting within ~2 min", …). Dashboard shows one or the other instead of echoing the start-time field. | ✅ 2 tests |
| Duration applies mid-day | `_reconcile_state_with_settings`: changing `automation_duration_hours` while a window is live (`duration_deadline` set or `RUNNING`) recomputes the deadline from `last_run_started_at + hours` (or clears it for 0). | ✅ 1 test |
| start_time hardening | rejects hour>23 / min>59; normalizes `"9:5"` → `"09:05"`. | ✅ 1 test |
| Test-search | `POST /api/automation/test-search` (`@limiter 3/min`) runs ONE real search now on the next pending queue item with the current settings, returns the funnel; does **not** advance `current_position` / `today_count` / queue-item status (merge-dedup = leads kept, no dupes). 503 if no queue. | ✅ 2 tests |
| Frontend redesign | `AutomationSettings.jsx` → Schedule / Search scope / Reliability sections; `automation_max_retries` input added; blur-clamp + payload-clamp to `_INT_BOUNDS`; form re-seeds from server after save; **dirty-state** ("Unsaved changes" pill, Save disabled when clean); live estimate ("≈ N searches left · minimum ~D days at L leads/day"); **"Run a test search"** button + inline result card; per-field hints; amber line when enabled-but-`STOPPED/PAUSED`. `AutomationDashboard.jsx` header shows the computed next-run / reason. `automationApi.testSearch()`. | ✅ build clean |
| — | Full regression **827 passed** (+9) · `npm run build` clean · live: enable-toggle STOPPED→SCHEDULED + `next_run_at` computed; disable→STOPPED; `daily_limit`/`duration_hours`/`max_retries`/`start_time` negative·zero·oversized·out-of-range all 422; dirty-state pill + Save enable/disable (change→enable, revert→disable); sections + estimate render (0 console errors); **`POST /test-search` ran a real scrape → "Dental Clinics / Abbeville, Alabama → 4 new, 28 raw", `current_position`/`today_count` unchanged, queue item still PENDING** | ✅ |

## Rules held throughout

- Every phase: `venv/Scripts/python.exe -m pytest -q` clean + `npm run build` clean before checkpoint.
- TDD for each new module (`superpowers:test-driven-development`).
- No new message-sending path; no bypass of human approval / opt-out / terminal status.
- No CAPTCHA solving / anti-bot evasion / proxy rotation / stealth plugins.
- No fabricated researched facts — researched fields trace to evidence rows.
- Additive DB only; existing 809 leads preserved.
- No secrets in source or frontend; API keys via `app_settings` (encrypted, redacted).
- Don't rebuild Email Finder / Lead Scorer / Research Agent — integrate.

## Risks

| Risk | Mitigation |
|---|---|
| Browser-driven SERP scraping blocked at volume | best-effort + `temporarily_unavailable` status + skip; DuckDuckGo-HTML is the reliable backbone; directory sources (Maps/YP) stay primary |
| Lead quality from web-search results | lead-extraction visits the domain and requires ≥1 real signal (business name from `<title>`/JSON-LD + a contact or address) before creating a lead; unknowns stay null |
| Research handoff load spike | one session, N seeds, existing single research lane + `research_agent_max_*` budgets |
| Duplicate research jobs | `research_status` guard + `candidate_key` idempotency |
| Auto-handoff runaway | `research_handoff_min_score` + daily cap |
| Breaking the 735-test suite / the fixed automation | phase-by-phase; run full suite each phase |
| Scope creep across 12 phases | checkpoint after every phase; stop on any broken functionality |
