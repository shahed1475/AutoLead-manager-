# Browser Research Agent — Design Spec

**Status:** Implementing directly against the user's 30-section brief (2026-08-25). No prior spec doc existed for this feature; this document exists to record the specific architectural calls the brief left open, before writing code — same discipline as the Phase 1 spec.

**Note on referenced material:** the brief refers to "Google AI conversation examples" already in the project as behavioral reference material. No such files exist anywhere in the repository (checked). Section 2 of the brief itself gives a complete 10-point behavioral description and a field list, which is sufficient to build from — `research_examples/` below is seeded from that description, not from transcripts that don't exist.

## 1. Scope confirmation

Standalone subsystem: `niche + location + target` in, structured lead records out, via a real iterative browser+LLM loop. Two entry modes, both supported:
- **Standalone discovery+research** (the brief's Test 1-4 shape): agent does its own business discovery via `google_search` and then deep-researches each candidate.
- **Deep-research of Phase-1-supplied candidates**: `seed_businesses` param skips discovery, goes straight to per-business research. (Phase 1 stays the fast layer; this is the deep layer — brief §9.)

Explicitly NOT built (per brief §29): website scoring, AI lead qualification, outreach/messaging, WhatsApp/email sending, CRM changes, Phase 2 work, LinkedIn scraping, anti-bot evasion, fine-tuning.

## 2. Module layout (`backend/research_agent/`)

Matches the brief's suggested layout closely; two adjustments explained below.

```
research_agent/
  __init__.py
  models.py       — ResearchLead, EvidenceItem (own, richer than intelligence/base.py's), Action schemas
  config.py       — budgets/limits, DB-settings-override pattern (matches sales_intelligence_enabled precedent)
  llm.py          — LocalResearchLLM: wraps ai_brain._call_llm_raw/_ollama_cfg (NOT a new client)
  prompts.py       — action-decision prompt + field-extraction prompt (two distinct LLM roles, see §5)
  actions.py      — controlled tool registry: schemas, validation, timeouts
  browser.py      — Playwright controller: launch, navigate, search, click, scroll, extract, screenshot, CAPTCHA/block detection
  extraction.py   — deterministic regex extraction (phone/email, reused from validators.py) + LLM-assisted name/title extraction
  evidence.py     — evidence recording helpers
  validation.py   — field-completeness / confidence checks, decides when a lead is "complete enough"
  planner.py      — geographic expansion (city/state/country/worldwide → bounded city list) + per-lead task queue
  agent.py        — the OBSERVE/DECIDE/ACT/VALIDATE loop, budget enforcement, per-lead failure isolation
```

No `backend/discovery/`-style split needed beyond this — the module count already matches the brief's own suggestion, which fits this subsystem's real complexity.

## 3. Why NOT `intelligence/base.py`'s `EvidenceItem`/`AgentResult`

`intelligence/` is a separate, already-shipped, feature-flagged subsystem (`sales_intelligence_enabled`) with its own orchestrator and DB tables (`company_profiles`, `research_evidence`). This task is explicitly "an independent subsystem." `EvidenceItem` (field_name/source_type/source_url/snippet) is close but lacks `confidence` and a `status` enum (FOUND/NOT_FOUND/SECURE_WEB_FORM/UNCONFIRMED) that brief §14-15 requires. Rather than importing and stretching a type that belongs to a different subsystem's contract, `research_agent/models.py` defines its own `ResearchEvidence` with the same field shape plus the two extra fields — same design pattern, zero coupling. This *is* the "reuse existing provenance/evidence architecture" the brief asks for (§19): same shape, independent implementation, matching Phase 1's own precedent of not routing new work through an unrelated subsystem's tables.

## 4. Database (additive, 3 new tables + optional `leads` integration)

```sql
lead_research_sessions   -- one row per research run (niche, location scope, target, budgets, status, counts)
lead_research_results    -- one row per researched business (the full structured record: city/state/country,
                          -- business_*, management_*, confidence, research_status, FK to session)
lead_research_evidence   -- one row per evidence item (FK to result, field_name, source_type, source_url,
                          -- snippet, confidence, status)
```

`leads`/`campaign_runs`/Phase 1's `lead_discovery_runs`/`lead_sources` — untouched.

**Optional integration, not a dependency:** once a result reaches `COMPLETE` or `PARTIAL` status, the agent can additionally save it into the main `leads` table via **Phase 1's own `db.create_or_merge_lead`** (source=`"BROWSER_RESEARCH_AGENT"`), so researched businesses show up in the existing Leads page/Pipeline like any other discovered lead — reusing Phase 1's merge+dedup+provenance work rather than building a second save path. Controlled by a `save_to_leads: bool = True` parameter so unit tests and isolated runs can skip it.

## 5. Two LLM roles, not one

The brief's flow diagram lists "Research Planner" (decide next action) and "Local LLM extracts structured information" as separate steps — this maps to two distinct, small, fast prompts rather than one large one:

- **Action-decision prompt** (`llm.py::decide_next_action`): compact state summary (business name if known, missing required fields, last action + truncated result, iteration/budget remaining) → one JSON action. Kept small deliberately — a local 8B model's tool-choice reliability degrades fast with prompt size and turn count.
- **Field-extraction prompt** (`llm.py::extract_fields`): given page text (capped) + which fields are still missing, → structured JSON for name/title findings. Phone/email extraction is deterministic (regex, reusing `validators.py`'s cleaners) — never asked of the LLM, matching the codebase's "prefer deterministic logic" rule.

Both use `ai_brain._call_llm_raw`/`_ollama_cfg` — no second LLM client. JSON parsing is a dedicated multi-strategy parser (raw → fenced → brace-span) preserving real types, same reasoning as Phase 1's planner (`ai_brain._extract_json`'s all-string coercion is wrong for typed fields like `confidence`).

**Malformed/failed LLM output never halts the loop.** A rule-based fallback decides the next action deterministically from current state (no business found yet → search; business found, website unopened → open it; website open, required fields still missing → try a targeted decision-maker search; nothing left to try → finish). This is the same "AI enhances, heuristics are the floor" pattern as every other agent in this codebase.

## 6. Controlled tools (brief §7, finalized list)

`google_search(query)`, `open_url(url)`, `extract_page_text()`, `find_links(keyword=None)`, `click(text_or_selector)`, `scroll(direction)`, `go_back()`, `open_new_tab(url)`, `screenshot()`, `save_evidence(field_name, value, source_url, snippet, confidence, status)`, `finish_research()`. Each has an input schema, timeout, and try/except → structured error result (never raises into the agent loop).

**No separate `google_maps_search` tool.** Google Maps' JS-heavy infinite-scroll UI is exactly what `scrapers/google_maps.py` already handles robustly (Selenium, UA rotation, CAPTCHA retry). Rebuilding that as a new Playwright tool would duplicate proven, hard-won complexity for no benefit — the brief's own instruction is "don't rewrite the 9 scraper adapters," and building a parallel Maps tool would be building a *second* one. The agent's own browser does real Google Search discovery (`google.com`, visible Chrome, exactly per §6's described flow); Maps stays the specialist's job. Noted as a scoping call, not an oversight.

