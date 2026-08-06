# Tenant Isolation Design

**Companion to:** `DATABASE_ERD.md` (schema), `AUTH_RBAC.md` (where `org_id` comes from per-request).

## 1. Model: Shared Database, Shared Schema, Row-Level Isolation

One Postgres database, one schema, every tenant-scoped table carries `org_id`. This is the standard, pragmatic choice for a SaaS at this stage:

| Model | Isolation strength | Operational cost | Chosen? |
|---|---|---|---|
| Shared schema + `org_id` + RLS | Strong (two independent enforcement layers, §2-3) | Lowest — one database, one migration run, one connection pool | **Yes** |
| Schema-per-tenant | Stronger | Migrations run N times (once per schema), cross-tenant admin queries harder | No — revisit only if a specific compliance requirement demands it |
| Database-per-tenant | Strongest | Connection pool per tenant, migration fan-out, highest ops cost | No — enterprise-tier concern, not foundation |

See `TDR.md` for the full trade-off record.

## 2. Enforcement Layer 1: Application-Level Scoping

Every query that touches a tenant-scoped table is filtered by `org_id`, sourced from the authenticated request's JWT claims — never from a client-supplied parameter. This is enforced structurally, not by convention: the repository layer's base query methods require an `org_id` argument (no "fetch without a tenant" code path exists for tenant-scoped tables), so a route handler that forgets to scope a query fails at the type/interface level rather than silently leaking data.

## 3. Enforcement Layer 2: Postgres Row-Level Security (defense-in-depth)

A bug in application-level scoping (a forgotten filter, a copy-pasted query) is the single most common real-world cause of cross-tenant data leaks in SaaS applications. RLS is the safety net for exactly that failure mode — it enforces isolation at the database engine level, independent of application code correctness.

Mechanism:

1. Every tenant-scoped table has `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` and a policy: `CREATE POLICY tenant_isolation ON <table> USING (org_id = current_setting('app.current_org_id')::uuid)`.
2. At the start of every request (a FastAPI dependency, running after auth resolves the caller's `org_id`), the API layer runs `SET LOCAL app.current_org_id = '<org_id>'` on the request's database transaction.
3. Even if application code executes a query with no explicit `org_id` filter, Postgres itself will only return rows matching the session's `app.current_org_id` — cross-tenant rows are invisible to the query, not just unintended-but-technically-reachable.
4. Background workers (job queue, sub-project 5) set the same session variable from the job's stored `org_id` before executing tenant-scoped work.

This is genuinely two independent layers, not one layer described twice: application scoping is correct-by-construction via the repository interface; RLS is correct-by-database-engine regardless of what application code does. Either one failing alone does not cause a leak.

## 4. What Is Not Tenant-Scoped

A small number of tables are intentionally global, not per-org: nothing in the current schema qualifies as of milestone 1 — every existing table belongs to a tenant's data. Future global tables (e.g., a `plans` table for Billing, once built) will be explicitly exempted from RLS and documented here when they're introduced.

## 5. Testing the Isolation Boundary

Milestone 1's test suite includes isolation-specific tests, not just functional CRUD tests:

- Create two orgs, seed each with leads, verify org A's authenticated context can never retrieve org B's rows via the repository layer.
- The same test repeated with RLS as the only enforcement (application-layer filter deliberately omitted in the test query) to prove layer 2 holds independently of layer 1.
- A negative test: attempting to `SET LOCAL app.current_org_id` to an org the authenticated user has no membership in is rejected before the query runs (checked against `ORG_MEMBERSHIPS`, not just trusted from a client-supplied value).

## 6. Migration of Existing Data

The current single-tenant SQLite database has no `org_id` concept at all — every row implicitly belongs to "the one user." The migration (`MIGRATION_STRATEGY.md`) creates a single `ORGANIZATIONS` row (tenant #1) and backfills every existing row's new `org_id` column to that row's id before the column is made `NOT NULL` and RLS is enabled. No existing data is reachable by any other tenant, by construction — tenant #1 is the only tenant that exists until real signups happen.
