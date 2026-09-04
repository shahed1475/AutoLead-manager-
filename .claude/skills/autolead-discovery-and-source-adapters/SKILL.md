---
name: autolead-discovery-and-source-adapters
description: Use when working on lead discovery, scraper sources, the Discovery Planner, source adapters/registry, Google Search or Google Maps discovery, Quick Lead Search, or campaign-mode source selection in AutoLead.
---

# AutoLead Discovery & Source Adapters

## Purpose

Governs how leads get discovered: the 9 existing scraper sources, how they're orchestrated today, and the adapter/registry + Discovery Planner design that wraps them without rewriting their internals.

## When to Use

Adding/fixing a scraper source, changing source selection logic, building the Discovery Planner, building Quick Lead Search, or changing dedup behavior.

## When NOT to Use

Website research/enrichment after a lead is already discovered (that's `autolead-lead-intelligence-and-scoring` + `autolead-browser-automation`), or CRM pipeline-stage changes.

## Project Context

**9 sources, all in `backend/scrapers/`, orchestrated by `run_bulk_scrape(campaign, log_callback, stage_callback, pause_callback)`:**

| Source | Technique | Entry point |
|---|---|---|
| Google Maps | Selenium/Chrome, most robust | `google_maps.py::scrape` |
| Google Search | HTTP + BeautifulSoup, fragile vs. Google blocking | `google_search.py::scrape_google_search` |
| Bing Search | Playwright primary, DuckDuckGo HTML fallback | `bing_search.py::scrape_bing_search` |
| Yelp | HTTP + BeautifulSoup, skips gracefully on Cloudflare block | `yelp.py::scrape_yelp` |
| Yellow Pages | HTTP + BeautifulSoup | `yellow_pages.py::scrape_yellow_pages` |
| Hotfrog | HTTP + BeautifulSoup | `hotfrog.py::scrape_hotfrog` |
| Foursquare | HTTP, parses Next.js JSON blob | `foursquare.py::scrape_foursquare` |
| Top List | HTTP, multi-hop (search → parse article → re-search) | `top_list.py::scrape_top_list` |
| Generic Directory | HTTP, CSS-selector configs for 6 sites | `generic_directory.py::scrape_generic_directory` |

Plus `email_finder.py::find_emails_from_website` — deep contact-discovery pass run after scraping.

Every non-Maps source falls back to Google Maps on failure/empty results (`_run_with_maps_fallback`), always logged. Dedup on the **legacy** Campaign-mode scraping path has two layers, both **discard on collision**: in-batch inside `scrapers/__init__.py` (`_norm_name`/`_is_fuzzy_dup` — email exact, phone last-8-digits, fuzzy name+city via `difflib` ≥0.85), then the DB-level `find_duplicate_lead` that `create_lead_deduped`/`create_lead_deduped_with_log` calls (exact `LOWER(email)`, exact `phone`, normalized `website`, **exact** `LOWER(name)+LOWER(city)` — no fuzzy match at this layer). DB-level backstop: unique indexes on `LOWER(email)` and `phone`. The discovery path's `create_or_merge_lead` adds the genuine fuzzy name+city pass and merges instead of discarding — see below.

`backend/scraper.py` is a legacy parallel orchestrator (`scrape_multi_source`) — not called by the current campaign router. Don't confuse it with `scrapers/__init__.py`.

## Discovery Planner + Adapters — NOW BUILT (was "design locked" in the 2026-08-25 spec)

`backend/discovery/` exists in the working tree (uncommitted as of 2026-08-30), tested in `tests/discovery/` (~5 files). It implements the previously-spec'd design almost exactly — read the code, not just the spec, before changing it:

| Module | What it is |
|---|---|
| `discovery/planner.py` | `DiscoveryPlanner().plan(query, niche, city, country, mode) -> DiscoveryPlan`. Rule-based first (`_classify_rule_based` via `industry_keywords.py`, word-boundary match not substring), LLM fallback (`_classify_via_llm`) only when nothing matches. Own `_parse_planner_json` (NOT `ai_brain._extract_json` — that str-coerces `confidence`). Never raises: any LLM failure → `classification_method="llm_fallback"`, `intent=STARTUP_TECH_COMPANY`, `sources=["GOOGLE_SEARCH","GOOGLE_MAPS"]`, `confidence=0.0`. Variant generation is **niche-phrasing only** (`_rule_based_variants` — base + singular/plural), never city/region expansion, never a fabricated fact. Caps are settings-backed (`discovery_quick_max_sources`=2, `discovery_quick_max_variants`=2, `discovery_campaign_max_variants`=4). |
| `discovery/industry_keywords.py` | `LOCAL_BUSINESS_KEYWORDS` (extends `lead_scorer.HIGH_VALUE_NICHES`) + `STARTUP_TECH_KEYWORDS`. Small, extendable — not a taxonomy. |
| `discovery/adapters.py` | `SourceAdapter` + `SourceRegistry` (fresh per run via `get_registry()`, no cross-run health store). Every `execute()` delegates to `scrapers._dispatch_source` — the **same** function `run_bulk_scrape` already uses — so per-source signatures, Maps-fallback and lead-tagging are never reimplemented. Run-scoped circuit breaker: 2 consecutive failures → `health_state="UNHEALTHY"`, `enabled=False`. |
| `discovery/merge_dedup.py` | `basic_validate` (mirrors `scrapers._validate_and_clean`: reject only on missing name, or no email/phone/website after clearing bad-format fields) + `merge_and_save` — the batch orchestration layer that calls `database.create_or_merge_lead` per candidate. |
| `discovery/quick_search.py` | `run_quick_search(payload)` — the **JobQueue** handler (`"QUICK_SEARCH"`, enqueued by `routers/discovery.py`). Planner → 1-2 recommended sources (falls to the next recommended source, not always Maps, if a source returns `< max(3, target//4)`) → `merge_and_save` → always terminal status (`COMPLETED`/`FAILED`/`CANCELLED`, never stuck `RUNNING`). Deliberately does NOT run research/scoring/messaging. |

**Merge-not-discard dedup — NOW BUILT for the discovery path only:** `database.create_or_merge_lead(data, source, source_identifier, run_id) -> (lead_id, is_new, merge_reason)`. Uses `find_duplicate_lead_fuzzy` (exact email/phone/website/name+city, then genuinely fuzzy `SequenceMatcher ≥ 0.85` name+city over that city's leads). On collision: **existing non-null values always win**, only missing fields are filled, and a `lead_sources` provenance row is **always** inserted (even when the lead already existed). Race-safe: an `IntegrityError` from a concurrent Quick Search worker re-resolves and falls through to merge (only email/phone have DB unique indexes, so website-only/fuzzy races remain a known narrow gap). `create_lead_deduped*` and the live Campaign scraping save path are **unchanged**.

**Campaign-mode integration (`routers/campaigns.py::_run_campaign_task`) — one scoped change:** the planner is called with `mode="CAMPAIGN"`; its `query_variants` replace the single fixed niche by **looping `run_bulk_scrape` once per variant** (there is no multi-query param), bounded by both the variant cap and `max(1, daily_cap // 5)` so a small cap never splits into unusable per-variant budgets. Manual source selection is unchanged. A planner failure logs and falls back to the original niche only. A `lead_discovery_runs` bookkeeping row (`mode="CAMPAIGN"`, `campaign_run_id` FK) is written but a DB error there is non-fatal. Campaign-mode scraping still does **not** populate `lead_sources` (Phase 1 scope).

**New tables:** `lead_discovery_runs`, `lead_sources` — see `autolead-database-and-migrations`.

## Rules

1. **Never rewrite a working scraper's internals to fit an adapter interface.** Wrap, don't rewrite — this was an explicit, deliberate decision to minimize regression risk on sources that already work.
2. **A source failure must never fail the whole discovery run.** Isolate per-source try/except, log via the existing `_log_source_event`/`campaign_log` pattern, continue with remaining sources.
3. **Dedup must merge and preserve provenance, not discard.** The discovery path already does this — `database.create_or_merge_lead` keeps existing non-null fields, fills gaps from the new candidate, and always records a `lead_sources` row. New discovery code saves through it, never through the legacy discard-on-collision `create_lead_deduped`.
4. **The Planner must never require the user to manually pick sources for Quick Search.** Campaign mode may still offer manual selection.
5. **LLM classification is a fallback, not the primary path** — keep the rule-based keyword classifier as the fast path for the common cases; only call the LLM when nothing matches.
6. **Never build a new scraper from scratch without first checking whether one of the 9 already covers it** or could be extended (e.g., adding a `SiteConfig` entry to `generic_directory.py` rather than a whole new module).

## Architecture Guidance

`Discovery Planner → SourceAdapter Registry → (existing scraper functions, unchanged) → Normalize → Merge-Dedup → Basic Validation → Results`. The Planner and any orchestrator (Quick Search, Campaign) talk to the registry, never to raw scraper functions directly, so source selection/health/rate-limiting stays centralized.

## Implementation Guidance

- The Planner, adapters, merge-dedup and Quick Search **already exist** in `backend/discovery/` — extend them in place; don't rebuild. Read the module before changing it (see the table above).
- LLM fallback goes through `ai_brain._call_llm_raw`; its JSON is parsed by `planner._parse_planner_json` (not `ai_brain._extract_json` — that would str-coerce `confidence`). See `autolead-ai-llm-engineering`.
- Adapters delegate to `scrapers._dispatch_source` — never reimplement a scraper's query/parse logic in the wrapper.
- Campaign target-splitting precedent: `_SOURCE_WEIGHTS` in `scrapers/__init__.py`, and the per-variant `daily_cap // 5` floor already in `routers/campaigns.py`.
- Both Quick Search and the Browser Research Agent run on the shared `JobQueue` (`queue_worker.py`) — use it, don't invent a new background mechanism. See `autolead-reliability-and-background-jobs`.
- Deeper per-business research (following contact/team pages, extracting management contacts) is a **different subsystem** — `backend/research_agent/`, see `autolead-browser-research-agent`. Quick Search deliberately stops at discovery + dedup.

## Testing Requirements

Existing coverage: `tests/discovery/` (`test_planner.py`, `test_adapters.py`, `test_merge_dedup.py`, `test_quick_search.py`, `test_discovery_router.py`) and `tests/test_campaign_discovery_integration.py`. Extend these, don't parallel them.

- Planner: keyword-match cases (word-boundary, not substring), LLM-fallback case (mocked), LLM-failure-degrades-to-general-search case, variant cap respected.
- Adapter wrapping: mock the underlying scraper function; verify health/circuit state updates and that a raised exception from the scraper doesn't propagate past the adapter.
- Merge-dedup: two candidates for the same business from different sources → one lead row, both `lead_sources` rows, non-null fields from both preserved; a concurrent-insert race falls through to merge, not a crash.
- Adapter/scraper tests mock at the scraper-function boundary — no real network/browser in the suite.

## Security Considerations

Respect each source's access patterns as already implemented (rate-limit delays via `scraper_delay_min`/`scraper_delay_max`, UA rotation, graceful skip on Cloudflare/CAPTCHA per `autolead-browser-automation`) — the Planner must not increase request pressure on any source beyond what the existing scraper already does.

## Performance Considerations

Quick Search must stay fast — 1-2 sources only, per the locked design decision. Don't let the Planner's LLM fallback add latency to the common case; only invoke it when the keyword classifier genuinely can't decide.

## Failure Modes

| Mistake | Fix |
|---|---|
| Adapter wrapper reimplements a scraper's query logic instead of delegating | Delegate to the existing `scrape(...)` function unchanged |
| One source's exception kills the whole `asyncio.gather` | Wrap each source call in its own try/except inside the registry, never let one propagate to fail the batch |
| Dedup silently drops the second-found source's data | Save via `create_or_merge_lead` — merges fields + always writes a `lead_sources` row |
| Quick Search runs all 9 sources "to be safe" | `discovery_quick_max_sources` (default 2) caps it — planner-selected, falls to the next recommended source only on insufficient results |
| New discovery code calls `create_lead_deduped` | Wrong path for discovery — use `create_or_merge_lead` (the legacy one discards duplicates' extra fields) |

## Verification Checklist

- [ ] Existing scraper internals unmodified (diff should show new wrapper files, not edits inside `scrapers/*.py` beyond a minimal compatibility shim if truly unavoidable)
- [ ] A forced single-source failure doesn't fail a multi-source test run
- [ ] Dedup test shows merged fields + provenance, not data loss
- [ ] Quick Search test confirms only 1-2 sources were invoked
- [ ] `pytest -q` clean, including no regression in existing campaign-flow tests

## Related Skills

`autolead-lead-generation-architecture`, `autolead-browser-research-agent`, `autolead-browser-automation`, `autolead-ai-llm-engineering`, `autolead-database-and-migrations`, `autolead-reliability-and-background-jobs`

---
Version: 1.1
Scope: AutoLead-manager
Last reviewed: 2026-08-30 (Discovery Planner / adapters / merge-dedup now implemented, not just spec'd)
