# AutoLead-manager — Features & How It Works

*Written 2026-08-30 from an inspection of the running code. AutoLead is mid-build; the
"newer subsystems" section marks what is wired into the UI but still stabilising.*

---

## 1. What AutoLead is

A **self-hosted, single-user lead-generation and outreach engine**. You give it a
niche + a city; it finds local businesses on the web, works out which ones are worth
contacting, drafts a personalised outreach message for each using a locally-run AI
model, and — after you approve — sends those messages by Gmail or WhatsApp Desktop.
A React dashboard runs and monitors the whole thing.

**Design goal stated by the project:** *"100% self-hosted, zero monthly cost."* No SaaS
subscription, no per-lead API fees, no cloud database. One machine, a free local LLM
(Ollama) by default, everything in a single SQLite file. It works fully offline.

**Stack**

| Layer | Tech |
|---|---|
| Backend | Python 3.11+ / FastAPI, `asyncio` throughout |
| Frontend | React 18 + Vite, React Query, `@tanstack/react-virtual` |
| Database | SQLite via `aiosqlite` — one file, no Postgres/Redis |
| AI | Ollama (llama3) by default; optional OpenAI/Anthropic if you add a key |
| Scraping | Selenium + `httpx` / BeautifulSoup |
| Email | `aiosmtplib` (send) + `imaplib` (reply polling) |
| WhatsApp | `pyautogui` UI-automation of WhatsApp Desktop (Windows only) |
| Scheduling | APScheduler (daily campaign + follow-up jobs) |
| Transport | REST + Server-Sent Events (live campaign log) |

---

## 2. The core pipeline, end to end

```
Niche + City
    │
    ▼
1. SCRAPE      pull business listings from a chosen source
    │          → raw leads: name, phone, email, website, address
    ▼
2. ENRICH      fetch each lead's website, extract structural signals
    │          (headings, CTAs, contact info, social links, page quality),
    │          then LLM-summarise: business summary, marketing gaps, growth potential
    ▼
3. SCORE       4-dimension, 0–100 model → HOT / WARM / COLD band
    │
    ▼
4. WRITE       LLM drafts a personalised outreach message per lead,
    │          using company_dna.txt (who you are / what you sell)
    ▼
5. APPROVE     human reviews generated messages → approve → staged onto the lead
    │
    ▼
6. SEND        Gmail SMTP or WhatsApp Desktop automation — rate-limited, logged
    │
    ▼
7. FOLLOW-UP   scheduler sends multi-stage follow-up sequences automatically
   & REPLIES   IMAP polling + AI reply-intent classification → Inbox view
```

Campaigns track a real `stage` column in the database
(`QUEUED → SCRAPING → ENRICHING → SCORING → WRITING → SENDING → COMPLETED`), so
pause/resume and progress reporting are reliable rather than guessed from log text.

---

## 3. Lead sources (`backend/scrapers/`)

Nine source modules, all normalised to the same lead shape:

- **Google Maps** (primary)
- Google Search
- Bing Search
- Yelp
- Yellow Pages
- Hotfrog
- Top List
- Foursquare
- Generic Directory (catch-all fallback)
- **Email Finder** — a secondary pass that backfills missing emails via multiple strategies

Each source runs its own real scraper and only falls back to Google Maps on genuine
failure or empty results. Scrapers **detect anti-bot protection and back off** — they
never attempt CAPTCHA-solving or evasion.

---

## 4. Enrichment & scoring

- **`enrichment/website_analyzer.py`** — fetches a lead's site (`httpx` + BeautifulSoup,
  no browser) and extracts: title, meta description, headings, body text, word count,
  image count, contact-form presence, on-page phone/email, CTA buttons, social links.
  In-process cache with a TTL so the same site isn't re-fetched repeatedly.
- **`enrichment/ai_enricher.py`** — feeds those signals + your company DNA to the LLM,
  returns a structured profile: service level, marketing gaps, growth potential, pitch
  angles.
- **`scoring/lead_scorer.py`** — deterministic 4-dimension, 100-point formula →
  HOT / WARM / COLD. Has a `HIGH_VALUE_NICHES` list (dental, real estate, law firm,
  accounting, medical, cosmetic, restaurant chain, gym, hotel, school).
