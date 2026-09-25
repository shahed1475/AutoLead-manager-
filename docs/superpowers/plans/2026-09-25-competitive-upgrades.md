# Competitive Upgrades Implementation Plan

> **For agentic workers:** executed natively (superpowers:executing-plans) in one session. Steps use checkbox (`- [ ]`) syntax. Condensed format by owner request (low token use): each task lists files, interfaces and the tests that pin it; code lives in the diff.

**Goal:** Close the five gaps found in the 2026-09-25 competitor research (GoHighLevel, Apollo/Clay, Instantly/Smartlead, Outscraper/Scrap.io, 11x/Artisan, WATI/AiSensy) — proof-backed messages, a verifiable lead audit, WhatsApp number safety, email deliverability, and client results + quick setup.

**Architecture:** Every feature extends an existing module (marketing agent, website analyzer, WhatsApp pacer, email-campaign send loop, reply detector, dashboard stats). All new facts are deterministic checks with a source and a timestamp; nothing new sends a message.

**Tech Stack:** FastAPI + aiosqlite (`backend/database.py` `_add_col_if_missing` migrations), dnspython (already a dependency), React + TanStack Query + Tailwind.

**Spec:** the competitor research answer of 2026-09-25 (items 1–4 and 6). Item 5 (BDT/bKash payments) — owner chose **skip for now**.

## Global Constraints

- The owner's existing WhatsApp integration (Web engine, QR login, campaigns, automatic replies) stays exactly as it is. New WhatsApp safety is advisory by default; the warm-up cap only applies when the owner switches on `wa_safe_mode` (default **off**).
- No second send path: `email_sender.send_email` / `whatsapp_sender.send_whatsapp` / `transport.send` stay the only senders. New code may only *block* or *pause* sends, never add them.
- No fabricated facts: an audit check with no data is `unknown`, never `issue` or `pass`.
- Additive DB changes only (`_add_col_if_missing`, `CREATE TABLE IF NOT EXISTS`); existing API fields unchanged.
- Client workspaces stay isolated: results/audit read only the workspace's own DB.

## Review Focus

1. Lead with no website → audit must not report website issues as found problems; site checks are `unknown` / not applicable.
2. Website that times out → every site check `unknown`, audit still saved.
3. Sender profile with `daily_limit = 0` (all existing profiles) → no behaviour change.
4. Bounce email whose body lists our own address → our address is never marked bounced.
5. `wa_safe_mode` off (default) → `pacing()` daily limit identical to today's.

---

### Task 1: Proof-backed messages

**Files:** Modify `backend/database.py` (generated_messages cols `evidence_url`, `evidence_checked_at`, `evidence_kind`; add to `_GENERATED_MESSAGE_WRITABLE`), `backend/intelligence/marketing_agent.py` (fill them from the strongest pain point), `frontend/src/components/MarketingMessagesPanel.jsx` (Proof block). Test: `tests/test_proof_messages.py`.

**Interfaces:** Produces message dict keys `evidence_url: str|None`, `evidence_checked_at: str|None`, `evidence_kind: "observed"|"inferred"`.

- [x] Test: MarketingAgent.run with an observed pain point (source_url, created_at) → every message carries those three keys; inferred classification → `evidence_kind == "inferred"`.
- [x] Test: `replace_generated_messages` round-trips the new columns.
- [x] Implement; UI shows snippet, source link, "checked <date>", and an amber "Inferred — check before approving" badge for inferred proof.

### Task 2: Lead audit report

**Files:** Create `backend/audit/__init__.py`, `backend/audit/lead_audit.py`, `backend/routers/audit.py`, `frontend/src/pages/LeadAudit.jsx`. Modify `backend/database.py` (table `lead_audits`), `backend/main.py` (router), `frontend/src/App.jsx` (route `/leads/:id/audit`), `frontend/src/api/client.js` (`auditApi`), `frontend/src/components/EnrichmentDrawer.jsx` (Audit button). Test: `tests/test_lead_audit.py`.

**Interfaces:**
- `run_checks(lead: dict, site: dict|None, checked_at: str) -> list[dict]` — pure; each check `{key, label, status: pass|issue|unknown, detail, source, checked_at, tip}`.
- `summarize(checks) -> {score: int 0-100 | None, passed, issues, unknown}`.
- `async build_audit(lead_id: int) -> dict` (runs `analyze_website`, saves a row) and `async latest_audit(lead_id) -> dict|None`.
- `POST /api/leads/{id}/audit`, `GET /api/leads/{id}/audit` (404 when none).

