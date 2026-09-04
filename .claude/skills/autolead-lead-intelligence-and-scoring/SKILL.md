---
name: autolead-lead-intelligence-and-scoring
description: Use when working on website analysis, pain-point detection, buying signals, opportunity mapping, solution matching, lead scoring, AI qualification, evidence tracking, or the sales-brief/why-this-lead/why-now generation in AutoLead.
---

# AutoLead Lead Intelligence & Scoring

## Purpose

Governs the research/analysis layer between "a lead was discovered" and "a message is ready to draft": website analysis, pain points, opportunities, solution matching, and deterministic scoring.

## When to Use

Adding/changing pain-point detection, opportunity/solution matching, lead scoring, contact validation, buying signals, or evidence tracking.

## When NOT to Use

Discovery/scraping (`autolead-discovery-and-source-adapters`) or message drafting/sending (`autolead-outreach-safety`).

## Project Context

**Deterministic layer (no LLM):**
- `enrichment/website_analyzer.py::analyze_website(url, timeout)` — HTTP-only (`httpx`, 6h cache), extracts title/meta/headings/body text/CTA buttons/social links/contact-form-or-not/tech-stack signals. `score_website(website_data)` — pure 0-25 structural scorer with fixed weights (SSL +5, contact form +5, phone +3, meta desc +3, ≥2 social +3, CTAs +3, word count>300 +3).
- `scoring/lead_scorer.py::score_lead(lead, enriched, website_scores)` — 4 dimensions × 25 pts = 100: Digital Presence, Website Quality (passthrough from above), Business Potential (niche/growth/reviews), Opportunity (marketing gaps + no contact form). Bands: HOT ≥70, WARM ≥40, COLD <40. **The LLM never sets this score directly** — it's a fixed formula over structured inputs.
- `intelligence/qualification_agent.py` — weighted heuristic (reachable, has name/contact/niche-match/location-match), no LLM, threshold 0.5.
- `intelligence/solution_matcher.py::match_solution` — keyword substring match against `service_knowledge_base.py`'s 14 services, fully deterministic.

