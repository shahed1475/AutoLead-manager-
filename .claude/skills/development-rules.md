# AutoLead Development Rules

Cross-cutting rules that apply regardless of which skill a task falls under. Individual skills restate the ones relevant to their domain; this is the full list in one place.

1. **Inspect before changing.** This codebase has duplicate/legacy paths (two campaign engines, `scraper.py` vs `scrapers/__init__.py`) more often than most — read the actual code path that runs before editing, don't assume from a file name.
2. **Reuse existing architecture.** A new scraper source, LLM call, sender, or DB-access pattern almost certainly has an existing shape to follow — check `autolead-discovery-and-source-adapters`, `autolead-ai-llm-engineering`, `autolead-outreach-safety`, `autolead-backend-architecture` respectively before writing something new.
3. **Avoid unnecessary rewrites.** The explicit decision for the Discovery Planner work was to wrap the 9 existing scrapers, not rewrite their internals — this principle generalizes: prefer wrapping/extending working code over rewriting it, even when a "cleaner" version is tempting.
4. **Preserve backward compatibility.** Existing API responses, DB columns, and frontend expectations keep working after your change — additive, not breaking.
5. **Write tests before claiming completion.** See `autolead-verification-before-completion` for the exact bar.
6. **Verify database changes** by actually running the migration against a real or throwaway DB, not just reading the DDL. See `autolead-database-and-migrations`.
7. **Verify background jobs** by triggering them, not by reading the registration code. `JobQueue` being started at boot doesn't mean anything is actually enqueued — check.
8. **Verify browser automation** against a real (or realistically mocked) run — Selenium/Playwright failures are common enough here (Google/Bing bot detection, Cloudflare blocks) that "the code looks right" isn't evidence.
9. **Verify API integrations** end-to-end (request → real response), not just that the client code compiles.
10. **Never expose secrets** — no hardcoded credentials, no unredacted secret fields in API responses or exports. See `autolead-security-and-secrets`.
11. **Never bypass security controls** — no CAPTCHA-solving, no anti-bot evasion, no access-control bypass, ever, regardless of how much easier it would make a scraper.
12. **Never claim success without evidence.** A command must actually have been run this session with output you read, per `autolead-verification-before-completion`.
13. **Never silently change business logic.** Scoring formulas, dedup rules, opt-out enforcement, and message-structure rules are all deliberate product decisions — changing their behavior is a decision to surface, not a side effect to slip in.
14. **Preserve existing Phase 4 safety behavior** — approval, opt-out, terminal statuses, follow-up controls, cancellation protection. See `autolead-outreach-safety`; this is the single most protected area of the codebase for good reason.
15. **Prefer deterministic logic over LLM guessing.** If a fact is programmatically checkable (HTTPS present, contact form exists, email format valid), check it in code. Use AI where reasoning over ambiguous/unstructured input is genuinely required (classifying intent, summarizing findings, drafting message wording) — not as a substitute for a regex or a DOM check.
16. **One subsystem at a time.** Multi-stage features (the lead-gen pipeline especially) get built stage by stage, each with its own spec → plan → implementation cycle, matching the existing `docs/superpowers/plans/` discipline — don't batch multiple stages into one uncontrolled pass.
