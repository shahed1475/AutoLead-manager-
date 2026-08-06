# About AutoLead

## What this is

AutoLead is a self-hosted, AI-powered lead-generation and outreach engine. It finds local businesses on the internet, figures out which ones are worth contacting, writes personalized outreach messages for them using a locally-run AI model, and sends those messages automatically by email or WhatsApp — with a web dashboard to run and watch the whole thing.

The design goal stated in the project itself: "100% self-hosted, zero monthly cost." No SaaS subscriptions, no per-lead API fees, no cloud database. It runs on one machine, uses a free local LLM (Ollama) by default, and stores everything in a single SQLite file.

It is being built as a desktop-style application: a Python/FastAPI backend, a React frontend, and a `start.bat`/`start.sh` script that launches both and opens the browser — with Electron packaging planned as a later phase so it can eventually ship as an installable desktop app rather than "open two terminals and a browser tab."

## The pipeline, end to end

1. **Scrape** — pull business listings for a niche + city from one of several sources (see below), producing raw leads: name, phone, email, website, address.
2. **Enrich** — fetch each lead's website, extract signals (headings, CTAs, contact info, social links, page quality), and run it through the local LLM to get a business summary, marketing gaps, and growth potential.
3. **Score** — a 4-dimension, 0–100 scoring model classifies each lead HOT / WARM / COLD based on the enrichment data.
4. **Write** — the LLM drafts a personalized outreach message per lead, using a "company DNA" file that tells it who *you* are and what you're pitching.
5. **Send** — messages go out via Gmail SMTP or WhatsApp Desktop automation, rate-limited and logged.
6. **Follow up & reply** — a background scheduler sends automatic follow-up sequences and detects/logs replies into an inbox view.

All of this is visible and controllable from a web dashboard, with a live campaign log (SSE stream), pause/resume, and history.

## Lead sources (scrapers)

`backend/scrapers/` — each source has its own scraper module, all normalized into the same lead shape:

- Google Maps (primary source)
- Google Search
- Bing Search
- Yelp
- Yellow Pages
- Hotfrog
- Top List
- Generic Directory (fallback/catch-all)
- Email Finder (secondary pass to backfill missing emails)

Every source calls its own real scraper first and only falls back to Google Maps on genuine failure or empty results — this was a real bug fixed during the production hardening pass (see below): 6 of 9 sources were previously silently hard-routed to Google Maps regardless of what the user picked.

## Enrichment & scoring

- `backend/enrichment/website_analyzer.py` — fetches a lead's website and extracts structural signals: title, meta description, headings, body text, word count, image count, contact form presence, phone/email on page, CTA buttons, social links. Cached in-process with a TTL to avoid re-fetching the same site repeatedly.
- `backend/enrichment/ai_enricher.py` — feeds those signals plus your company DNA into the local LLM and gets back a structured business summary: service level, marketing gaps, growth potential, pitch angles.
- `backend/scoring/lead_scorer.py` — a 4-dimension scorer (0–100) that turns the enrichment output into a HOT/WARM/COLD classification, so you know which leads are worth prioritizing.
- `backend/ai_brain.py` — the shared LLM dispatch layer everything above (and the message writer) goes through. Defaults to a local Ollama model (llama3); can optionally use a cloud provider (OpenAI/Anthropic) if you add an API key in Settings, but Ollama stays the default so the app works fully offline and free out of the box.

## Messaging & delivery

- `backend/email_sender.py` — Gmail SMTP sending, using an app password (not your real Gmail password).
- `backend/whatsapp_sender.py` — WhatsApp Desktop automation (drives the actual WhatsApp Desktop app via UI automation rather than the paid WhatsApp Business Cloud API). This is a deliberate, confirmed architectural choice: it avoids Meta Business account setup and per-message costs, at the cost of being Windows-only and more fragile than an official API. Hardening this rather than replacing it with the Cloud API was explicitly chosen over the alternative.
- `backend/followup_engine.py` + APScheduler jobs in `backend/scheduler.py` — automatic multi-stage follow-up sequences on a schedule (daily campaign run + periodic follow-up checks), so leads that don't reply get a second and third touch without manual work.
- `backend/reply_detector.py` — watches for replies and surfaces them in the Inbox.

## The web dashboard (frontend)

React + Vite, talking to the backend over a REST API plus a live SSE log stream for running campaigns.

- **Dashboard** — stats, recent activity, campaign health.
- **Leads** — the full lead table: virtualized (handles large lists smoothly via `@tanstack/react-virtual`), sortable/resizable columns, debounced search, score badges.
- **Campaign** — configure and launch a scraping+outreach run (niche, city, source, daily cap), watch it progress through real pipeline stages (QUEUED → SCRAPING → ENRICHING → SCORING → WRITING → SENDING → COMPLETED), pause/resume mid-run, and see campaign history.
- **Inbox** — replies that came in, so you can see who responded.
- **AI Lab** — a message-generation playground, including a "regenerate all messages" flow with skeleton loading states.
- **Settings** — configure sources, SMTP, the LLM provider, and (as of this session) the sales-intelligence feature toggle.
- Shared UI primitives (`components/ui/`): skeleton loaders, error states, empty states, an error boundary, sortable headers, resizable-column and debounced-value hooks, animated stat numbers — added as part of frontend polish work.

## Under the hood: correctness, safety, auth

