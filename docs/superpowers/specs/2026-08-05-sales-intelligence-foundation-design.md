# AI Sales Intelligence Engine — Sub-project 1: Foundation + Qualification + Company Research

## Context

The user wants a 7-agent "AI Sales Intelligence Engine" (Qualification → Company Research →
Decision Maker Discovery → Contact Verification → ICP Match → Sales Intelligence →
Personalization) bolted onto AutoLead-manager's existing campaign pipeline. That request spans
too many independent subsystems for one spec, so it was decomposed into 6 sub-projects, each
getting its own spec → plan → implementation cycle:

1. **Foundation + Qualification Agent + Company Research Agent** (this doc)
2. ICP Match Agent + Sales Intelligence Agent (extends `scoring/lead_scorer.py`)
3. AI Personalization Agent (feeds `ai_brain.py` message generation)
4. UI — 6 tabs on Lead Details
5. Decision Maker Discovery + Contact Verification (compliance-sensitive — LinkedIn ToS
   explicitly prohibits scraping; scope to be re-discussed before that sub-project starts)
6. Test-suite hardening + monitoring/observability polish

This spec covers **only sub-project 1**.

## Relationship to existing code (critical — read before implementing)

As of 2026-08-05 the app already has, from a prior production-hardening pass:
- `backend/enrichment/website_analyzer.py::analyze_website(url)` — fetches a page and extracts
  title/meta/headings/body text/CTAs/social links/contact signals. Has an in-process TTL cache.
- `backend/enrichment/ai_enricher.py::enrich_lead_with_ai(lead, website_data, company_dna)` —
  LLM synthesis (business_summary, marketing_gaps, growth_potential, etc.), persists to
  `enriched_data` + updates `leads`. Uses `ai_brain._call_llm_raw`/`_ollama_cfg` (provider-
  dispatching: local Ollama by default, optional cloud key from Settings).
- `backend/scoring/lead_scorer.py::score_lead(lead, enriched, website_scores)` — 4-dimension
  0-100 scorer, HOT/WARM/COLD categorization, persists to `scores` table.
- Both `routers/campaigns.py::_run_campaign_task` (UI-driven campaigns) and
  `scheduler.py::run_campaign` (daily cron) call `analyze_website` → `enrich_lead_with_ai` →
  `score_lead` inline, with bounded `asyncio.Semaphore` concurrency.

**The new Company Research Agent reuses `analyze_website`/`enrich_lead_with_ai`, it does not
duplicate them.** The new pipeline is a richer, evidence-tracked, qualification-gated layer that
sits *alongside* the existing enrichment — `enriched_data`/`scores` keep being populated exactly
as today. `lead_scorer.py` is not modified in this sub-project.

## Backward compatibility & safety

A new `app_settings` key, `sales_intelligence_enabled` (default absent → treated as `"false"`),
gates all new behavior:

- **Off (default):** `_run_campaign_task` and `scheduler.run_campaign` behave byte-for-byte as
  they do today. Zero risk to any campaign already running or scheduled.
- **On:** the pipeline's enrichment step calls the new orchestrator instead of the direct
  `analyze_website`/`enrich_lead_with_ai` calls. The orchestrator still calls those same
  functions internally (no duplicate network/LLM calls), and additionally populates
  `company_profiles` + `research_evidence`.

No UI toggle in this sub-project (UI is sub-project 4). Toggle it via the existing
`PUT /api/settings` endpoint (`{"key": "sales_intelligence_enabled", "value": "true"}`) or
directly in the DB for testing.

## Agent framework

New package `backend/intelligence/`.

`backend/intelligence/base.py`:

```python
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Protocol

@dataclass
class EvidenceItem:
    field_name: str
    source_type: str          # "website" | "ai_inference" | "heuristic"
    source_url: Optional[str]
    snippet: Optional[str]

@dataclass
class AgentResult:
    status: Literal["ok", "rejected", "failed"]
    data: dict[str, Any] = field(default_factory=dict)
    evidence: list[EvidenceItem] = field(default_factory=list)
    confidence: float = 0.0
    reason: Optional[str] = None   # populated on "rejected" or "failed"

class ResearchAgent(Protocol):
    name: str
    async def run(self, lead: dict, campaign: Optional[dict]) -> AgentResult: ...
```

No registry/discovery framework — YAGNI for 2 agents. The orchestrator holds an explicit
ordered list. Adding agent 3+ later means: implement the class, add one line to that list.

## Database schema

All 6 tables from the original request are created now (schema stability across
sub-projects); only `company_profiles` and `research_evidence` are written to in this
sub-project. `decision_makers`/`verification_results`/`sales_scores`/`personalization_context`
are empty scaffolding for sub-projects 2/3/5.

