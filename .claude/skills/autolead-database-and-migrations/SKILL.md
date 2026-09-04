---
name: autolead-database-and-migrations
description: Use when adding or changing database tables, columns, indexes, or migrations in AutoLead, or when debugging a schema/dedup/cascade-delete issue.
---

# AutoLead Database & Migrations

## Purpose

Documents AutoLead's SQLite schema conventions, migration pattern, and dedup/cascade gotchas that have caused real bugs before.

## When to Use

Any new table/column, any migration, or debugging duplicate leads / a cascade-delete crash / a schema-mismatch startup error.

## When NOT to Use

Pure application-logic changes with no schema impact.

## Project Context

- **SQLite only, single file** (`aiosqlite`) — deliberate choice for a one-click local install; no Postgres. Schema source of truth is `backend/database.py::_SCHEMA_SQL` (raw DDL) plus additive migration helpers (`_add_col_if_missing`-style) for columns added after initial release.
- **~25+ tables**, grouped roughly as: core (`leads`, `campaign_runs`, `campaign_log`, `messages`, `replies`), enrichment (`enriched_data`, `scores`), sales-intelligence (`company_profiles`, `research_evidence`, `pain_points`, `business_opportunities`, `solution_recommendations`, `generated_messages`, plus unused scaffolding: `decision_makers`, `verification_results`, `sales_scores`, `personalization_context`), CRM (`lead_stage_history`), **lead discovery** (`lead_discovery_runs`, `lead_sources`), **browser research** (`lead_research_sessions`, `lead_research_results`, `lead_research_evidence`).
- **Dedup is DB-enforced, not just application-logic:** unique indexes on `LOWER(email)` and `phone` (both `WHERE ... IS NOT NULL`) on `leads`. `find_duplicate_lead` matches exact `LOWER(email)`, exact `phone`, normalized `website`, and **exact** `LOWER(name)+LOWER(city)` (no fuzzy at this layer — the in-batch scraper dedup and `find_duplicate_lead_fuzzy` are where `difflib` fuzzy matching lives). `create_lead_deduped`/`create_lead_deduped_with_log` do check-then-insert with IntegrityError retry — the unique index is the real backstop if application logic races.
- **Two lead-save paths now exist, and they behave differently on collision:**
  - `create_lead_deduped` / `create_lead_deduped_with_log` — the legacy path (Campaign-mode scraping, scheduler). **Discards** a duplicate candidate's extra fields.
  - `create_or_merge_lead(data, source, source_identifier, run_id) -> (lead_id, is_new, merge_reason)` — the discovery path (Quick Search; also the Browser Research Agent's `save_to_leads`). Uses `find_duplicate_lead_fuzzy` (exact email/phone/website/name+city, then genuinely fuzzy `SequenceMatcher ≥ 0.85` name+city over that city's ≤500 most-recent leads). On collision: **existing non-null values always win** (including falsy-but-real `0`/`False`/`""`), only missing fields fill, and a `lead_sources` provenance row is **always** inserted — even when the lead already existed. `business_name`/`status` are never overwritten by a merge. New-lead insert + its first `lead_sources` row commit in one `transaction()`. Race handling: an `IntegrityError` from a concurrent JobQueue worker re-resolves and falls through to merge; only email/phone have DB unique indexes, so a website-only / fuzzy-name race remains a known narrow gap (documented in the discovery spec).
  - The fuzzy matcher is reimplemented in `database.py` (`_fuzzy_norm_name` + `SequenceMatcher`) rather than imported from `scrapers/__init__.py` — importing back would be circular (`scrapers` already imports `database`).
