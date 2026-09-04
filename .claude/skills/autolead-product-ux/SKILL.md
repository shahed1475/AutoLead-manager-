---
name: autolead-product-ux
description: Use when designing a new user flow, form, or page in AutoLead, or deciding how to present automation complexity, progress, or AI-generated content to the user.
---

# AutoLead Product / UX Principles

## Purpose

AutoLead's core UX challenge is making heavy backend automation (9 scrapers, multiple AI agents, background jobs) feel like two simple actions: search for leads, or start a campaign. This skill captures the product principles that keep it that way.

## When to Use

Designing any new user-facing flow, especially the Lead Search / Lead Search Campaign entry points, or any screen presenting AI-generated content, scores, or long-running progress.

## When NOT to Use

Pure backend/API work with no UI decisions involved.

## Project Context

- The product's own spec is explicit: the user should never have to understand scrapers, browser sessions, source adapters, queues, AI agents, or verification providers. The interface shows "Find Leads" or "Create Campaign" — complexity lives entirely behind the scenes.
- Existing progress-visualization pattern: `Campaign.jsx`'s `PIPELINE_STEPS`/`STAGE_TO_STEP` turns backend stage strings into a human-readable step tracker with a live SSE log feed underneath — this is the reference pattern for any new long-running operation, not a spinner with no detail.
- `LeadSearch.jsx` (Quick Search) and `ResearchAgent.jsx` (Browser Research Agent) now exist as their own pages/nav entries; `Campaign.jsx` is relabeled "Lead Search Campaign". These poll their `/api/discovery` and `/api/research-agent` status endpoints (no SSE) — the research agent surfaces `current_action` / `current_business` / `current_query` so the progress view can show what the agent is doing right now, per the "real progress view, not a spinner" rule.
- Research results carry per-field evidence (`lead_research_evidence` — source URL, snippet, confidence, status like `NOT_FOUND` / `SECURE_WEB_FORM` / `UNCONFIRMED`). Show the status and let the user open the evidence; a blank field is "we looked and didn't find it", not an error — display it that way.
- Existing loading/empty/error primitives (`Skeleton.jsx`, `EmptyState.jsx`, `ErrorState.jsx`) are already built and used across every page — a new screen with no loading/empty/error state is a regression relative to the rest of the app, not a neutral omission.
- Score/status conveyed via consistent colored badges (`ScoreBadge.jsx`, the `border-{color}-500/50 bg-{color}-500/15 text-{color}-300` pattern) — HOT/WARM/COLD, status pills, etc. all read the same way across pages.
- The spec for AI-derived findings is explicit about epistemic honesty: "I couldn't find an online booking option" not "you don't have online booking." This is a UX/copywriting rule as much as a technical one — any screen presenting AI/heuristic findings should phrase them as observations with a confidence level, not as absolute claims.

## Rules

1. **Two entry points, not a source/option picker.** Lead Search = 3 fields (what/where/how many), no source selection. Lead Search Campaign = the existing richer form, but source selection stays optional/automatic where the Discovery Planner can decide — don't add a screen that makes the user choose between 9 scrapers.
2. **Long-running operations get a real progress view** (step tracker + live log), never a bare spinner — matching `Campaign.jsx`'s existing pattern.
3. **Every new screen needs loading, empty, and error states** using the existing primitives — not a blank page while data loads, not silence on zero results, not a raw stack trace on failure.
4. **AI/heuristic findings are phrased as observations, not absolute claims** — "couldn't find X" not "you don't have X" — anywhere a finding, score reason, or evidence snippet is shown to the user.
5. **Editable-before-send, always.** Any AI-generated message (a draft, a follow-up) must be shown as editable text with Generate/Regenerate/Edit/Approve actions, never auto-sent from the UI — this mirrors the backend approval requirement in `autolead-outreach-safety` and the UI must not create a false impression that something was sent when it was only drafted.
6. **Don't over-design.** Match the existing dark, dense, data-forward aesthetic (Tailwind `brand` scale, card-with-border sections) — a new page with a substantially different visual language will look bolted-on.

## Architecture Guidance

New complex flows (multi-stage research, campaign progress) render as a step tracker over the backend's actual stage enum, not a UI-invented progress percentage disconnected from real state — this keeps the UI honest about what's actually happening and cheap to keep in sync (see `autolead-frontend-architecture`'s rule about mirroring backend stage/status changes).

## Implementation Guidance

When adding a new lead-detail section (evidence, buying signals, opportunity map, sales brief), follow the existing `EnrichmentDrawer`/`StageHistoryPanel` pattern: a drawer or card section that lazily loads its own data via React Query, with its own loading/empty state, rather than blocking the whole lead-detail view on every section's data being ready.

## Testing Requirements

No automated UX/visual test tooling exists — verification is manual browser walkthrough of the golden path plus the empty/error/loading states specifically (not just the happy path with data already present).

## Security Considerations

Don't surface raw scraped HTML or unsanitized external content directly in the UI (see `autolead-frontend-architecture`'s XSS note).

## Performance Considerations

Progressive disclosure matters for perceived performance too — load the lead list/table first, lazy-load per-lead detail sections (research, evidence, messages) on demand rather than eagerly fetching everything for every row.

## Failure Modes

| Mistake | Fix |
|---|---|
| New Lead Search form exposes a source-selection dropdown | Remove it — sources are Planner-selected, not user-selected, per the locked design |
| New long operation shows only a spinner | Build a step tracker over the real backend stage, matching `Campaign.jsx` |
| AI finding rendered as "Your website has no booking system" | Rephrase as "couldn't find an online booking option" |
| New draft-message UI has a direct "Send" button | Route through Approve → existing outreach flow; never a direct send from a new screen |
| New page skips empty/error states because "there's usually data" | Add them anyway — every existing page has them |

## Verification Checklist

- [ ] No manual source/adapter/queue selection exposed to the user where the Planner should decide
- [ ] Long operation has a real progress view, not a bare spinner
- [ ] Loading/empty/error states present and manually verified
- [ ] AI/heuristic findings phrased as observations, not absolute claims
- [ ] No UI path sends a message directly, bypassing approval

## Related Skills

`autolead-frontend-architecture`, `autolead-browser-research-agent`, `autolead-outreach-safety`, `autolead-lead-generation-architecture`

---
Version: 1.1
Scope: AutoLead-manager
Last reviewed: 2026-08-30 (Lead Search / Research Agent pages now exist; evidence-visibility note added)
