# AutoLead-manager — Production Stability Audit

Date: 2026-08-04
Scope: Backend correctness across scraping, data quality, enrichment, AI scoring, Mission Control pipeline, logging, security, performance, DB integrity. Read-only investigation — no code changed in this pass. All citations are `file:line` in the repo at the time of audit.

Prior context: a 2026-08-04 fix already corrected 6/9 scraper sources being hard-routed to Google Maps, added the `campaign_runs.stage` field + Pause/Resume, wired `score_lead` into the automatic campaign flow, added auth + rate limiting, and removed dead Postgres/Redis/scraper-engine infra. This audit verified all of that from current code (not assumed) and found it intact, then went further into the 11 areas below.

## Overall picture

The core scraping → save → score → send pipeline runs end-to-end and most of the individual pieces are genuinely solid: 8 of 9 scrapers work correctly with real pagination/retry/cleanup, per-source failures are properly isolated, Mission Control's stage tracking is real (not the old log-text-guessing heuristic), SQLite is configured correctly (WAL, busy_timeout, serialized writes), and dedup/unique-constraint handling at the DB layer is sound.

The real risk is **two silent gaps that both point the same direction**: the enrichment step (website analysis + AI business intelligence) never runs in either live pipeline, and AI scoring runs automatically but on incomplete data as a direct consequence — so the app looks like it's scoring and enriching leads, but a large fraction of the signal it's supposed to use never gets computed. On top of that there's one real security leak (plaintext secrets returned from a GET endpoint) and one scraper that silently never works in your actual Docker deployment (Bing).

## Findings by severity

### Critical