- **`ON DELETE CASCADE` is not automatic/consistent** — the `replies` table needed a dedicated migration (`_migrate_replies_cascade`) after shipping without it, which crashed lead deletion for existing installs. Check cascade behavior explicitly for any new per-lead table; don't assume SQLite FK cascade is on by default (it requires `PRAGMA foreign_keys=ON` plus the constraint itself).
- **`generated_messages` is deliberately separate from `messages`** — writing Phase-4-style marketing drafts into `messages` would break `followup_engine.py`'s idempotency check (`count_messages_for_lead(lead_id) > 0`). This is a real example of "looks like the same kind of data, must be the same table" being wrong.
- **`set_lead_stage` is the single choke-point** for every CRM-board-relevant status write, logging to `lead_stage_history` — a precedent for how a new subsystem should centralize writes to a shared/sensitive column rather than letting every caller UPDATE it directly.
- **The discovery/research tables are uncommitted — adding a column to one still needs a `_run_migrations` line.** `_SCHEMA_SQL`'s `CREATE TABLE IF NOT EXISTS` only creates the table on a *fresh* DB; a dev DB that already ran it will not pick up a later column without an `_add_col_if_missing(raw, "<table>", "<col>", "<type>")` entry in `_run_migrations`. This bites harder here than for older tables because every developer's local `leads.db` already has these tables from an earlier run. `save_research_result` / `create_discovery_run` build their INSERT column list from a module-level tuple, so a column present in the tuple but missing from the DB fails the whole (atomic) write.
- **The new discovery/research tables follow the additive pattern exactly** — new FK'd tables, `ON DELETE CASCADE` where per-lead-meaningful, no change to `leads`/`campaign_runs`. `lead_sources.lead_id` → `leads(id)` CASCADE; `lead_sources.run_id` → `lead_discovery_runs(id)` SET NULL (a run's deletion shouldn't erase provenance). `lead_research_results.session_id` CASCADE, `.lead_id` SET NULL; `lead_research_evidence.result_id` CASCADE. `save_research_result` writes the result row + all its evidence rows in one transaction (no partial provenance). List columns (`sources_planned`) are JSON-encoded like the other `_JSON_ARRAY_COLS`.

## Rules

1. **New tables are additive**, FK'd to `leads` with explicit `ON DELETE CASCADE` when the row is meaningless without its lead — verify this with a real delete test, not just by reading the DDL.
2. **Never modify an existing table's meaning in a way that breaks a current reader** — add a new column/table instead of repurposing an existing one.
3. **New schema goes in `_SCHEMA_SQL` for fresh installs, plus an additive migration for existing installs** (`_add_col_if_missing`-style) — both paths must be updated together or existing users' DBs diverge from fresh ones.
4. **Check whether a new sensitive/status column needs a single choke-point function** (like `set_lead_stage`) rather than being writable from N call sites — especially if it interacts with dedup, cascade, or the outreach safety rules.
5. **If a new table looks similar to an existing one, check whether writing to it would break something downstream** (the `generated_messages`-vs-`messages` lesson) before reusing an existing table.
6. **Test cascade-delete explicitly** for any new per-lead table — this exact class of bug has shipped before.

## Architecture Guidance

Schema changes belong in `database.py` alongside the existing DDL/migration helpers — not in a separate migrations framework/tool. This is a deliberate simplicity choice for a single-file SQLite app; don't introduce Alembic or similar for one feature.

## Implementation Guidance

When adding a column to an existing hot table (`leads`, `campaign_runs`), use the additive `_add_col_if_missing` pattern so existing installs upgrade cleanly on next startup without a manual migration step.

## Testing Requirements

- New table: a CRUD test plus a cascade-delete test (insert lead + child row, delete lead, confirm child row is gone) — follow `tests/test_database_intelligence_schema.py`'s existing pattern.
- New unique/dedup constraint: a test inserting a genuine duplicate and confirming the expected merge/reject behavior, not a silent second row.
- New migration: a test (or at minimum a manual check) confirming it's idempotent — running it twice against an already-migrated DB doesn't error.

## Security Considerations

Never store a secret in plaintext in a new table — see `autolead-security-and-secrets`'s `secrets_crypto.py` pattern.

## Performance Considerations

Add an index for any new column used in a `WHERE`/`ORDER BY` on a table that can grow large (`leads`, `campaign_log`, `research_evidence`) — check existing index definitions in `_SCHEMA_SQL` for the pattern.

## Failure Modes

| Mistake | Fix |
|---|---|
| New per-lead table with no `ON DELETE CASCADE`, discovered when lead deletion crashes | Add the cascade constraint and test delete explicitly, matching the `replies` table's fix |
| Repurposing `messages` for a new draft type | Use a new table — see why `generated_messages` exists separately |
| Schema added to `_SCHEMA_SQL` but no migration for existing installs | Add both together |
| New status column writable from many call sites with no central function | Consider a `set_lead_stage`-style choke-point if it's safety/dedup-relevant |

## Verification Checklist

- [ ] New table is additive, doesn't repurpose an existing one
- [ ] `ON DELETE CASCADE` present and tested where the row is per-lead
- [ ] Both `_SCHEMA_SQL` (fresh installs) and an additive migration (existing installs) updated together
- [ ] Dedup/unique-constraint behavior tested with a real duplicate
- [ ] Index added for any new large-table filter/sort column

## Related Skills

`autolead-backend-architecture`, `autolead-discovery-and-source-adapters`, `autolead-browser-research-agent`, `autolead-lead-intelligence-and-scoring`

---
Version: 1.1
Scope: AutoLead-manager
Last reviewed: 2026-08-30 (Discovery Planner + Browser Research Agent additions)
