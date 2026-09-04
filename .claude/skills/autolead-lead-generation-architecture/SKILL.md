---
name: autolead-lead-generation-architecture
description: Use when planning or implementing any lead-generation feature in AutoLead — discovery, Lead Search, Lead Search Campaign, website research, contact validation, buying signals, scoring, opportunity mapping, or the sales-intelligence pipeline — to orient which subsystem owns which part before writing code.
---

# AutoLead Lead-Generation Architecture (Master Overview)

## Purpose

The single map of the full lead-generation pipeline, what already exists vs. what's still being built, and which of the other AutoLead skills to consult for each stage. Read this first for any lead-gen task; it routes you to the detailed skill.

## When to Use

Any task touching discovery, research, scoring, or messaging for leads — especially before starting a new sub-feature, to avoid duplicating something that already exists.

## When NOT to Use

Pure CRM/pipeline-board work (`Pipeline.jsx`, `lead_stage_history`) or reply-handling work with no discovery/research/scoring component — those are already-shipped Phase 4 features, not part of this pipeline's build-out.

## Project Context — the full pipeline

```
USER (Lead Search | Lead Search Campaign)
    |
    v
Discovery Planner  ──────────────────────────  autolead-discovery-and-source-adapters
    |  (intent classify -> source select -> query variants)
    v
Source Registry (9 existing adapters: Google Maps, Google Search,
    Bing, Yelp, Yellow Pages, Hotfrog, Foursquare, Top List, Generic Dir)
    |
    v
Normalize -> Merge-Dedup (create_or_merge_lead + lead_sources provenance)
    |
    +----------------------------+
    |                            |
    v                            v
Quick Search: stop here    Deep research (optional / separate entry point):
(discovery + dedup only)    Browser Research Agent  ──────  autolead-browser-research-agent
                             (Playwright + Ollama OBSERVE/DECIDE/ACT/VALIDATE loop;
                              business + management contact fields, evidence-backed;
                              optionally merged back into `leads`)
    |
    v
Website Research (deterministic: website_analyzer.py)  ──────────  autolead-browser-automation
    |                                                                (Playwright/Selenium boundary)
    v
Contact Validation (email/phone/WhatsApp — STILL regex-format-only; backend/verification/ spec'd not built)
    |
    v
Evidence Engine (research_evidence for intelligence/; lead_research_evidence for research_agent/)
    |
    v
AI Qualification + Pain Points + Opportunities + Solution Match  ─  autolead-lead-intelligence-and-scoring
    |
    v
Lead Score (deterministic formula, lead_scorer.py)
    |
    v
Sales Brief / "Why This Lead" / "Why Now"
    |
    v
Personalized Message (MarketingAgent/FollowUpAgent)  ────────────  autolead-outreach-safety
    |
    v
Human Approval  (generated_messages -> approve -> staged into leads.ai_*)
    |
    v
EXISTING Phase 4 Outreach System (send_email / send_whatsapp)  ──  autolead-outreach-safety
```

## Build Status (verify against the repo before assuming — this decays fast)

**Already live:**
- 9 scraper sources + orchestrator (`backend/scrapers/`, `run_bulk_scrape`) — Discovery, unplanned/manual source selection.
- Deterministic website analysis (`enrichment/website_analyzer.py`) — no browser needed, `httpx` + BeautifulSoup.
- AI enrichment (`enrichment/ai_enricher.py`).
- Evidence-tracked intelligence agents (`backend/intelligence/`): Qualification, Company Research, Pain Point, Opportunity, Solution Matcher (14-service catalog in `service_knowledge_base.py`), Marketing (message drafting), Follow-up, Reply Intelligence — all gated behind `sales_intelligence_enabled`.
- Deterministic lead scoring (`scoring/lead_scorer.py`) — 4-dimension, 100-point formula, HOT/WARM/COLD bands.
- CRM pipeline board (`Pipeline.jsx`, `lead_stage_history`) — separate from this pipeline, already shipped.
- **Discovery Planner + Source Adapter Registry + merge-dedup** (`backend/discovery/`, `tests/discovery/`) — intent classification (rule-based → LLM fallback), bounded niche-phrasing query variants, `SourceRegistry` wrapping the 9 scrapers, `create_or_merge_lead` + `lead_sources` provenance. Runs on `JobQueue`. See `autolead-discovery-and-source-adapters`. **Uncommitted in the working tree as of 2026-08-30.**
- **Quick Lead Search** (`routers/discovery.py`, `LeadSearch.jsx`, `/api/discovery`) and **Lead Search Campaign** (relabeled `Campaign.jsx`, planner-driven query variants via one scoped change in `routers/campaigns.py`) — both live in the working tree.
- **Browser Research Agent** (`backend/research_agent/`, `tests/research_agent/`, `routers/research_agent.py`, `ResearchAgent.jsx`, `/api/research-agent`) — the iterative Playwright+Ollama OBSERVE/DECIDE/ACT/VALIDATE loop that deep-researches a business into an evidence-backed `ResearchLead` (business + management contact fields), optionally merged into `leads`. Its own 3 tables (`lead_research_*`). See `autolead-browser-research-agent`. **Uncommitted in the working tree as of 2026-08-30.**

