---
name: autolead-backend-architecture
description: Use when adding or changing FastAPI routers, backend modules, app settings/config, or the module boundaries between scraping/enrichment/intelligence/scoring/outreach in AutoLead.
---

# AutoLead Backend Architecture

## Purpose

Documents the FastAPI backend's module layout, settings-layer convention, and startup/lifespan wiring so new backend code fits existing patterns instead of inventing parallel ones.

## When to Use

Adding a new router/endpoint, a new backend package/module, or a new configurable setting.

## When NOT to Use

Frontend work (`autolead-frontend-architecture`), DB schema changes specifically (`autolead-database-and-migrations`), or lead-gen-domain logic (use the relevant lead-gen skill — this one is about backend plumbing, not domain rules).

## Project Context

- **Stack:** FastAPI, `uvicorn`, `aiosqlite`, `pydantic`/`pydantic-settings`, `slowapi` (rate limiting), `apscheduler` (cron jobs), `bcrypt` (auth). No ORM — raw SQL via `database.py`.
- **Module layout:** `backend/routers/` (one file per domain: campaigns, leads, pipeline, replies, marketing, intelligence, settings, auth, `discovery`, `research_agent`, ...), `backend/scrapers/` (the 9 scraper sources), `backend/discovery/` (Discovery Planner + source-adapter registry + merge-dedup + Quick Search), `backend/research_agent/` (the Browser Research Agent subsystem), `backend/enrichment/` (deterministic + AI website analysis), `backend/intelligence/` (evidence-tracked sales-intelligence agents), `backend/scoring/` (deterministic lead scoring), plus top-level modules for cross-cutting concerns: `ai_brain.py` (LLM access), `database.py` (schema + all DB access), `auth.py`, `email_sender.py`, `whatsapp_sender.py`, `reply_detector.py`, `followup_engine.py`, `scheduler.py`, `queue_worker.py`, `validators.py`, `secrets_crypto.py`. `backend/discovery/` and `backend/research_agent/` each follow the `backend/intelligence/` package shape (own `config.py` with the `app_settings`-override pattern, own models, own tests dir).
- **Settings layer (follow this for any new config):** `config.py` (pydantic-settings, `.env`-backed) provides defaults; the `app_settings` DB table (read via `db.get_all_settings()`) overrides at runtime, editable live from the Settings UI without a restart. Every module reading config follows the same shape: `stored.get(x) or _env.x` (see `email_sender._smtp_cfg`, `whatsapp_sender._wa_cfg`, `ai_brain._ollama_cfg`, `scraper._scraper_cfg`).
- **Startup (`main.py` lifespan):** initializes DB schema/migrations, starts APScheduler (`scheduler.py`), starts `JobQueue` (`queue_worker.py::init_queue`, 4 workers — now driven by Quick Search and the Browser Research Agent, see `autolead-reliability-and-background-jobs`).
- **Auth:** router-level FastAPI dependencies (`auth.py`), open API when no password set. See `autolead-security-and-secrets` for the full model — no RBAC/multi-tenancy exists.
- **No ORM migrations tool** — schema changes go through `_add_col_if_missing`-style additive helpers in `database.py`, not Alembic/similar.

## Rules

1. **New endpoints go in `routers/`, one file per domain**, matching the existing split — don't add unrelated endpoints to an existing router file just because it's convenient.
2. **New config follows the two-layer settings pattern** (pydantic default + DB override) — don't read `os.environ` directly in new code when `config.py`/`app_settings` already covers it.
3. **New backend subsystems get their own package** (`backend/<name>/`), mirroring `intelligence/`/`enrichment`/`scoring` — not dumped into an existing module.
4. **No ORM** — new DB access goes through `database.py` functions (or a new function added there), using the same raw-SQL/`aiosqlite` style as everything else. Don't introduce SQLAlchemy or another data layer for one feature.
5. **Reuse `ai_brain._call_llm_raw` for LLM calls**, the existing `send_email`/`send_whatsapp` for sending (see `autolead-outreach-safety`), and the existing `scrapers` functions for discovery (see `autolead-discovery-and-source-adapters`) — this file's job is to remind you those exist before you write a parallel version.

## Architecture Guidance

Router → domain module (scrapers/enrichment/intelligence/scoring/outreach) → `database.py`. Cross-cutting concerns (`ai_brain`, auth, secrets) are imported by domain modules, not by routers directly, except where a router legitimately needs a direct DB read for a simple CRUD endpoint.

## Implementation Guidance

Before adding a new router, check `main.py` for how existing routers are registered (prefix/tags convention) and follow it. Before adding a new settings field, check `routers/settings_router.py` for the redaction pattern if the field is remotely secret-adjacent (see `autolead-security-and-secrets`).

## Testing Requirements

New routers/endpoints need at least one integration test in `tests/` following the existing `test_*_router.py` naming and `clean_db` fixture usage.

## Security Considerations

See `autolead-security-and-secrets` in full — the short version: no hardcoded secrets, redact on read, respect the single-user auth model as-is.

## Performance Considerations

Prefer async DB access (`aiosqlite`) throughout — don't introduce a blocking call in an async route handler without offloading it (existing scrapers/browser automation already run via `asyncio.to_thread`-style patterns where needed for Selenium).

## Failure Modes

| Mistake | Fix |
|---|---|
| New endpoint reads `os.environ` directly | Use `config.py`/`app_settings` layered pattern |
| New feature introduces an ORM or a second DB-access style | Use `database.py`'s existing raw-SQL functions |
| New router mixes unrelated domains | Split into its own router file |
| Blocking call inside an async handler | Offload appropriately, matching existing scraper patterns |

## Verification Checklist

- [ ] New endpoint registered/tagged consistently with existing routers
- [ ] New config uses the two-layer settings pattern
- [ ] New DB access goes through `database.py`, no second data-access style introduced
- [ ] Integration test added for new endpoint(s)

## Related Skills

`autolead-database-and-migrations`, `autolead-security-and-secrets`, `autolead-reliability-and-background-jobs`, `autolead-lead-generation-architecture`, `autolead-browser-research-agent`

---
Version: 1.1
Scope: AutoLead-manager
Last reviewed: 2026-08-30 (backend/discovery/, backend/research_agent/ packages added; JobQueue now has producers)