## 7. Browser control

Playwright, visible (`headless=False`) by default during development, matching `bing_search.py`'s existing precedent and the brief's explicit requirement. Same launch args / context pattern as `bing_search.py` (UA string, `webdriver` property override — this is basic fingerprint normalization already accepted in this codebase, not CAPTCHA-solving). CAPTCHA/block detection: same string-scan approach already proven in `bing_search.py` (`"captcha"|"verify"|"challenge"` in page text) → record the obstacle as evidence, abandon that path, continue with the next planned action/source. Never attempts to solve/bypass.

## 8. Budgets (brief §8, all settings-backed like every other configurable limit in this codebase)

`max_actions_per_lead`, `max_searches_per_lead`, `max_pages_per_lead`, `max_time_per_lead_seconds`, `max_total_leads`, `max_consecutive_failures`, `max_geographic_units` (new, for §10's hierarchical expansion — caps how many cities a STATE/COUNTRY/WORLDWIDE request expands into, so "500 leads worldwide" doesn't attempt real exhaustive coverage).

## 9. Geographic hierarchy (brief §10)

`planner.py::expand_geography(location, target)`: if `location` parses as a single city (contains a comma, e.g. "Abbeville, USA" or matches no broader-scope keyword), no expansion — one research task. If it's a state/country/"worldwide", ask the LLM for up to `max_geographic_units` representative cities (with a small deterministic fallback list per broad region if the LLM is unavailable/fails); target count is split across them. This is intentionally simple — a real "enumerate all cities" system is out of scope and explicitly warned against.

## 10. Missing-information discipline (brief §12-14)

Every optional field defaults to `null` + a `*_status` enum (`FOUND`/`NOT_FOUND`/`SECURE_WEB_FORM`/`UNCONFIRMED`), never a guess. A phone number found only on a general Contact page is `management_phone = <that number>`, `management_phone_type = "BUSINESS"` — never presented as a personal line. This is enforced in `validation.py`, checked by a dedicated test per brief §23.

## 11. Testing strategy (brief §23-24)

Unit: action validation, LLM JSON parsing (valid/malformed/exception), research-state/missing-field detection, evidence recording, lead-schema validation (including the missing-info discipline above), budget enforcement (hard stop at each limit), failure recovery (one lead's exception doesn't kill the batch) — all with a **mocked Playwright browser and mocked LLM**, no live network/Ollama dependency, matching this repo's existing `tests/intelligence/` convention.

Live: a separate, clearly-marked manual test script (not part of `pytest`/CI-equivalent), run once against real Google + a real local Ollama instance for the brief's Test 1 (small, Abbeville dental clinics). Tests 2-4 (California/USA/Worldwide, 20-500 leads) are **not executed live in this session** — hours-scale runs are outside what a single session can respons­ibly verify; documented as a known limitation, not silently skipped.

## 12. Explicitly deferred (matches brief §29, restated for this doc's own scope list)

Contact technical verification (MX/phone-type — Phase 1's `autolead-lead-intelligence-and-scoring` already flags this as a future `backend/verification/` package), fine-tuning pipeline, autonomous unrestricted browser control, LinkedIn/private-source research, outreach generation from research results (that's the existing Phase 4 `MarketingAgent`'s job once a lead has research attached — out of scope here).
