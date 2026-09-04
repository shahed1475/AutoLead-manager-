# Phase 1: Universal Lead Discovery — Design Spec

**Status:** Approved for implementation (reconciled from an in-chat design presented 2026-08-25, confirmed via 4 rounds of scoped Q&A, and finalized/extended by explicit implementation instructions in the same session — see "Provenance of this document" at the end).

## 1. Objective

Introduce a universal discovery layer (Discovery Planner → Source Adapter Registry → Normalize → Merge/Dedup → Basic Validation → Results) in front of AutoLead's existing 9 scraper sources, without rewriting any scraper's internals, and without changing existing Campaign/Phase-4 behavior beyond one scoped integration point.

Two entry points:
- **Quick Search** — fast, one-time, 1-2 Planner-selected sources, no research/scoring/messaging.
- **Lead Search Campaign** (existing Campaign page, relabeled) — Planner-generated adaptive query variants replace the current single fixed query; manual multi-source selection and the existing stage machine are unchanged.

## 2. Architecture

```
Quick Search          Campaign Search
     |                       |
     v                       v
        Discovery Planner
   (rule-based intent classify,
    LLM fallback if ambiguous,
    bounded query-variant generation)
                |
                v
      SourceAdapter Registry
   (thin wrapper over the 9 existing
    scrapers — delegates to the same
    scrapers._dispatch_source used by
    run_bulk_scrape today)
                |
                v
           Normalize
                |
                v
       Merge + Deduplicate
      (preserve provenance)
                |
                v
        Basic Validation
                |
                v
           Lead Results
```

## 3. Discovery Planner (`backend/discovery/planner.py`)

`DiscoveryPlanner.plan(query, niche, city, country, mode) -> DiscoveryPlan`

Returns: `intent` (`LOCAL_BUSINESS` | `STARTUP_TECH_COMPANY` | `AMBIGUOUS`), `confidence` (0-1), `recommended_sources` (ordered list, most-preferred first), `query_variants` (bounded list of niche-phrasing strings), `mode`.

**Classification — hybrid, rule-based first:**
- `backend/discovery/industry_keywords.py` holds two keyword groups, `LOCAL_BUSINESS_KEYWORDS` and `STARTUP_TECH_KEYWORDS`, seeded from and kept consistent with `lead_scorer.HIGH_VALUE_NICHES` (dental, real estate, law firm, accounting, medical, cosmetic, restaurant chain, gym, hotel, school) plus the additional examples given in the implementation brief (dentist, restaurant, salon, clinic, contractor, medical practice / startup, SaaS, software, AI, technology, tech, B2B, developer, platform). Small, extendable lists — not exhaustive taxonomies.
- A query matching a `LOCAL_BUSINESS` keyword (substring match against niche+query, case-insensitive) → `intent=LOCAL_BUSINESS`, `recommended_sources=["GOOGLE_MAPS", "YELLOW_PAGES"]`.
- A query matching a `STARTUP_TECH_COMPANY` keyword → `intent=STARTUP_TECH_COMPANY`, `recommended_sources=["GOOGLE_SEARCH", "BING_SEARCH"]`.
- No match on either list → LLM fallback.

**LLM fallback (ambiguous only):** one call via `ai_brain._call_llm_raw`/`_ollama_cfg` (no second LLM client), prompted for strict JSON `{intent, confidence, recommended_sources, query_variants}`. Response is parsed with a dedicated lightweight parser (`_parse_planner_json`: raw `json.loads` → fenced-block extraction → brace-span regex — deliberately *not* `ai_brain._extract_json`/`_map_keys`, which coerce every field to a string and would corrupt `confidence`'s float and the two list fields; still routes the actual model call through `_call_llm_raw` only). Malformed/unparseable output, or any exception (timeout, connection error, wrong model), never raises — falls back to `intent=STARTUP_TECH_COMPANY` (general), `recommended_sources=["GOOGLE_SEARCH"]`, `query_variants=[the literal niche/query]`, `confidence=0.0`. The planner is called once per search; it is never re-invoked mid-run when a source fails — failure handling at that point is the orchestrator/registry's job (§6).

**Query variants — bounded, never fabricated:** Quick Search: 1-2 variants. Campaign: default max 4. Both limits are settings-backed (`config.py` + `app_settings` override), not hardcoded. Variants are niche-phrasing variations (e.g. "dental clinic" → "dental clinics", "dentist office") for both modes — **not** city/region expansion. Rationale: Campaign mode's form already has separate, explicit `city`/`country` fields (a real, specific city, not a state), so expanding into multiple cities would silently change what the user typed into a dedicated field; Quick Search's free-text "Location" field could be a broader region, but scope for Phase 1 keeps variant generation to niche-phrasing only in both modes for a consistent, low-risk first version. Multi-location expansion is an explicit Phase 2 candidate (§9).

## 4. Source Adapters & Registry (`backend/discovery/adapters.py`)

**Lightweight wrapper, confirmed against source:** `backend/scrapers/__init__.py::_dispatch_source(source, niche, city, country, budget, cfg, log_fn)` is already the single, correct per-source dispatch function used by `run_bulk_scrape` — it already knows each of the 9 sources' exact call signature, already never raises (catches internally, logs, returns `[]`), and already carries source-tagging + a built-in Maps-fallback for non-Maps sources. `SourceAdapter.execute()` delegates to this function directly rather than re-implementing per-source signature knowledge (which would duplicate exactly what `_dispatch_source` already does correctly, and would drift out of sync if a scraper's signature changes).