- **`ai_brain.py`** — the shared LLM dispatch layer everything routes through. Defaults
  to local Ollama; optional cloud provider via a key in Settings. There is exactly one
  LLM dispatch path — no subsystem calls a provider SDK directly.

---

## 5. Messaging, delivery & safety

- **`email_sender.py`** — Gmail SMTP using a 16-char App Password (not your real password).
- **`whatsapp_sender.py`** — drives the actual WhatsApp Desktop app via UI automation.
  A deliberate choice: avoids Meta Business setup and per-message cost, at the price of
  being Windows-only and more fragile than the official API.
- **`followup_engine.py`** + **`scheduler.py`** — automatic multi-stage follow-up
  sequences (daily campaign run + periodic follow-up checks).
- **`reply_detector.py`** — IMAP inbox polling + AI reply-intent classification; replies
  surface in the Inbox and can advance a lead to INTERESTED / LOST.

**Non-negotiable safety rules baked into the code:**

- Exactly **one** real send function per channel (`email_sender.send_email`,
  `whatsapp_sender.send_whatsapp`) — no second sending path.
- Human approval, opt-out (`DO_NOT_CONTACT`), and terminal lead statuses are never
  bypassed.
- No CAPTCHA-solving or anti-bot evasion anywhere.

---

## 6. The web dashboard (frontend pages)

| Page | What it does |
|---|---|
| **Dashboard** | Live stats (total leads, sent today, reply rate, est. revenue), 7-day trend chart, lead distribution, engine status. Auto-refreshes every 15 s. |
| **Leads** | Full lead table — virtualised for large lists, sortable/resizable columns, debounced search, score badges. CSV export. |
| **Pipeline** | CRM-style drag-and-drop stage board (NEW → CONTACTED → INTERESTED → …). Manual stage moves, per-lead stage history. |
| **Lead Search** | *(newer)* Quick one-time discovery — Planner picks 1–2 sources, returns leads, no research/scoring/messaging. |
| **Research Agent** | *(newer)* Deep browser+LLM research loop — niche + location + target in, structured lead records with per-field evidence out. |
| **Lead Search Campaign** | The full scrape → enrich → score → write → send run. Niche, city, source, daily cap; live SSE progress; pause/resume; campaign history. |
| **AI Lab** | Message-generation playground; "regenerate all messages" flow. |
| **Inbox** | Replies that came in, with AI-classified intent; process/act on each. |
| **Settings** | Sources, SMTP, LLM provider + key, sales-intelligence toggle, app password. |

---

## 7. Sales Intelligence Engine (opt-in, `backend/intelligence/`)

An opt-in deep-research layer (gated behind `sales_intelligence_enabled`) that runs
*alongside* the normal enrichment/scoring pipeline and adds an evidence-tracked pass:

- **Qualification Agent** — fast, free, no-LLM check: is the site reachable, is there a
  real business name + contact info, does niche/location match? → QUALIFIED / REJECTED
  with a weighted confidence score and a plain-language reason.
- **Company Research Agent** — for qualified leads: heuristic tech-stack detection
  (WordPress, Shopify, Wix, Squarespace, Webflow, Next.js/React, Google Analytics from
  page HTML) + an LLM-synthesised industry/services/size/maturity profile. **Every fact
  is tagged with where it came from** (heuristic / website content / AI inference) so
  nothing looks more certain than it is.
- Also present: Pain Point, Opportunity, Solution Matcher (14-service catalog),
  Marketing (message drafting), Follow-up, Reply Intelligence agents — with a resumable
  per-lead orchestrator and their own FK'd `research_evidence` / `company_profiles`
  tables.

---

## 8. Newer subsystems (wired into the UI, still stabilising)

### Discovery Planner + Source Adapter Registry (`backend/discovery/`)

A universal discovery layer *in front of* the 9 scrapers, without touching scraper
internals:

```
Quick Search / Campaign Search
        │
        ▼
Discovery Planner   — rule-based intent classify (LOCAL_BUSINESS vs STARTUP_TECH_COMPANY),
        │             LLM fallback only when ambiguous; bounded niche-phrasing query variants
        ▼
Source Adapter Registry  — thin wrapper delegating to the same dispatch the bulk scrape uses
        ▼
Normalize → Merge + Deduplicate (preserves provenance) → Basic Validation → Results
```

