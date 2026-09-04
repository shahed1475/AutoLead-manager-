# Deliberate Page Research — Design Spec (Research Agent, Cycle 1 of 3)

**Date:** 2026-09-04
**Subsystem:** `backend/research_agent/` only.
**Status:** design — awaiting review before an implementation plan is written.

---

## 0. Where this sits

The user asked to make lead-search automation "behave like a careful web researcher rather than
a high-speed scraper." The audit (2026-09-04, three parallel investigations) established:

- **The 9 scrapers are listing parsers, not crawlers** — 7 of 10 sources never fetch the
  business's own website; the rest visit only the homepage + ~5–8 hard-coded `/contact` `/about`
  paths for email/phone. They are shallow *by design* and feed the pipeline fast. Not touched here.
- **The Browser Research Agent (`backend/research_agent/`) is the careful researcher** — an
  OBSERVE→DECIDE→ACT loop with structural no-fabrication enforcement — **but** it browses as fast
  as Playwright + Ollama allow: `scroll` is a broken stub whose output `_process_result` discards,
  `extract_page_text` is a single 20 000-char DOM snapshot that misses everything below the fold,
  link discovery only matches `contact|about|team`, and there is no pacing anywhere.
- **Technology / opportunity detection** exists only in `backend/intelligence/` (gated off,
  7 tech signatures total) — entirely absent from the research agent.

The work was split into three spec → plan → build cycles:

| Cycle | Scope | This doc |
|---|---|---|
| **1. Deliberate Page Research** | pacing controller · adaptive scroll + scroll-then-read · crawl breadth · depth modes + time budget · per-domain caps · failure recovery · benchmark harness | ✅ this spec |
| 2. Signals | technology detection (curated + Wappalyzer-style dataset) · presence-driven opportunity inference · FACT/INFERENCE/OPPORTUNITY report · richer decision-maker extraction | later |
| 3. Automation integration | automation "Deep mode" (Research Agent session per queue item) · configurable auto-handoff threshold · Lead Search Automation UI | later |

Cycle 1 changes **no** scraper, **no** `automation/` code, **no** UI, and does not touch
`sales_intelligence_enabled`.

---

## 1. Objective

The Research Agent should read a web page the way a person does: wait for it to render, scroll
through it, read what appears, then decide where to go next — all inside a bounded, configurable
budget. "Research quality per bounded minute," not pages per minute.

Concrete, measurable targets (validated by the benchmark harness, §9):

- On a tall / lazy-loading fixture page, the agent extracts content that is currently below the
  fold and therefore invisible to it today.
- More *useful* pages visited per business (a useful page = ≥ 1 field or role-sentence extracted),
  not just more pages.
- No single website consumes the whole per-lead or per-session budget.
- Every timing value lives in one module; no `sleep`/`wait_for_timeout` is scattered through
  agent logic.

---

## 2. Current state (facts from the audit — file:line)

| Area | Today |
|---|---|
| `browser.py::scroll` (332–336) | one fixed `window.scrollBy(0, 800)` + `wait_for_timeout(400)`; returns `{"direction"}` only |
| `agent.py::_process_result` (376–394) | branches for `google_search` / `open_url` / `find_links` / `extract_page_text` only — **`scroll` output is discarded**; `_forced_action` never issues `scroll` |
| `browser.py::extract_page_text` (284–301) | one-shot `body.innerText`, hard cap `[:20000]`; links cap 80; headings cap 30; **no scroll-then-read** |
| `extraction.py::_TEAM_PAGE_HINTS` (17–20) | `contact\|about\|team\|staff\|leadership\|doctors\|management\|our-team\|meet-the-team\|location` — no services / providers / pricing / booking / careers |
| `agent.py::_collect_relevant_links` (396–415) | filter by the regex above, contact/team-first sort, **cap 6** |
| Pacing | `browser.py` waits only: 600ms (consent), 1200ms (post-`google_search`), 600ms (DDG), 800ms (click), 400ms (scroll); `page_timeout_ms`=20000. **Zero** inter-action / inter-page / inter-lead / inter-search delay |
| `config.py::_DEFAULTS` (18–29) | `max_actions_per_lead`=12, `max_searches_per_lead`=4, `max_pages_per_lead`=5, `max_time_per_lead_seconds`=180, `max_total_leads`=20, `max_consecutive_failures`=3, `max_geographic_units`=5, `page_timeout_ms`=20000, `headless`=False, `save_to_leads`=True. **No** `max_scrolls`, no per-domain cap, no time budget, no pacing profile |
| `screenshot` action | `browser.py:363-367` writes a PNG nobody reads — dead, same class as `scroll` |
| Config layering | `app_settings` DB row > `.env`/pydantic (`config.py`) > `_DEFAULTS` — reused unchanged |
| No-fabrication | `evidence.record_finding` is the sole field-setting chokepoint; LLM assertions capped at `UNCONFIRMED`/0.3, never written as values — **preserved as-is** |