`SourceAdapter` fields: `name`, `source_type` (`BROWSER` for GOOGLE_MAPS/BING_SEARCH, `HTTP` for the other 7), `priority` (int, lower = preferred, seeded from `_SOURCE_WEIGHTS` order), `enabled` (bool), `rate_limit`/`timeout` (carried for metadata/future use — actual throttling stays inside each scraper's existing `scraper_delay_min/max`, not duplicated here), `health_state` (`HEALTHY`/`DEGRADED`/`UNHEALTHY`, run-scoped only, reset per registry instance), `metrics` (calls, zero-result count, exception count, last latency).

`SourceRegistry`: constructed fresh per discovery run (no persistent cross-run health store — explicitly out of scope for Phase 1 per the brief). `get(name)`, `list_enabled()`, `execute(name, niche, city, country, budget, cfg, log_fn)` (wraps the adapter call in its own try/except as defense-in-depth even though `_dispatch_source` shouldn't raise; updates health/metrics from the result: an exception or a zero-result response both count toward `mark_unhealthy` after 2 consecutive occurrences within the run — a **temporary, run-scoped circuit breaker**, not a persistent one). Registers all 9 sources by the same string keys `_SOURCE_WEIGHTS` already uses (`GOOGLE_MAPS`, `GOOGLE_SEARCH`, `YELP`, `YELLOW_PAGES`, `BING_SEARCH`, `HOTFROG`, `FOURSQUARE`, `TOP_LIST`, `GENERIC_DIR`).

## 5. Normalization

Reuses existing normalization helpers (`validators.clean_business_name/clean_email/clean_phone/normalize_website`) — the same ones `database.py::_normalize_lead_fields` already applies. No new normalization logic is written; the discovery layer calls the same functions the rest of the app already uses for this, so a lead normalized via Quick Search and one normalized via Campaign scraping are byte-for-byte comparable.

## 6. Merge + Deduplication (`backend/discovery/merge_dedup.py`)

New function `db.create_or_merge_lead(data, source, source_identifier, run_id) -> (lead_id, is_new, merge_reason)` — additive, does **not** modify `create_lead_deduped`/`create_lead_deduped_with_log` (the functions the live Campaign scraping path uses today; those stay exactly as they are, so Campaign-mode lead saving is unaffected by this work beyond §8's query-variant looping).

Uses the existing three-signal match (`db.find_duplicate_lead`: email exact → phone exact → fuzzy name+city, already live). On match: **existing lead's non-null fields win**; null/missing fields on the existing row are filled from the new candidate; a `lead_sources` provenance row is always inserted (even on merge — every source that ever found this lead gets a row); `merge_reason` is one of `"email"`, `"phone"`, `"name_city"` recorded for observability, `None` when it's a brand-new lead. Deterministic: given the same two candidate dicts, the merge always produces the same result (field-by-field null-coalesce, no ordering-dependent behavior beyond "existing row wins ties").

**Scope note:** this new merge-aware path is used by Quick Search only in Phase 1. Campaign-mode scraping keeps using the existing `create_lead_deduped_with_log` (discard-on-duplicate, no provenance row) — extending Campaign's save path to merge+provenance is explicitly deferred (§9), to keep this phase's change to the protected Campaign engine limited to query-variant generation only.

## 7. Source Provenance (`lead_sources` table)

```sql
CREATE TABLE IF NOT EXISTS lead_sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id         INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    source          TEXT NOT NULL,
    source_identifier TEXT,
    run_id          INTEGER REFERENCES lead_discovery_runs(id) ON DELETE SET NULL,
    discovered_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    raw_snapshot    TEXT
);
```

**Addition beyond the literal 5-field list given in the brief:** `run_id`, nullable, FK to `lead_discovery_runs`. Justification: without it there is no way to answer "which leads did discovery run #N find" for the results endpoint (§8) — `lead_sources` is the only place that records the source→lead link, so it's the natural home for the run link too, and it's still pure provenance data (which run's search produced this discovery), not a scope change.

`raw_snapshot` is capped at **2000 characters** (a serialized JSON excerpt of the raw scraper dict, truncated) — enough for debugging/audit, not a full page dump. No credentials or secrets are ever present in scraper output, so no redaction step is needed here beyond the size cap itself.

`leads.source` (existing column) is left completely untouched — first/primary source, unchanged meaning, unchanged callers.

## 8. `lead_discovery_runs` table

```sql
CREATE TABLE IF NOT EXISTS lead_discovery_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    mode                TEXT NOT NULL,               -- QUICK | CAMPAIGN
    raw_query           TEXT,
    niche               TEXT,
    city                TEXT,
    country             TEXT,
    target_count        INTEGER,
    planner_intent      TEXT,
    planner_confidence  REAL,
    sources_planned     TEXT,                         -- JSON list
    status              TEXT DEFAULT 'QUEUED',         -- QUEUED|RUNNING|COMPLETED|FAILED|CANCELLED
    raw_candidates      INTEGER DEFAULT 0,
    deduplicated_count  INTEGER DEFAULT 0,
    results_count       INTEGER DEFAULT 0,
    campaign_run_id     INTEGER REFERENCES campaign_runs(id) ON DELETE SET NULL,
    error_message       TEXT,
    started_at          TIMESTAMP,
    finished_at         TIMESTAMP,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

`campaign_run_id` (addition beyond the literal field list, same justification pattern as §7): Campaign-mode discovery runs link to the existing `campaign_runs` row per the brief's own instruction ("Campaign mode should link to the existing campaign run") — this is the FK that implements that link without adding anything to `campaign_runs` itself. `error_message` captures why a run is `FAILED`, needed for any real status endpoint to be useful. Both new tables are added directly to `database.py::_SCHEMA_SQL` as `CREATE TABLE IF NOT EXISTS` — safe/idempotent for both fresh and existing installs via the existing `executescript` migration runner; no separate migration function needed since these are whole new tables, not new columns on an existing one.

## 9. Quick Search (`backend/discovery/quick_search.py`)

`run_quick_search(run_id)` — the `JobQueue` handler, enqueued via `get_queue().enqueue("QUICK_SEARCH", {"run_id": run_id}, run_quick_search)` from the API layer (§10). Steps exactly per the brief: create-run happens in the router before enqueueing; the handler then sets `status=RUNNING`, asks the Planner, executes the top 1-2 recommended sources via the Registry **with a fallback**: if the first-choice source returns fewer than `max(3, target_count // 4)` results, try the next Planner-recommended source (not just Maps — the Planner's own ordered list, so a failed Google-Search attempt for a tech-startup query tries Bing next, not an unrelated Maps fallback); normalizes; merges/dedupes via `db.create_or_merge_lead`; basic-validates (reuses `validators.is_valid_email`/`is_valid_phone`, same as `scrapers._validate_and_clean`'s logic, not a new validation scheme); updates the run row's counters throughout; sets `status=COMPLETED` (or `FAILED` with `error_message` on an unhandled exception — still leaves the run row in a terminal, queryable state rather than stuck `RUNNING`). Explicitly does **not** run website research, AI qualification, scoring, or messaging — those are later phases.

## 10. API (`backend/routers/discovery.py`, prefix `/api/discovery`, existing auth dependency)

- `POST /api/discovery/search` — body `{query, niche, city, country, target_count}` (mode is always `QUICK` for this endpoint). Creates the `lead_discovery_runs` row (`status=QUEUED`), enqueues the job, returns `{run_id, status}` immediately (does not block on the search completing).
- `GET /api/discovery/search/{run_id}` — returns the run row (status, counts, planner intent/confidence) for polling.
- `GET /api/discovery/search/{run_id}/results` — returns the leads found by that run (via `lead_sources.run_id`), each shaped like the existing `leads` list response so the frontend can reuse `ScoreBadge` etc. directly.

This is a new, separate surface from the existing legacy `POST /api/scraper/search`/`GET /api/scraper/status` (`routers/scraper_router.py`) — a single-global-state, Google-Maps-only, fire-and-forget endpoint still used by `Dashboard.jsx`. That endpoint is unrelated pre-existing functionality and is not touched by this work.

## 11. Campaign Integration (`routers/campaigns.py::_run_campaign_task`)

**Confirmed constraint:** `scrapers.run_bulk_scrape(campaign, ...)` accepts exactly one `niche`/`city` pair per call — there is no multi-query parameter. "Adaptive query variants replacing a single fixed query" therefore means: call the Planner once at the start of `_run_campaign_task` (mode=`CAMPAIGN`) to get up to `discovery_campaign_max_variants` niche-phrasing variants (§3), then loop `run_bulk_scrape` once per variant — bounded additionally by budget (`effective_variants = min(planner_variants, max(1, daily_cap // 5))`, so a small `daily_cap` doesn't split into unusably small per-variant budgets), splitting `daily_cap` across the loop, merging each call's returned lead list into the existing `all_leads_map` (keyed by `id` — the current code at lines 186-191 already merges by id for the existing-PENDING-leads case, so cross-variant duplicate leads collapse into that same map with zero new dedup code needed). `stage_callback`/`pause_callback` are passed to every iteration (idempotent — re-setting the same stage string is harmless, and `_wait_while_paused` is safe to call repeatedly). A `lead_discovery_runs` row is created before the loop (`mode=CAMPAIGN`, `campaign_run_id` = the existing `campaign_runs.id`) and updated after, purely for audit/observability — it does not replace or alter `campaign_runs`' own status/stage columns. Everything downstream of the scraping section (enriching/scoring/writing/sending, lines ~203 onward) is completely untouched. Manual source selection (the `sources` list from the campaign form) is unchanged and still passed through as-is.

## 12. Frontend

- New page `frontend/src/pages/LeadSearch.jsx`, route `/lead-search`, new Sidebar nav entry. Form: "What are you looking for?" / "Location" / "Number of leads" / Search button. Loading (poll `GET /api/discovery/search/{run_id}` every ~1.5s while `QUEUED`/`RUNNING`), progress (planner intent + source being tried, from the run row), empty (`EmptyState`), error (`ErrorState`, with retry), and results (a purpose-built lightweight results table reusing `ScoreBadge` + `SkeletonTableRows` visual patterns — **not** a literal reuse of `LeadTable.jsx`, which is tightly coupled to the Leads page's CRM mutation actions — delete/skip/resend/mark-replied — that don't apply to freshly-discovered, not-yet-actioned Quick Search results; reusing it as-is would either drag in irrelevant buttons or require gutting most of its props, so a smaller dedicated component matching the same visual language is the safer, more honest interpretation of "reuse wherever appropriate").
- `Campaign.jsx`: the sidebar label and page header text change to "Lead Search Campaign". No layout/behavior change otherwise, per the brief.

## 13. Settings added (`config.py`, DB-override pattern like every other module)

- `discovery_quick_max_sources` (int, default 2)
- `discovery_quick_max_variants` (int, default 2)
- `discovery_campaign_max_variants` (int, default 4)

## 14. Explicitly deferred to Phase 2

- Multi-location query-variant expansion (state → cities) for either mode.
- Merge+provenance for Campaign-mode scraping (Campaign keeps `create_lead_deduped_with_log`'s discard-on-duplicate behavior for now).
- Persistent (cross-run) source health tracking — Phase 1 is run-scoped only.
- Website research, contact verification, AI qualification, scoring, opportunity mapping, messaging, and export for Quick-Search-discovered leads — all later-phase scope per the original spec's own phasing.
- Automatic source selection for Campaign mode (manual selection stays, per the brief).

## Provenance of this document

Presented in chat 2026-08-25 across 4 rounds of scoped questions (quick-search source count, intent-classification strategy, adapter-refactor depth — all answered and locked in) but never written to disk before the conversation moved to an unrelated task. This document reconstructs that design and reconciles it with a subsequent, more detailed implementation brief from the same user in the same project, which is consistent with and extends every earlier decision (it did not contradict any of them) — see the two "addition beyond the literal field list" notes in §7-8 and the run_bulk_scrape single-query-per-call constraint in §11 for the specific places this document had to make an explicit call the brief didn't fully specify.