**AI-assisted layer (LLM adds reasoning/wording, never invents facts it wasn't given):**
- `enrichment/ai_enricher.py::enrich_lead_with_ai` — 8-field structured JSON via `ai_brain._call_llm_raw`, degrades to structural-score-only on any LLM failure.
- `intelligence/company_research_agent.py` — industry/services/tech-stack via LLM, reuses `analyze_website`+`enrich_lead_with_ai` rather than duplicating.
- `intelligence/pain_point_agent.py` — heuristic checks (no contact method, no booking for booking-relevant niches, no social, no SSL) PLUS up to 3 LLM-suggested pain points, **each requiring a literal evidence quote from the page or it's dropped**.
- `intelligence/opportunity_agent.py` — mostly deterministic (groups pain points by niche-derived "ease area"), LLM only polishes wording of an already-fixed sentence.

**Feature flag:** all of `backend/intelligence/` is gated by `sales_intelligence_enabled` (app_setting, default off) via `intelligence_enabled(stored_settings)`.

**Evidence schema (already live):** `company_profiles` (1:1 lead), `research_evidence` (agent_name, field_name, source_type, source_url, snippet — every agent writes here), `pain_points`, `business_opportunities`, `solution_recommendations`. Scaffolding exists but is unused for `decision_makers`, `verification_results` (deferred — LinkedIn ToS concern), `sales_scores` (deferred ICP-match sub-project), `personalization_context` (deferred).

**Not yet built:** contact validation beyond regex format checks (`validators.py::is_valid_email/is_valid_phone` — no MX/DNS, no disposable-domain list, no phone-type/WhatsApp verification, no `phonenumbers` library dependency). Buying-signal engine (temporal/company-event signals — distinct from the existing structural pain-point checks). Freshness tracking.

## Rules

1. **Prefer deterministic logic; use AI only where reasoning genuinely adds value.** This is the existing codebase's own stated pattern (see `docs/superpowers/specs/2026-08-05-sales-intelligence-foundation-design.md`) — don't ask an LLM to detect "does this page have a booking widget" when a DOM/regex check can do it.
2. **Every AI-derived claim needs an evidence quote or it's dropped**, matching `pain_point_agent.py`'s existing pattern. Never let an LLM assert a fact about a business that isn't traceable to a `research_evidence` row.
3. **"Not detected" is not "confirmed absent."** Phrase findings as "I couldn't find X" not "you don't have X" — the spec is explicit about this, and it's also just correct given these are heuristic/incomplete checks.
4. **Never let the LLM set the final lead score.** Component scores feed a fixed formula; the LLM may inform inputs (e.g., growth_potential classification) but never outputs the final number directly.
5. **New verification (email/phone/WhatsApp) must produce honest status values**, not false confidence — `UNKNOWN` is a legitimate and expected outcome, not a failure state to eliminate. Never claim `CONFIRMED`/`VALIDATED` without a verification method that actually supports that conclusion.
6. **Reuse `ai_brain._call_llm_raw`/`_ollama_cfg` for every LLM call** — never call OpenAI/Anthropic/Ollama SDKs directly from a new agent.

## Architecture Guidance

New agents follow the existing `intelligence/base.py::ResearchAgent` protocol (`AgentResult(status, data, evidence, confidence, reason)`), write to new additive tables, and are invoked through `orchestrator.py`-style entry points that persist progressively (so a crash mid-pipeline doesn't lose prior stages' work) and never raise out of their public interface.

## Implementation Guidance

For contact validation specifically: format checks stay in `validators.py`; real verification (MX/DNS lookup, disposable-domain list, `phonenumbers`-based phone typing) belongs in a new `backend/verification/` package, with clear status enums (`VALIDATED/LIKELY_VALID/UNKNOWN/INVALID/DISPOSABLE/ROLE_EMAIL` for email; similar for phone/WhatsApp) and a timestamp + method recorded per check, following the DB-settings-override pattern for any optional paid provider.

## Testing Requirements

- Deterministic checks: table-driven tests (input → expected structural score/flag), no mocking needed.
- AI-assisted checks: mock `_call_llm_raw`, test both a well-formed and malformed LLM response, confirm graceful degradation on failure (never raises).
- Evidence requirement: a test asserting an LLM-suggested pain point with no evidence quote is dropped.
- Scoring: confirm the formula is a pure function of structured inputs — same inputs always produce the same score, no LLM call in the scoring function itself.

## Security Considerations

Any new external verification provider (email/phone) needs its API key behind the existing `app_settings`-override pattern, never hardcoded, and never logged.

## Performance Considerations

Run cheap deterministic checks before any LLM call, and before any external verification-provider call (which likely costs money per lookup) — dedupe/validate before spending on research, per the pipeline's own cheap-first ordering.

## Failure Modes

| Mistake | Fix |
|---|---|
| New agent calls an LLM for something a regex/DOM check could do | Move it to the deterministic layer |
| LLM-suggested pain point ships without an evidence quote | Drop it, matching existing `pain_point_agent.py` behavior |
| New scoring dimension lets the LLM output the number directly | Compute it from structured sub-scores in code |
| Contact validator returns `VALID` when it only checked format | Use the correct status — `UNKNOWN` unless the check genuinely proves validity |

## Verification Checklist

- [ ] New agent follows `ResearchAgent` protocol, never raises out of its public entry point
- [ ] Every AI-derived fact has a `research_evidence` row backing it
- [ ] Deterministic checks used wherever the fact is programmatically detectable
- [ ] Final score/status values come from code, not directly from LLM output
- [ ] Gated behind a settings flag if it's a new major capability, matching `sales_intelligence_enabled` precedent

## Related Skills

`autolead-lead-generation-architecture`, `autolead-ai-llm-engineering`, `autolead-discovery-and-source-adapters`, `autolead-outreach-safety`

---
Version: 1.0
Scope: AutoLead-manager
Last reviewed: 2026-08-25
