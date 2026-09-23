---
name: autolead-frontend-architecture
description: Use when adding or changing a React page, component, or API call in the AutoLead frontend.
---

# AutoLead Frontend Architecture

## Purpose

Documents the frontend stack, existing pages/components, and API-client conventions so new UI matches the existing design system instead of drifting.

## When to Use

Adding a new page/component, changing an existing one, or adding a new frontend API call.

## When NOT to Use

Backend-only changes with no UI surface.

## Project Context

- **Stack:** React 18 + Vite 6 + Tailwind (dark-mode class strategy, `brand` indigo color scale, Inter font) + `@tanstack/react-query` (all data fetching/mutation) + `react-router-dom` + `recharts` (charts) + `@dnd-kit/core` (drag-and-drop, Pipeline only) + `lucide-react` (icons) + `react-hot-toast` (notifications) + `clsx`.
- **No frontend test framework configured** — no vitest/jest in `package.json`. Verification is `npm run build` (catches import/syntax errors) + manual browser check. Don't claim frontend "tests pass"; there are none yet.
- **Pages** (`frontend/src/pages/`): `Dashboard.jsx` (stats/activity), `Leads.jsx` (main table, filters, CSV import/export), `Pipeline.jsx` (CRM Kanban, `@dnd-kit`), `LeadSearch.jsx` (Quick Search — `/lead-search`, polls `/api/discovery`), `ResearchAgent.jsx` (Browser Research Agent — `/research-agent`, polls `/api/research-agent`), `Campaign.jsx` (relabeled **"Lead Search Campaign"** in the nav — largest page: source selection, pipeline-stage visualization, live SSE log feed, pause/resume/stop; **a "Lead Search Campaign" feature extends this page, not replaces it**), `Inbox.jsx` (reply intent triage), `AILab.jsx` (per-lead message preview/regen), `Settings.jsx` (config + company DNA + feature toggles). `Sidebar.jsx`'s `nav` array is the route list — new pages get a `NavLink` entry there.
- **Shared components** (`frontend/src/components/`): `Sidebar.jsx`, `Topbar.jsx`, `StatCard.jsx`, `ScoreBadge.jsx`, `LeadTable.jsx`, `CampaignControls.jsx`, `CampaignHistoryTable.jsx`, `EnrichmentDrawer.jsx`/`EnrichmentCard.jsx`, `MarketingMessagesPanel.jsx`, `StageHistoryPanel.jsx`, `ViewMessagesModal.jsx`; `components/ui/`: `Skeleton.jsx`, `ErrorState.jsx`/`EmptyState.jsx`, `SortableHeader.jsx`, `DrawerPrimitives.jsx`, `ErrorBoundary.jsx`.
- **API client** (`frontend/src/api/client.js`): per-domain modules — `leadsApi`, `campaignApi`, `aiApi`, `settingsApi`, `pipelineApi`, `inboxApi`, `repliesApi`, `statsApi`, `engineApi`, `enrichApi`, `scraperApi`, `authApi`, `logsApi`, `followupsApi`, `discoveryApi` (`search`/`status`/`results`/`cancel`), `researchAgentApi` (`start`/`status`/`results`/`cancel`). New backend domains get a new module here, following the existing shape. Both new modules follow a start→poll-status→fetch-results→cancel job pattern (there is no SSE for these — the frontend polls). `lib/badges.js` has a `BROWSER_RESEARCH_AGENT` source badge/label.
- **Established visual patterns to reuse:** card-with-`border-slate-700` sections, `border-{color}-500/50 bg-{color}-500/15 text-{color}-300` badge pattern for score/status pills, `SectionCard` wrapper (Settings.jsx), progress-step pipeline visualization (`Campaign.jsx`'s `PIPELINE_STEPS`/`STAGE_TO_STEP`), route-level code splitting (lazy-loaded page chunks) with manual vendor/charts chunk splitting in `vite.config.js`.
- **Performance patterns already in place:** `useVirtualizer` (`@tanstack/react-virtual`) in `LeadTable.jsx`, `useDebouncedValue` for search, `useAnimatedNumber` for stat cards, `useResizableColumns` for table columns.

## Design system & theming (redesign 2026-09-23)

- **Identity:** warm graphite (night) / soft ivory (day) neutrals, ONE accent — pine (`brand`/`primary`). Brand mark: `components/Logo.jsx` (`Logo`, `LogoMark`, `BRAND` = HOM · Sales Growth Engine); static copies `public/brand/*.svg`, favicon `public/favicon.svg`. `public/logo.png` is the retired logo, unused.
- **Tokens:** `src/theme.css` is **generated** by `node scripts/gen-theme.mjs` — edit the script. Semantic tokens (`bg-background`, `bg-surface`, `bg-surface-elevated`, `bg-surface-muted`, `border-border(-subtle)`, `bg-primary`, `text-muted-foreground`, `success/warning/error/info`) are what new UI should use. Every palette class (`slate-*`, `emerald-*` …) is also a token so older markup follows both themes; decorative hues (blue/violet/purple/pink/sky/cyan) are desaturated and `indigo` aliases the accent — don't introduce new accent colours.
- **Primitives (`src/index.css`):** type levels `.text-display/.text-page/.text-section/.text-subheading/.text-body/.text-support/.text-meta/.text-overline`; surfaces `.surface-subtle` → `.surface-raised` (`.card` alias) → `.surface-overlay` (dialogs) — most content should sit open on the background, raise only what needs grouping or is the primary action; `.btn-primary/.btn-secondary/.btn-ghost/.btn-danger/.btn-success`, `.input`, `.label`, `.badge-*`.
- **Type:** system UI stack (SF Pro / Segoe UI Variable), Inter fallback; weights are calmed in `tailwind.config.js` (bold = 640); no emoji as icons (lucide only); avoid all-caps + wide tracking for buttons.
- **Theme state:** `src/lib/theme.js` (`useTheme`, `chartColors`); `index.html` applies it before first paint; toggle in `Topbar.jsx`. Never hardcode hex in JSX — SVG attributes take `chartColors(theme)` or `className="stroke-*"`. A subtree that must stay dark gets `data-theme="dark"` (campaign log console).
- **Phones & install (added 2026-09-24):** installable PWA — `public/manifest.webmanifest`, `public/sw.js` (never caches `/api/*`; pages network-first with `public/offline.html` fallback; hashed `/assets` cache-first — bump `VERSION` if caching rules change), icons in `public/icons/`, `lib/install.js` (`useInstall`, `registerServiceWorker` — registers after DOMContentLoaded, never on window `load`), `components/InstallApp.jsx` (Chrome prompt / iPhone instructions). `components/BottomNav.jsx` shows below `lg`. Page pattern for phones: `px-4 py-5 sm:p-6 lg:h-full … lg:overflow-hidden` and stack side columns (`flex-col lg:flex-row`, `lg:w-80`); never hover-only controls (add `[@media(hover:none)]:opacity-100`); Leads uses `MobileLeadCards` below `md`. Never load third-party CSS with a blocking `@import` (a slow Google Fonts stalled the whole app). Measure phones with an overflow audit at 390px, not only screenshots.
- **Shell:** `Sidebar` is off-canvas below `lg` (`open`/`onClose` from `App.jsx`), `Topbar` has the menu button.

## Rules

1. **New pages/components reuse existing primitives** (`Skeleton`, `ErrorState`/`EmptyState`, `ScoreBadge`, badge color classes) rather than inventing new loading/error/badge styles.
2. **New API calls go in `api/client.js`** as a new or extended domain module, following the existing per-domain shape — not inline `fetch`/`axios` calls scattered in components.
3. **New data-fetching uses React Query**, matching every existing page.
4. **`Campaign.jsx` is the base to extend for "Lead Search Campaign"**, not a page to replace — its source-selection/pipeline-stage/live-log UX already matches what the spec asks for; add to it rather than starting over.
5. **A new backend stage/status must be reflected in the frontend's mapping tables** (e.g., `STAGE_TO_STEP`, status-badge color maps) in the same change — a backend-only addition renders as blank/unknown in the UI.
6. **Large tables need virtualization** (`useVirtualizer`) if they can realistically grow past ~100 rows, matching `LeadTable.jsx`'s existing approach.

## Architecture Guidance

Page → React Query hook (via `api/client.js` domain module) → shared component. Keep page files focused; when a page file grows very large (`Campaign.jsx` is already 1050 lines, `AILab.jsx` 967), prefer extracting a new component over adding more inline JSX, especially for genuinely new UI surfaces like a new "Lead Search" entry point.

## Implementation Guidance

For a new page: add to `frontend/src/pages/`, register a lazy-loaded route matching the existing code-splitting pattern, add a nav entry in `Sidebar.jsx`, and add/extend the relevant `api/client.js` module before wiring components.

## Testing Requirements

No automated frontend test suite exists yet. Minimum bar: `npm run build` clean, plus manual verification in a running browser (navigate to the page, exercise the golden path, check the console for errors) — see `autolead-verification-before-completion`.

## Security Considerations

Never render user-supplied or scraped content as raw HTML (`dangerouslySetInnerHTML`) without sanitizing — scraped website content in particular is untrusted input.

## Performance Considerations

Reuse `useDebouncedValue` for any new search/filter input, `useVirtualizer` for any new large list/table, and respect the existing manual vendor/charts chunk-splitting convention in `vite.config.js` rather than letting a new heavy dependency bloat the main bundle.

## Failure Modes

| Mistake | Fix |
|---|---|
| New page invents its own loading spinner/error banner | Reuse `Skeleton`/`ErrorState`/`EmptyState` |
| New API call is an inline `fetch` in a component | Add it to the relevant `api/client.js` domain module |
| Backend adds a campaign stage, frontend pipeline visualization doesn't update | Update `STAGE_TO_STEP`/`PIPELINE_STEPS` in the same change |
| New large table renders all rows unvirtualized | Use `useVirtualizer`, matching `LeadTable.jsx` |
| Claiming a frontend change is "tested" | No test framework exists — say "built cleanly, manually verified" instead |

## Verification Checklist

- [ ] `npm run build` clean
- [ ] New UI reuses existing shared components/badge patterns, not new ad-hoc styles
- [ ] New API calls live in `api/client.js`, not inline
- [ ] Any new backend stage/status has a corresponding frontend mapping entry
- [ ] Manually verified in a running browser (not just build-clean)

## Related Skills

`autolead-product-ux`, `autolead-backend-architecture`, `autolead-browser-research-agent`, `autolead-verification-before-completion`

---
Version: 1.1
Scope: AutoLead-manager
Last reviewed: 2026-08-30 (LeadSearch / ResearchAgent pages, discoveryApi / researchAgentApi added)