**Deliberate deviation from the original request:** no `workspace_id` column anywhere. This
app has no workspace/multi-tenancy concept in any existing table (`leads`, `campaigns`,
`campaign_runs` etc. are all single-tenant) — inventing one only for this feature would be
inconsistent with the rest of the schema and add a column with no current meaning. Every new
table links to `lead_id` (and transitively to `campaign_run_id` via the lead), matching how
every other table in this codebase already links to `leads`.

```sql
CREATE TABLE IF NOT EXISTS company_profiles (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id                  INTEGER UNIQUE REFERENCES leads(id) ON DELETE CASCADE,
    status                   TEXT DEFAULT 'PENDING',  -- PENDING/QUALIFYING/RESEARCHING/DONE/FAILED/REJECTED
    qualification_status     TEXT,                    -- QUALIFIED/REJECTED
    qualification_reason     TEXT,
    qualification_confidence REAL,
    industry                 TEXT,
    services                 TEXT,   -- JSON list
    products                 TEXT,   -- JSON list
    company_description      TEXT,
    social_profiles          TEXT,   -- JSON list
    tech_stack               TEXT,   -- JSON list
    company_size_estimate    TEXT,   -- solo/small/medium/large/unknown
    maturity_estimate        TEXT,   -- startup/growing/established/enterprise/unknown
    hiring_signal            INTEGER,  -- NULL = unknown, 0/1 once a future adapter sets it
    recent_activity_summary  TEXT,     -- NULL in this sub-project
    partnerships             TEXT,     -- JSON list, NULL in this sub-project
    research_confidence      REAL,
    researched_at            TIMESTAMP,
    created_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_company_profiles_status ON company_profiles (status);

CREATE TABLE IF NOT EXISTS research_evidence (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    company_profile_id INTEGER REFERENCES company_profiles(id) ON DELETE CASCADE,
    agent_name         TEXT,    -- 'qualification' | 'company_research'
    field_name         TEXT,
    source_type        TEXT,    -- 'website' | 'ai_inference' | 'heuristic'
    source_url         TEXT,
    snippet            TEXT,
    collected_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_research_evidence_profile ON research_evidence (company_profile_id);

-- Scaffolding for later sub-projects (empty, unused until then):
CREATE TABLE IF NOT EXISTS decision_makers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_profile_id INTEGER REFERENCES company_profiles(id) ON DELETE CASCADE,
    full_name TEXT, role_title TEXT, seniority_rank INTEGER,
    email TEXT, phone TEXT, linkedin_url TEXT,
    source_type TEXT, source_url TEXT, confidence REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS verification_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_maker_id INTEGER REFERENCES decision_makers(id) ON DELETE CASCADE,
    check_name TEXT, passed INTEGER, detail TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sales_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_profile_id INTEGER UNIQUE REFERENCES company_profiles(id) ON DELETE CASCADE,
    company_quality_score REAL, decision_maker_quality_score REAL,
    contact_confidence_score REAL, icp_match_score REAL,
    outreach_readiness_score REAL, overall_prospect_score REAL,
    briefing TEXT, scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS personalization_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_profile_id INTEGER UNIQUE REFERENCES company_profiles(id) ON DELETE CASCADE,
    outreach_angle TEXT, value_proposition TEXT, talking_points TEXT,
    email_tone TEXT, whatsapp_tone TEXT, recommended_cta TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Startup migration sweep (mirrors the existing `campaign_runs` FAILED sweep in
`_run_migrations`): `UPDATE company_profiles SET status='PENDING' WHERE status IN
('QUALIFYING','RESEARCHING')` — a crash mid-research becomes retryable, not stuck forever.

## Lead Qualification Agent

`backend/intelligence/qualification_agent.py`. No LLM — fast and free, runs for every lead.

Checks (each contributes a weight to a 0.0–1.0 confidence score; threshold 0.5 default):
- Website reachable (HTTP HEAD, fallback GET, 6s timeout) — skipped (neutral, not penalized)
  if lead has no website at all.
- Business name present and not junk (reuses `validators.clean_business_name`).
- Contact info present (email/phone/website — reuses existing spam/validity checks from
  `validators.py`).
- Niche match against `campaign.niche` (fuzzy substring, case-insensitive) — skipped
  gracefully when `campaign` is `None` (standalone/manual trigger, e.g. CSV-imported lead).
- City/country match against `campaign.city`/`campaign.country` — same graceful skip.

Output: `AgentResult(status="ok" or "rejected", data={"qualification_status": ..., "confidence": ...}, reason=...)`.
Rejection reason lists every failed check, e.g. `"website unreachable; niche mismatch"`.

## Company Research Agent

`backend/intelligence/company_research_agent.py`. Only runs if qualification passed.

1. Calls `analyze_website(lead["website"])` (existing, cached) if a website exists.
2. Tech-stack heuristic (`backend/intelligence/techstack.py`, new, pure function over the
   already-fetched HTML — zero extra network calls): regex signatures for WordPress, Shopify,
   Wix, Squarespace, Webflow, React/Next.js, common analytics tags. Returns `list[str]`.
3. LLM synthesis: new prompt (local `_build_research_prompt`, mirrors `ai_enricher.py`'s
   established pattern) asking for JSON: `industry`, `services[]`, `products[]`,
   `company_description`, `company_size_estimate` (enum), `maturity_estimate` (enum). Uses
   `ai_brain._call_llm_raw`/`_ollama_cfg` — same provider dispatch as everything else. A local
   JSON-repair parse helper (small, ~15 lines, deliberately duplicated rather than importing
   `ai_enricher`'s private `_parse_ai_response` — keeps agents independently replaceable per
   the "modular" requirement).
4. Also calls `enrich_lead_with_ai(lead, website_data, company_dna)` — this is what keeps
   `enriched_data`/`scores` populated exactly as before; the agent's job is to additionally
   capture evidence-tracked structured fields, not to replace the existing enrichment.
5. Evidence rows: direct-from-page fields (`social_profiles`, page-derived `company_description`
   fallback) get `source_type="website"` with the real URL and a snippet. LLM-synthesized
   fields get `source_type="ai_inference"` — explicitly distinguished, never presented as if
   directly sourced. Tech stack gets `source_type="heuristic"`.

**Explicitly deferred (not built in this sub-project):** hiring signals, recent
news/blog activity, partnerships. Fields exist in the schema (NULL by default) with a
documented extension point — a future `collect_search_evidence` adapter — rather than adding a
flaky, ToS-sensitive external search dependency to this MVP.

Confidence: 1.0 if website fetched + LLM parsed cleanly; degrades gracefully (same pattern as
`ai_enricher.py`) to a lower score with heuristic-only fields if the website fetch fails or the
LLM output can't be parsed — never raises, matching "research failures must never stop
campaigns."

## Orchestrator

`backend/intelligence/orchestrator.py`:

```python
async def run_research_pipeline(lead: dict, campaign: Optional[dict] = None) -> dict:
    """Get-or-create the lead's company_profiles row, run Qualification then (if qualified)
    Company Research, persisting progressively after each agent. Never raises."""

