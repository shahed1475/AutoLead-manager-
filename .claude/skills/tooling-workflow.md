# AutoLead Tooling Workflow

## Status as of 2026-08-25

**Only the `superpowers` plugin is installed** (v6.2.0, user-scoped). **`gstack` is not installed anywhere on this machine** — checked the project, user config, and plugin cache; `/office-hours`, `/plan-ceo-review`, `/plan-eng-review`, `/qa`, `/browse`, `/review` (gstack's version) do not exist here. If `gstack` is genuinely available in some other environment this project runs in, use it directly and this substitution table doesn't apply — otherwise, use the right column.

| gstack step (spec'd, not installed) | Substitute actually available | Notes |
|---|---|---|
| `/office-hours` (product exploration) | `superpowers:brainstorming` | One question at a time, propose approaches, get approval before designing |
| `/plan-ceo-review` (product/UX review) | Independent review pass — a fresh-context `Plan` or `general-purpose` agent reviewing the design doc before implementation | No installed equivalent; this is the closest honest substitute |
| `/plan-eng-review` (architecture review) | `Plan` agent (architect role) reviewing the design for feasibility/risk before coding | Same as above |
| `/review` (code review) | `code-review` skill | Actually installed, use directly — supports low/medium/high/max/ultra effort levels |
| `/qa` / `/browse` (browser QA) | `claude-in-chrome` or Playwright MCP tools against the running local app | Actually installed; drive the real running app, don't just read code |

## Recommended workflow for major lead-gen work

1. **`superpowers:brainstorming`** — one sub-project/stage at a time (per `autolead-lead-generation-architecture`'s pipeline stages). Produces a spec in `docs/superpowers/specs/`.
2. **Independent design review** — a fresh-context agent reviews the spec against `autolead-lead-generation-architecture` and `autolead-outreach-safety` specifically (the two highest-stakes constraints: don't duplicate what exists, never touch the sending path).
3. **`superpowers:writing-plans`** — turn the approved spec into an implementation plan.
4. **`superpowers:subagent-driven-development`** or direct implementation — small, verified changes, one stage at a time.
5. **`code-review` skill** — after implementation, before merge.
6. **`claude-in-chrome`/Playwright** — real browser QA against the running app (backend on :8000, frontend on :5173) for anything with a UI surface.
7. **`autolead-verification-before-completion`** — final gate before claiming done.

## Minimum-appropriate-skill-set rule

Don't invoke every step for every change. A one-line bug fix needs `autolead-systematic-debugging` → the relevant subsystem skill → `autolead-verification-before-completion`, not a full brainstorming → design-review → plan cycle. Reserve the full workflow above for genuinely new features or architecture-level changes, matching `superpowers:using-superpowers`'s own skill-priority guidance (process skills before implementation skills, scaled to the size of the change).
