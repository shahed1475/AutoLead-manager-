# AutoLead — Roadmap

**Companion to:** `ARCHITECTURE.md`. This document sequences the work; it does not re-explain the reasoning (see `TDR.md`) or the target state (see the other design docs).

**Rule governing this roadmap:** each milestone must be production-ready — tested, reviewed, documented — before the next one starts. No milestone begins until the previous one's own spec/plan/implementation/review cycle is complete.

---

## Near-term: SaaS Foundation Initiative (active build)

| # | Milestone | Depends on | Produces |
|---|---|---|---|
| 1 | **Database & multi-tenancy foundation** | — | Postgres schema, SQLAlchemy + Alembic, `organizations`/`users`/`org_memberships` tables, `org_id` on every existing table, reversible SQLite→Postgres data migration (existing data becomes tenant #1) |
| 2 | **Authentication & authorization** | 1 | Signup/login, JWT + refresh tokens, org creation/invites, roles (owner/admin/member), permission-check dependencies |
| 3 | **Infrastructure abstraction layer** | 2 (needs org context to be tenant-aware) | `LLMProvider`, `MessageChannel`, `StorageBackend`, `JobQueue`, `EventPublisher` protocols + local implementations |
| 4 | **WhatsApp Business Cloud API migration** | 3 | Desktop-automation replacement, per-org WhatsApp Business config, webhook receiver, template message handling |
| 5 | **Background jobs redesign** | 3 | Tenant-scoped campaign/enrichment/follow-up jobs on the Postgres-backed queue, replacing the dormant `queue_worker.py` + global `APScheduler` |
| 6 | **API versioning** (`/api/v1/`) | 1, 2 | Versioned routes across all routers, frontend updated in lockstep |
| 7 | **Structured logging & monitoring** | 2 (needs org_id/user_id for correlation) | JSON structured logs, `/health` + `/metrics` endpoints, error-tracking hook |
| 8 | **Feature flags** | 1, 2 | `feature_flags` table, evaluation service, admin API, global + per-org overrides |
| 9 | **Audit logs** | 2 | `audit_log` table, write-path integration on mutating actions, read API |
| 10 | **Multi-environment config & Docker optimization** | 1–9 (needs the real multi-service stack to configure) | dev/staging/prod profiles, docker-compose overlays, multi-stage Dockerfiles |
| 11 | **Production deployment readiness** | 1–10 | Deployment-agnostic runbook, generic prod Compose stack, cloud deployment guides, backup/restore procedure |

Each milestone gets its own brainstorm → spec → implementation-plan → build → review cycle, per `superpowers:brainstorming` → `superpowers:writing-plans` → `superpowers:subagent-driven-development`, exactly as sub-project 1 of the Sales Intelligence Engine was built.

## Standing principle: AI Agent Framework growth

Not a numbered milestone — a rule applied whenever agent-related work happens (this includes the rest of the Sales Intelligence Engine sub-projects already noted in `project_saas_transformation` memory: ICP Match, Decision Maker Discovery, Contact Verification, Personalization, etc.). Each new agent extends `backend/intelligence/` only with what it concretely needs beyond what exists; shared framework capability is extracted once a pattern has appeared in 2-3 real agents. See `AI_ARCHITECTURE.md`.

## Long-term: Reserved Modules (named, not scheduled)

These exist in the module map (`ARCHITECTURE.md` §4.2) so nothing built now blocks them, but none are folders, interfaces, or code yet. Each becomes its own milestone, inserted into this roadmap, when it's actually prioritized:

- **Billing & Subscriptions** — needs a real pricing model decided first (seat-based? usage-based? per-tenant plan tiers?) before any interface makes sense.
- **CRM** — pipeline/deal-stage management beyond the current lead table.
- **Marketing Automation** — cross-channel automation flows beyond the current campaign/follow-up sequences.
- **AI Agent Platform** (as a standalone, user-facing surface — e.g. a UI for configuring/composing agents) — distinct from the AI Agent *Framework* (§ above), which is internal and already growing.
- **Integrations** — third-party connectors (CRMs, ad platforms, calendars, etc.).
- **Analytics** — cross-campaign/cross-org reporting beyond the existing dashboard.
- **Marketplace** — third-party agents/templates/integrations distribution.
- **Public API** — external, versioned, rate-limited, API-key-authenticated access for customers to integrate against (distinct from the internal `/api/v1` the frontend uses).
- **Enterprise deployment** — SSO/SAML, dedicated-tenant deployment options, compliance certifications — evaluated once there's a concrete enterprise customer need.

## Definition of "production-ready" for a milestone

A milestone is not done until:
1. Its own design doc is written and (for anything touching the documents above) those documents are updated to match reality.
2. Tests exist and pass (unit + integration where the milestone touches persistence or external services).
3. It has been through task-level and whole-branch code review (per `superpowers:subagent-driven-development`).
4. Any migration included is reversible and has been exercised (`upgrade`/`downgrade`, or the data-migration rollback path).
5. Nothing it touches regresses existing behavior — verified by the existing test suite plus manual smoke testing of the affected area.
