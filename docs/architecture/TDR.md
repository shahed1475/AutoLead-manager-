# Technical Decision Records

Format per decision: Context, Decision, Trade-offs considered, Consequences. Numbered for reference from other documents and future discussion — not a changelog; a record of *why*, kept even after a decision is later superseded (a superseded record is marked, not deleted).

---

## TDR-001: Multi-tenant SaaS over single-tenant desktop app

**Context:** The project previously had explicit, validated decisions to stay SQLite-only, single-user, desktop-oriented (see `feedback_validated_architecture_choices` memory). This initiative reverses that.

**Decision:** Build toward true multi-tenant SaaS — many organizations sharing one deployed instance with real data isolation.

**Trade-offs considered:** Staying single-tenant is simpler and requires no reversal of prior decisions, but doesn't match the stated goal of an AI Marketing OS serving multiple businesses.

**Consequences:** Cascades into TDR-002 through TDR-006 below. The existing single-user product must keep working (principle 1) — it becomes "a deployment with one tenant," not a separate code path.

---

## TDR-002: PostgreSQL over SQLite as primary database

**Context:** SQLite (a single file, no server) was previously chosen specifically because the app was single-user/desktop. Multi-tenant SaaS with concurrent writes across many orgs needs a real database server.

**Decision:** PostgreSQL, both in production and local development (via Docker Compose) — not a dual-engine (SQLite locally / Postgres in prod) split.

**Trade-offs considered:** A dual-engine approach would keep local dev at zero setup cost, but risks behavior diverging between what's tested locally and what runs in production (exactly the kind of divergence 12-factor's "dev/prod parity" principle exists to prevent) — and SQLite's single-writer model is a poor fit for concurrent multi-tenant access regardless of environment.

**Consequences:** Requires the one-time data migration (`MIGRATION_STRATEGY.md`). Adds `postgresql` as a Docker Compose service for local dev (no cost, no external account needed).

---

## TDR-003: Shared schema + `org_id` + Row-Level Security over schema-per-tenant or database-per-tenant

**Context:** Multi-tenancy needs an isolation strategy.

**Decision:** One shared schema, `org_id` on every tenant table, enforced by application-level scoping *and* Postgres RLS as defense-in-depth.

**Trade-offs considered:** Schema-per-tenant and database-per-tenant both give stronger isolation guarantees but multiply operational cost (migrations run N times, connection pooling per tenant) for a benefit (regulatory/compliance-grade isolation) this stage doesn't need yet.

**Consequences:** Full design in `TENANT_ISOLATION.md`. Revisit only if a specific enterprise/compliance requirement demands stronger isolation for a subset of tenants — that would be an additive change (e.g., a dedicated-deployment tier), not a rework of this decision for everyone.

---

## TDR-004: SQLAlchemy (async) + Alembic over raw asyncpg + hand-rolled migrations

**Context:** The current `database.py` is a hand-rolled SQL layer with a custom migration sweep. Postgres + real reversibility (principle 4) needs proper tooling.

**Decision:** SQLAlchemy 2.0 async ORM + Alembic for versioned, reversible migrations.

**Trade-offs considered:** Raw asyncpg gives more control and less abstraction overhead, but reversible migrations become manual and error-prone, and it doesn't naturally support the repository-pattern boundary the tenant-isolation design (`TENANT_ISOLATION.md` §2) relies on. A lighter query builder (e.g. `databases`, `piccolo`) was considered — smaller ecosystem, less mature async/Alembic-equivalent tooling than SQLAlchemy.

**Consequences:** New dependencies: `sqlalchemy[asyncio]`, `alembic`, `asyncpg`.

---

## TDR-005: WhatsApp Business Cloud API over desktop automation (for the SaaS product)

**Context:** The current `whatsapp_sender.py` drives one logged-in WhatsApp Desktop session via UI automation — a previously-validated choice specifically because it avoided Meta Business setup and per-message cost for a single user. This cannot serve multiple tenants: one desktop session cannot be multiple customers' WhatsApp accounts simultaneously.

**Decision:** Migrate to the official WhatsApp Business Cloud API, with per-org phone number/business account configuration.

**Trade-offs considered:** Two alternatives were presented and explicitly declined: (a) one deployment per tenant, each with its own desktop session — avoids Cloud API cost/setup but isn't a shared multi-tenant SaaS, it's single-tenant software sold repeatedly; (b) drop WhatsApp from the SaaS tier entirely, email-only — simplest, but removes a core existing capability from the product's SaaS offering.