---

## 3. New unit: `research_agent/pacing.py` — `PacingController`

**One purpose:** decide how long to pause for a given operation, and wait for a page to settle.

```
PacingController(profile: PacingProfile)

  async def wait(op: str) -> None
      # sleep a uniform-random duration in profile.ranges[op] = (min_s, max_s)

  async def settle(page, *, cap_s: float) -> str
      # poll every ~300ms until BOTH:
      #   document.readyState == "complete"
      #   len(body.innerText) unchanged across 2 consecutive samples
      # or cap_s elapsed. Returns "settled" | "timeout".
```

**Operation types** (the `op` keys): `search_page_load`, `result_review`, `click`, `page_load`,
`initial_read`, `scroll`, `after_scroll`, `link_selection`, `navigation`, `between_domains`,
`between_leads`.

**Profiles** — a profile is a complete `{op: (min_s, max_s)}` map plus a `settle_cap_s` and a
`retry` policy `{attempts: int, backoff_s: (min, max)}`:

| Profile | Character | Rough ranges (finalised after §9 BEFORE benchmark) |
|---|---|---|
| `fast` | ≈ current behaviour | mostly `(0, 0.3)`; `settle_cap_s` 3 |
| `standard` | moderate | `scroll`/`after_scroll` `(0.6, 1.4)`; `page_load` `(0.8, 2.0)`; `result_review` `(1.0, 2.5)`; `between_leads` `(2, 5)`; `settle_cap_s` 6 |
| `deliberate` (**default**) | careful reader | `scroll`/`after_scroll` `(1.2, 2.8)`; `page_load` `(1.5, 3.5)`; `result_review` `(2.0, 4.5)`; `link_selection` `(0.8, 2.0)`; `between_domains` `(3, 7)`; `between_leads` `(4, 9)`; `settle_cap_s` 10 |

Ranges are **starting proposals**; the plan's first task benchmarks the current agent and tunes
them so `deliberate` is meaningfully slower/deeper without blowing the per-lead time budget.

**Config:** the three profiles live in `config.py` as nested dicts under new `_DEFAULTS` keys
(`research_agent_pacing_profiles` is not overridable per-key; the *active profile name* is —
`research_agent_pacing_profile`, default `deliberate`). Depth modes (§7) select the profile.

**Rule:** after this lands, the only `sleep` / `wait_for_timeout` calls in the subsystem are
(a) inside `pacing.py`, and (b) Playwright's own navigation/selector timeouts in `browser.py`.
A lint-style test asserts no new bare `asyncio.sleep` / `wait_for_timeout` appears in
`agent.py` / `reader.py`.

---

## 4. New unit: `research_agent/reader.py` — `PageReader`

**One purpose:** read the page currently open in the browser, carefully and completely, within
limits.

```
@dataclass
class PageContent:
    text: str                # whole page, accumulated across scroll rounds, capped at ~40k
    links: list[dict]         # {href, text} — deduped, cap ~120
    headings: list[str]       # h1/h2/h3, cap ~40
    scroll_rounds: int
    stopped_reason: str       # "stable" | "max_scrolls" | "page_time_cap" | "bottom" | "empty" | "error"

async def read_page(browser, pacing, *, max_scrolls: int, page_time_cap_s: float) -> PageContent
```

