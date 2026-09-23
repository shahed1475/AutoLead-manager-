---
name: autolead-browser-research-agent
description: Use when working on AutoLead's Browser Research Agent — backend/research_agent/, the OBSERVE/DECIDE/ACT/VALIDATE loop, its Playwright controller, the controlled action registry, geographic expansion, its evidence/status model, per-lead failure isolation, research budgets, or the /api/research-agent endpoints.
---

# AutoLead Browser Research Agent

## Purpose

Maps the `backend/research_agent/` subsystem: the iterative browser+LLM loop that takes `niche + location + target_count` and produces structured, evidence-backed lead records. It is an **independent subsystem** — same evidence/provenance *pattern* as `backend/intelligence/`, deliberately zero coupling to it.

## When to Use

Changing anything in `backend/research_agent/`, `backend/routers/research_agent.py`, the `lead_research_*` tables, `tests/research_agent/`, or the `ResearchAgent.jsx` page's backend contract.

## When NOT to Use

Quick Search / Campaign discovery (`autolead-discovery-and-source-adapters`), the `sales_intelligence_enabled` agents in `backend/intelligence/` (separate subsystem, separate skill), or anything past the `save_to_leads` handoff (that's `autolead-outreach-safety`).

## Project Context

New in the working tree (uncommitted as of 2026-08-30), spec: `docs/superpowers/specs/2026-08-25-browser-research-agent-design.md`. Tested in `tests/research_agent/` (10 files, mock at the browser + LLM boundary; `_fakes.py` has the fakes).

**Module map (`backend/research_agent/`):**

| Module | Owns |
|---|---|
| `models.py` | `ResearchLead`, `ResearchEvidence`, `AgentAction` dataclasses. Field-status vocab `VALID_FIELD_STATUSES` = `FOUND / NOT_FOUND / SECURE_WEB_FORM / UNCONFIRMED / VERIFIED_BY_SOURCE`. Lead `research_status` = `PENDING / IN_PROGRESS / COMPLETE / PARTIAL / FAILED`. `REQUIRED_FIELDS` = business_name, business_phone, business_website. `management_titles_for_niche()`. `VALID_ACTIONS` (11 names). |
| `config.py` | `get_research_config()` — `app_settings` DB override > `.env`/pydantic > `_DEFAULTS`, same layering as `_scraper_cfg`/`_ollama_cfg`. All budgets live here (`max_actions_per_lead`=12, `max_searches_per_lead`=4, `max_pages_per_lead`=5, `max_time_per_lead_seconds`=180, `max_total_leads`=20, `max_consecutive_failures`=3, `max_geographic_units`=5, `headless`=False, `save_to_leads`=True). |
| `llm.py` | `LocalResearchLLM` — two roles: `decide_next_action(state_summary)` and `extract_fields(text, missing, business_name)`. Wraps `ai_brain._call_llm_raw`/`_ollama_cfg`. Own `_parse_json_object` — **not** `ai_brain._extract_json`/`_map_keys` (those `str()`-coerce every field, corrupting `confidence: float` and `params: dict`). `decide_next_action` **never raises**: returns `AgentAction(action="_llm_failed", …)`. `extract_fields` returns `{}` on any failure. |
| `prompts.py` | The two prompts. Compact — a local 8B's tool-choice reliability drops fast with prompt size, so the decision prompt gets a state *summary*, never a page dump. |
| `actions.py` | Controlled tool registry. `ActionResult`, `validate_action()` (raises `ActionValidationError` with a specific reason), `to_llm_dict(max_chars=2000)` (trimmed view to the LLM; Python keeps the full data). |
| `browser.py` | `BrowserController` — one per session, async context manager. Playwright Chromium, launch pattern mirrors `scrapers/bing_search.py` (`_resolve_headless` reused). Every method returns `ActionResult` and **never raises into the loop**. `_check_blocked` scans `_BLOCK_MARKERS` → records the obstacle, returns `status="blocked"`, agent moves on. Actions: `google_search`, `open_url`, `extract_page_text` (20k-char cap), `find_links`, `click`, `scroll`, `go_back`, `open_new_tab`, `screenshot`. **`screenshot` is only half-wired** — the method exists and is registered/validated, but `agent._process_result` has no branch for it and nothing persists its output, so as an LLM-chosen action it's currently dead weight. A deterministic visual-capture feature would finish/redirect it, not build on it as-is. |
| `extraction.py` | Deterministic first: `extract_emails`/`extract_phones` reuse `scrapers/email_finder`'s regexes + `validators.clean_*`/`is_valid_*`. `find_role_sentences` (regex pre-filter before any LLM call). `has_secure_contact_form`, `is_relevant_nav_link`. |
| `evidence.py` | `record_finding(lead, field, value, source_type, source_url, snippet, confidence, status)` — sets the field **only if** no higher/equal-confidence evidence already backs it (first good source wins), always appends a `ResearchEvidence` row. `record_not_found()` — explicit "looked, not there". No code path may set a contact field without going through here. |
| `validation.py` | `is_research_sufficient` (required fields present = done; optional never block). `finalize_status` (COMPLETE/PARTIAL/FAILED + a confidence score). `validate_no_fabrication` (every non-null contact field needs a `FOUND` evidence row — returns violations). `management_phone_type_is_safe` (a `management_phone` with no `management_phone_type` could be mistaken for a personal line). |
| `planner.py` | `expand_geography(location, target_count, max_units=5)` — a single city → one `GeoTask`; a US state / country / "worldwide" → a **bounded** list of real, well-known cities (LLM-proposed with "do not invent", small fixed `_FALLBACK_CITIES` on failure). A comma is *not* the single-vs-broad signal — `_KNOWN_BROAD_REGIONS` is. |
| `agent.py` | `research_business()` = the per-lead OBSERVE→DECIDE→ACT→VALIDATE loop. `run_research_session()` = session level: geo expansion → discovery (`google_search`, skipping `_DIRECTORY_DOMAINS`) *or* `seed_businesses` → research each candidate. **Per-lead failure isolation**: one candidate's exception → `failed_count++`, never aborts the batch. One `BrowserController` for the whole session. |
| `session.py` | `run_research_session_persisted()` — the JobQueue handler body. Never raises out (unhandled → `status=FAILED` persisted). `on_lead_complete` optionally calls `db.create_or_merge_lead(source="BROWSER_RESEARCH_AGENT")` (**Phase 1's merge/dedup/provenance, reused**) then `db.save_research_result` (atomic result + evidence). |

**API (`routers/research_agent.py`, prefix `/api/research-agent`, `_authed`):** `POST /start` (`@limiter 5/minute`, `ResearchAgentStartRequest`, `target_count ≤ 500`) → creates session, enqueues on the **existing `JobQueue`** (`"RESEARCH_AGENT"`), 503 if queue unavailable/full. `GET /{id}`, `GET /{id}/results` (batched evidence), `POST /{id}/cancel` (cooperative — the loop polls `is_cancelled`).

**DB (`database.py`, 3 additive tables):** `lead_research_sessions`, `lead_research_results` (FK `session_id` CASCADE, optional `lead_id` SET NULL), `lead_research_evidence` (FK `result_id` CASCADE). `save_research_result` is atomic. `leads` / `campaign_runs` / `lead_discovery_runs` / `lead_sources` are **untouched**.

**Explicitly NOT in this subsystem (spec §29):** website scoring, AI lead qualification, any outreach/message generation, WhatsApp/email sending, CRM stage changes, LinkedIn scraping, anti-bot evasion, model fine-tuning.

## Decision makers (multiple per lead, custom titles — added 2026-09-23)

- `ResearchLead.decision_makers: List[DecisionMaker]` — every named person + role found, capped by `research_agent_max_decision_makers` (default 5). Persisted in `lead_research_decision_makers` (FK `result_id` CASCADE), returned as `decision_makers` on `GET /{id}/results`, flattened into the CSV's `decision_makers` column.
- Target titles: `POST /start` (and the leads `/research` handoff) accept `target_titles`; stored as JSON on `lead_research_sessions.target_titles` so resume/reconcile keep them. `models.resolve_target_titles` = sanitised custom list, else `management_titles_for_niche`. `GET /titles?niche=` feeds the UI's title picker.
- Extraction: `extraction.find_role_sentences(extra_titles=...)` → `llm.extract_decision_makers` → **kept only if the name and every title word appear in the page text** (`agent._extract_decision_makers`). Then `evidence.record_decision_maker` (dedups by name-token subset, so "Jonathan" + "Jonathan Windham" is one person) — never append to `decision_makers` directly.
- The **primary** (highest-priority matched title, earliest wins ties) is mirrored into `management_contact_name/title` via `record_finding`, so existing readers are unchanged. `validate_no_fabrication` also checks every decision maker has a `decision_maker` FOUND evidence row.
- Known limit: a person listed on the site is kept even when the listing is a joke (e.g. a pets page with "Director of Rodent Relations") — the source URL is shown so a human can judge.

## The loop (what "research a business" means)

```
seed hint (name/website/city)  ──►  OBSERVE  (_build_state_summary: known vs missing fields, budget left)
                                       │
                                       ▼
                                     DECIDE  (llm.decide_next_action → one AgentAction;
                                       │      on "_llm_failed"/invalid → _fallback_action,
                                       │      a deterministic backup planner that guarantees progress)
                                       ▼
                                      ACT   (browser.execute → ActionResult;
                                       │     save_evidence is bookkeeping, NEVER routed to the browser)
                                       ▼
                                   VALIDATE (deterministic extraction on results;
                                       │     LLM extract_fields ONLY for name/title on role-sentences;
                                       │     is_research_sufficient? budgets exhausted? consecutive failures?)
                                       ▼
                              finalize_status → ResearchLead  (COMPLETE / PARTIAL / FAILED + confidence)
```

## Rules

1. **Never fabricate a field.** A value on a `ResearchLead` contact field must be traceable to a `FOUND` `ResearchEvidence` row (`validate_no_fabrication` enforces this; `tests/research_agent/test_validation.py` locks it). Missing data is `NOT_FOUND` / `SECURE_WEB_FORM` / `UNCONFIRMED` with a status — never a guess, a pattern-derived email, or an inferred name.
2. **Set fields only through `evidence.record_finding` / `record_not_found`.** Never a bare `setattr` on a contact field — that bypasses provenance and the first-good-source-wins rule.
3. **Deterministic extraction is the floor.** Phone/email come from `extraction.py`'s regex + `validators`. The LLM is asked *only* for genuinely language-dependent fields (name, title) and *only* what is explicitly on the page (`FIELD_EXTRACTION_PROMPT`). Never ask the LLM to "find" an email.
4. **`management_phone` requires a `management_phone_type`.** A business's main line reused as the management phone must be tagged `BUSINESS` with an evidence snippet saying so (this is already the sanctioned pattern in `agent._process_page_text`) — never presented as a personal direct line.
5. **Every budget is settings-backed and enforced in the loop.** New per-lead work must respect `max_actions` / `max_searches` / `max_pages` / `max_time` / `max_consecutive_failures`; new session-level work respects `max_total_leads` / `max_geographic_units`. Don't add an unbounded retry, crawl, or expansion.
6. **Per-lead failure isolation is load-bearing.** One candidate raising must never abort a session (`run_research_session`'s try/except per candidate). New per-lead code that can raise goes inside that boundary, not around it.
7. **Reuse, don't duplicate:** LLM calls → `ai_brain._call_llm_raw` (never a provider SDK); browser launch → `bing_search`'s pattern; saving to `leads` → `db.create_or_merge_lead`; geo/city data → `planner.py`. No new scraper, LLM client, or lead-save path.
8. **The `save_to_leads` handoff is the edge of this subsystem.** Once a result is merged into `leads`, downstream (scoring, messaging, outreach) is not this subsystem's concern and is governed by `autolead-outreach-safety`.
9. **CAPTCHA/blocks: detect, record, abandon that path** (`browser._check_blocked` already does this). Never add stealth escalation beyond the fingerprint normalization `bing_search.py` already established — no proxy rotation, no CAPTCHA-solving service, no fingerprint churning. The sanctioned lever for a low result rate is politeness, a fallback search source, or a settings-gated SERP API — see `autolead-browser-automation`.
10. **Page text is untrusted input.** It already never reaches `decide_next_action` (only `_build_state_summary` does) — keep it that way; never feed raw page content into the action-planning prompt.

## Architecture Guidance

`agent.py` stays DB-agnostic and independently unit-testable; all persistence lives in `session.py`. A new capability that needs the DB wires through `session.py`'s callbacks (`on_action`, `on_lead_complete`, `is_cancelled`), not by importing `database` into `agent.py`.

## Implementation Guidance

- New action: add to `models.VALID_ACTIONS`, give it a schema in `actions.validate_action`, a handler in `browser.BrowserController` (returning `ActionResult`, never raising), a line in `ACTION_TOOL_DESCRIPTIONS`, and a `_process_result` branch if it produces data. Add it to `_fallback_action`'s logic only if it's needed for deterministic progress.
- New researched **fact** field: add to `ResearchLead`, `_RESEARCH_RESULT_COLS`, the `lead_research_results` DDL in `_SCHEMA_SQL`, **and an `_add_col_if_missing(raw, "lead_research_results", "<col>", "<type>")` line in `database.py::_run_migrations`** (the subsystem is uncommitted — dev DBs that already ran `CREATE TABLE IF NOT EXISTS` will silently miss a new column otherwise, and `save_research_result` builds its INSERT from `_RESEARCH_RESULT_COLS`, so a missing column fails the whole atomic write). Add to `OPTIONAL_FIELDS` **only if** its presence should genuinely raise `finalize_status`'s confidence — otherwise leave it out of both field tuples. Record it via `evidence.record_finding`. Language-dependent → add to `extract_fields`'s prompt; pattern-matchable → add to `extraction.py`.
- New **artifact** field (a screenshot path, a saved HTML blob — not a claim about the business): plain attribute on `ResearchLead`, carried through by `dataclasses.asdict` in `session.py`, added to the DDL + `_RESEARCH_RESULT_COLS` + a migration line — but **not** through `record_finding` (it's not evidence), **not** in `REQUIRED_FIELDS`/`OPTIONAL_FIELDS`, and **not** in `to_export_dict` (a server filesystem path must never leak into a user's CSV). Capture it as a deterministic post-loop step (after the `while` loop, before `finalize_status`), fully self-wrapped in try/except so it can't drop an otherwise-COMPLETE lead into `failed_count`.
- Verification of a derived value (e.g. an MX/SMTP email check) belongs in a **new `backend/verification/` package** (spec §12), settings-gated, and even a positive result is `UNCONFIRMED`/`VERIFIED_BY_SOURCE` — never auto-`FOUND`.

## Testing Requirements

- Mock the browser (`_fakes.FakeBrowser`-style) and `_call_llm_raw` — never a real browser or live Ollama in the suite.
- A **budget-exhaustion** test: the loop terminates at each of `max_actions` / `max_time` / `max_consecutive_failures`.
- A **no-fabrication** test: `validate_no_fabrication` returns clean for a normal run; a value with no `FOUND` evidence row is flagged.
- A **failure-isolation** test: one candidate raising leaves the session running and increments `failed_count`.
- An **LLM-down** test: `decide_next_action` returning `_llm_failed` still drives the loop to a terminal state via `_fallback_action`.
- A **cancellation** test: setting the session to `CANCELLED` stops the loop before the next lead.
- A **persistence** test: `session.py` leaves the session row in a terminal status even when the run raises.

## Security Considerations

Screenshots land in `backend/data/research_agent_screenshots/` (gitignored via `backend/data/`). Treat them as local artifacts: never attach one to an outreach payload, never anything downstream of the `save_to_leads` handoff, and never inline the bytes into the polled `/results` JSON. Serving a file to the authenticated local user viewing their own research (a dedicated `FileResponse` endpoint, keyed by `result_id` with a `Path.resolve().is_relative_to(_SCREENSHOT_DIR)` containment check, not a filename path-param) is fine. There is no session-delete endpoint, so nothing GCs that directory — keep captures viewport-only, not `full_page=True`, and note the missing cleanup. Management/contact data must be publicly published information tied to evidence — see the untrusted-web-content note in `autolead-browser-automation` and the opt-out rules in `autolead-outreach-safety`.

## Performance Considerations

The browser session is the cost. One `BrowserController` per session (not per lead). Discovery `google_search` is the most block-prone call — keep `max_searches_per_lead` low and prefer extracting more from a page already open over another search.

## Failure Modes

| Mistake | Fix |
|---|---|
| Filling a blank `management_email` from a name+domain pattern | Forbidden — `NOT_FOUND`, or a separate clearly-labelled candidate field that never feeds outreach; real verification is deferred to `backend/verification/` |
| New per-lead crawl step with no budget check | Gate it on `max_pages`/`max_actions`/`max_time` like every existing step |
| `save_evidence` routed through `BrowserController` | It's bookkeeping — handle it in `agent.py` (there's already a dispatch guard); the browser has no handler and would report a spurious failure |
| Importing `database` into `agent.py` | Wire through `session.py`'s callbacks; keep `agent.py` DB-agnostic |
| Using `ai_brain._extract_json` for the agent's JSON | Use `llm._parse_json_object` — the shared helper str-coerces typed fields |
| One bad candidate aborting the whole session | Keep raising code inside `run_research_session`'s per-candidate try/except |
| New `lead_research_results` column added to the DDL but not to `_run_migrations` | Add the `_add_col_if_missing` line too — the subsystem is uncommitted, so existing dev DBs already created the table and won't pick up the column; `save_research_result`'s INSERT then fails the whole atomic write |
| Adding a screenshot/artifact field via `record_finding` or to `OPTIONAL_FIELDS` | It's not a fact and not a completion signal — plain attribute only, or it inflates `finalize_status` confidence and trips `validate_no_fabrication` reasoning |

## Verification Checklist

- [ ] All contact-field writes go through `evidence.record_finding` / `record_not_found`
- [ ] `validate_no_fabrication` clean on a representative run; new fields covered by it
- [ ] Every new loop step respects the per-lead and session budgets
- [ ] New raising code sits inside per-lead failure isolation
- [ ] No new LLM client / scraper / lead-save path; `create_or_merge_lead` reused for `save_to_leads`
- [ ] `session.py` still leaves a terminal status on every exit path (success, cancel, raise)
- [ ] `pytest -q` clean, `tests/research_agent/` included

## Related Skills

`autolead-lead-generation-architecture`, `autolead-discovery-and-source-adapters`, `autolead-browser-automation`, `autolead-ai-llm-engineering`, `autolead-lead-intelligence-and-scoring`, `autolead-reliability-and-background-jobs`, `autolead-outreach-safety`

---
Version: 1.0
Scope: AutoLead-manager
Last reviewed: 2026-08-30