- **Local single-user auth** — an optional app password (bcrypt-hashed), session tokens, applied at the router level. If you never set a password, the app runs fully open (friendly for first-run/local-only use).
- **Rate limiting** — applied to campaign start/send and AI generation endpoints so you can't accidentally hammer your own SMTP account or the LLM.
- **Secrets handling** — API keys and SMTP credentials are redacted from `GET /api/settings` responses and encrypted at rest (falls back to plaintext with a warning if the optional `cryptography` package isn't installed — a known, flagged limitation, not a silent one).
- **Database** — SQLite only, single file, no Postgres/Redis. This was a deliberate simplification: the app only ever actually used SQLite in practice even though Postgres/Redis were half-provisioned in config; removing them keeps a desktop app to a single file with nothing extra to run or manage.
- **Real campaign pipeline stages** — campaigns track an actual `stage` field in the database (not guessed from log text) so pause/resume and progress reporting are reliable.
- **Dedup & data integrity** — cross-run duplicate lead detection, cascading deletes (deleting a lead cleans up its replies/scores/research data instead of leaving orphaned rows).

A dedicated production-stability audit pass (7 parallel subsystem audits covering scrapers, enrichment, scoring, dedup, Mission Control/dashboard, logging, and security) found and fixed 29 issues, the headline one being that AI scoring was silently running on structurally-incomplete lead data because the enrichment step wasn't actually wired into the live campaign pipeline — it now is, in both the manual campaign flow and the scheduled daily job.

## The newest feature: AI Sales Intelligence Engine (foundation)

The latest addition, built and merged into `main` in this session. It's an opt-in layer that sits *alongside* the existing enrichment/scoring pipeline (which keeps working exactly as before) and adds a deeper, evidence-tracked research pass on top:

- **Lead Qualification Agent** — a fast, free, no-LLM check that scores whether a lead is even worth researching further: is the website reachable, is there a real business name, is there contact info, does the niche/location match the campaign. Produces a QUALIFIED/REJECTED verdict with a weighted confidence score and a plain-language reason.
- **Company Research Agent** — for qualified leads, does a deeper pass: heuristic tech-stack detection (spots WordPress, Shopify, Wix, Squarespace, Webflow, Next.js/React, Google Analytics from the page's HTML), plus an LLM-synthesized industry/services/products/company-description/size/maturity profile. Every fact it produces is tagged with *where it came from* — heuristic, website content, or AI inference — so nothing is presented as more certain than it is.
- **Evidence-tracked storage** — six new database tables (`company_profiles`, `research_evidence`, plus scaffolding for future decision-maker discovery, contact verification, sales scoring, and personalization — not built yet, just reserved).
- **Resumable orchestrator** — runs qualification then research per lead, saving progress after each step so a crash mid-research doesn't lose the qualification verdict; automatically retries leads that failed transiently; drains any backlog on app startup.
- **Read API** — `GET /api/leads/{id}/research` to see a lead's qualification verdict, research profile, and full evidence trail.
- **Off by default** — gated behind a `sales_intelligence_enabled` setting. With it off (the default), every existing campaign behaves exactly as it did before this feature existed — verified line-by-line, not just by testing. You turn it on via Settings when you're ready to use it.

This was built and reviewed task-by-task (9 discrete steps, each independently implemented and code-reviewed), then given a full whole-branch review that caught — and fixed — several things no single step could have seen on its own: a memory-bloat issue in the page-fetch cache, a mismatched HTTP header between two parts of the pipeline causing false "unreachable" verdicts, a dead-end failure state with no retry path, a background job with no actual caller, unvalidated AI output that could have silently discarded a whole research result, and a missing safety net that could have aborted an entire campaign run over a single transient error. All of that shipped fixed, not just flagged.

It was tested live against real leads with a real local Ollama model — for example, "Ritzi Italian Restaurant Dubai Marina" was correctly qualified and researched, coming back with `industry: "Restaurant"`, a real generated company description, and a full evidence trail, all reachable through the actual API.

## Where things stand right now

**Done:**
- Full scraping → enrichment → scoring → messaging → sending → follow-up pipeline, working end to end.
- Production hardening pass (29 fixes: correctness, security, data integrity, performance).
- Frontend polish: virtualized tables, skeleton/error/empty states, code-split routes, bundle-size cleanup — further along than originally planned for this stage.
- AI Sales Intelligence Engine, foundation layer (qualification + company research), merged and working, off by default.

**Not built yet:**
- The rest of the Sales Intelligence Engine: ICP match scoring, decision-maker discovery, contact verification (flagged as compliance-sensitive — LinkedIn's terms prohibit scraping, so this needs its scope discussed before it's built), AI personalization, and a dedicated UI for all of this (currently API-only, no dashboard tab yet).
- Electron desktop packaging — the app currently runs as a backend + browser tab via `start.bat`/`start.sh`; there's no installable desktop build yet, though some groundwork (relative asset paths in the frontend build) is already in place for it.
- A final production QA checklist and packaging/deliverables document — planned, not started.

**Known limitations to keep in mind:**
- WhatsApp sending depends on WhatsApp Desktop being open and logged in — it's UI automation, not an official API, so it's inherently a bit fragile and Windows-only.
- Secrets fall back to plaintext storage if the `cryptography` package isn't installed.
- Gmail sending needs a real Gmail App Password filled into `backend/.env` — not configured by default.