- **Quick Search** — 1–2 Planner-selected sources, one-time, no downstream pipeline.
- **Lead Search Campaign** — the relabeled Campaign page; Planner-generated adaptive
  query variants replace the old single fixed query. Manual multi-source selection and
  the stage machine are unchanged.

### Browser Research Agent (`backend/research_agent/`)

A standalone iterative browser + LLM research loop:

- **Input:** `niche + location + target`. **Output:** structured lead records
  (business + management contact fields) each with per-field evidence.
- **Two modes:** standalone discovery + research (`google_search` then deep-research each
  candidate), or deep-research of Phase-1-supplied `seed_businesses`.
- **Loop:** OBSERVE → DECIDE (action-decision LLM prompt) → ACT (controlled tool
  registry — navigate, search, click, scroll, extract, screenshot) → VALIDATE
  (field-completeness check). Per-lead failure isolation, budget/iteration caps.
- **Two LLM roles, kept small:** one prompt decides the next action, a separate prompt
  extracts fields from page text. Phone/email extraction is **deterministic regex**
  (reused from `validators.py`) — never asked of the LLM.
- **Storage:** 3 additive tables (`lead_research_sessions`, `lead_research_results`,
  `lead_research_evidence`). On COMPLETE/PARTIAL it can also save into the main `leads`
  table via the shared merge/dedup path (`source="BROWSER_RESEARCH_AGENT"`), so
  researched businesses appear on the Leads page like any other lead.
- Explicitly **not** built here: website scoring, AI qualification, any
  outreach/messaging, CRM changes, LinkedIn scraping, anti-bot evasion.

---

## 9. Under the hood — correctness, auth, data integrity

- **Local single-user auth** — optional app password (bcrypt-hashed) + in-memory session
  tokens, enforced at the router level. No password set → app runs fully open (good for
  local first-run).
- **Rate limiting** (`slowapi`) on campaign start/send and AI generation endpoints so you
  can't accidentally hammer your own SMTP account or the LLM.
- **Secrets** — API keys / SMTP creds are redacted from `GET /api/settings` and
  encrypted at rest with Fernet (`secrets_crypto.py`); falls back to plaintext *with a
  visible warning* if `cryptography` isn't installed.
- **SQLite only** — deliberate simplification; Postgres/Redis were half-provisioned in
  config and removed. Single file, nothing extra to run.
- **Dedup & integrity** — cross-run duplicate detection; cascading deletes (deleting a
  lead cleans up its replies/scores/research rows).
- **Prefer deterministic checks over LLM guessing** wherever a fact is programmatically
  verifiable — a cross-cutting project rule.
- A production-stability audit pass (7 parallel subsystem audits) found and fixed 29
  issues; the headline one: AI scoring had been running on structurally-incomplete lead
  data because enrichment wasn't wired into the live campaign pipeline — now fixed in
  both the manual and scheduled flows.
- Backend test suite: `venv/Scripts/python.exe -m pytest -q` (292 passing as of
  2026-08-24). No frontend test framework yet — `npm run build` + manual browser check.

---

## 10. Running it locally

```
venv/Scripts/python.exe run_server.py     # backend  → http://127.0.0.1:8000
cd frontend && npm run dev                # frontend → http://127.0.0.1:5173
```

Prerequisites: Python 3.11+, Node 18+, Ollama with `llama3` pulled, and a Gmail App
Password in `backend/.env` if you want to send email. Fill in `backend/company_dna.txt`
so the AI knows who you are. `start.bat` / `start.sh` does the whole setup + launch in
one click.

> **Note (this machine, 2026-08-30):** port 8000 is currently used by another local app,
> so this session runs the AutoLead backend on **8001** via `run_server_8001.py` with the
> Vite proxy pointed at 8001. Revert `frontend/vite.config.js` and delete
> `run_server_8001.py` to return to the standard setup.

Roadmap items not yet built: contact validation (MX / disposable-domain / WhatsApp
reachability — currently regex-format-only), a buying-signal engine (hiring / redesign /
recently-launched), freshness tracking, full evidence/provenance CSV export, Electron
desktop packaging. A 2026-08-06 multi-tenant SaaS rebuild (Postgres, org/auth) was
spec'd but never executed — the app remains single-tenant SQLite.