| # | Issue | Evidence | Impact |
|---|---|---|---|
| C1 | `GET /api/settings` returns SMTP/IMAP passwords and LLM API keys **unredacted** to any caller | `backend/routers/settings_router.py:14-20` | Secrets exposed in a plain GET response |
| C2 | Enrichment (`website_analyzer.py`, `ai_enricher.py`) is not called by either live campaign pipeline — only by a manual single-lead endpoint. The one function that did wire it into bulk flow is unreferenced dead code | `backend/scheduler.py:583` (`run_full_pipeline`, uncalled); live paths: `backend/routers/campaigns.py:_run_campaign_task`, `backend/scheduler.py:204 run_campaign` | business_summary / marketing_gaps / growth_potential / website_quality_score never populate for normally-generated leads |
| C3 | AI scoring runs automatically (good) but on incomplete data — `score_lead` never receives `enriched_data`, so up to 56/100 scoring points are structurally unreachable | `backend/scoring/lead_scorer.py:190-232`; call sites `backend/routers/campaigns.py:194-208`, `backend/scheduler.py:735-747,1026-1034` | Scores are systematically deflated/meaningless for enrichment-dependent dimensions |
| C4 | `ai_enricher.py` bypasses the cloud-LLM provider dispatcher and always speaks Ollama's protocol, even when OpenAI/Anthropic is configured in Settings | `backend/enrichment/ai_enricher.py:28,233` vs. the correct dispatcher `backend/ai_brain.py:654-666` | Enrichment silently 404s/fails and degrades to structural-only for any user who configured a cloud provider |
| C5 | Bing Search scraper hardcodes `headless=False` in Playwright with no headless-display fallback; your Docker image has no Xvfb/DISPLAY | `backend/scrapers/bing_search.py:119` vs. the correct pattern already used in `backend/scrapers/google_maps.py:100-111 _resolve_headless` | Bing never actually succeeds in production; every run silently degrades to a thin DuckDuckGo fallback |
| C6 | Scheduled/cron campaign write path skips ALL data validation and normalization | `backend/scheduler.py:683-692` (no `validators.py` import at all) | Automated campaigns store unvalidated/unnormalized email, phone, website — data quality depends entirely on which pipeline ran |
| C7 | `replies` table has no `ON DELETE` action while `PRAGMA foreign_keys=ON` is set | `backend/database.py:300-309` vs. `:136` | Deleting a lead that has any reply raises an unhandled 500; bulk-delete-all-leads aborts entirely if one in-scope lead has a reply |
| C8 | `google_maps.py` (your highest-weighted source, and the universal fallback for every other source) never normalizes phone/email | `backend/scrapers/google_maps.py` (no `validators` import); contrast with 8 other scraper modules that do call `clean_email`/`clean_phone` | Majority of leads (Maps is weighted 35% + catches every other source's failures) have inconsistent phone/email formatting |

### High

| # | Issue | Evidence |
|---|---|---|
| H1 | Raw exception text (`str(exc)`) returned directly in HTTP response bodies | `settings_router.py:20,29,42,62,75,87,101`; `ai.py:79,150`; `leads.py:212`; `campaigns.py:476-486` |
| H2 | `backend/routers/leads.py` has zero logging — exceptions vanish with no server-side record | confirmed via grep, no `logger.*` calls in the file |
| H3 | No durable record of scraper failures/fallbacks — only live SSE; if nobody's watching, the event is gone and campaign history can't show which sources actually ran vs. fell back | `backend/scrapers/__init__.py:199-231` (fallback logic), only feeds `log_stream.py:14` (in-memory queue) |
| H4 | Cross-run duplicate detection is weak — only exact email/phone match, no fuzzy name/website fallback; a lead with only name+website is never deduplicated against prior runs | `backend/database.py:633-650 find_duplicate_lead` |
| H5 | No real multi-step DB transactions — each write auto-commits independently; a crash mid-operation leaves partial state (e.g. lead saved but no campaign_log row) | `backend/database.py:100-104,131-139`; example at `backend/routers/campaigns.py:259-283` |
| H6 | N+1 queries in scoring/writing/sending loops — a fresh DB connection opened per lead | `backend/routers/campaigns.py:211-215,242-306`; `backend/scheduler.py:735-834` |
| H7 | Country field derived via unreliable heuristic (last comma-segment of scraped address) instead of the actual campaign `country` parameter already available | `backend/scrapers/google_maps.py:364-369` |
| H8 | Website field never normalized (scheme/`www`/trailing slash) despite a normalize helper existing but unused in the write path | helper: `backend/scraper.py:279-283`; unused by `backend/scrapers/google_maps.py:315-390`, `backend/scrapers/__init__.py:376-407` |
| H9 | Most non-Maps scrapers accept the Settings-UI headless/delay `cfg` parameter but never use it | `backend/scrapers/__init__.py:299` passes `cfg`; ignored in `yelp.py`, `yellow_pages.py`, `hotfrog.py`, `foursquare.py`, `top_list.py` |
| H10 | No pause-check during SCRAPING/ENRICHING — clicking Pause is a no-op until that phase finishes | `backend/scrapers/__init__.py:440-589` never calls `_wait_while_paused()` |
| H11 | SSE endpoint drops log level/channel; frontend falls back to sniffing emoji in message text for error/warning classification — the same anti-pattern the stage-tracking fix eliminated, still present for severity | `backend/main.py:255-262` vs. `backend/log_stream.py:19-33`; frontend workaround at `frontend/src/pages/Campaign.jsx:75-83` |
| H12 | Email-finding forked into two divergent implementations across the two live pipelines (deep multi-strategy finder vs. simple single-page regex finder) | `backend/scrapers/email_finder.py` (used by UI campaign path) vs. `backend/scraper.py:599` (used by cron path) |
| H13 | Batch-scoring loop has no per-item try/except — one failing lead silently aborts the rest of that background batch | `backend/routers/inbox.py:199-200` |

### Medium / cleanup

| # | Issue | Evidence |
|---|---|---|
| M1 | Foursquare scraper doesn't paginate past the first successful URL variant — can silently under-deliver vs. `max_results` with no warning | `backend/scrapers/foursquare.py:316-318` |
| M2 | WHOIS email lookup has no timeout — can hang a worker thread | `backend/scrapers/email_finder.py:529-559` |
| M3 | No caching anywhere in enrichment (Redis `cache.py` removed in Phase 1, nothing replaced it) — same domain re-fetched/re-scored every time | confirmed via grep, zero remaining imports of the deleted module |
| M4 | Dead code accumulating: `backend/scraper.py` + `routers/scraper_router.py` (superseded duplicate scraping logic), `backend/queue_worker.py`'s `JobQueue` (started at boot, never actually enqueued into), `backend/scheduler.py:run_full_pipeline` (unreferenced) | grep-confirmed zero live callers for each |
| M5 | API keys / SMTP passwords stored unencrypted at rest in `app_settings` (separate from the C1 GET-leak — even the storage itself has no encryption) | `backend/email_sender.py:39`; `backend/ai_brain.py:188-193` |
| M6 | A few endpoints accept raw untyped `dict` bodies, bypassing Pydantic validation | `settings_router.py:34,92` (bulk_update, save_dna); `ai.py:118,128` (test_prompt, test) |
| M7 | Campaign-recovery-on-restart resets `status` to FAILED but not the stale `stage` column — can show `status=FAILED, stage=SCRAPING` | `backend/database.py:381-384` |
| M8 | DB unique index on `email` is case-sensitive while the app-level pre-check normalizes case (`LOWER(email)`) — a race could create two rows differing only by case | `backend/database.py:194-197` vs. `find_duplicate_lead`'s `LOWER()` comparison |

## What's already solid (verified working, no action needed)

- 8/9 scrapers (all but Bing) — real per-source logic, pagination, retry policy (2 attempts, backoff on 429/503, no retry on permanent 403/404), max_results enforcement, browser/driver cleanup in `finally` blocks.
- Per-source failure isolation — one scraper dying never aborts a campaign (`asyncio.gather(..., return_exceptions=True)` + inner try/except); silent Maps substitution does not happen, all fallbacks are logged and tagged.
- Campaign stage tracking (`QUEUED → ... → COMPLETED/FAILED`) is real and DB-persisted, not the old log-text heuristic; frontend correctly consumes it.
- SSE reconnects on drop with backoff; a stalled SSE connection doesn't make the UI look stuck (status comes from a separate poll).
- Per-lead send failures (email + WhatsApp desktop automation) are caught, logged, and never crash the run; WhatsApp's `FailSafeException` is specifically handled.
- Pause/Resume correctly continues the same coroutine mid-loop for scoring/writing/sending (no restart, no duplicate work) — only gap is the scrape/enrich phase (H10).
- Auth, rate limiting, SQL injection surface (parameterized queries + whitelisted columns), and secrets-in-logs are all clean.
- Within-batch and within-run duplicate detection (fuzzy name + normalized phone/email) works correctly; the DB-level unique index + `IntegrityError` race handling is sound.
- WAL mode, `busy_timeout`, and serialized writes (`asyncio.Semaphore(1)`) are correctly configured for SQLite concurrency.
- Browser concurrency is well-bounded (single campaign at a time, single Selenium driver alive at once).

## Proposed phased fix order

Given the size of this list, I'd suggest the same phasing approach we used for the backend-correctness pass, rather than fixing all ~29 items in one giant pass:

- **Phase A — Critical (C1–C8):** the security leak, the enrichment/scoring gap (these three are one root cause: wire enrichment into the live pipelines, fix the provider bypass, then scoring gets its missing data for free), the Bing scraper fix, the scheduler validation gap, the `replies` FK crash, and Maps phone/email normalization.
- **Phase B — High (H1–H13):** exception leakage, logging gaps, durable failure records, weaker cross-run dedup, DB transactions, N+1 queries, and the remaining scraper/pipeline polish items.
- **Phase C — Medium/cleanup (M1–M8):** dead code removal, WHOIS timeout, caching, at-rest encryption, minor validation gaps.

---

## Fixes applied (2026-08-05)

All 29 findings (C1–C8, H1–H13, M1–M8) were implemented in this pass, in one continuous session per your direction. Summary by area:

**Enrichment / scoring (the core architectural gap, C2+C3+C4):**
- `enrich_lead_with_ai`/`analyze_website` are now called in both live pipelines — `routers/campaigns.py:_run_campaign_task` (new ENRICHING stage, bounded concurrency of 3) and `scheduler.py:run_campaign` (per-lead, before scoring) — not just the manual single-lead endpoint.
- Every `score_lead(...)` call site (`campaigns.py`, `scheduler.py` ×3, `routers/inbox.py`) now fetches and passes `enriched_data`, so the D2/D3/D4 scoring dimensions are no longer structurally stuck near 0.
- `enrichment/ai_enricher.py` now calls the provider-dispatching `_call_llm_raw` instead of hardcoding `_call_ollama_raw` — cloud-provider (OpenAI/Anthropic) users get real enrichment instead of silent 404s degrading to structural-only.
- `scheduler.py`'s email probe now uses the deep multi-strategy finder (`scrapers/email_finder.py`) instead of the old single-page regex finder, matching the UI pipeline (H12).

**Security:**
- `GET /api/settings` now redacts `*_password`/`*_api_key`/`*_secret`/`*_token` values (C1).
- SMTP/IMAP passwords and LLM API keys are now encrypted at rest (Fernet, key auto-generated at `backend/data/.secret.key`) — transparent to every existing caller via `db.get_setting`/`get_all_settings`/`upsert_setting`; degrades gracefully to plaintext (with a logged warning) if `cryptography` isn't installed yet (M5).
- Raw exception text (`str(exc)`) no longer flows into HTTP response bodies in `settings_router.py`, `ai.py`, `leads.py`, `campaigns.py` — errors are logged server-side with full detail and a generic message returned to the client (H1). Added logging to `leads.py`, which previously had none (H2). Replaced 4 raw-`dict` request bodies with real Pydantic models (M6).
- **Bonus fix (not in the original audit):** `settings_router.py`'s `/reset-stats` endpoint called `conn.transaction()` — leftover asyncpg-style code with no such method on the SQLite shim, so this endpoint would have thrown `AttributeError` on every call. Fixed using the new `db.transaction()` primitive.

**Scrapers:**
- Bing Search no longer hardcodes `headless=False` — it now detects a no-display Linux host (Docker) the same way `google_maps.py` already does, so it actually succeeds in your deployment instead of always silently degrading to the thin DuckDuckGo fallback (C5).
- `google_maps.py` — phone/email are now normalized like every other source (via the new central DB-layer choke point, see below) (C8); the `country` field now prefers the campaign's actual country parameter over the unreliable last-comma-segment address heuristic, with the heuristic kept only as a fallback (H7).
- Yelp, Yellow Pages, Hotfrog, Foursquare, Top-List now honor the Settings-UI delay_min/delay_max instead of ignoring `cfg` entirely (H9).
- Foursquare now logs a clear warning when it under-delivers vs. the requested lead count instead of silently stopping at the first successful URL variant (M1).
- Every scraper failure/fallback is now persisted to `campaign_log` (lead_id=NULL), not just the live SSE feed — visible in campaign history after the fact (H3).
- WHOIS email lookup now has an 8s hard timeout instead of being able to hang a worker thread indefinitely (M2).

**Data quality & database integrity:**
- Added a single normalization choke point (`database.py:_normalize_lead_fields`, called from `create_lead`/`update_lead`) — business name, email, phone, and website are now cleaned on **every** write path (scheduler, Maps, manual API create, CSV import), closing C6 and C8 without needing per-caller changes.
- Cross-run duplicate detection (`find_duplicate_lead`) now falls back to normalized-website and normalized-name+city matching when email/phone are absent, instead of never deduplicating those leads across runs (H4).
- The `email` unique index is now case-insensitive (`LOWER(email)`), matching the app-level dedup check (M8).
- `replies.lead_id`/`message_id` now cascade on delete (auto-migrated for existing databases) — deleting a lead that has a reply no longer throws an unhandled 500, and bulk-delete no longer aborts entirely because one lead has a reply (C7).
- Added a real `db.transaction()` primitive and used it for lead-insert + campaign_log writes (`create_lead_deduped_with_log`) so that pairing is now atomic instead of two independent auto-committed statements (H5).
- Added `db.get_leads_by_ids()` batch fetch and used it to replace N+1 per-lead re-fetch loops in the campaign pipeline and batch-scoring endpoint (H6).
- Campaign-recovery-on-restart now resets the stale `stage` column alongside `status=FAILED`, instead of leaving `status=FAILED, stage=SCRAPING` (M7).

**Mission Control / pipeline:**
- Pause now takes effect during SCRAPING/ENRICHING too (previously only scoring/writing/sending), via a new `pause_callback` threaded into `run_bulk_scrape` (H10).
- The SSE log stream now forwards `level`/`channel` instead of dropping them — the frontend uses the real severity field instead of sniffing emoji in the message text (H11, both backend `main.py` and frontend `Campaign.jsx`).
- `routers/inbox.py`'s batch-scoring loop no longer aborts entirely when one lead fails to score (H13).

**Cleanup:**
- Removed `scheduler.py:run_full_pipeline` (285 lines, confirmed zero callers, fully superseded now that `run_campaign` does real enrichment+scoring) (M4, partial).
- Added a lightweight in-process TTL cache for `analyze_website()` fetches (M3).
- `backend/scraper.py` and `routers/scraper_router.py` were **not** removed — both are still load-bearing (`scheduler.py` calls `scraper.scrape_google_maps`/`scrape_multi_source`; `status.py` imports `_scraper_state` from `scraper_router.py`). The audit's "duplicate legacy code" framing was accurate for a few internal helper functions, not these modules as a whole.
- `queue_worker.py`'s `JobQueue` was **not** removed — it's started at boot in `main.py`'s lifespan, and safely disabling that touches app startup/shutdown, which needs a live-environment check I couldn't do in this pass. It remains harmless dead infrastructure (started, never enqueued into); listed below as recommended future work.

### Verification performed

- `python -m compileall backend` — every backend `.py` file compiles cleanly.
- Imported `backend.main`, `backend.scheduler`, `backend.scrapers`, and every touched router through the project's actual virtualenv (`backend/.venv`) — the full app wires together with no import-time errors, including the `cryptography`-not-yet-installed degradation path.
- Ran an end-to-end smoke test against a throwaway SQLite database covering: field normalization on insert, website+name+city dedup fallback, case-insensitive email uniqueness, the atomic insert+log transaction, the `replies` cascade-delete fix, batch lead fetch, and the settings-encryption round-trip (raw DB value is ciphertext; `db.get_setting` transparently decrypts it). All passed.
- Live 100/500/1000/5000-lead benchmark campaigns, a real Docker Bing Search run, and a real WhatsApp-desktop send were **not** run — no live environment available in this pass. Everything above was verified by direct code execution against a real (temporary) SQLite database and the project's real dependency set, not by reasoning about the code alone.

### Remaining known limitations

- `queue_worker.py` is still dead infrastructure (started at boot, nothing ever enqueues into it) — wiring it in for real parallelism, or removing it along with its `main.py` lifespan hook, is a small follow-up that needs a live-environment smoke test.
- `cryptography` needs `pip install -r requirements.txt` to actually take effect for M5 — until then, secrets remain plaintext exactly as before (logged as a warning on startup), so this is a deploy-time action item, not just a code change.
- Live performance benchmarking (100/500/1000/5000-lead campaigns) from the original Task 9 was out of scope for a no-environment pass — the DB-layer fixes (transactions, batch fetch, WAL/busy_timeout, which were already correct) address the obvious static bottlenecks, but real throughput numbers are unverified.
- The pause-check added to `run_bulk_scrape` (H10) is checked at phase boundaries (before scrape, before enrich, before save), not mid-scrape within a single source — pausing mid-Google-Maps-scrape still finishes that source's current batch first.

### Recommended future work

- Decide the fate of `queue_worker.py`: either wire real concurrency through it (enrichment and per-lead scoring are the natural candidates) or remove it and its `main.py` lifespan hook.
- Consider consolidating `backend/scraper.py` and `backend/scrapers/` — the former is still live (used by the scheduler's cron path) but duplicates logic the latter package already does better; unifying them would remove a whole class of "which pipeline has which fix" bugs like the ones this audit found.
- Run a real Docker deployment smoke test for the Bing Search headless fix (C5) — the logic mirrors the already-proven Maps pattern, but wasn't exercised against an actual container in this pass.
- Frontend polish and Electron packaging remain Phase 2/3 of the original production-upgrade plan and are still not started.

