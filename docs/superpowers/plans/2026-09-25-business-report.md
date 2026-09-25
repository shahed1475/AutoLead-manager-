# Business Report + Leads Freshness Implementation Plan

> Executed natively (superpowers:executing-plans). Condensed format (owner asked for low token use).

**Goal:** (1) leads found by Find leads always show in Leads; (2) the lead audit becomes a full, user-friendly business report — business details, owners/managers with their contact details, a website analysis with the technology the site uses, online profiles and a company summary; (3) audit every lead from the Leads table.

**Root cause (bug 1, verified 2026-09-25):** run #6 saved 17 leads (ids 48–64) and `GET /api/leads` returns all 55, but the Leads page kept its cached list: global `staleTime: 30s`, `refetchOnWindowFocus: false`, and no Find-leads screen invalidates `['leads']` while a ~30-minute run adds leads.

**Data that already exists (verified):** `lead_research_results` (owner/manager name, title, phone + type, email + status, business phone/email + status), `lead_research_decision_makers` (27 people for run #6, each with source page), `lead_research_evidence`, `enriched_data` (AI summary for 47 leads). Nothing new needs to be scraped for contacts.

## Global Constraints
- Every fact shown traces to a stored source (listing, their website, research evidence). Missing data shows its status (`NOT_FOUND_AFTER_SEARCH`, `SECURE_WEB_FORM`, …) in plain words — never a guess. AI summaries are labelled as AI.
- A management phone that is the business line is labelled as such.
- Technology = deterministic signatures in the page HTML, each with the matched evidence. Separate module (`backend/audit/technology.py`); `intelligence/techstack.py` untouched.
- Additive only: new analyzer keys, new `lead_audits.details_json`, new optional `audit_score` on leads; no send path touched.

## Tasks
1. **Leads freshness** — Leads query refetches on mount and every 20 s while visible; LeadRun invalidates `['leads']` as leads arrive and offers "See these leads" → `/leads?run=<id>`; backend `run_id` filter on `GET /api/leads`. Tests: `run_id` filter returns only that run's leads.
2. **Analyzer details** — `social_profiles` (URLs), `emails_on_page`, `phones_on_page`. Test with header/footer page.
3. **Technology detection** — `detect(html) -> [{name, category, evidence}]` covering CMS/builders, e-commerce, analytics & ad pixels, chat widgets, booking, payments, SEO plugins, frameworks/CDN. Tests: WordPress+Elementor+GA4+Meta Pixel page; plain page → [].
4. **Business report** — `lead_audits.details_json` (technology, profiles, on-page contacts, page facts); report view adds `business`, `contacts` (research bundle), `summary` (enriched_data). Tests: contacts come from research rows with statuses; no research → `contacts.researched == False`.
5. **Audit from Leads** — `audit_score` column in the list; `POST /api/leads/audit-batch` (≤100, sequential background) + `GET` progress; bulk "Audit" button. Tests: batch audits all given leads; list shows score.
6. **Report page redesign** (taste/redesign skill rules) — sections: summary header, Contacts (business + people with source links, research button when none), Website check, Technology, Online profiles, About the business; printable.

## Review Focus
1. Lead with research but no audit → report still shows contacts after running audit.
2. Management phone equal to business phone → labelled "main business line".
3. A site with 300 KB HTML → tech/links found past the 40 KB snapshot.
4. Batch started twice → second call doesn't start a parallel batch.
5. `?run=` for a run with no leads → empty list with a clear message, not all leads.