**Algorithm:**

1. `reason = await pacing.settle(page, cap_s=profile.settle_cap_s)`. If the page never settles,
   still proceed (best-effort) but record it.
2. `pacing.wait("initial_read")`. Capture visible text / links / headings.
3. Incremental scroll loop, `round` from 1:
   - `await page.evaluate("window.scrollBy(0, Math.round(window.innerHeight * 0.8))")`
   - `await pacing.wait("after_scroll")`
   - re-capture `body.innerText`, link set, `scrollHeight`, and a card-ish count
     (`document.querySelectorAll("article, li, .card, [class*='card'], [class*='team'], [class*='member']").length`)
   - accumulate any **new** text (append the delta) and **new** links
   - **stop** if: nothing new (text length, link count, scrollHeight, card count all unchanged)
     for **2 consecutive rounds** → `"stable"`; OR `round >= max_scrolls` → `"max_scrolls"`; OR
     wall time since `read_page` start `>= page_time_cap_s` → `"page_time_cap"`; OR
     `window.scrollY + window.innerHeight >= scrollHeight` → `"bottom"`.
4. If total captured text is still near-empty after settling + scrolling → `stopped_reason="empty"`
   (the caller treats this as a non-useful page and moves on).
5. Any exception anywhere → return `PageContent(stopped_reason="error", ...)` with whatever was
   captured. **Never raises.**

`read_page` is **one action** from the agent loop's accounting perspective, regardless of
`scroll_rounds`. `max_scrolls` and `page_time_cap_s` come from the active depth mode (§7).

`reader.py` imports only `pacing` and the `browser` handle — it does not import `database`,
`agent`, or `evidence` (keeps it unit-testable against a fake page).

---

## 5. Wiring into `agent.py`

- **R2 rule** ("a page was opened and not yet read → read it", `_forced_action` ~343–349):
  call `PageReader.read_page(...)` instead of emitting a bare `extract_page_text` action.
  Its `PageContent` flows through a **new `_process_result` branch** that does exactly what the
  current `extract_page_text` branch does — `_apply_deterministic_extraction` over the full text,
  `find_role_sentences` → `extract_fields`, `_collect_relevant_links` from `PageContent.links` —
  just over the fuller text.
- `pages_visited` / `pages_read` bookkeeping unchanged (still one increment per page).
- **Between candidates** in `run_research_session`'s per-candidate loop: `await pacing.wait("between_leads")`.
- **On changing domain** within a candidate (a queued link whose host ≠ current host):
  `await pacing.wait("between_domains")`.
- `scroll` and `screenshot` are **removed** from `models.VALID_ACTIONS`, `actions.validate_action`,
  `ACTION_TOOL_DESCRIPTIONS`, and the decision prompt. The LLM no longer decides about scrolling —
  the reader owns it. (`browser.py::scroll` / `screenshot` methods may stay as private helpers
  `reader.py` uses, or be deleted; the plan decides.)
- `_fallback_action` and the deterministic planner otherwise unchanged.

**Untouched:** `evidence.py`, `validation.py`, `llm.py`'s prompts for `extract_fields`, the
no-fabrication chokepoint, `session.py`'s persistence, `planner.expand_geography`.

---

## 6. Crawl breadth (`extraction.py`)

Replace the flat `_TEAM_PAGE_HINTS` regex with a **scored hint map** used by a new
`link_relevance(href, anchor_text) -> int` (0 = ignore):

| Weight | Matches (in href path or anchor text) |
|---|---|
| 3 | `contact`, `about`, `team`, `our-team`, `meet-the-team`, `staff`, `leadership`, `management`, `providers`, `provider`, `doctors`, `physicians`, `dentists`, `attorneys`, `people`, `who-we-are` |
| 2 | `services`, `treatments`, `procedures`, `what-we-do`, `specialties`, `pricing`, `plans`, `fees`, `book`, `booking`, `appointment`, `appointments`, `schedule`, `request`, `patient-portal`, `new-patients` |
| 1 | `locations`, `location`, `offices`, `careers`, `jobs`, `blog`, `news`, `press`, `insights` |

