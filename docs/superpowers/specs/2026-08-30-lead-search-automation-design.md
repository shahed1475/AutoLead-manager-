# Lead Search Automation — Design Spec (Phase 1)

**Status:** Approved 2026-08-30 (in-chat brainstorm; scoping + limit/target/backend decisions confirmed via Q&A).
**Scope:** Phase 1 — core automation, single config + single queue. Multi-campaign management is Phase 2.

## 1. Objective

Let a user upload a CSV/XLSX of locations and niches, then have the backend search
every niche×location combination **sequentially**, collect deduplicated leads,
stop each day at a configurable limit or time window, persist its exact position,
and automatically resume the next day at a configured start time — all as a
server-side job that does not depend on the browser being open.

This is a **discovery-only** feature. It collects leads. It never enriches,
scores, drafts, or sends outreach — that is the existing Phase 4 system's
territory and is not touched here.

## 2. Confirmed decisions

| Question | Decision |
|---|---|
| Search backend | The built-in discovery pipeline (`DiscoveryPlanner` → `SourceRegistry` → `merge_and_save` → `create_or_merge_lead`), wrapped in a swappable `LeadSearchService` interface. |
| First scope | Core automation with **one** active config + queue. Multiple named campaigns, add-to/replace flows → Phase 2. |
| Daily-limit precision | **Allow the final item to overshoot.** Each search runs at its per-item target; new leads are counted; once `today_count ≥ daily_limit` the current item finishes and the run stops. No mid-batch discard. |
| Per-item target | "As many as the source returns" — no per-combination cap beyond a bounded default (`automation_per_item_target = 100`, the existing discovery ceiling). |
| Iteration order | **Niche-outer, location-inner** (matches the spec's own example: all locations for niche #1, then all locations for niche #2, …). |
| File formats | `.csv` and `.xlsx`. `.xls` (old binary format) is **not** supported — the upload UI states this. |
| Timezone | Configurable IANA timezone, default `America/New_York`. APScheduler stays UTC; the tick job compares against the configured zone via `zoneinfo`. |
| Reset Progress | Requires explicit confirmation. Resets `current_position` to 0 and every queue item to `PENDING`, zeroes `today_count`, **preserves `total_count`** (historical). Does not delete collected leads. |

## 3. Architecture

```
CSV / XLSX upload
      │  POST /api/automation/import/preview   (parse only, no persistence)
      ▼
Import Preview  (locations, niches, N×M combinations, estimated days)
      │  POST /api/automation/import/confirm
      ▼
automation_queue  (niche-outer × location-inner, position 0..N-1)
automation_state.current_position = 0

APScheduler  automation_tick()  — every 2 min
      │  enabled? AND now-in-tz ≥ start_time? AND today not yet run? AND not RUNNING?
      │  → daily reset (today_count = 0), set duration_deadline, enqueue slice
      ▼
JobQueue  run_automation_slice()          ── server-side, page-independent
      │  loop automation_queue from current_position:
      │     re-check pause / stop / limit / deadline   (between EVERY item)
      │     LeadSearchService.search_leads(niche, city, state)
      │        → DiscoveryPlanner.plan(mode=QUICK) → SourceRegistry per source
      │        → merge_and_save → create_or_merge_lead   (dedup + lead_sources)
      │     update queue item (COMPLETED / PARTIAL / FAILED), leads_found, new_leads
      │     today_count += new_leads ;  total_count += new_leads
      │     ATOMIC write: current_position, counts, last_niche/location/query
      │     today_count ≥ daily_limit  → status LIMIT_REACHED, stop
      │     now ≥ duration_deadline    → status SCHEDULED,     stop
      │     transient error → retry ≤2 with backoff; hard fail → item FAILED, advance
      ▼
queue exhausted → status COMPLETED (position stays at end)
```

**Server restart:** `run_automation_slice` is idempotent from `current_position`
and every queue item carries its own status, so a re-enqueue after a crash
resumes exactly where it stopped. A `RUNNING` `automation_state` whose worker is
gone is re-enqueued by the startup reconciler (same pattern as
`research_agent.session.reconcile_interrupted_sessions`).

## 4. Backend package `backend/automation/`

Mirrors `backend/discovery/` / `backend/research_agent/` (own `config.py` with the
`app_settings`-override pattern, own models, own `tests/` dir, no cross-imports
into unrelated subsystems).

### `config.py`
`get_automation_settings() -> dict` — layering `app_settings` DB override > `.env`/
pydantic default > hardcoded fallback (same as `_scraper_cfg` / `_ollama_cfg` /
`research_agent/config.py`).

| Key | Default | Notes |
|---|---|---|
| `automation_enabled` | `False` | master on/off for the daily schedule |
| `automation_daily_limit` | `500` | new leads per day; `[1, 100000]` |
| `automation_start_time` | `"07:00"` | 24h `HH:MM` in the configured tz |
| `automation_timezone` | `"America/New_York"` | IANA name; validated against `zoneinfo` |
| `automation_duration_hours` | `4` | daily run window; `0` = no time cap |
| `automation_per_item_target` | `100` | discovery `target_count` per combination; `[1, 100]` |
| `automation_max_retries` | `2` | transient-error retries per queue item |

### `file_import.py`
`parse_upload(filename: str, data: bytes) -> ParsedImport`

- Dispatch by extension: `.csv` → stdlib `csv` (`utf-8-sig`); `.xlsx` → `openpyxl`
  (read-only mode). Anything else → `ValueError` with a clear message.
- **Layout detection:**
  - *Combined:* one sheet/file whose header row contains a location column
    (`city` / `town` / `location`) **and** a niche column
    (`niche` / `industry` / `category` / `keyword` / `service`). Optional
    `state` / `province` / `region` column.
  - *Separate:* two sheets in one xlsx (or a locations file + a niches file) —
    one has location columns and no niche column, the other has only a niche
    column. Cartesian product is built later by `queue_builder`.
- Header matching is case-insensitive, whitespace/underscore-insensitive, and
  fuzzy on the token set above; unmatched columns are ignored with a warning.
- Trims whitespace; drops fully-empty rows; de-dupes exact-duplicate locations
  and niches (case-insensitive) with a warning count.
- Returns `ParsedImport { locations: [{city, state}], niches: [str],
  combinations: int, layout: "combined"|"separate", warnings: [str] }`.
- Never persists anything.

### `queue_builder.py`
`build_queue(locations, niches) -> list[QueueItem]` — for each niche in order,
for each location in order, emit `QueueItem(position, niche, city, state)`.
`len == len(niches) * len(locations)`. Empty niches or locations → empty queue
(caller rejects with 422).

### `lead_search_service.py`
```python
class LeadSearchService(ABC):
    @abstractmethod
    async def search_leads(self, niche, city, state, country, target) -> SearchResult: ...

@dataclass
class SearchResult:
    new_leads: int          # count of is_new==True from create_or_merge_lead
    total_found: int         # everything the sources returned (incl. merges)
    lead_ids: list[int]
    sources_used: list[str]
    error: str | None = None  # set on hard failure — never raises
```
Phase-1 implementation `DiscoveryLeadSearch`:
1. `location = ", ".join(p for p in (city, state) if p)`.
2. `plan = await DiscoveryPlanner().plan(query=niche, niche=niche, city=city, country=country or "", mode="QUICK")`.
3. For each `plan.recommended_sources` (respecting the run-scoped circuit
   breaker in `SourceRegistry`): `registry.execute(source, search_text, city, country, target, cfg)`.
4. `save = await merge_and_save(all_candidates, run_id=None, default_source="AUTOMATION")`.
5. Tag: `merge_and_save` already writes `lead_sources`; the automation runner
   additionally records `automation_queue.new_leads = save["new_count"]`.
6. Return `SearchResult(new_leads=save["new_count"], total_found=len(all_candidates), …)`.
7. Any exception → caught, logged, returned as `SearchResult(new_leads=0, error=str(exc))`.

The interface is deliberately small so a paid provider (SerpAPI / Google Places /
etc.) can be swapped in later behind a settings key, honouring that provider's
auth, rate limits, pagination and ToS.

### `runner.py`
`async def run_automation_slice(payload: dict) -> None` — JobQueue handler.

1. Load `automation_state` + `get_automation_settings()`.
2. Guard — exit immediately (no status change) if: not enabled, `status in
   {PAUSED, STOPPED}`, `status == RUNNING` (a slice already owns it — dedupe),
   `today_count >= daily_limit`, `now >= duration_deadline`, or the queue is
   empty / already at the end.
3. Set `status = RUNNING`, `last_run_started_at = now`.
4. Loop `automation_queue` where `position >= current_position` ordered by
   `position`:
   - Reload `automation_state` each iteration; break on `status in {PAUSED,
     STOPPED}` (cooperative — position already persisted, so it resumes cleanly).
   - `today_count >= daily_limit` → `status = LIMIT_REACHED`; break.
   - `duration_hours > 0 and now >= duration_deadline` → `status = SCHEDULED`; break.
   - Mark item `SEARCHING`; `automation_log` line.
   - `for attempt in range(max_retries + 1)`: `result = await service.search_leads(...)`;
     on `result.error` and attempt < max → `asyncio.sleep(backoff)`, continue;
     else break.
   - Item status: `FAILED` (final attempt returned `error` and 0 found),
     `PARTIAL` (returned `error` but some leads were found), `COMPLETED`
     (no error — including a legitimate 0-result search for a niche/location
     that genuinely has none).
   - **Atomic transaction:** update the queue item **and** `automation_state`
     (`current_position = position + 1`, `today_count += new`, `total_count += new`,
     `last_niche/last_location/last_query`, `last_success_at` if new > 0,
     `queue_completed += 1`) in one `transaction()`.
   - `automation_log` line with the per-item result.
5. Queue exhausted → `status = COMPLETED`.
6. `last_run_finished_at = now`. Compute `next_run_at` for the UI.
7. Wrap the whole body so it never raises out; on an unexpected exception set
   `status = SCHEDULED`, log it, and leave `current_position` untouched (the
   last atomic write is the checkpoint).

### `scheduler_hooks.py`
- `async def automation_tick() -> None` — registered on APScheduler with
  `IntervalTrigger(minutes=2)`.
  - `s = get_automation_settings()`; return if not `s["automation_enabled"]`.
  - `tz = ZoneInfo(s["automation_timezone"])`; `now_local = datetime.now(tz)`.
  - Parse `start_time`; if `now_local.time() < start` → return.
  - `state = get_automation_state()`; return if `state["status"] == "RUNNING"` or
    `state["today_date"] == now_local.date().isoformat()` (already ran today) or
    `state["status"] == "STOPPED"`.
  - **Daily reset + launch (atomic):** `today_date = today`, `today_count = 0`,
    `status = SCHEDULED`, `duration_deadline = now_utc + duration_hours`,
    `current_position` unchanged (resume, not restart).
  - Enqueue `run_automation_slice` on the JobQueue.
- `async def resume_running_slice(queue) -> None` — startup reconciler. If
  `automation_state.status == "RUNNING"` (worker died mid-slice) → re-enqueue a
  slice (state and per-item statuses make it resume from `current_position`).
- `async def kick_slice_now(queue) -> None` — used by `POST /start` and
  `POST /resume`: performs the same daily-reset-if-new-day logic then enqueues.

## 5. Database (additive)

Added to `_SCHEMA_SQL` **and** `_run_migrations` (`_add_col_if_missing` /
`CREATE TABLE IF NOT EXISTS` — dev DBs must pick these up).

### `automation_state` — single row, `id = 1`, created on first access
| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | always 1 |
| `status` | TEXT | IDLE / SCHEDULED / RUNNING / PAUSED / LIMIT_REACHED / STOPPED / COMPLETED |
| `current_position` | INTEGER DEFAULT 0 | index into `automation_queue.position` |
| `today_count` | INTEGER DEFAULT 0 | new leads collected today |
| `total_count` | INTEGER DEFAULT 0 | new leads collected all-time by automation |
| `today_date` | TEXT | ISO date (in configured tz) of the current day's run |
| `duration_deadline` | TIMESTAMP | UTC; when today's window closes |
| `next_run_at` | TIMESTAMP | UTC; derived, for the UI |
| `queue_total` | INTEGER DEFAULT 0 | `len(automation_queue)` |
| `queue_completed` | INTEGER DEFAULT 0 | items in a terminal status |
| `last_niche` / `last_location` / `last_query` | TEXT | last processed |
| `last_success_at` | TIMESTAMP | last item that produced ≥1 new lead |
| `last_run_started_at` / `last_run_finished_at` | TIMESTAMP | |
| `paused_at` | TIMESTAMP | |
| `import_id` | INTEGER → automation_imports(id) | current queue's source file |
| `updated_at` | TIMESTAMP | |

### `automation_queue`
| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `position` | INTEGER UNIQUE NOT NULL | 0-based |
| `niche` / `city` / `state` | TEXT | `state` nullable |
| `status` | TEXT DEFAULT 'PENDING' | PENDING / SEARCHING / COMPLETED / PARTIAL / FAILED / SKIPPED |
| `leads_found` | INTEGER DEFAULT 0 | total returned by sources |
| `new_leads` | INTEGER DEFAULT 0 | net-new after dedup |
| `attempts` | INTEGER DEFAULT 0 | |
| `error_message` | TEXT | |
| `started_at` / `finished_at` | TIMESTAMP | |
Indexes: `(status)`, `(position)`.

### `automation_log`
`id`, `ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP`, `level TEXT`, `message TEXT`.
Index `(ts)`. Capped read (last N); a periodic prune keeps it bounded
(`DELETE ... WHERE id NOT IN (SELECT id ... ORDER BY id DESC LIMIT 2000)` on write).

### `automation_imports`
`id`, `filename`, `layout`, `n_locations`, `n_niches`, `n_combinations`,
`imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`.

### `leads`
No change. Automation-collected leads carry `source = "AUTOMATION"` (added to
`SOURCE_BADGE` / `SOURCE_LABEL` in `frontend/src/lib/badges.js`). Dedup and
`lead_sources` provenance unchanged.

### DB helpers (in `database.py`)
`get_automation_state()` (creates row 1 if absent), `update_automation_state(data)`
(writable-field allow-list, like `update_research_session`), `bulk_insert_automation_queue(items)`,
`get_automation_queue(status?, offset, limit)`, `count_automation_queue_by_status()`,
`update_automation_queue_item(id, data)`, `reset_automation_queue()`,
`append_automation_log(level, message)`, `get_automation_log(limit)`,
`create_automation_import(data)`. The runner's per-item checkpoint uses one
`transaction()` spanning the queue-item update and the state update.

## 6. API — `backend/routers/automation.py`

Prefix `/api/automation`, all routes behind `_authed` (existing single-user
session dependency). Rate-limited where it triggers work.

| Method + path | Body / params | Behaviour |
|---|---|---|
| `POST /import/preview` | multipart `file` (+ optional second `file`) | `parse_upload` → `{locations, niches, combinations, layout, estimated_days, warnings}`. `estimated_days = ceil(combinations / daily_limit)` — a crude **minimum** (assumes ≥1 net-new lead per combination), always shown with an "estimate, actual results vary" caveat. No persistence. `422` on bad type / missing niche column / empty. |
| `POST /import/confirm` | JSON: `{locations, niches, filename, layout}` (the preview payload) | `mode` is fixed `"replace"` in Phase 1: `reset_automation_queue()`, `bulk_insert_automation_queue(build_queue(...))`, `current_position = 0`, `today_count = 0`, `total_count` preserved, `status = SCHEDULED` if enabled else `IDLE`, `create_automation_import(...)`. Returns the new `/status` body. |
| `GET /status` | — | `automation_state` + settings + derived: `progress_pct`, `time_remaining_seconds`, `next_run_at`, `current_niche`, `current_location`, `searches_completed`, `searches_remaining`. |
| `GET /queue` | `status?`, `offset=0`, `limit=100` | Paginated items + `counts_by_status`. |
| `GET /log` | `limit=100` | Recent lines, newest first. |
| `PUT /settings` | JSON subset of the 7 settings | Validates (`HH:MM`, IANA tz via `zoneinfo`, numeric ranges) → writes `app_settings`. If `automation_start_time` / `timezone` / `enabled` changed, no APScheduler reschedule needed (the 2-min tick reads settings live). Returns `/status`. |
| `POST /start` | — | Start Now: `kick_slice_now` (daily-reset if new day, then enqueue). Still bounded by `daily_limit`; if `today_count >= daily_limit` returns `409` "daily limit already reached". `503` if queue empty / not imported. |
| `POST /pause` | — | `status = PAUSED`, `paused_at = now`. Runner stops at the next item boundary; `current_position` already persisted. `409` if not RUNNING/SCHEDULED. |
| `POST /resume` | — | `status = SCHEDULED`; `kick_slice_now`. |
| `POST /stop` | — | `status = STOPPED`. No auto-runs until settings re-enabled or `/start`. Collected leads and position preserved. |
| `POST /reset` | `confirm=true` (required) | `reset_automation_queue()` (all items → PENDING), `current_position = 0`, `today_count = 0`, `total_count` preserved, `status = IDLE`/`SCHEDULED`. `400` without `confirm=true`. |

Existing endpoints unchanged. No new auth surface.

## 7. Frontend

### `frontend/src/pages/LeadSearch.jsx`
Add a **"Lead Search Automation"** section **above** the existing manual search
form (the manual Quick Search is untouched). The section renders:
- If no queue imported → `AutomationUpload` (dropzone + copy: "CSV or XLSX. Columns: City/Town, State, Niche — or separate location and niche sheets.").
- If a queue exists → `AutomationDashboard` + collapsible `AutomationQueueTable`,
  `AutomationLog`, `AutomationSettings`.

### `frontend/src/components/automation/`
| Component | Contents |
|---|---|
| `AutomationUpload.jsx` | Dropzone → `POST /import/preview` → **Import Preview modal**: "✓ N locations · ✓ M niches · ✓ N×M combinations · Daily limit L · Estimated ≥ D days (estimate)", scrollable location + niche lists, warnings, `[Cancel] [Confirm Import]`. Confirm → `POST /import/confirm`. |
| `AutomationDashboard.jsx` | Status pill (🟢 Running / 🟡 Scheduled / 🔴 Stopped / ⏸ Paused / ✅ Limit Reached / ✔ Completed), progress bar (`today_count / daily_limit` + %), tiles: today's leads, total leads, current niche, current city, current state, searches completed, searches remaining, duration, time remaining today, next scheduled run, last successful search, status. Controls row: `▶ Start Now` · `⏸ Pause` · `▶ Resume` · `⏹ Stop` · `🔄 Reset Progress` (confirm dialog). Buttons enabled per status. |
| `AutomationQueueTable.jsx` | Virtualised (`@tanstack/react-virtual`, like `LeadTable`), columns: # · Niche · City · State · Status (pill) · New leads. Status filter chips. |
| `AutomationLog.jsx` | Last ~100 lines, monospace, newest first, error lines tinted red. |
| `AutomationSettings.jsx` | `SectionCard`-style: Daily Lead Limit (number), Daily Start Time (`<input type=time>`), Search Duration (number, hours, `0` = unlimited), Timezone (`<select>` of ~30 common IANA zones + the current value), `[✓] Enable Daily Automation`, `[Save Settings]`. On save → `PUT /settings`. |

### `frontend/src/api/client.js`
`automationApi` module: `previewImport(formData)`, `confirmImport(payload)`,
`status()`, `queue(params)`, `log(limit)`, `saveSettings(payload)`, `start()`,
`pause()`, `resume()`, `stop()`, `reset()`.

### Polling
`automationApi.status` via React Query with
`refetchInterval: RUNNING||SCHEDULED ? 3000 : 15000`. State is entirely
server-side — navigating away or refreshing never affects the job (same
guarantee as the Research Agent reconnection work). No client-side timers for
scheduling anywhere.

### `frontend/src/lib/badges.js`
`AUTOMATION` → a distinct source badge + label (e.g. `🤖 Auto`).

## 8. Modified existing files

| File | Change |
|---|---|
| `backend/database.py` | 4 new tables in `_SCHEMA_SQL`; matching `_run_migrations` block; ~11 helper functions; the runner's atomic checkpoint. |
| `backend/main.py` | `include_router(automation_router.router, dependencies=_authed)`; call `resume_running_slice` inside the existing `_reconcile_interrupted_jobs` startup task. |
| `backend/scheduler.py` | Register `automation_tick` with `IntervalTrigger(minutes=2)` in `start_scheduler` (+ `misfire_grace_time`). |
| `backend/config.py` | 7 `automation_*` setting defaults on the `Settings` model. |
| `backend/requirements.txt` | `+ openpyxl>=3.1`. |
| `frontend/src/pages/LeadSearch.jsx` | Mount the automation section above the manual form. |
| `frontend/src/api/client.js` | `automationApi`. |
| `frontend/src/lib/badges.js` | `AUTOMATION` badge + label. |

**New:** `backend/automation/{__init__,config,file_import,queue_builder,lead_search_service,runner,scheduler_hooks}.py`, `backend/routers/automation.py`, `frontend/src/components/automation/{AutomationUpload,AutomationDashboard,AutomationQueueTable,AutomationLog,AutomationSettings}.jsx`, `tests/automation/`.

## 9. Testing (TDD — existing project bar; all 465 current tests stay green)

**`tests/automation/`:**
- `test_file_import.py` — combined sheet; separate sheets; separate files; messy/aliased headers; missing niche column → error; `.xls` → error; whitespace/blank-row trimming; duplicate collapse; csv + xlsx parity.
- `test_queue_builder.py` — niche-outer ordering; `N*M` count; position uniqueness/contiguity; empty inputs.
- `test_lead_search_service.py` — mocked `DiscoveryPlanner`/`SourceRegistry`/`merge_and_save`: `new_leads` == `new_count`; error in a source → `SearchResult.error` set, never raises; `sources_used` recorded.
- `test_runner.py` — stops at `daily_limit` (final item overshoot allowed, counted); stops at `duration_deadline`; `current_position` written after **every** item (kill mid-loop, resume continues); PAUSE mid-loop honored at boundary, position preserved; STOP mid-loop honored; transient error retried `max_retries` then item FAILED + advance; queue exhausted → COMPLETED; never raises out; guard exits are no-ops.
- `test_scheduler_hooks.py` — `automation_tick` fires only when enabled + past start_time in tz + not already run today + not RUNNING; crossing into a new day zeroes `today_count`, preserves `current_position`/`total_count`; disabled → no-op; `resume_running_slice` re-enqueues a stuck RUNNING state.
- `test_automation_router.py` — preview does not persist; confirm builds the queue + resets position; `/start` respects `daily_limit` (409 when hit); `/reset` requires `confirm=true`; pause/resume/stop transitions; all routes `_authed`; `/status` derived fields.
- `test_automation_db.py` — `automation_state` row-1 auto-create; writable-field allow-list; atomic checkpoint (queue item + state commit together); `reset_automation_queue`; log prune keeps ≤ cap.

**Live check:** small real xlsx (3 niches × 3 cities), enable with a low limit
(e.g. 10), `POST /start`, confirm leads land with `source=AUTOMATION`, dedup
holds across overlapping combinations, position advances, limit stops the run,
UI reflects it after a navigate-away/refresh.

## 10. Non-negotiables

- **No outreach.** No message generation, no send, no `email_sender`/`whatsapp_sender` import anywhere in `backend/automation/`.
- **No fabrication.** Leads store only what the scrapers return; a missing email/phone/social stays `NULL`.
- **Dedup reuses `create_or_merge_lead`** (email / phone / website / fuzzy name+city) + `lead_sources` provenance — no second dedup path.
- **Backend-only scheduling** — APScheduler + JobQueue. Zero `setTimeout`/`setInterval` for scheduling; the frontend only polls.
- **Progress is atomic and restart-safe** — the per-item checkpoint transaction is the recovery point; a crash never corrupts position or double-counts.
- **Scraper internals untouched.** `SourceRegistry` / `_dispatch_source` are called as-is.
- **Existing functionality preserved** — manual Quick Search, the outreach "Lead Search Campaign", CRM, follow-ups, sales intelligence, the Research Agent: all unchanged.
- **Provider limits respected** — the automation runs one combination at a time, reusing the scrapers' existing per-source delays/rate-limits and the run-scoped circuit breaker; it adds no request pressure beyond a single Quick Search per combination.

## 11. Out of scope (Phase 2)

- Multiple named campaigns; `campaign_locations` / `campaign_niches` as separate
  first-class entities; add-to-existing / replace-existing import flows;
  per-campaign schedules running concurrently.
- `.xls` (legacy binary) support.
- A pluggable paid search provider implementation (the `LeadSearchService`
  seam exists; only `DiscoveryLeadSearch` ships).
- Streaming/SSE progress (polling is sufficient at this cadence).
- Per-combination "search depth" tuning beyond the single `per_item_target`.

## Provenance

In-chat brainstorm 2026-08-30. Design presented and approved by the user
("yes it looking right, go ahead"). Four scoping questions answered:
search backend = built-in discovery pipeline; first scope = core automation
(single config/queue); limit precision = allow final-item overshoot;
per-item target = as many as the source returns (bounded at 100).
