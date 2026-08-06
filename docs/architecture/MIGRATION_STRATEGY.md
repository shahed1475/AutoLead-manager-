# Migration Strategy

**Companion to:** `DATABASE_ERD.md`, `TENANT_ISOLATION.md` §6.

Two distinct kinds of migration happen in this initiative, with different reversibility guarantees. Conflating them is a common source of overclaiming — this document is explicit about which guarantee applies where.

## 1. Schema Migrations (ongoing, every milestone) — genuinely reversible

**Tool: Alembic**, replacing the current hand-rolled `_SCHEMA_SQL` executescript + `_run_migrations` sweep approach in `database.py`.

- Every schema change is a versioned Alembic migration file with both `upgrade()` and `downgrade()` functions.
- `downgrade()` is written and tested for every migration before it's considered done — not an afterthought. A migration without a working `downgrade()` does not merge (this is what principle 4, "every migration is reversible," concretely means in practice).
- Migrations run automatically on backend startup in local/dev (matching today's "schema migrations applied" startup behavior), and via an explicit deploy-time step in any future production environment (never silently auto-applied against production data without an operator decision).
- Alembic's own version table (`alembic_version`) is the single source of truth for "what schema state is this database in" — replacing the current app's own migration-tracking approach.

## 2. The One-Time SQLite → Postgres Data Migration — reversible by preservation, not by automated rollback

This is a **one-time, one-directional data copy**, not an ongoing schema migration. Being precise about what "reversible" means here matters:

- The **source SQLite file is never modified or deleted** by the migration script. It remains a complete, untouched, working copy of every existing record for as long as anyone wants to keep it. If the Postgres migration is ever abandoned, the original single-user app can resume running against that untouched SQLite file exactly as it does today — nothing was ever a one-way door.
- The migration is **not** an automated `downgrade()` that reconstructs SQLite from Postgres — building and maintaining a bidirectional sync for a one-time cutover would be real engineering effort spent on a path that's only useful if the entire Postgres migration is abandoned, which is a business decision, not a technical rollback. If that ever happens, the untouched SQLite file (previous bullet) is the rollback.
- The script is **idempotent and re-runnable against a fresh Postgres database**: it can be run against an empty Postgres instance as many times as needed during development/testing without needing to "undo" a partial run first (each run starts from a clean Postgres schema via Alembic).

### Migration steps

1. Run all Alembic migrations against a fresh Postgres database (creates the full new schema, empty).
2. Create one `ORGANIZATIONS` row — this becomes tenant #1.
3. Create one `USERS` row for the existing local user (email/password prompted at migration time, or defaulted for local-only use per `AUTH_RBAC.md` §4) and one `ORG_MEMBERSHIPS` row (`role=owner`) linking them.
4. For every existing SQLite table, in dependency order (parents before children — `leads` before `enriched_data`/`scores`/`company_profiles`, `company_profiles` before `research_evidence`/`decision_makers`, etc.): read all rows via the existing `backend/database.py` SQLite connection, write them into the corresponding Postgres table via the new SQLAlchemy models, setting `org_id` to tenant #1's id on every row.
5. Verify row counts match between source and destination, table by table, before declaring the migration complete.
6. Only after verification: the application's `DATABASE_URL` config is switched from the SQLite path to the Postgres connection string. The SQLite file stays on disk, untouched, as the rollback point described above.

### Testing the migration

- A test fixture with representative data in every existing table (including edge cases already known to matter from this project's history: leads with no website, JSON-array columns, cascading-delete relationships) is migrated end-to-end in CI, and the destination Postgres data is asserted equal to the source.
- The migration script is run against a copy of the real 178+-lead database as part of milestone 1's manual verification step, with row counts and a spot-check of specific records confirmed before it's considered done.

## 3. What This Means for `database.py`

The current file becomes the SQLite-side **source** for the one-time migration (step 4 above reads through it) and is otherwise retired once the migration is complete and the application's data layer is fully SQLAlchemy-based. It is not deleted before that point — per principle 5 (no dead code) read the other direction: it's still live code serving the still-running single-user SQLite path until the cutover happens, not dead code kept "just in case."