- `_collect_relevant_links` keeps only weight ≥ 1, dedupes by normalised path, sorts by weight
  desc then by original order, and truncates to the depth mode's `max_queued_links` (was fixed 6).
- `is_relevant_nav_link` becomes `link_relevance(...) > 0` (back-compat shim for existing callers/tests).
- The R3 follow rule (only follow a queued link while a required field is missing) is loosened:
  in `deep` / `max` modes the agent may also follow **one** weight-3 people/offering link **per
  candidate** even when required fields are already satisfied, bounded by `max_pages_per_lead` —
  this is how it reaches a providers/services page for Cycle 2's signals.

---

## 7. Depth modes + time budget (`config.py` + `run_research_session` params)

New session parameter `research_depth ∈ {quick, standard, deep, max}` (default `standard`),
threaded from `ResearchAgentStartRequest` and `handoff` payloads (both default `standard`;
Cycle 3 exposes the control). It selects a **preset** that overrides the per-lead budgets:

| key | quick | standard | deep | max |
|---|---|---|---|---|
| `pacing_profile` | fast | standard | deliberate | deliberate |
| `max_pages_per_lead` | 2 | 5 | 9 | 14 |
| `max_scrolls_per_page` | 3 | 6 | 12 | 20 |
| `max_searches_per_lead` | 2 | 4 | 6 | 8 |
| `max_actions_per_lead` | 8 | 14 | 24 | 36 |
| `max_time_per_lead_seconds` | 90 | 200 | 420 | 900 |
| `max_domain_seconds` | 45 | 120 | 240 | 480 |
| `page_time_cap_s` (per `read_page`) | 20 | 40 | 75 | 120 |

- The preset is resolved once at session start; individual `research_agent_*` DB settings still
  override a resolved value (settings > preset > `_DEFAULTS`), so power users keep fine control.
- **`research_agent_time_budget_seconds`** — a session-level deadline (default: `0` = none;
  a session may also pass an explicit value). Checked at the top of `run_research_session`'s
  per-candidate loop; when exceeded, the loop stops and whatever is done is persisted
  (`session.py` already writes terminal status on every exit path).
- **`max_domain_seconds`** — tracked per candidate: when the wall time spent on the current
  business's domains exceeds it, the candidate is finalised with what's been found (`PARTIAL`),
  the loop moves on. Enforced in `research_business` alongside the existing `max_time_per_lead`.

These numbers are **proposals**; the plan tunes them against the benchmark so that, e.g., `deep`
genuinely reads providers + services + about pages on a typical clinic site inside its per-lead
budget.

---

## 8. Decision-maker source policy (settings-gated)

New setting **`research_agent_allow_professional_profiles`** — default **`false`**.

- **Off (default):** unchanged behaviour. Professional-network / company-profile domains are not
  visited (today they're simply never in the link queue because the hints don't match them; this
  stays true).
- **On:** the agent MAY `open_url` a publicly-visible company / professional profile page it
  discovered as a link on the business's own site (e.g. a "Find us on LinkedIn" link to the
  business's *company* page), as a **read-only evidence source** for owner/manager names + titles.

**Hard rules (unchanged, absolute):** no login, no auth flow, no connection requests, no
messaging, no enumerating individual people's personal profiles, no rate escalation. An
auth wall / CAPTCHA / block → `browser._check_blocked` records `status="blocked"` and the agent
abandons that URL (never a bypass). A cap of **2** professional-profile page opens **per candidate**.
Requests to those hosts get the `deliberate` profile's `navigation` pacing regardless of the
active profile.

This is a **deliberate reversal** of the current `autolead-browser-automation` skill line
("Never build … LinkedIn scraping") and the browser-research-agent design's "No LinkedIn
scraping", scoped to: settings-gated, off by default, company/public pages only, read-only,
graceful-skip. The design doc records it as an explicit product decision (dev rule 13). Deeper
decision-maker *extraction* (distinct direct lines, richer roles, cross-source corroboration)
is Cycle 2.

