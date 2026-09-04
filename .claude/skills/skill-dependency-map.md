# AutoLead Skill Dependency Map

How the 15 skills relate. "Depends on" means: read the parent first, it establishes context the child assumes.

```
autolead-lead-generation-architecture   (master map — read first for any lead-gen work)
├── autolead-discovery-and-source-adapters
│   ├── autolead-browser-automation        (only when a source needs a real browser)
│   └── autolead-reliability-and-background-jobs   (JobQueue, campaign engines)
├── autolead-browser-research-agent        (the backend/research_agent/ subsystem)
│   ├── autolead-browser-automation        (BrowserController specifics, anti-evasion + untrusted-content line)
│   ├── autolead-ai-llm-engineering        (two-LLM-role pattern, own JSON parser, never-raises contract)
│   └── autolead-reliability-and-background-jobs   (JobQueue handler, cooperative cancellation)
├── autolead-lead-intelligence-and-scoring
│   └── autolead-ai-llm-engineering        (every LLM call routes through here)
└── autolead-outreach-safety               (terminal node — everything hands off here, nothing extends past it)

autolead-backend-architecture             (plumbing — routers, settings layer)
├── autolead-database-and-migrations
└── autolead-security-and-secrets

autolead-frontend-architecture
└── autolead-product-ux

autolead-systematic-debugging             (cross-cutting — pulls in whichever subsystem skill matches the bug)
autolead-verification-before-completion   (cross-cutting — terminal step for every task)
```

## Notes

- `autolead-lead-generation-architecture` is the entry point for the entire lead-gen domain — it exists specifically so you don't need to guess which of the lead-gen sub-skills applies.
- The lead-gen domain now has **three distinct subsystems**, each with its own skill: discovery/scraping (`autolead-discovery-and-source-adapters`), deep browser research (`autolead-browser-research-agent`), and opt-in sales intelligence (`autolead-lead-intelligence-and-scoring`). They share the evidence/provenance *pattern* and the `ai_brain` LLM dispatch but have separate packages, separate DB tables, and no cross-imports. A task usually touches exactly one — the master map says which.
- `autolead-outreach-safety` is intentionally a dead end in the dependency graph: every lead-gen path terminates there, and it never hands off to anything further. That asymmetry is deliberate — it's the boundary past which the existing Phase 4 system, not new lead-gen code, is responsible.
- `autolead-verification-before-completion` and `autolead-systematic-debugging` aren't subsystem-specific — they compose with whichever subsystem skill(s) a task actually touches, per `project-skill-router.md`.
- `autolead-security-and-secrets` is referenced by nearly every other skill (secrets, redaction, auth model) rather than nesting under one parent — treat it as a standing constraint, not a leaf.
