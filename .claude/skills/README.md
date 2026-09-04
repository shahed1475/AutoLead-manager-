# AutoLead Project Skills

A project-specific `.claude/skills/` system for AutoLead-manager, built 2026-08-25 after inspecting the actual repository (not the docs, not memory — the code), refreshed 2026-08-30 when the Discovery Planner and Browser Research Agent work landed in the working tree. 15 skills, grouped below. See `project-skill-router.md` for "what task → which skills", `skill-dependency-map.md` for how they relate, and `development-rules.md` for the cross-cutting rules that apply regardless of which skill you're using.

Each skill lives at `.claude/skills/<name>/SKILL.md` and is auto-discoverable by name (`autolead-<topic>`). Claude Code surfaces these automatically when relevant — you don't need to invoke them by path.

## CORE

| Skill | Purpose | When to use | Depends on | Priority |
|---|---|---|---|---|
| `autolead-backend-architecture` | FastAPI module layout, settings-layer convention, no-ORM DB access | New router, new backend package, new config | — | High |
| `autolead-database-and-migrations` | SQLite schema, dedup/cascade conventions | New table/column, migration, dedup/cascade bug | — | High |
| `autolead-frontend-architecture` | React/Vite/Tailwind stack, existing pages/components, API client shape | New page/component, new frontend API call | — | High |
| `autolead-systematic-debugging` | Reproduce→root-cause discipline + known landmines (dual campaign engines, legacy scraper.py) | Any bug, test failure, unexpected behavior | — | Critical |
| `autolead-verification-before-completion` | Exact commands + checklist that define "done" | Before claiming any fix/feature complete, before commit | — | Critical |

## PRODUCT

| Skill | Purpose | When to use | Depends on | Priority |
|---|---|---|---|---|
| `autolead-product-ux` | Progressive disclosure, states, honesty-in-AI-copy principles | New user flow/form/page, presenting AI findings or progress | `autolead-frontend-architecture` | Medium |

## LEAD GENERATION

| Skill | Purpose | When to use | Depends on | Priority |
|---|---|---|---|---|
| `autolead-lead-generation-architecture` | Master pipeline map + build-status (what's live vs. planned) | Any lead-gen task — read this first | — | Critical |
| `autolead-discovery-and-source-adapters` | 9 scrapers, Discovery Planner + adapter registry + merge-dedup (all now built), Quick Search, Campaign query variants | Discovery, Lead Search, source selection | `autolead-lead-generation-architecture` | High |
| `autolead-browser-research-agent` | `backend/research_agent/` — the OBSERVE/DECIDE/ACT/VALIDATE Playwright+Ollama loop, action registry, evidence/status model, budgets, geo expansion, `/api/research-agent` | Any `backend/research_agent/` change, the Research Agent page's backend contract | `autolead-lead-generation-architecture`, `autolead-browser-automation`, `autolead-ai-llm-engineering` | High |
| `autolead-lead-intelligence-and-scoring` | Website analysis, pain points, opportunities, scoring formula | Research/analysis/scoring work | `autolead-lead-generation-architecture`, `autolead-ai-llm-engineering` | High |
| `autolead-outreach-safety` | Phase 4 protection — single sending path, approval, opt-out | Anything that sends, drafts, or approves a message | `autolead-lead-generation-architecture` | Critical |

## AI

| Skill | Purpose | When to use | Depends on | Priority |
|---|---|---|---|---|
| `autolead-ai-llm-engineering` | `ai_brain.py` conventions, structured-output/retry pattern, content-quality gate | Any LLM call, new AI agent, malformed/hallucinated output | — | High |

## BROWSER

| Skill | Purpose | When to use | Depends on | Priority |
|---|---|---|---|---|
| `autolead-browser-automation` | Selenium/Playwright usage boundaries, the fingerprint-normalization-vs-evasion line, untrusted-web-content rule | New/changed browser-driven scraper or research feature | — | High |

For the Browser Research Agent specifically (`backend/research_agent/`), start at `autolead-browser-research-agent` (LEAD GENERATION group) — it pulls in this skill for the browser-controller specifics.

## SECURITY

| Skill | Purpose | When to use | Depends on | Priority |
|---|---|---|---|---|
| `autolead-security-and-secrets` | Actual auth model (single-user, no RBAC), secrets/settings layering, redaction | Credentials, new endpoint, exports, auth | — | Critical |

## PERFORMANCE / INFRASTRUCTURE

| Skill | Purpose | When to use | Depends on | Priority |
|---|---|---|---|---|
| `autolead-reliability-and-background-jobs` | The two-campaign-engines landmine, `JobQueue`, idempotency, Docker | Campaign execution, scheduling, queues, background jobs | — | Critical |

## Not created (and why)

- **Separate `browser-research-agent` vs `playwright-browser-research` vs `python-automation` vs `ollama-local-agent` vs `evidence-provenance` vs `lead-research-validation` skills** (as proposed in an 2026-08-30 skill-setup brief) — the Browser Research Agent's browser/LLM/evidence/validation concerns are one tightly-coupled subsystem best read as one map (`autolead-browser-research-agent`), with the cross-cutting rules living where they already do (`autolead-browser-automation` for the anti-evasion + untrusted-content line, `autolead-ai-llm-engineering` for the Ollama/structured-output pattern, `autolead-reliability-and-background-jobs` for async/JobQueue/cancellation, `autolead-verification-before-completion` for the test bar). Splitting them would duplicate ~60% of each file.
- **PostgreSQL / Redis / multi-tenant / RBAC / OAuth skills** — not present in the codebase. A 2026-08-06 planning memory describes a SaaS-transformation mission for these, but it was never executed; every commit since stayed SQLite/single-tenant. Don't build a skill for infrastructure that doesn't exist — revisit if that mission is actually picked up.
- **n8n / webhook / workflow-automation skill** — no n8n integration found anywhere in the repo.
- **CI/CD / GitHub Actions skill** — no `.github/workflows` exists; verification is manual (`pytest -q`, `npm run build`), documented in `autolead-verification-before-completion` instead of a dedicated infra skill.
- **Per-technology skills** (a separate file for Selenium vs. Playwright vs. BeautifulSoup, or for each of the 9 scrapers) — grouped into `autolead-browser-automation` and `autolead-discovery-and-source-adapters` per the "don't overcreate" instruction; the engineering guidance is genuinely shared across these, not source-specific.
- **A dedicated Docker/deployment skill** — the current Docker setup is thin (SQLite-only, profile-gated, no orchestration complexity); folded into `autolead-reliability-and-background-jobs` rather than justifying its own file.

## Related docs

- `project-skill-router.md` — task → skill lookup
- `skill-dependency-map.md` — skill relationships
- `development-rules.md` — cross-cutting rules
- `tooling-workflow.md` — how to use Superpowers (and what to do since gstack isn't installed)