---

## 9. Benchmark harness (`tests/research_agent/benchmark/`)

Not part of the `pytest -q` unit suite (it drives the real agent loop against local fixtures and
takes minutes). A small runner:

- **Fixture sites** under `tests/research_agent/fixtures/` served by a local `http.server` on a
  random port: a "clinic" (multi-page: home, about, team with lazy-loaded member cards, services,
  contact-form-only), a "SaaS" (SPA-ish, infinite-scroll team section), a "thin" one-pager, a
  "broken" site (500s / JS error), and a "slow" site (delayed content). No live network.
- Runs the agent (`headless=True`, LLM mocked to a deterministic planner, or real Ollama if
  available — flagged) against each fixture at each depth mode.
- Records per run: pages inspected, useful pages, fields found, role-sentences found, decision
  makers found, wall time, `stopped_reason` distribution.
- Emits a `benchmark_results.json` + a printed BEFORE/AFTER table.

**Task 1 of the plan** runs it against `main`-behaviour (BEFORE). **The final task** runs it
again (AFTER). Completion (§27 of the brief) requires the AFTER table to show the fixture
below-the-fold content is now captured and useful-page/field counts are up, not merely that
unit tests pass.

---

## 10. Failure recovery (brief §19)

Every one of these already returns an `ActionResult`/`PageContent` rather than raising; Cycle 1
hardens the handling so a bad page never aborts a candidate and a bad candidate never aborts the
session:

| Condition | Handling |
|---|---|
| page never settles | `PageReader` proceeds best-effort, `stopped_reason` notes it |
| nav timeout | `open_url` already returns `status="timeout"`; agent records it, tries the next queued link |
| JS-heavy / SPA | scroll loop + `settle()` give content a chance; if still empty → `"empty"`, move on |
| empty / broken page | `PageContent(stopped_reason in {"empty","error"})` → not counted as useful, next link |
| unavailable domain | `open_url` error result → next link; domain marked bad for this candidate |
| duplicate result / lead | unchanged — `candidate_key` dedup + `create_or_merge_lead` |
| per-domain time exceeded | finalise candidate `PARTIAL`, next candidate |
| session time budget exceeded | stop loop, persist partial (`session.py` terminal-status guarantee) |
| retry policy | from the active pacing profile (`attempts`, `backoff_s`); applied to nav/read failures only, never to a `blocked` result |

---

## 11. Config keys added (all `research_agent_` prefixed, DB-overridable)

| key | default | notes |
|---|---|---|
| `research_agent_pacing_profile` | `deliberate` | `fast\|standard\|deliberate` |
| `research_agent_research_depth` | `standard` | default when a session doesn't pass one |
| `research_agent_time_budget_seconds` | `0` | 0 = no session-level budget |
| `research_agent_max_scrolls_per_page` | (from depth preset) | overridable |
| `research_agent_max_domain_seconds` | (from depth preset) | overridable |
| `research_agent_page_time_cap_seconds` | (from depth preset) | per `read_page` |
| `research_agent_max_queued_links` | (from depth preset) | was hard-coded 6 |
| `research_agent_allow_professional_profiles` | `false` | §8 |

The three pacing-profile range maps are module constants in `config.py` (not per-key
overridable — too fiddly; the profile *name* is the knob).

No DB schema change. No `_add_col_if_missing`. `lead_research_results` / `lead_research_evidence`
columns unchanged (Cycle 2 adds the tech/opportunity/report columns).

---

## 12. Testing requirements (TDD; mock browser + LLM; local fixtures only — never live)

- **pacing:** `wait(op)` samples within the configured range for each op; `settle()` returns
  `"settled"` when text stabilises and `"timeout"` at the cap; unknown `op` → safe no-op + warn.