**In progress / not yet built (verify against the repo — this decays fast):**
- Contact validation (email MX/disposable-domain, phone-type, WhatsApp reachability) — still **regex-format-only** (`validators.py`). The research agent produces honest `NOT_FOUND`/`SECURE_WEB_FORM`/`UNCONFIRMED` statuses but does not verify a found value. A `backend/verification/` package is spec'd (research-agent design §12) but not built. Still the biggest real gap.
- Buying signal engine (temporal/company-event signals: recently launched, hiring, redesign) — not yet built; distinct from the existing structural pain-point checks.
- Freshness tracking — not yet built.
- Excel/CSV export with full evidence/provenance — verify current `Leads.jsx` CSV export scope; it likely covers basic lead fields only, not the full evidence/audit/opportunity bundle the spec calls for.
- ICP-match scoring, AI personalization, Decision Maker Discovery — explicitly deferred in prior planning docs (`docs/superpowers/specs/`); Decision Maker Discovery specifically flagged as compliance-sensitive (LinkedIn ToS) and not to be built without a separate scoping discussion.

## Rules

1. **Check build status against the actual repo before designing** — this pipeline is mid-construction and the "not yet built" list above will go stale fast.
2. **Never duplicate an existing stage.** If a stage says "already live" above, extend it (see the relevant sub-skill) rather than building a parallel version.
3. **New tables are additive only.** No existing `leads`/`campaign_runs`/Phase 4 table gets modified in a way that breaks current readers — extend via new FK'd tables (the pattern every `backend/intelligence/` table already follows).
4. **Everything downstream of "Human Approval" is out of scope for lead-gen work** — that's `autolead-outreach-safety`'s territory, never touch it from here except to hand off a draft.

## Architecture Guidance

New lead-gen subsystems live in their own `backend/<subsystem>/` package (mirrors `backend/intelligence/`, `backend/enrichment/`, `backend/scoring/`), behind their own settings toggle following the `sales_intelligence_enabled` precedent, reusing `ai_brain._call_llm_raw` for any LLM call and `scrapers`'s existing functions for any scraping — never a new provider SDK call or a new scraper written from scratch when an adapter can wrap what exists.

## Implementation Guidance

Work one stage at a time, in pipeline order, each with its own spec → plan → build cycle (per `superpowers:brainstorming` → `superpowers:writing-plans`). Don't batch multiple stages into one implementation pass — the existing codebase's own planning docs (`docs/superpowers/plans/`) all follow this discipline and it's why Phase 4 shipped without breaking anything.

## Testing Requirements

See `autolead-verification-before-completion`'s lead-gen-specific checklist. At minimum: source isolation (one source failing doesn't fail a run), dedup produces a merge not data loss, and AI-assisted stages degrade to a deterministic fallback on LLM failure.

## Security Considerations

Contact validation (when built) touches external services (DNS/MX lookups, possibly a paid verification provider) — never hardcode a provider API key; follow the existing `app_settings` DB-override pattern. See `autolead-security-and-secrets`.

## Performance Considerations

Cheap-first ordering matters: discovery → dedup → basic validation → website research → contact verification → AI qualification → message generation. Don't spend an LLM call or a browser session on a lead that's about to be deduped away.

## Failure Modes

| Mistake | Fix |
|---|---|
| Building a new scraper from scratch for a "missing" source | Check the 9 existing adapters first — it's very likely already there |
| Building contact verification as an LLM prompt ("does this email look valid?") | This is exactly the kind of deterministic task the project rule says AI should not do — use real MX/format checks |
| Wiring a new "send" button on a lead-search result | Out of scope — hand off to the existing approval flow, see `autolead-outreach-safety` |
| Assuming Phase 2/3 of the spec still needs building from zero | Most of it already exists in `backend/intelligence/`, `backend/discovery/`, `backend/research_agent/` — verify before designing |
| Building "deep per-business research" as a new thing | The Browser Research Agent already is that — extend `backend/research_agent/`, see `autolead-browser-research-agent` |
| Confusing the two evidence models | `intelligence/` writes `research_evidence` (+ `company_profiles`); `research_agent/` writes `lead_research_evidence` (+ `lead_research_results`). Separate subsystems, separate tables, same pattern. |

## Verification Checklist

- [ ] Confirmed which pipeline stage this task touches and whether it already exists (discovery? research_agent? intelligence? — three different subsystems now)
- [ ] No duplicate scraper/sender/LLM-call path introduced
- [ ] New DB tables are additive, FK'd with `ON DELETE CASCADE` to `leads` where per-lead
- [ ] Downstream handoff (if any) goes through the existing approval flow, not a new send path

## Related Skills

`autolead-discovery-and-source-adapters`, `autolead-browser-research-agent`, `autolead-lead-intelligence-and-scoring`, `autolead-outreach-safety`, `autolead-browser-automation`, `autolead-ai-llm-engineering`

---
Version: 1.1
Scope: AutoLead-manager
Last reviewed: 2026-08-30 (Discovery Planner, Quick Search, Lead Search Campaign, Browser Research Agent all now built in the working tree)