async def run_pending_research(limit: int = 50, max_concurrent: int = 3) -> dict:
    """Find leads with company_profiles.status='PENDING' (or no profile row at all),
    process with bounded asyncio.Semaphore concurrency. Used for resume-after-interruption
    and as the campaign-pipeline hook."""
```

Persistence is progressive: qualification result is saved (status→`QUALIFYING` then result)
before company research starts, so a crash mid-research still leaves the qualification verdict
intact — satisfies "partial results should be saved progressively."

**Async execution:** `asyncio.Semaphore`-bounded `asyncio.gather`, the same proven pattern
already used for enrichment/email-finding in this codebase — not the dormant
`queue_worker.JobQueue`. Wiring that dead-but-started job queue into a live-traffic path is a
larger, separate risk (flagged as follow-up work from the prior hardening pass) and conflating
it here works against "make sure it works 100%." `run_pending_research`'s DB-status-driven
resume achieves the "resume after interruption" requirement without needing durable queue
infra, consistent with this app's SQLite-only philosophy.

## Pipeline integration points

- `routers/campaigns.py::_run_campaign_task` — at the existing ENRICHING stage: if
  `sales_intelligence_enabled`, call `run_pending_research` scoped to this run's leads instead
  of the direct `analyze_website`+`enrich_lead_with_ai` loop; else, unchanged.
- `scheduler.py::run_campaign` — same conditional at its per-lead enrichment point.

## API

New router `backend/routers/intelligence.py`, mounted in `main.py`:

- `GET /api/leads/{lead_id}/research` → company profile + its evidence list (404 if no
  profile row exists yet). Needed to verify this works end-to-end even before sub-project 4's
  UI exists.

## Testing

New dev dependencies in `requirements.txt`: `pytest`, `pytest-asyncio`, `respx` (httpx mocking
— `analyze_website` uses `httpx.AsyncClient`).

- `tests/intelligence/test_qualification_agent.py` — pure unit tests, no I/O, table-driven
  over the check combinations.
- `tests/intelligence/test_company_research_agent.py` — `respx`-mocked HTTP, mocked
  `_call_llm_raw` (no real Ollama/network dependency in CI), asserts evidence rows and
  graceful degradation on fetch/parse failure.
- `tests/intelligence/test_orchestrator.py` — integration test against a temp SQLite DB (same
  pattern as the manual smoke test from the prior session, formalized): full pipeline run,
  progressive-persistence-on-failure, and the startup resume sweep.
- `tests/conftest.py` — temp-DB fixture (env-var `DATABASE_PATH` override + `init_db()`).

## Out of scope for this sub-project

Decision Maker Discovery, Contact Verification, ICP Match, Sales Intelligence scoring/briefing,
Personalization, all 6 Lead Details UI tabs, `queue_worker.JobQueue` wiring, hiring/news/
partnership signal collection, any Settings-page UI toggle (backend setting only).
