# HOM as a Global SaaS — Architecture Assessment (2026-09-26)

**Question:** can HOM go from "owner + a few client workspaces on one PC" to a SaaS any business in any country can sign up for — and what's the cheapest safe path?

**Short answer:** yes, without a big-bang rewrite. Keep the per-client workspace model (it is HOM's strongest safety property), move it off the home PC, add a control plane (sign-up, billing, quotas), and make AI, maps and messaging "bring your own key". A full multi-tenant rebuild (the 2026-08-06 plan in this folder) is a *later* option, only when the numbers demand it.

---

## 1. Where HOM is today (measured, not assumed)

| Area | Today | Measured / verified |
|---|---|---|
| App | FastAPI + React, one codebase, owner and client "editions" (`backend/edition.py`) | — |
| Tenancy | **Silo model**: every client gets its own Docker stack (backend, frontend, WhatsApp engine) and its own SQLite file, run by `scripts/hom_supervisor.py` | 2 client workspaces live |
| Cost per client | Idle: backend ≈ 95 MB, WhatsApp engine ≈ 240 MB, frontend ≈ 6 MB → **≈ 350 MB**; backend may use up to 2 GB during research | `docker stats`, 2026-09-26 |
| Data per client | 0.6 MB for an active small workspace | `du workspaces/ws-2/data` |
| Host | One home PC: 6 cores, 23 GB RAM, home internet, quick Cloudflare tunnels (URLs change on restart) | `nproc`, `free` |
| AI | Local Ollama, **1 request at a time** (`ollama_max_parallel = 1`) shared by owner + all clients | `backend/config.py` |
| Updates | Commit → publish → smoke-tested roll-out to every workspace | works (3 publishes today) |
| Safety | Human approval before any send, opt-out that wins over everything, evidence for every researched fact, egress guard in client workspaces | tests + skills |

## 2. What breaks first as clients grow

1. **Availability.** A power cut or the home line takes every client down (happened 2026-09-25). Quick-tunnel links change on every restart.
2. **AI throughput.** One local model answering one request at a time is shared by every client. At ~10 active clients, research and drafting queue behind each other. This is the real ceiling today, before RAM.
3. **Memory.** ≈350 MB idle per client → roughly 40–50 idle clients on this PC, far fewer when several run research (each research browser needs 0.3–0.5 GB).
4. **Data sources at scale.** Scraping Google Maps/Search is acceptable for the owner's own use but is against Google's terms at SaaS scale — the first thing that gets a SaaS blocked or challenged.
5. **Self-service.** Clients are created by the owner by hand; no sign-up, billing, plans or usage limits.
6. **Compliance.** Global customers need per-country rules (GDPR/PECR, CAN-SPAM, CASL, Spam Act, UAE PDPL, WhatsApp opt-in), a data-processing agreement, export/delete on request, and often EU hosting.

## 3. Options

| | A. Silo in the cloud (recommended first) | B. Full multi-tenant rebuild | C. Hybrid (later) |
|---|---|---|---|
| What | Same per-client stacks, on rented servers, with a control plane in front | One shared app + Postgres, every row tagged with its organisation (the 2026-08-06 plan) | Shared control plane and worker pools; each client keeps its own database |
| Rewrite | Small: supervisor learns multiple hosts; control plane is new | Large: every table and query (55 tables), auth, every test | Medium, done step by step |
| Isolation | Strongest — separate process, DB and secrets per client | Depends on every query being right (one missed filter = a data leak) | Strong (separate databases) |
| Cost per client | Highest (≈100–350 MB each) | Lowest | Low |
| GDPR delete/export | Trivial: one folder per client | Needs careful tooling | Trivial |
| Time to first paying global client | Weeks | Months | — |

**Recommendation: A now, C when cost demands it, B probably never.** HOM's isolation is a selling point (clients' data is physically separate); B trades it away for server savings that only matter at hundreds of clients.

## 4. Roadmap

### Phase 0 — Leave the home PC (1–2 weeks)
- Rent one cloud server (16 GB RAM is plenty for 30–40 light clients); keep the home PC for development.
- **Permanent domain + named Cloudflare Tunnel** (`deploy/tunnel.env` is already supported by `start.sh`) → stable links, no URL changes.
- Nightly off-site backups of every workspace folder; restore tested.
- Slim client stacks: skip the WhatsApp engine for clients who use the Meta API (saves ≈240 MB each; clients are Meta-only already).

### Phase 1 — Self-service SaaS (1–2 months)
- **Control plane** (small separate service): sign-up with email verification, plans, billing (a global payment provider: cards, local methods, tax handling), trials, workspace creation through the existing supervisor.
- **Quotas per plan**, enforced in the workspace: searches/day, research runs, report runs, AI requests, sending limits. Needed for cost control *and* abuse prevention (a SaaS that sends outreach is a spam target).
- **Bring your own key** per workspace (settings already exist for several): AI provider (hosted models), Google Places / Foursquare / HERE for maps data, Meta for WhatsApp and social. Scraping stays available as an opt-in extra, off by default for clients.
- **Country compliance profile** per workspace: which channels are allowed, required footer and unsubscribe, consent rules, retention period; DPA and sub-processor list; one-click data export and delete.
- Multi-language UI and AI messages (the AI already writes in the lead's context; the interface is English-only).

### Phase 2 — Scale (when > ~150–200 clients or server cost hurts)
- Several servers; the supervisor places workspaces by region (EU clients on EU servers = data residency).
- Shared worker pools (research browsers, website audits, AI calls) serving many workspaces through a queue with per-client limits, instead of one browser per client.
- Reconsider Postgres only if a single database per client becomes an operational problem.

## 5. Risks to manage

| Risk | Mitigation |
|---|---|
| Platform used for spam | Quotas, sender verification (SPF/DKIM check already built), bounce auto-pause (built), human approval (built), abuse reporting and suspension |
| Scraping terms at scale | Official APIs with client keys as the default; scraping opt-in with clear responsibility |
| WhatsApp bans | Clients on the official Meta API only (already enforced); owner's Web engine stays advisory-guarded |
| AI cost | Client's own AI key, or metered hosted AI in the plan price |
| Secret handling across many clients | Per-workspace encrypted secrets (already built); control plane never stores client API keys |
| Single-person operations | Monitoring + alerts, automatic restarts, tested restores before the first paying client |

## 6. Decisions for the owner
1. **Hosting region to start** (one region first; EU region needed early if targeting EU customers).
2. **AI model for clients:** hosted AI (you pay, priced into plans) or each client's own key.
3. **Payments:** which provider (global cards + local methods), and plan prices/limits.
4. **Default data sources for clients:** official APIs only (safest for a SaaS) or scraping allowed as an opt-in.

*Related: `ARCHITECTURE.md`, `TENANT_ISOLATION.md`, `MIGRATION_STRATEGY.md` (the 2026-08-06 multi-tenant plan — kept as the option-B reference, not scheduled).*