- **reader:** on a tall fixture, `read_page` returns text that a single snapshot misses; stops on
  2-round stability; stops at `max_scrolls`; stops at `page_time_cap_s`; stops at bottom; empty
  page → `stopped_reason="empty"`; thrown error mid-scroll → `stopped_reason="error"`, partial
  content, no exception.
- **agent wiring:** R2 uses `PageReader`; `_process_result` consolidated branch extracts the same
  fields the old branch did on equivalent text; `scroll`/`screenshot` no longer in `VALID_ACTIONS`
  (a prompt/vocab test); `between_leads` / `between_domains` pacing called.
- **crawl breadth:** `link_relevance` weights (people 3 / offering 2 / peripheral 1 / junk 0);
  queue sorts by weight and truncates to `max_queued_links`; `is_relevant_nav_link` shim still
  green for existing callers.
- **depth modes:** each preset resolves the documented budgets; a DB setting overrides a preset
  value; unknown depth → `standard`.
- **time budgets:** session `time_budget_seconds` stops the candidate loop and `session.py` still
  writes a terminal status with partial results; `max_domain_seconds` finalises a candidate
  `PARTIAL` and advances.
- **professional profiles:** setting off → discovered profile URLs never opened; on → opened
  read-only, capped at 2, auth-wall fixture → `blocked` recorded, agent continues.
- **regression:** the existing `tests/research_agent/*` suite stays green (budget-exhaustion,
  no-fabrication, failure-isolation, LLM-down, cancellation, persistence).
- **no-hardcoded-sleep guard:** a test greps `agent.py` / `reader.py` for `asyncio.sleep(` /
  `wait_for_timeout(` and fails on a new occurrence.

`pytest -q` baseline is **827 passing** on this branch; Cycle 1 adds tests, keeps all green.
Frontend: no change, so no `npm run build` gate for Cycle 1.

---

## 13. Explicitly out of scope for Cycle 1

Technology / tech-stack detection · analytics/pixel/CRM/booking/chat/AI-widget fingerprinting ·
the Wappalyzer-style dataset · automation-opportunity / pain-point / buying-signal inference ·
the FACT / INFERENCE / OPPORTUNITY report format · richer decision-maker extraction (direct
lines, cross-source) · contact verification (`backend/verification/` Checkpoint 5C) · the
automation "Deep mode" · configurable auto-handoff threshold · any Lead Search / Automation /
Research Agent **UI** change · `sales_intelligence_enabled` behaviour · scraper changes.

---

## 14. Risks

| Risk | Mitigation |
|---|---|
| `deliberate` pacing blows the per-lead time budget → fewer leads researched | benchmark-tune the ranges against `max_time_per_lead_seconds`; `max_domain_seconds` caps the worst case; depth mode is the user's speed/quality dial |
| adaptive scroll loops forever on a truly-infinite feed | hard `max_scrolls_per_page` + `page_time_cap_s` + bottom check; 2-round stability is the common exit |
| removing `scroll`/`screenshot` from the vocab breaks a test or a fallback path | vocab is small and centralised (`models.VALID_ACTIONS`); update the 3 sites + tests together; `_fallback_action` never used either |
| professional-profile setting invites over-collection | off by default; company/public pages only; 2-open cap; read-only; blocked→skip; documented as a policy decision |
| benchmark fixtures don't resemble real sites | model them on the audit's real targets (dental clinics, small SaaS); the AFTER gate is comparative, not absolute |
| `settle()` polling adds latency on every page | cap is per-profile (3/6/10s); it replaces guesswork, and a settled page means the scroll loop starts from real content |

---

## 15. Rollback

Cycle 1 is additive: two new modules (`pacing.py`, `reader.py`), new config keys with defaults
that (at `pacing_profile=fast` + `research_depth=quick`) reproduce close-to-current behaviour,
and edits to `agent.py` / `extraction.py` / `models.py` / `actions.py` / `config.py`. No schema
change, no data migration. Revert the branch to undo. Setting `research_agent_pacing_profile=fast`
and `research_agent_research_depth=quick` in `app_settings` is a runtime kill-switch back to
near-original speed without a deploy.