Checks: website listed, site reachable, HTTPS, mobile-ready (viewport meta), page title, meta description, contact form, phone on site, WhatsApp link, online booking, social links, Google rating (≥4.0), review count (≥20).

- [x] Tests: no website → `has_website: issue`, site checks `unknown`; unreachable site → site checks `unknown`; healthy site → passes; rating None → `unknown`; score ignores unknowns; router POST/GET with monkeypatched `analyze_website`; 404 for missing lead.
- [x] Implement; the report page is printable (Save as PDF) and follows the taste skill (no generic AI patterns, real data only, works in light and dark themes).

### Task 3: WhatsApp number safety

**Files:** Create `backend/whatsapp/safety.py`. Modify `backend/whatsapp/service.py` (`wa_safe_mode` setting, `pacing()` adds `safety` and applies the cap only in safe mode), `frontend/src/pages/WhatsAppCampaigns.jsx` (risk card + Safe mode toggle). Test: `tests/test_whatsapp_safety.py`.

**Interfaces:** `warmup_limit(age_days: int|None) -> int` (≤7d 15, ≤14d 30, ≤28d 50, else 100); `assess(engine, sent_today, daily_limit, age_days, auto_reply, reply_scope, auto_replies_today) -> {level: low|medium|high, reasons: [str], recommended_limit: int|None}`. `pacing()` gains `safety` and `safe_mode` keys.

- [x] Tests: meta engine → low; web + new number + limit above ramp → high; auto-reply to everyone raises level; safe mode off → pacing daily_limit unchanged; safe mode on → min(limit, ramp).
- [x] Implement.

### Task 4: Email deliverability

**Files:** Create `backend/email_campaigns/deliverability.py`, `backend/email_campaigns/bounces.py`. Modify `backend/database.py` (`email_sender_profiles.daily_limit INTEGER DEFAULT 0`, table `email_bounces`, helpers), `backend/email_campaigns/service.py` (run_batch: bounced-address block + per-sender daily limit pause), `backend/reply_detector.py` (bounce hook before reply matching), `backend/routers/email_senders.py` (`GET /{id}/deliverability`, `daily_limit` writable), `frontend/src/components/settings/EmailSendersSection.jsx`. Test: `tests/test_deliverability.py`.

**Interfaces:** `check_domain(domain, resolve=None) -> {domain, spf, dmarc, dkim, mx, advice}` (each `{status: ok|missing|weak|unknown, detail}`); `parse_bounce(from_email, subject, body, own=()) -> list[str]|None`; db `record_bounce(email, reason)`, `is_email_bounced(email)`, `sender_sent_today(profile_id)`, `pause_campaigns_over_bounce_rate(threshold=0.05, min_sent=20)`.

- [x] Tests: SPF/DMARC/DKIM parsing with a fake resolver (present, missing, `p=none` weak, `+all` weak); gmail.com advice; parse_bounce on a Gmail DSN, on a normal reply (None), never returns own address; run_batch skips bounced address; run_batch pauses at the sender's daily limit; limit 0 unchanged; bounce rate ≥5% of ≥20 sent pauses the campaign.
- [x] Implement.

### Task 5: Client results + industry quick setup

**Files:** Create `backend/industry_presets.py`, `frontend/src/components/ResultsCard.jsx`, `frontend/src/components/settings/QuickSetupWizard.jsx`. Modify `backend/database.py` (`get_results_report(days)`), `backend/main.py` (`GET /api/stats/results`), `backend/routers/settings_router.py` (`GET /industry-presets`), `frontend/src/pages/Dashboard.jsx`, Settings page. Test: `tests/test_results_report.py`.

**Interfaces:** `get_results_report(days=7) -> {days, current: {...}, previous: {...}}` with keys `leads_found, contacted, replies, interested, meetings, won`; `PRESETS: list[{id, label, services, pains, keywords}]`, `build_dna(preset_id, business_name, city, services) -> str`.

- [x] Tests: counts split by period correctly (seeded leads, stage history, replies, WhatsApp IN); preset DNA contains business name and never invents claims (no %, $, "guarantee").
- [x] Implement; wizard writes Company DNA through the existing `PUT /api/settings/dna`.

### Final verification

- [x] `venv/bin/python -m pytest -q` — all pass (baseline 984).
- [x] `cd frontend && npm run build`.
- [x] Browser check of the audit page, proof block, WhatsApp risk card, sender DNS check, results card and wizard on a throwaway DB.