**Consequences:** Requires webhook infrastructure for inbound messages (replacing whatever polling/desktop-based reply detection exists today for WhatsApp specifically — email reply detection via IMAP is unaffected). Requires each tenant to complete Meta Business verification before sending. Real per-message cost, unlike the current free desktop-automation path. Scoped as its own milestone (`ROADMAP.md` #4) given the size of this migration.

---

## TDR-006: Self-hosted Ollama (swappable endpoint) over pure cloud LLM, for now

**Context:** Ollama running on one machine was the previous default, chosen for zero cost and offline capability. A shared multi-tenant server can't reasonably run a local model for many concurrent tenants without dedicated GPU hardware — which isn't budgeted yet.

**Decision:** Keep Ollama as the model, but formalize it behind a configurable-endpoint `LLMProvider` interface (`ARCHITECTURE.md` §5) so it can move from "running on the dev machine" to "running on a dedicated GPU server" with a config change, not a code change. Cloud LLM (already supported as an optional path via `ai_brain`) remains available per-org if/when needed, through the same interface.

**Trade-offs considered:** Cloud-LLM-as-default was considered and declined — the user explicitly chose to keep self-hosted Ollama despite the current lack of GPU infrastructure, prioritizing the zero-marginal-cost model over immediate multi-tenant readiness for this specific component.

**Consequences:** Until GPU infrastructure exists, concurrent multi-tenant LLM throughput is bounded by whatever machine runs Ollama — an explicit, accepted limitation for the foundation phase, not a defect to fix now.

---

## TDR-007: Postgres-backed job queue over Redis/Celery, for now

**Context:** Background jobs (campaigns, enrichment, follow-ups) need to be tenant-aware and horizontally scalable. `queue_worker.py` exists but is dormant/unwired (per prior audit).

**Decision:** A `JobQueue` interface with a Postgres-backed default implementation (`SELECT ... FOR UPDATE SKIP LOCKED`), swappable to Celery/arq + Redis later via the same interface.

**Trade-offs considered:** Celery+Redis is more battle-tested for high-scale production job processing, but requires running Redis even in local development — a new required service for a "no budget yet" constraint. `SELECT FOR UPDATE SKIP LOCKED` is a well-established pattern that is safely horizontally scalable (multiple worker processes across multiple machines can pull from the same queue without double-processing) without any new infrastructure beyond the Postgres already being added.

**Consequences:** Revisit if job volume/throughput ever actually exceeds what Postgres-backed polling can handle — the interface exists specifically so that's a swap, not a rewrite.

---

## TDR-008: Transactional outbox pattern over a message broker, for now

**Context:** "Event-driven where appropriate" was requested, without committing to new infrastructure.

**Decision:** State changes are recorded in a Postgres `events` table within the same transaction as the change, dispatched by a background worker — the transactional outbox pattern.

**Trade-offs considered:** A real broker (Kafka/RabbitMQ/SNS) gives stronger delivery guarantees and decoupling at higher scale, but is a new infrastructure dependency with no current consumer that needs it. The outbox pattern gives genuine event-driven behavior (publish without direct coupling to subscribers) using only Postgres.

**Consequences:** Dispatcher is swappable to a real broker later behind the same publish interface (`EventPublisher`, `ARCHITECTURE.md` §5) without changing how modules publish events.

---

## TDR-009: JWT + Postgres-backed refresh tokens over server-side sessions

**Context:** Current auth uses server-side session tokens (bcrypt password, session table). Multi-instance horizontal scaling needs either shared session storage or stateless verification.

**Decision:** Short-lived JWT access tokens (stateless verification) + revocable refresh tokens stored (hashed) in Postgres.

**Trade-offs considered:** Pure server-side sessions would need a shared session store (Redis, or DB-backed sessions checked on every request) to work across multiple backend instances — DB-backed sessions checked per-request has the same DB round-trip cost as what's being avoided, without JWT's stateless-verification benefit for the common case.

**Consequences:** Full design in `AUTH_RBAC.md`. Access tokens carry `org_id` + `role` claims, directly enabling the permission-check pattern in that document.

---

## TDR-010: URL-path API versioning (`/api/v1/`) over header-based versioning

**Context:** Current routes are unversioned (`/api/leads`, `/api/campaign`, etc.).

**Decision:** URL-path versioning, `/api/v1/...`.

**Trade-offs considered:** Header/Accept-based versioning is arguably more RESTfully "correct," but adds real friction for API consumers and tooling (OpenAPI docs, curl testing, browser debugging) for a benefit that doesn't materialize until there are multiple API consumers needing different versions simultaneously — not the case yet.

**Consequences:** Frontend updates to call `/api/v1/*` in lockstep with the backend change (milestone 6) — no external API consumers exist yet to break.

---

## TDR-011: Self-built feature flags over an external flag service

**Context:** Feature flags requested as a deliverable, with per-org override capability implied by the multi-tenant model.

**Decision:** A `feature_flags` table + evaluation service + admin API, built in-house.

**Trade-offs considered:** Self-hosted OSS options (Unleash, Flagsmith) offer more built-in capability (percentage rollouts, targeting rules, a UI) but are a whole additional service to run and maintain — heavier than the foundation phase needs. A hardcoded-in-config approach was considered and rejected as too limited (no per-org control, no runtime toggling).

**Consequences:** Revisit if flag-management needs grow beyond simple global/per-org boolean and percentage toggles.

---

## TDR-012: Modular monolith over microservices

**Context:** The long-term vision spans many domains (auth, orgs, lead intelligence, sales intelligence, outreach, and reserved future modules). The instruction was explicit: "I don't want a monolithic application... design the platform around independent modules."

**Decision:** A modular monolith — one deployable application, internally organized into domain modules with enforced boundaries (only call through public service-layer functions, never reach into another module's tables), extractable into real services later per-module if scale demands it.

**Trade-offs considered:** True microservices from day one give the strongest boundaries and independent scaling/deployment, but at real cost — network calls between every module, service discovery, distributed transactions, N deployments to operate — for a product with no production scale pressure and no team large enough yet to need independent deploy cadences per module. A modular monolith gets the primary benefit sought (independent testability, clear ownership, ability to add capability without rewriting the platform) at a fraction of the operational cost, while remaining extractable later.

**Consequences:** Full design in `ARCHITECTURE.md` §4 and `MODULE_DEPENDENCIES.md`. The discipline of "never reach into another module's tables" is what makes future extraction possible — this is enforced in code review, not just documentation.

---

## TDR-013: Incremental AI agent framework growth over upfront generic framework

**Context:** The long-term vision names 12 future agents and a long list of shared framework capabilities (memory, tool execution, cost tracking, etc.) they should all share.

**Decision:** Grow `backend/intelligence/` incrementally — extract shared capability once at least 2-3 real agents need it the same way, rather than designing the full generic framework now for agents that don't exist yet.

**Trade-offs considered:** Building the full framework upfront would give every future agent a consistent shape from day one, but risks guessing the abstraction wrong for requirements that don't exist yet (the standard failure mode: the third or fourth real agent needs something the speculative design didn't anticipate, forcing rework of the framework *and* every agent built against it). The existing two agents (`QualificationAgent`, `CompanyResearchAgent`) already prove the base contract (`ResearchAgent`/`AgentResult`/`EvidenceItem`) generalizes across at least two real implementations — exactly the evidence a framework should be extracted from.

**Consequences:** Full design and target capability map in `AI_ARCHITECTURE.md`. Every future agent still follows the same long-term shape (the table in that document) — the *destination* is fixed, only the *build order* is driven by real need rather than upfront speculation.

---

## TDR-014: Deployment-agnostic, 12-factor, local-first — no cloud provider chosen yet

**Context:** No budget for cloud/GPU infrastructure currently exists.

**Decision:** Every infrastructure-facing component (DB, storage, job queue, LLM endpoint) sits behind an interface with a local, zero-cost implementation today, deferring the actual cloud provider decision.

**Trade-offs considered:** Committing to a specific provider (AWS/GCP/Azure) now would let deployment tooling be more concrete/less abstract, but would be a decision made without the information (actual budget, actual customer requirements, actual team ops expertise) that should drive it — and 12-factor design means that decision, whenever made, doesn't require reworking application code.

**Consequences:** Full design in `DEPLOYMENT.md`. Milestone 11 (production deployment readiness) produces guides for multiple providers rather than committing to one.
