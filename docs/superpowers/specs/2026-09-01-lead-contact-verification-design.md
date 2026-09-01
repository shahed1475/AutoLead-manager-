# Phase 5C — Lead Contact Verification (Design)

**Date:** 2026-09-01
**Branch:** `feat/email-campaigns`
**Checkpoint:** 5C (second implementation phase of Checkpoint 5 — follows 5B "Lead Search → Email Campaign Handoff", precedes 5D/5E "Lead Intelligence Reports").
**Status:** awaiting user approval (`APPROVED — IMPLEMENT 5C`).

Skills applied in this design: `superpowers:brainstorming`, `autolead-lead-generation-architecture`,
`autolead-backend-architecture`, `autolead-database-and-migrations`,
`autolead-discovery-and-source-adapters` (run/poll pattern), `autolead-reliability-and-background-jobs`
(JobQueue), `autolead-outreach-safety` (§11), `autolead-security-and-secrets` (§10),
`autolead-frontend-architecture` / `autolead-product-ux` (§9),
`autolead-verification-before-completion` (§12/§16). `autolead-ai-llm-engineering` evaluated and
**not applicable** — 5C is 100% deterministic, no LLM call anywhere. n8n skill evaluated —
**not applicable except as a negative constraint** (§11: verification must never reach n8n).

---

## 1. Scope

### 1.1 In scope

Wire the disabled **"Verify Selected"** control on Lead Search (`LeadSearch.jsx`) to a
deterministic contact-verification workflow:

- A new `backend/verification/` package behind a **`contact_verification_enabled`** feature
  flag (default **OFF**, mirrors `email_campaigns_enabled` / `sales_intelligence_enabled`).
- **Deterministic + DNS** email verification: syntax → role-account → disposable-domain →
  live **MX** lookup → **A/AAAA** fallback lookup.
- **Deterministic** phone verification via the offline **`phonenumbers`** library: parse,
  validity, region, line-type.
- A **format-based WhatsApp capability hint** derived solely from phone line-type — labelled
  everywhere as *"WhatsApp capability hint — format-based only"*, never as verified / reachable /
  active / confirmed.
- A **background job** on the existing `JobQueue` with a run row, progress polling, refresh
  reconnect, and cooperative cancellation — mirroring the Quick Search discovery-run pattern.
- A dedicated **`lead_contact_verification`** table (one current row per lead) plus a
  **`verification_runs`** run-tracker table. Both additive; **no existing table is altered**.
- **Rolled-up status** per lead: exactly one of **`VALID` / `RISKY` / `INVALID` / `UNVERIFIED`**.
- UI: a **verification badge column** on the Lead Search results table **and** the main
  `Leads.jsx` table (display-only there), plus a read-only **detail drawer** with the per-check
  breakdown.
- The 5B campaign handoff folds a compact verification snapshot into the handoff
  `raw_json._handoff.verification` — **only** when the flag is on; a single guarded, additive
  change to `email_campaigns/service.py`.
- A dedicated safety regression test — **`test_verification_never_touches_a_send_path`** — of
  equal strength to 5B's `test_handoff_never_touches_a_send_path`.

### 1.2 Explicitly out of scope

- **No SMTP probing** — no connection to port 25, no `MAIL FROM`/`RCPT TO`, no mailbox-existence
  claim of any kind.
- **No paid verification API, no third-party service, no API key.**
- **No LLM / AI** verification or classification.
- **No live WhatsApp reachability** — no WhatsApp Desktop, no `pyautogui`, no WhatsApp API, no
  `whatsapp_sender`.
- **No email or WhatsApp sending. No campaign start. No scheduler. No n8n. No outbound path.**
- **No mutation of the `leads` row** — `leads.email`, `leads.status`, `leads.verified_email`
  (the legacy unused column) are never written by 5C.
- **No "Verify Selected" action on `Leads.jsx`** — 5C only *displays* the badge there.
  Selection on `Leads.jsx` remains deferred (per the 5B spec).
- **No verification history** — the table keeps one current row per lead (UPSERT). The full last
  result blob is retained in `raw_json`; earlier results are not kept.
- No 5D/5E work (reports / PDF / DOCX / ZIP).
- No re-architecture of 5B. The only 5B code touched is the one guarded snapshot fold-in.
- No change to the pre-existing Quick Search discovery poll (its background-tab pause quirk,
  recorded as "D1" in the 5B verification, is not a 5C bug and is not fixed here — but the **new**
  verification poll is built with `refetchIntervalInBackground: true` from day one).

---

## 2. User Flow

1. User runs a Quick Search on `/lead-search` and gets results (existing 5B behaviour).
2. User ticks one or more result rows → the 5B selection action bar appears.
3. **If `contact_verification_enabled` is OFF:** "Verify Selected" stays disabled with tooltip
   *"Enable Contact Verification in Settings"*. Nothing else happens.
4. **If ON:** "Verify Selected" is enabled whenever `selected.size > 0`. User clicks it.
   - If the selection is `> 100` leads, a confirm toast appears: *"Verify N leads? Runs in the
     background."* — user confirms.
5. Client `POST /api/verification/runs { lead_ids: [...] }` → `201 { run_id, status, requested_count }`.
   Client stores `run_id` in `localStorage['autolead_verification_run']`.
6. UI shows an inline progress panel above the results table:
   *"Verifying… 12 / 40"* with a **Cancel** button, driven by polling
   `GET /api/verification/runs/{run_id}` every 1500 ms while the run is active
   (`refetchIntervalInBackground: true`).
7. As the run progresses, the backend commits one `lead_contact_verification` row per lead.
8. On completion (`status === COMPLETED`), the client fetches
   `GET /api/verification/runs/{run_id}/results`, merges the per-lead verification state into the
   results-table rows (keyed by `lead_id`), and the **Verification** badge column populates.
9. **Selection is preserved** — the user can immediately hand off the `VALID` subset to a campaign
   (5B), or select a different subset.
10. Clicking a badge opens the **detail drawer**: email checks, phone checks, DNS result, MX
    hosts, line-type, the format-based WhatsApp hint, `checked_at`, and any per-lead error.
11. **Errors:**
    - Run-level fatal error → `status === FAILED`, an error state with **Retry** (re-submits the
      same `lead_ids`).
    - A single lead's check raising an exception → that lead gets a row with `status = UNVERIFIED`
      and an `error` note; the run still completes for every other lead.
    - Feature disabled between selection and click → `503` → toast *"Enable Contact Verification
      in Settings."*, no run created.
12. **Browser refresh / navigation away and back:** on mount the client calls
    `GET /api/verification/runs/active`; if an in-flight run exists it reconnects and resumes the
    progress panel; if the most recent run is terminal it shows its results.
13. On the main **`/leads`** page the same badge column is shown (read-only, flag-gated) using a
    batch lookup for the visible page of leads.

---

## 3. Architecture

```
LeadSearch.jsx  (selected: Set<lead_id>, existing 5B state)
   │  click "Verify Selected"  (enabled iff flag ON && selected.size > 0)
   ▼
verificationApi.startRun({ lead_ids })
   │  POST /api/verification/runs           router: verification.py  [_authed + require_feature_enabled]
   ▼
db.create_verification_run({ lead_ids })  → verification_runs row (QUEUED)
   │
queue.enqueue_nowait("CONTACT_VERIFICATION", { run_id }, engine.run_verification_job)   ── existing JobQueue
   │        (enqueue fails → run FAILED + HTTP 503)
   ▼
engine.run_verification_job(payload)                                     ── NEW, one JobQueue handler
   ├─ update_verification_run(run_id, status=RUNNING, started_at=now)
   ├─ leads_map = db.get_leads_by_ids(run.lead_ids)                      ── existing (also used by 5B)
   ├─ dns_cache = {}                                                     ── per-run domain cache
   ├─ for each lead_id in run.lead_ids:
   │     if run.status == CANCEL_REQUESTED → set CANCELLED, return
   │     if lead_id not in leads_map → not_found, continue
   │     try:    result = engine.verify_contact(lead, dns_cache)         ── NEW, pure per-lead
   │     except: result = {status: UNVERIFIED, error: repr(exc)}; error_count += 1   (isolation)
   │     db.upsert_lead_contact_verification(lead_id, run_id, result)    ── NEW  (UNIQUE(lead_id))
   │     db.increment_verification_run_progress(run_id, result.status)
   └─ set COMPLETED, finished_at
   │
LeadSearch.jsx  polls GET /runs/{id}  → progress; on COMPLETED → GET /runs/{id}/results
   ▼
ResultsTable rows merged with verification state → <VerificationBadge> column
   click badge → <VerificationDetailDrawer>  (read-only per-check breakdown)

Leads.jsx  → GET /api/verification/leads?lead_ids=…  → same badge column (display-only)

5B handoff (unchanged path, additive read):
  add_leads_from_db(campaign_id, lead_ids)
    if contact_verification_enabled:
        v_by_id = db.get_lead_contact_verifications_by_ids(lead_ids)     ── NEW, one query
    _lead_row_to_campaign_row(campaign_id, lead, verification=v_by_id.get(lead["id"]))
        raw["_handoff"]["verification"] = {status, email_status, phone_status,
                                           whatsapp_hint, checked_at}    ── additive key only
```

### 3.1 Units

| Unit | Responsibility | Interface | Depends on |
|---|---|---|---|
| `verification/status_rules.py` | pure roll-up: per-contact statuses → lead status | `roll_up(email_status, phone_status) -> str` | — |
| `verification/email_checks.py` | syntax, role, disposable, DNS → `email_status` + evidence | `check_email(email, dns_cache) -> dict` | `validators`, `dns_lookup`, `disposable_domains` |
| `verification/phone_checks.py` | parse, validity, region, line-type, WhatsApp hint | `check_phone(phone, default_region) -> dict` | `phonenumbers` |
| `verification/dns_lookup.py` | async MX then A/AAAA lookup, timeout, per-run cache | `resolve_domain(domain, cache) -> str` | `dnspython` |
| `verification/disposable_domains.py` | load + query the vendored blocklist | `is_disposable(domain) -> bool` | data file |
| `verification/engine.py` | one-lead `verify_contact`; the JobQueue handler `run_verification_job` | `verify_contact(lead, dns_cache) -> dict`, `run_verification_job(payload) -> None` | all of the above + `database` |
| `verification/service.py` | feature flag, run creation + summary shaping, activity logging | `is_feature_enabled()`, `start_run(lead_ids)`, `get_run_public(id)`, `get_results(id)` | `database`, `config`, `queue_worker` |
| `routers/verification.py` | HTTP surface, validation, feature gate, rate limit | 6 FastAPI routes | `verification/service.py`, `database` |
| `VerificationBadge.jsx` | status → coloured pill + icon | `<VerificationBadge status running? />` | `verificationBadges.js` |
| `VerificationDetailDrawer.jsx` | read-only per-check breakdown for one lead | `<… verification onClose />` | — |

---

## 4. Verification Rules

### 4.1 Email — ordered checks

`check_email(email, dns_cache)` returns
`{ email, email_status, syntax_ok, is_role, is_disposable, domain, dns_result, mx_hosts }`.

| Step | Rule | Data |
|---|---|---|
| 0. Presence | `email` empty/None → `email_status = MISSING`, stop. | — |
| 1. Syntax | Reuse `validators.is_valid_email` (RFC-ish regex, length ≤ 254, rejects known placeholder domains and spam locals). Fail → `syntax_ok = False`, `email_status = INVALID`, stop. | `validators.py` |
| 2. Normalise | lowercase, strip; split `local@domain`. | — |
| 3. Role account | `local` ∈ role set → `is_role = True`. Role set = `validators._SPAM_LOCALS` ∪ `{info, sales, contact, hello, hi, admin, support, office, team, marketing, enquiries, inquiries, help, mail, billing, accounts, accounting, hr, jobs, careers, recruitment, press, media, pr, general, reception, frontdesk, service, customerservice, orders, booking, bookings}`. Role is **not** disqualifying — it downgrades to `RISKY` later. | static |
| 4. Disposable | `domain` (or its registrable suffix) ∈ vendored blocklist → `is_disposable = True` → `email_status = INVALID`, stop (a throwaway address is not a real business contact). | `disposable_domains.txt` |
| 5. DNS — MX | `dns_lookup.resolve_domain(domain, cache)`. ≥ 1 MX record → `dns_result = MX_FOUND`, capture up to 5 MX host names in `mx_hosts`. | dnspython |
| 6. DNS — A/AAAA fallback | No MX, but an `A` or `AAAA` record exists → `dns_result = A_ONLY` (RFC 5321 §5.1: a host with an A record but no MX is still a valid mail target — but weaker signal). | dnspython |
| 7. DNS — none | `NXDOMAIN`, or the domain resolves to nothing usable → `dns_result = NO_RECORDS`. | dnspython |
| 8. DNS — timeout | Lookup exceeds the lifetime budget → `dns_result = TIMEOUT`. | dnspython |
| 9. DNS — error | `dns.resolver.NoNameservers`, socket error, misc → `dns_result = DNS_ERROR`. | dnspython |
| 10. Roll-up | see table 4.3. | — |

DNS budget: `verification_dns_timeout_seconds` (default **3.0** s per query) and
`verification_dns_lifetime_seconds` (default **5.0** s total incl. retries). Per-run in-memory
cache keyed by lowercase domain — repeated domains in one batch cost one lookup.

### 4.2 Phone — checks

`check_phone(phone, default_region)` returns
`{ phone, phone_status, e164, region, line_type, whatsapp_hint }`.
`default_region` derives from the lead's `country` (mapped to ISO-3166 alpha-2; `None` if unknown —
`phonenumbers` still parses `+`-prefixed international numbers without a region).

| Step | Rule |
|---|---|
| 0. Presence | empty/None → `phone_status = MISSING`, `whatsapp_hint = unknown`, stop. |
| 1. Parse | `phonenumbers.parse(phone, default_region)`. `NumberParseException` → `phone_status = INVALID`, `whatsapp_hint = unknown`, stop. |
| 2. Validity | `phonenumbers.is_valid_number(parsed)` False → `phone_status = INVALID`, `whatsapp_hint = unknown`, stop. |
| 3. Normalise | `e164 = format_number(parsed, E164)`; `region = region_code_for_number(parsed)`. |
| 4. Line type | `number_type(parsed)` → one of `MOBILE`, `FIXED_LINE`, `FIXED_LINE_OR_MOBILE`, `VOIP`, `TOLL_FREE`, `PREMIUM_RATE`, `SHARED_COST`, `PAGER`, `UAN`, `VOICEMAIL`, `UNKNOWN`. |
| 5. Status | `phone_status = VALID`. |
| 6. WhatsApp **hint** (format-based only) | `MOBILE` → `possible`; `FIXED_LINE_OR_MOBILE` → `possible`; any other line type → `unlikely`; missing/invalid (steps 0-2) → `unknown`. |

Phone has **no `RISKY`** state — offline metadata is either a valid dial plan or it isn't.
`line_type` and `whatsapp_hint` are **informational** and never change a status.

### 4.3 Per-contact → lead status roll-up

`email_status ∈ {VALID, RISKY, INVALID, MISSING}` (RISKY assigned here from the DNS result +
role flag):

| email_status is `RISKY` when | |
|---|---|
| syntax OK, not disposable, and (`is_role` **or** `dns_result ∈ {A_ONLY, TIMEOUT, DNS_ERROR}`) | "usable but flagged / unconfirmed" |
| email_status is `VALID` when | syntax OK, not role, not disposable, `dns_result == MX_FOUND` |
| email_status is `INVALID` when | fails syntax, **or** disposable, **or** `dns_result == NO_RECORDS` |
| email_status is `MISSING` when | no email on the lead |

Lead-level status (`lead_contact_verification.status`):

| email_status | phone_status | → **lead status** | rationale |
|---|---|---|---|
| `VALID`   | any | **VALID** | deliverable domain, non-role, non-disposable |
| `RISKY`   | any | **RISKY** | email usable but flagged (role / A-only / DNS unconfirmed) |
| `INVALID` | `VALID` | **RISKY** | email dead, but a valid phone exists — salvageable, needs a manual email |
| `INVALID` | `INVALID` or `MISSING` | **INVALID** | no usable contact path |
| `MISSING` | `VALID` | **RISKY** | no email at all, but reachable by phone — manual work |
| `MISSING` | `INVALID` or `MISSING` | **INVALID** | no contact info |
| (per-lead check raised) | — | **UNVERIFIED** | row written with `error` set; user can re-verify |
| (no row exists) | — | **UNVERIFIED** | default display state before a run touches the lead |

### 4.4 Named example combinations (from the brief)

| Combination | email_status | phone_status | lead status |
|---|---|---|---|
| valid email + valid phone | VALID | VALID | **VALID** |
| valid email + role account | RISKY | (any) | **RISKY** |
| valid email + disposable domain | INVALID | (any) | INVALID unless phone VALID → **RISKY** |
| invalid email + valid phone | INVALID | VALID | **RISKY** |
| valid syntax + dead domain (`NO_RECORDS`) | INVALID | (any) | INVALID unless phone VALID → **RISKY** |
| missing email + valid phone | MISSING | VALID | **RISKY** |
| missing email + missing phone | MISSING | MISSING | **INVALID** |
| DNS timeout | RISKY | (any) | **RISKY** |
| DNS failure (`DNS_ERROR`) | RISKY | (any) | **RISKY** |
| malformed phone | (by email) | INVALID | driven by email_status; phone contributes nothing |
| duplicate leads in the selection | — | — | de-duplicated to distinct `lead_ids` before processing; one row per lead |

> **Deterministic guarantee:** `roll_up()` is a pure total function over the finite product of
> `{VALID,RISKY,INVALID,MISSING} × {VALID,INVALID,MISSING}` plus the two sentinel cases. Every
> cell is covered by a parametrised test (§12).

### 4.5 Honesty constraints (hard requirements)

The system **must** distinguish and **must never conflate**:

- *syntactically valid email* (`syntax_ok = True`) — a well-formed address string.
- *live email domain* (`dns_result = MX_FOUND` or `A_ONLY`) — the domain can plausibly receive mail.
- *role-based email* (`is_role = True`) — `info@`, `sales@`, …
- *disposable email* (`is_disposable = True`) — a throwaway domain.
- *invalid email* — fails syntax, or disposable, or domain cannot receive mail.
- *domain with no usable MX/A record* (`dns_result = NO_RECORDS`).
- *valid phone* / *invalid phone* — dial-plan validity per `phonenumbers`.

The system **must never** state or imply that DNS verification proves an individual **mailbox
exists**. API field names, DB column names, UI copy, drawer labels, docs, and tests all preserve
this. The WhatsApp hint is labelled **"WhatsApp capability hint — format-based only"** and never
"verified / reachable / active / confirmed".

---

## 5. Status Calculation

Exactly four lead-level statuses, produced only by `status_rules.roll_up`:

| Status | Meaning | Produced when |
|---|---|---|
| **VALID** | High confidence there is a usable, deliverable email contact. | `email_status == VALID` (syntax OK, non-role, non-disposable, `MX_FOUND`). |
| **RISKY** | A contact path exists but carries a caveat the user should see. | `email_status == RISKY`; **or** email `INVALID`/`MISSING` **and** `phone_status == VALID`. |
| **INVALID** | No usable contact path found. | email `INVALID`/`MISSING` **and** phone `INVALID`/`MISSING`. |
| **UNVERIFIED** | No confident result. | No verification row yet, **or** the per-lead check raised (row has `error`). |

Notes:

- `RISKY` is **informational, not a gate.** A `RISKY` (or `INVALID`, or `UNVERIFIED`) lead can
  still be selected and handed off to a campaign — exactly as `MISSING_EMAIL` leads already flow
  through 5B. 5C never blocks an action; it annotates.
- The run-tracker's `valid_count / risky_count / invalid_count / unverified_count` are the tally
  of these four values across the run. `error_count` counts per-lead exceptions (a subset of
  `unverified_count`). `not_found` ids are listed separately and are **not** counted in any bucket.

---

## 6. Database Schema

Two new tables, appended to `_SCHEMA_SQL` in `backend/database.py` (created via the existing
`CREATE TABLE IF NOT EXISTS` path in `_run_migrations`; **no `ALTER`, no change to any existing
table**).

### 6.1 `verification_runs` — one row per "Verify Selected" click

```sql
CREATE TABLE IF NOT EXISTS verification_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    status            TEXT    NOT NULL DEFAULT 'QUEUED',  -- QUEUED|RUNNING|COMPLETED|FAILED|CANCEL_REQUESTED|CANCELLED
    requested_count   INTEGER NOT NULL DEFAULT 0,         -- distinct lead_ids requested
    processed_count   INTEGER NOT NULL DEFAULT 0,         -- leads with a row written so far (progress numerator)
    valid_count       INTEGER NOT NULL DEFAULT 0,
    risky_count       INTEGER NOT NULL DEFAULT 0,
    invalid_count     INTEGER NOT NULL DEFAULT 0,
    unverified_count  INTEGER NOT NULL DEFAULT 0,
    error_count       INTEGER NOT NULL DEFAULT 0,         -- per-lead exceptions (⊆ unverified_count)
    lead_ids_json     TEXT    NOT NULL,                   -- frozen, de-duplicated requested id list
    not_found_json    TEXT,                               -- JSON array of requested ids not present in `leads`
    error_message     TEXT,                               -- run-level fatal error (status=FAILED)
    started_at        TIMESTAMP,
    finished_at       TIMESTAMP,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_verification_runs_status ON verification_runs (status);
```

| Field | Why it exists |
|---|---|
| `id` | run identifier; returned to the client, used for polling/reconnect. |
| `status` | drives the progress panel and the poll's `refetchInterval` (active vs terminal). `CANCEL_REQUESTED` is the cooperative-cancel signal the worker checks between leads. |
| `requested_count` | progress denominator; the distinct count of `lead_ids` after de-dup. |
| `processed_count` | progress numerator; incremented as each `lead_contact_verification` row is committed. |
| `valid/risky/invalid/unverified_count` | live summary for the completion toast and the panel. |
| `error_count` | how many leads raised during their check (surfaced so the user knows some results are "we tried and failed", not "clean"). |
| `lead_ids_json` | the worker reads this to know exactly what to process — immune to any later selection change in the browser. |
| `not_found_json` | requested ids deleted between select and submit; reported, non-fatal (mirrors 5B `not_found`). |
| `error_message` | only set when the whole job fails (e.g. `get_leads_by_ids` throws) → `status = FAILED`. |
| `started_at` / `finished_at` / `created_at` | timing + ordering; `created_at DESC` powers `/runs/active`. |

### 6.2 `lead_contact_verification` — one **current** row per lead

```sql
CREATE TABLE IF NOT EXISTS lead_contact_verification (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id              INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    run_id               INTEGER REFERENCES verification_runs(id) ON DELETE SET NULL,

    status               TEXT NOT NULL,          -- VALID | RISKY | INVALID | UNVERIFIED

    -- email
    email                TEXT,                   -- normalised (lowercased) address checked, or NULL
    email_status         TEXT,                   -- VALID | RISKY | INVALID | MISSING
    email_syntax_ok      INTEGER,                -- 0/1
    email_is_role        INTEGER,                -- 0/1
    email_is_disposable  INTEGER,                -- 0/1
    email_domain         TEXT,
    email_dns_result     TEXT,                   -- MX_FOUND | A_ONLY | NO_RECORDS | TIMEOUT | DNS_ERROR | SKIPPED
    email_mx_hosts       TEXT,                   -- JSON array, ≤5 host names (evidence)

    -- phone
    phone                TEXT,                   -- raw value checked, or NULL
    phone_status         TEXT,                   -- VALID | INVALID | MISSING
    phone_e164           TEXT,
    phone_region         TEXT,                   -- ISO-3166 alpha-2
    phone_line_type      TEXT,                   -- MOBILE | FIXED_LINE | FIXED_LINE_OR_MOBILE | VOIP | ...
    whatsapp_hint        TEXT,                   -- possible | unlikely | unknown   (FORMAT-BASED ONLY)

    checked_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    duration_ms          INTEGER,
    error                TEXT,                   -- set only when this lead's check raised
    raw_json             TEXT,                   -- full structured result blob (audit / forward-compat)

    UNIQUE (lead_id)
);
CREATE INDEX IF NOT EXISTS idx_lcv_status ON lead_contact_verification (status);
CREATE INDEX IF NOT EXISTS idx_lcv_run    ON lead_contact_verification (run_id);
```

| Field group | Why |
|---|---|
| `lead_id` + `ON DELETE CASCADE` | per-lead fact; deleting a lead removes its verification (same pattern as every `backend/intelligence/` table). |
| `run_id` + `ON DELETE SET NULL` | which run last wrote this row (for `/runs/{id}/results`); keeping the verification if the run row is ever pruned. |
| `status` | the rolled-up value the badge shows; `NOT NULL` — always one of the 4. |
| `email_*` | every input to the roll-up, stored so the drawer can explain *why* a lead is `RISKY`/`INVALID` without re-running anything. `email_dns_result = SKIPPED` is used if a future path ever needs to skip DNS (not in 5C — 5C always attempts DNS when syntax passes). |
| `email_mx_hosts` | evidence: the actual MX targets found (capped at 5, JSON). |
| `phone_*` | phone inputs to the roll-up + the informational line-type. |
| `whatsapp_hint` | the format-only hint; values constrained to `possible|unlikely|unknown`. |
| `checked_at` | drives "verified 3 days ago" and a **stale** indicator after 14 days (badge gets a subtle dot; re-verify recommended). |
| `duration_ms` | per-lead timing (mostly DNS) — useful for diagnosing slow runs. |
| `error` | non-NULL ⇒ the check raised ⇒ `status = UNVERIFIED`; the drawer shows the message. |
| `raw_json` | the complete result dict `json.dumps(..., default=str)` — forward-compatible store; also the shape the 5B handoff snapshot is derived from. |
| `UNIQUE (lead_id)` | **latest-only** semantics: re-verifying a lead **UPSERTs** (`INSERT … ON CONFLICT(lead_id) DO UPDATE SET …`). No history table. |

**Re-verification:** every "Verify Selected" processes every selected lead and UPSERTs its row —
there is no "skip if recently verified" in 5C (kept simple; the user sees `checked_at` and a
stale hint and decides). **Retention:** rows live until the lead is deleted (CASCADE). No TTL,
no scheduled cleanup (single-tenant SQLite, negligible size).

### 6.3 Not touched

`leads` (incl. `leads.verified_email` — the legacy unused 0/1 column stays untouched),
`verification_results` (the unrelated decision-maker-scaffolding table — **name is similar,
purpose is different, 5C does not read or write it**), `campaign_runs`, `email_campaign_runs`,
`lead_discovery_runs`, `lead_sources`, every Phase 4 table.

---

## 7. API Contract

Router `backend/routers/verification.py`, prefix `/api/verification`, registered in `main.py`
with `dependencies=_authed` (single-operator session — same as every other router). Every route
additionally depends on **`require_feature_enabled`** → **`503`** when
`contact_verification_enabled` is off (config bool **or** `app_settings['contact_verification_enabled']`
truthy — identical resolution to `email_campaigns` `is_feature_enabled`).

Feature-disabled response (all routes):
`503 {"detail": "Contact Verification is not enabled on this instance (app_settings 'contact_verification_enabled')."}`

### 7.1 `POST /api/verification/runs` — start a verification run

- `@limiter.limit("10/minute")` (matches `discovery/search`).
- **Request** `VerifySelectedRequest` (defined in the router, per the 5B convention):

  | field | type | rules |
  |---|---|---|
  | `lead_ids` | `list[int]` | `Field(min_length=1, max_length=2000)`; de-duplicated server-side before processing |

- **Responses**
  - `201 { run_id, status, requested_count }` — `requested_count` = distinct id count.
  - `422` — empty `lead_ids`, `> 2000` ids, wrong types (Pydantic).
  - `503` — feature disabled; **or** JobQueue unavailable/full (run row is marked `FAILED` first,
    then `503` returned — mirrors `discovery`).
- **Side effects** — one `verification_runs` row (`QUEUED`), one `CONTACT_VERIFICATION` job
  enqueued, one `campaign_activity`-style log line is **not** used (verification has its own run
  row; no activity table needed).

### 7.2 `GET /api/verification/runs/{run_id}` — status / progress

- `200` — `{ id, status, requested_count, processed_count, valid_count, risky_count,
  invalid_count, unverified_count, error_count, not_found, error_message, started_at,
  finished_at, created_at }`.
- `404` — unknown `run_id`.
- `503` — feature disabled.

### 7.3 `GET /api/verification/runs/{run_id}/results` — per-lead results for this run

- `200` — `{ run_id, status, results: [ { lead_id, business_name, status, email_status,
  phone_status, whatsapp_hint, email_dns_result, email_is_role, email_is_disposable,
  phone_line_type, checked_at, error } ], not_found: [int] }`.
  (The full per-check detail incl. `email_mx_hosts`, `phone_e164`, `phone_region` is included so
  the drawer needs no extra call.)
- `404` / `503` as above.

### 7.4 `GET /api/verification/runs/active` — reconnect target

- Static path, declared **before** `/{run_id}`.
- `200` — the most recent `verification_runs` row (any status), or `null`.
- `503` — feature disabled.

### 7.5 `POST /api/verification/runs/{run_id}/cancel` — request cancellation

- If the run is terminal → returns it unchanged.
- Else → sets `status = CANCEL_REQUESTED`; the worker transitions it to `CANCELLED` at the next
  between-leads checkpoint. Returns the run row.
- `404` / `503` as above.

### 7.6 `GET /api/verification/leads?lead_ids=1,2,3` — current verification state for arbitrary leads

- Used by the results table merge, the `Leads.jsx` badge column, and (server-side) the 5B
  handoff fold-in.
- `lead_ids` — comma-separated ints, capped at 500 per call (page-sized).
- `200` — `{ verifications: { "<lead_id>": { status, email_status, phone_status, whatsapp_hint,
  checked_at, ... } | null } }` — `null` for a lead with no verification row.
- `422` — malformed / > 500 ids.
- `503` — feature disabled.

### 7.7 Validation summary

| Case | Result |
|---|---|
| `lead_ids` empty | `422` |
| `lead_ids` length > 2000 (`/runs`) or > 500 (`/leads`) | `422` |
| Duplicate ids | de-duplicated; `requested_count` is the distinct count |
| id deleted between select and submit | skipped, listed in `not_found`, non-fatal |
| feature flag off | `503`, **no run row, no job, no verification row** |
| JobQueue not started (tests without app lifespan) | run row → `FAILED`, `503` |
| JobQueue full | run row → `FAILED`, `503` |
| unknown `run_id` | `404` |

---

## 8. Background Job

**Job type:** `"CONTACT_VERIFICATION"`. **Handler:** `verification.engine.run_verification_job`.
**Enqueue:** `queue.enqueue_nowait("CONTACT_VERIFICATION", {"run_id": run_id}, run_verification_job)`
from `service.start_run`, exactly like `discovery.create_search`.

| Phase | Behaviour |
|---|---|
| **Creation** | `POST /runs` → `db.create_verification_run({lead_ids: distinct})` (status `QUEUED`) → enqueue. Enqueue returns `False` (queue full) or `get_queue()` is `None` → `db.update_verification_run(id, status=FAILED, error_message=...)` → `503`. |
| **Start** | Worker calls the handler → `update_verification_run(id, status=RUNNING, started_at=now)`. |
| **Execution** | `leads_map = db.get_leads_by_ids(run.lead_ids)`; `dns_cache = {}`. Iterate the frozen `lead_ids_json` order. Per lead: (a) re-read `run.status`; if `CANCEL_REQUESTED` → `status=CANCELLED, finished_at=now`, return. (b) `lead_id not in leads_map` → append to `not_found`, `continue`. (c) `try: result = verify_contact(lead, dns_cache)` `except Exception as e: result = {status:"UNVERIFIED", error: repr(e)[:500]}`, `error_count += 1`, and **log at WARNING with lead_id + exception type only** (never the email). (d) `db.upsert_lead_contact_verification(lead_id, run_id, result)`. (e) `db.increment_verification_run_progress(run_id, result["status"])` → `processed_count += 1` and the matching bucket. |
| **Progress** | The client polls `GET /runs/{id}`; `processed_count / requested_count` drives the bar. Poll interval 1500 ms while `status ∈ {QUEUED, RUNNING, CANCEL_REQUESTED}`, else stopped. |
| **Per-lead failure isolation** | One lead raising **never** aborts the run (mirrors the Browser Research Agent's per-candidate isolation). It becomes an `UNVERIFIED` row with `error` set; `error_count` increments; the run continues and still reaches `COMPLETED`. |
| **Completion** | After the loop: `update_verification_run(id, status=COMPLETED, finished_at=now, not_found_json=...)`. |
| **Run-level failure** | Only an exception *outside* the per-lead `try` (e.g. `get_leads_by_ids` throws, DB unavailable) → caught at the handler top level → `status=FAILED, error_message=repr(e)`, `finished_at=now`. The JobQueue worker's own outer `try/except` is the final backstop (already logs + increments `failed`). |
| **Retry** | **No automatic run retry.** DNS gets one retry inside its lifetime budget. The user retries by clicking "Verify Selected" again (new run, UPSERT is safe). Rationale: keeps the job idempotent and reasoning simple; a failed run is cheap to re-request. |
| **Cancellation** | Cooperative, checked once per lead (between leads). Worst case the user waits for the current lead's DNS lookup (≤ 5 s) before the run flips to `CANCELLED`. Leads already processed keep their rows (committed per-lead). |
| **Idempotency** | Each `POST /runs` = a new run. No idempotency key. Re-processing the same lead UPSERTs — last run wins. Two concurrent runs over overlapping leads = last-writer-wins per lead (acceptable; UI discourages a second run while one is active). |

Concurrency: the JobQueue has 4 workers, so up to 4 verification runs (or a mix with Quick
Search / research-agent jobs) run in parallel. DNS lookups are `async` (`dns.asyncresolver`), so a
worker isn't blocked. No global lock needed — verification touches only its own two tables +
read-only `leads`.

---

## 9. Frontend

### 9.1 `frontend/src/api/client.js` — `verificationApi`

```js
export const verificationApi = {
  startRun:   (payload) => api.post('/verification/runs', payload).then(r => r.data),
  runStatus:  (id)      => api.get(`/verification/runs/${id}`).then(r => r.data),
  runResults: (id)      => api.get(`/verification/runs/${id}/results`).then(r => r.data),
  activeRun:  ()        => api.get('/verification/runs/active').then(r => r.data),
  cancelRun:  (id)      => api.post(`/verification/runs/${id}/cancel`).then(r => r.data),
  leadStates: (ids)     => api.get('/verification/leads', { params: { lead_ids: ids.join(',') } }).then(r => r.data),
}
```

### 9.2 `frontend/src/pages/LeadSearch.jsx`

State added alongside the existing 5B `selected` Set:

```js
const [verifyRunId, setVerifyRunId] = useState(loadStoredVerifyRunId)   // localStorage 'autolead_verification_run'
```

- **Feature flag** — read `contact_verification_enabled` from the settings payload
  (`settingsApi.get`, already fetched app-wide) → `verifyEnabled`. Fallback: if the flag can't be
  read, the button attempts the call and a `503` shows the "enable in Settings" toast (same
  degradation as the 5B campaign modal).
- **"Verify Selected" button** (`SelectionActionBar`) — remove `disabled`/`opacity-50`; set
  `disabled={!verifyEnabled || selected.size === 0 || verifyRunActive}`,
  `title={verifyEnabled ? '' : 'Enable Contact Verification in Settings'}`,
  `onClick={handleVerify}`.
- `handleVerify` — if `selected.size > 100`, `window.confirm`-style toast confirm first; then
  `verificationApi.startRun({ lead_ids: [...selected] })` → store `run_id` → start polling.
  On error: `toast.error(isDisabledError(err) ? 'Enable Contact Verification in Settings.' : err.message)`.
- **Status poll** — `useQuery(['verify-status', verifyRunId], () => verificationApi.runStatus(verifyRunId), {
  enabled: !!verifyRunId, retry: false,
  refetchInterval: q => (VERIFY_ACTIVE.has(q.state.data?.status) ? 1500 : false),
  refetchIntervalInBackground: true })`.
  404 on the status → clear stored id (stale run).
- **Progress panel** — rendered above `ResultsTable` when `verifyRunActive`: spinner +
  *"Verifying… {processed_count} / {requested_count}"* + **Cancel** button
  (`verificationApi.cancelRun`).
- **Results merge** — when the run reaches `COMPLETED`/`CANCELLED`, `useQuery(['verify-results', verifyRunId], …)`
  fetches the per-lead results; build `verificationByLeadId` map; also merge any state from a
  page-level `verificationApi.leadStates([...visibleLeadIds])` on mount (so badges show for leads
  verified in a *previous* run too). Pass `verificationByLeadId` into `ResultsTable`.
- **Selection is preserved** after a verify run (no `clearSelection()` — unlike the campaign
  handoff). `useEffect(() => setSelected(new Set()), [runId])` (the *search* runId) still clears
  it on a new search; `verifyRunId` changes do **not** clear selection.
- **Reconnect** — on mount, if no `verifyRunId`, call `verificationApi.activeRun()`; if it returns
  a run, adopt its id and resume.
- **`ResultsTable`** — new trailing column `Verification` → `<VerificationBadge
  status={verificationByLeadId[lead.id]?.status} running={verifyRunActive}
  onClick={() => setDrawerLead(lead.id)} />`.
- **`<VerificationDetailDrawer>`** mounted when `drawerLead != null`.

### 9.3 `frontend/src/components/lead-search/VerificationBadge.jsx` (new)

Pure presentational. `status → { label, className, Icon }` via `verificationBadges.js`:

| status | label | colour | icon |
|---|---|---|---|
| `VALID` | "Valid" | green | `CheckCircle` |
| `RISKY` | "Risky" | amber | `AlertTriangle` |
| `INVALID` | "Invalid" | red | `XCircle` |
| `UNVERIFIED` / undefined | "Unverified" | slate | `MinusCircle` |
| (run active, no result yet) | spinner | slate | `Loader2` |

A subtle dot if `checked_at` older than 14 days ("stale — re-verify"). Clicking opens the drawer.

### 9.4 `frontend/src/components/lead-search/VerificationDetailDrawer.jsx` (new)

Read-only. Sections:

- **Email** — address, `syntax OK`, `role account` (yes/no), `disposable` (yes/no), `domain`,
  `DNS: MX found / A record only / no records / timeout / error`, MX hosts list.
- **Phone** — `E.164`, `region`, `line type`.
- **WhatsApp** — the value with the exact heading **"WhatsApp capability hint — format-based
  only"** and a one-line explainer: *"Inferred from the phone's line type. Not a check that the
  number has WhatsApp."*
- **Meta** — `checked_at`, `duration_ms`, `error` (if any).

No actions, no buttons except close.

### 9.5 `frontend/src/pages/Leads.jsx`

- Add a `Verification` column to the leads table, **display-only**, shown only when
  `contact_verification_enabled`.
- On each page render, `verificationApi.leadStates(visibleLeadIds)` → badge per row. Same
  `<VerificationBadge>` component; clicking opens the same drawer.
- **No "Verify Selected" / selection on `Leads.jsx`** in 5C.

### 9.6 `frontend/src/pages/Settings.jsx`

- Add a **"Contact Verification"** toggle in the same group as the existing "Email Campaigns" /
  "Sales Intelligence" toggles → `PATCH` the `contact_verification_enabled` setting.

### 9.7 State transitions & edge cases

| Situation | Behaviour |
|---|---|
| Empty selection | button disabled; action bar not shown at 0 (existing 5B). |
| Large selection (> 100) | confirm toast before starting. |
| Duplicate ids in selection | impossible (it's a `Set`); server also de-dups. |
| Already-verified leads | re-verified; row UPSERTed; badge updates; `checked_at` refreshed. |
| Partially verified (run cancelled/failed mid-way) | processed leads show their new badge; the rest keep whatever they had (or `Unverified`). |
| Failed run | error panel with **Retry** (re-submits the same `lead_ids`). |
| Per-lead error | that lead → `Unverified` badge, drawer shows the error; run still completes. |
| Browser refresh during a run | `activeRun()` reconnects; progress panel resumes. |
| Navigate away and back | same as refresh. |
| Feature turned off mid-session | next `startRun` → `503` → toast; button becomes disabled on next settings refetch. |
| Second "Verify Selected" while one is active | button disabled (`verifyRunActive`); user waits or cancels. |

---

## 10. Security & Privacy

- **No secrets, no API key, no auth surface added.** `contact_verification_enabled` is a plain
  boolean setting (config default + `app_settings` override). No credential is read, stored,
  logged, or returned.
- **External communication is DNS only.** The only outbound traffic 5C generates is standard
  DNS resolution (UDP/TCP port 53) via the OS resolver, for the domain of an email **already
  present on the lead**. No HTTP, no SMTP (port 25), no WebSocket, no third-party endpoint.
- **DNS privacy note (documented, accepted):** an MX/A lookup discloses the email's *domain* to
  the configured resolver. This is inherent to any deliverability check and is the minimum
  disclosure — the full address is never transmitted anywhere.
- **Logging discipline:** verification logs record `lead_id`, `domain`, `dns_result`, timings,
  and exception *types* — **never** the full email address, never the phone number, never
  `raw_json`. Enforced by a test that greps the log capture for `@` in a verification run.
- **Stored data:** `lead_contact_verification` stores the email/phone that were *already* on the
  lead plus derived booleans/enums. No new PII category. Local SQLite, single-tenant, no export
  path added in 5C.
- **Input safety:** `lead_ids` are ints (Pydantic), capped (2000 / 500). We never fetch a URL or
  resolve an attacker-controlled host beyond the lead's own email domain → no SSRF surface. DNS
  resolver has a hard lifetime budget → no hang, no amplification vector.
- **Rate limiting:** `POST /runs` is `@limiter.limit("10/minute")`.
- **Disposable-domain list** is vendored (committed) with a dated provenance comment; it is not
  fetched at runtime and is not security-sensitive (a stale entry at worst mis-labels one lead
  `RISKY` vs `INVALID`).

---

## 11. Safety — proof no send path can be reached

**Hard boundary:** the verification workflow must never invoke `send_email`, `_send_smtp`,
`send_whatsapp`, `_send_whatsapp_desktop`, `whatsapp_sender.*`, `pyautogui`, WhatsApp Desktop
automation, `email_campaigns.senders.resolve_transport`, `email_campaigns.n8n_client.*`,
`scheduler` dispatch, `smtplib`, `aiosmtplib`, campaign `start`/`prepare`, or any other outbound
communication path.

### 11.1 By construction

- **Imports:** the `backend/verification/` package imports only `dns` (dnspython),
  `phonenumbers`, `backend.database`, `backend.config`, `backend.validators`,
  `backend.queue_worker` (for `get_queue`), and stdlib. It does **not** import `email_sender`,
  `whatsapp_sender`, `email_campaigns.*`, `scheduler`, `routers.campaigns`, `pyautogui`,
  `smtplib`, or `aiosmtplib`. A test asserts this via `sys.modules` inspection after importing
  the package.
- **Call graph:** `POST /api/verification/*` → `service` → `db.create_verification_run` +
  `queue.enqueue_nowait` → `engine.run_verification_job` → (`db.get_leads_by_ids` read-only) +
  `engine.verify_contact` (→ `email_checks` → `dns_lookup`; `phone_checks` → `phonenumbers`) →
  `db.upsert_lead_contact_verification` + `db.increment_verification_run_progress`. Every leaf is
  a pure function or a read/write of the two new tables. There is no branch to a sender.
- **No SMTP:** `dns_lookup` uses `dns.asyncresolver.Resolver.resolve(...)` only. It never opens a
  socket to port 25. No code in the package constructs an SMTP client.
- **`leads` is read-only to 5C** — the engine reads via `db.get_leads_by_ids`; it calls no
  `leads` writer. A test snapshots the selected `leads` rows (hash) before and after a run and
  asserts byte-equality.
- **5B integration is a read** — `add_leads_from_db` gains a `get_lead_contact_verifications_by_ids`
  call and folds a dict key. 5B's send-path guards and DRAFT/`test_mode` behaviour are untouched.

### 11.2 By test — `tests/verification/test_verification_never_touches_a_send_path.py`

Equivalent in strength to `test_handoff_never_touches_a_send_path`. Using `monkeypatch` with a
raising sentinel (`def _boom(*a, **k): raise AssertionError("verification reached a send/outbound path")`):

- `backend.email_sender.send_email`, `backend.email_sender._send_smtp`
- `backend.whatsapp_sender.send_whatsapp`, `backend.whatsapp_sender._send_whatsapp_desktop`
- `backend.email_campaigns.senders.resolve_transport`
- `backend.email_campaigns.n8n_client.describe`
- `backend.scheduler._dispatch_send` (and any scheduler send entrypoint)
- `smtplib.SMTP`, `aiosmtplib.send`
- `pyautogui` — assert the module is not imported by the verification package (and patch
  `pyautogui.write`/`click`/`press` to `_boom` if it is importable in the test env)

Then: enable the flag, seed a mixed batch of leads (valid email + MX, role account, disposable,
dead domain, missing email, valid phone, garbage phone, one lead whose check is forced to raise),
run the full job to `COMPLETED`, and assert:

- none of the sentinels fired;
- `SELECT COUNT(*) FROM campaign_runs` and `FROM email_campaign_runs` are unchanged;
- every touched `leads` row is byte-identical to its pre-run snapshot;
- DNS is mocked (no real network in the test) — a fake resolver returns canned MX/A/NXDOMAIN.

### 11.3 n8n

n8n is never imported, referenced, or reachable from `backend/verification/`. The
`n8n_client.describe` sentinel in 11.2 covers the negative assertion.

---

## 12. Testing

### 12.1 Backend unit (`tests/verification/`)

| File | Covers |
|---|---|
| `test_status_rules.py` | **parametrised over the full `{VALID,RISKY,INVALID,MISSING}×{VALID,INVALID,MISSING}` grid + sentinels** — every cell of §4.3 and every named combination in §4.4. |
| `test_email_checks.py` | syntax pass/fail (reuse `validators` cases); role detection (positive + negative); disposable detection (in-list + registrable-suffix + not-in-list); each DNS result (`MX_FOUND`/`A_ONLY`/`NO_RECORDS`/`TIMEOUT`/`DNS_ERROR`) → correct `email_status`; `mx_hosts` captured and capped at 5. Resolver mocked. |
| `test_phone_checks.py` | valid US / UK / `+`-intl → `VALID` + correct `e164`/`region`/`line_type`; unparseable → `INVALID`; invalid-but-parseable → `INVALID`; missing → `MISSING`; `whatsapp_hint` mapping for MOBILE / FIXED_LINE_OR_MOBILE / FIXED_LINE / VOIP / missing. |
| `test_dns_lookup.py` | MX present → `MX_FOUND`; no MX + A → `A_ONLY`; NXDOMAIN → `NO_RECORDS`; slow resolver → `TIMEOUT` within budget; `NoNameservers` → `DNS_ERROR`; per-run cache hit (resolver called once for a repeated domain). `dns.asyncresolver` patched. |
| `test_disposable_domains.py` | list loads; known throwaway domains match; a real business domain does not; subdomain of a disposable domain matches. |
| `test_verification_engine.py` | `verify_contact(lead, cache)` end-to-end (mocked DNS) for ~8 lead shapes; a lead that makes a checker raise → returns `{status: UNVERIFIED, error: ...}` and does **not** propagate. |

### 12.2 Backend integration

| File | Covers |
|---|---|
| `test_verification_run.py` | enqueue + drain on the **real `JobQueue`** (mocked DNS): `QUEUED → RUNNING → COMPLETED`; `processed_count` / bucket counts correct; UPSERT overwrites on a second run for the same lead; `CANCEL_REQUESTED` between leads → `CANCELLED` with partial rows committed; `not_found` ids listed and non-fatal; a per-lead exception → `error_count` incremented, run still `COMPLETED`. |
| `test_verification_router.py` | `201` happy path + shape; `422` empty / `> 2000`; `404` unknown run; `/runs/active` reconnect; `/runs/{id}/results` shape incl. `not_found`; `/leads` batch shape + `null` for unverified + `422` `> 500`; `cancel` on a terminal run is a no-op. |
| `test_feature_flag_off.py` | flag OFF → `POST /runs` `503`; **assert no `verification_runs` row, no `lead_contact_verification` row, `queue.enqueue_nowait` not called** (patched + asserted); `GET /runs/active` and `/leads` also `503`. |
| `test_verification_never_touches_a_send_path.py` | **the §11.2 safety test.** |
| `test_5b_handoff_with_verification.py` | flag ON + verification rows present → `from-search` handoff still lands `DRAFT`, still `0` `email_campaign_runs`, and `raw_json._handoff.verification` carries `{status, email_status, phone_status, whatsapp_hint, checked_at}`; flag OFF → `raw_json` is **byte-identical** to the pre-5C shape (no `verification` key). |

### 12.3 Regression

- `venv/Scripts/python.exe -m pytest -q` — green; count = current **698** + the new 5C tests.
- All 21 `tests/test_campaign_from_search.py` pass **unchanged**.
- `tests/test_send_guards.py`, `test_followup_engine.py`, `test_replies_router.py`,
  `test_reply_detector.py`, `test_marketing_router.py` — unchanged and green.
- `cd frontend && npm run build` — clean.

### 12.4 Browser / end-to-end (claude-in-chrome, manual — mirrors the 5B verification method)

1. Flag **OFF**: `/lead-search` → select rows → "Verify Selected" disabled, tooltip present. No
   `/api/verification/*` call fires.
2. Toggle flag **ON** in Settings.
3. Select 3–5 leads → "Verify Selected" → progress panel counts up → completes → badges render
   (`Valid` / `Risky` / `Invalid`) → **selection still intact**.
4. Click a badge → drawer shows email checks, DNS result, MX hosts, phone line type, and the
   **"WhatsApp capability hint — format-based only"** label.
5. Start a run, **refresh mid-run** → reconnects, progress resumes.
6. Cancel a run → flips to `Cancelled`, partial badges present.
7. `/leads` → badge column visible (read-only), drawer opens.
8. Hand off a `Valid` lead to a campaign (5B) → campaign lands `DRAFT`, and its
   `email_campaign_leads.raw_json._handoff.verification` contains the snapshot.
9. **Network tab:** only `/api/verification/*` (+ existing calls). **No** send-path request.
10. **Backend log:** no `send_email` / `send_whatsapp` / `smtp` / `pyautogui` lines during the run.
11. Restart both servers → `/api/health` ok → repeat a core verify smoke test.

### 12.5 Data-flow verification

Trace one lead end-to-end: `leads` row → `engine.verify_contact` inputs → `email_checks` /
`phone_checks` outputs → `status_rules.roll_up` → `lead_contact_verification` row →
`/runs/{id}/results` payload → badge → drawer → 5B `raw_json` snapshot. Assert the value is
identical at every hop (no silent transformation).

---

## 13. Migration & Dependencies

### 13.1 Dependencies (`backend/requirements.txt`)

| Package | Why | Notes |
|---|---|---|
| `phonenumbers>=8.13` | phone parse / validity / region / line-type | **Offline** — bundles its own metadata, **no network**. ~a few MB. Widely used, permissive licence. |
| `dnspython>=2.6` | MX / A resolution | **Already installed transitively** (via `pydantic[email]` → `email_validator`); 5C now depends on it **directly**, so it is pinned explicitly (best practice). |

No other dependency. `email_validator` is present but **not used** — 5C rolls its own
deterministic syntax layer on top of the existing `validators.is_valid_email` for full control
and testability.

### 13.2 Database migration

- Purely additive: append the two `CREATE TABLE IF NOT EXISTS` blocks + their `CREATE INDEX IF
  NOT EXISTS` to `_SCHEMA_SQL` in `backend/database.py`. `_run_migrations` runs `executescript`
  on every startup — the new tables appear automatically on a fresh **or** existing DB.
- **No `ALTER TABLE`, no `_add_col_if_missing` entry, no change to any existing table.**
- Verified by: start the app against the existing `backend/data/leads.db` → "Schema migrations
  applied" with no error; `PRAGMA table_info` on every pre-existing table unchanged.

### 13.3 Config (`backend/config.py`)

```python
# ── Contact Verification (Checkpoint 5C) ────────────────────────────────────
contact_verification_enabled:        bool  = False   # DB override: app_settings 'contact_verification_enabled'
verification_dns_timeout_seconds:    float = 3.0     # per-query
verification_dns_lifetime_seconds:   float = 5.0     # total incl. retries
verification_max_leads_per_run:      int   = 2000
```

---

## 14. File-Level Plan

### 14.1 Create

**Backend**
- `backend/verification/__init__.py`
- `backend/verification/status_rules.py` — `roll_up(email_status, phone_status) -> str`; role/DNS → `email_status` promotion.
- `backend/verification/disposable_domains.py` — load + `is_disposable(domain)`.
- `backend/verification/data/disposable_domains.txt` — vendored blocklist (dated comment).
- `backend/verification/dns_lookup.py` — `async resolve_domain(domain, cache) -> str`.
- `backend/verification/email_checks.py` — `async check_email(email, cache) -> dict`.
- `backend/verification/phone_checks.py` — `check_phone(phone, default_region) -> dict`.
- `backend/verification/engine.py` — `async verify_contact(lead, cache) -> dict`; `async run_verification_job(payload) -> None`.
- `backend/verification/service.py` — `is_feature_enabled()`, `start_run(lead_ids)`, `get_run_public(id)`, `get_results(id)`, `get_active_run()`, `request_cancel(id)`, `get_lead_states(ids)`.
- `backend/routers/verification.py` — 6 routes + `VerifySelectedRequest` + `require_feature_enabled`.

**Backend tests**
- `tests/verification/__init__.py`
- `tests/verification/test_status_rules.py`
- `tests/verification/test_email_checks.py`
- `tests/verification/test_phone_checks.py`
- `tests/verification/test_dns_lookup.py`
- `tests/verification/test_disposable_domains.py`
- `tests/verification/test_verification_engine.py`
- `tests/verification/test_verification_run.py`
- `tests/verification/test_verification_router.py`
- `tests/verification/test_feature_flag_off.py`
- `tests/verification/test_verification_never_touches_a_send_path.py`
- `tests/verification/test_5b_handoff_with_verification.py`

**Frontend**
- `frontend/src/components/lead-search/VerificationBadge.jsx`
- `frontend/src/components/lead-search/VerificationDetailDrawer.jsx`
- `frontend/src/lib/verificationBadges.js`

**Docs**
- `docs/superpowers/specs/2026-09-01-lead-contact-verification-design.md` — this file.
- `docs/superpowers/plans/2026-09-01-lead-contact-verification.md` — the step-by-step TDD plan
  (produced via `superpowers:writing-plans` **after** approval).

### 14.2 Modify

| File | Change | Size |
|---|---|---|
| `backend/database.py` | append 2 `CREATE TABLE` + 3 `CREATE INDEX` to `_SCHEMA_SQL`; add ~8 helper fns (`create_verification_run`, `get_verification_run`, `update_verification_run`, `get_latest_verification_run`, `increment_verification_run_progress`, `upsert_lead_contact_verification`, `get_verification_results_for_run`, `get_lead_contact_verifications_by_ids`). **No existing table or fn touched.** | +~140 lines |
| `backend/config.py` | 4 new settings (§13.3). | +6 lines |
| `backend/requirements.txt` | `phonenumbers>=8.13`, `dnspython>=2.6`. | +2 lines |
| `backend/main.py` | `from .routers import verification as verification_router` + `app.include_router(verification_router.router, dependencies=_authed)`. | +2 lines |
| `backend/routers/settings_router.py` | expose `contact_verification_enabled` in the settings GET + allow it in the PATCH allow-list (mirror `email_campaigns_enabled` / `sales_intelligence_enabled`). | +~4 lines |
| `backend/email_campaigns/service.py` | `add_leads_from_db`: if `contact_verification_enabled`, batch-fetch `get_lead_contact_verifications_by_ids(lead_ids)`; `_lead_row_to_campaign_row(campaign_id, lead, verification=None)` gains an optional param and folds `raw["_handoff"]["verification"]` when a snapshot is passed. Guarded, additive. | +~12 lines |
| `frontend/src/api/client.js` | `verificationApi` object. | +8 lines |
| `frontend/src/pages/LeadSearch.jsx` | wire "Verify Selected": run state + localStorage, status/results polls (`refetchIntervalInBackground: true`), progress panel + Cancel, results merge, `Verification` column, drawer mount, flag gate. | +~90 lines |
| `frontend/src/pages/Leads.jsx` | display-only `Verification` column (flag-gated) + batch `leadStates` fetch + drawer mount. | +~40 lines |
| `frontend/src/pages/Settings.jsx` | "Contact Verification" toggle. | +~6 lines |

### 14.3 Delete

None.

---

## 15. Regression Protection

- **No existing DB table altered** — `git diff backend/database.py` shows only additive
  `_SCHEMA_SQL` lines + new helper fns; a test dumps `PRAGMA table_info` for `leads`,
  `email_campaign_leads`, `email_campaigns`, `lead_discovery_runs`, `campaign_runs` and asserts
  the column lists are unchanged.
- **`leads` never written by 5C** — enforced by the safety test's before/after row-hash check.
- **5B** — the only 5B file touched is `email_campaigns/service.py`, and only additively:
  `_lead_row_to_campaign_row` gains an optional 3rd param (default `None`) so its existing 2-arg
  callers and all 21 `test_campaign_from_search.py` cases pass unchanged; the extra `raw_json`
  key appears only when the flag is on and a snapshot exists.
- **Feature flag OFF ⇒ zero behaviour change** anywhere — the router 503s before any work, the
  `LeadSearch.jsx` button stays disabled, the badge columns are hidden, `_lead_row_to_campaign_row`
  takes its existing path. Verified line-by-line (the `sales_intelligence_enabled` standard).
- **Outreach guards** — `test_send_guards.py` et al. untouched; the new safety test adds
  coverage, removes none.
- **The Quick Search discovery flow** — untouched; 5C adds a sibling run type, it does not modify
  `routers/discovery.py`, `discovery/quick_search.py`, `lead_discovery_runs`, or the discovery
  poll in `LeadSearch.jsx` (the verify poll is a separate `useQuery`).
- **Full `pytest -q` + `npm run build` + browser E2E + send-path log audit + `git status`**
  (only intended files changed; pre-existing uncommitted 3A–4 work untouched) — the same
  acceptance bar 5B passed.

---

## 16. Acceptance Criteria

- [ ] `contact_verification_enabled` defaults `False`. With it `False`: `POST /api/verification/runs`
      → `503`, **no** `verification_runs` row, **no** `lead_contact_verification` row, **no** job
      enqueued (asserted); `LeadSearch.jsx` button disabled with tooltip; badge columns hidden;
      5B `raw_json` byte-identical to pre-5C.
- [ ] New tables created on a fresh **and** the existing DB with no migration error; **no existing
      table altered** (`PRAGMA` diff test passes).
- [ ] `phonenumbers` + `dnspython` pinned in `requirements.txt`; `pip install -r` clean; **no API
      key anywhere; no paid service; no SMTP; no LLM**.
- [ ] Email checks: syntax, role, disposable, MX, A-fallback — each unit-tested (DNS mocked);
      real DNS exercised in the manual E2E.
- [ ] Phone checks: valid / invalid / missing + region + line-type + `whatsapp_hint` — unit-tested.
- [ ] Every §4.3 grid cell and every §4.4 named combination has a passing parametrised test.
- [ ] Lead status is **always** one of `VALID | RISKY | INVALID | UNVERIFIED` — no other value
      reaches the DB or the API (constrained + tested).
- [ ] WhatsApp hint ∈ `{possible, unlikely, unknown}`, labelled **"WhatsApp capability hint —
      format-based only"** in the drawer, API docs, and this spec; a test asserts the label and
      that no "verified/reachable/active/confirmed" wording is attached anywhere.
- [ ] Background job: `QUEUED→RUNNING→COMPLETED`; progress counts accurate; cancel works between
      leads; a per-lead exception is isolated (run still `COMPLETED`); `not_found` ids reported.
- [ ] Re-verify UPSERTs — exactly one `lead_contact_verification` row per lead (`UNIQUE(lead_id)`).
- [ ] Refresh / navigate-away reconnect works via `/runs/active`.
- [ ] "Verify Selected" enabled **iff** flag on **and** `selected.size > 0` **and** no run active.
- [ ] Badges render on Lead Search results **and** `/leads`; drawer shows the full per-check
      breakdown; **selection preserved** after a run.
- [ ] **`test_verification_never_touches_a_send_path` passes** — raising sentinels on
      `send_email` / `_send_smtp` / `send_whatsapp` / `_send_whatsapp_desktop` / `resolve_transport`
      / `n8n_client` / scheduler / `smtplib` / `aiosmtplib` / `pyautogui`; `campaign_runs` and
      `email_campaign_runs` counts unchanged; `leads` rows byte-unchanged.
- [ ] All 21 `test_campaign_from_search.py` pass; 5B handoff still `DRAFT` with 0 runs;
      `raw_json._handoff.verification` present only with the flag on.
- [ ] Full `pytest -q` green (698 + 5C); `npm run build` clean.
- [ ] Browser E2E walkthrough (§12.4) passes; network shows only `/api/verification/*`; backend
      log shows no send-path activity; servers restart cleanly and the smoke test passes.
- [ ] `git status`: only the §14 files changed; pre-existing uncommitted 3A–4 work untouched; no
      commit made without explicit instruction.

---

## 17. Risks

| # | Risk | Mitigation |
|---|---|---|
| 1 | DNS lookups slow / flaky on some networks → long runs | 3 s per-query / 5 s lifetime budget; per-run domain cache; **background** job so the UI never blocks; `TIMEOUT`/`DNS_ERROR` → `RISKY` (honest "couldn't confirm"), never a hang. |
| 2 | `phonenumbers` line-type wrong for some regions (esp. outside NANP) | `line_type` and `whatsapp_hint` are **informational only** — they never affect `VALID/RISKY/INVALID`; both are labelled as hints. |
| 3 | Users read `RISKY` as "do not contact" | the drawer explains exactly why (role / A-only / DNS unconfirmed / phone-only); `RISKY`/`INVALID`/`UNVERIFIED` leads are **still selectable and still hand off** — 5C annotates, never gates. |
| 4 | Latest-only table loses verification history | acceptable for single-tenant; `raw_json` keeps the full last result; re-verify is cheap. `raw_json` + `run_id` give enough audit trail. Revisit if history is ever needed (append model would be the change). |
| 5 | DNS query discloses the email domain to the resolver | documented in §10; unavoidable for any deliverability check; the full address is never transmitted; no SMTP. |
| 6 | Disposable-domain list goes stale | vendored with a dated provenance comment; refresh = a 1-line data-file edit, no code change; a stale entry only mis-labels one lead `RISKY` vs `INVALID`. |
| 7 | 5B `_lead_row_to_campaign_row` signature change ripples | new param is optional with default `None` → fully backward compatible; existing 2-arg unit tests unchanged. |
| 8 | Frontend poll pausing in a background tab (the "D1" quirk from 5B verification) | the **new** verification poll sets `refetchIntervalInBackground: true` from day one. |
| 9 | Two concurrent verify runs over overlapping leads | UPSERT = last-writer-wins per lead (acceptable); the UI disables "Verify Selected" while a run is active. |
| 10 | `dnspython` async resolver behaviour differs across platforms / no nameserver configured | `dns_lookup` catches `NoNameservers`/`NoResolverConfiguration`/socket errors → `DNS_ERROR` → `RISKY`; a unit test covers the "no resolver" path. |
| 11 | Large batch (2000) of unique domains → 2000 sequential DNS lookups | acceptable in the background (≈ a few minutes worst case); could be parallelised later with a bounded `asyncio.Semaphore` — **not** in 5C (keep the first cut simple and observable). Noted as a follow-up. |
| 12 | `verification_results` table name confusion (existing decision-maker table) | 5C uses `lead_contact_verification` + `verification_runs`; a code-review checklist item confirms `verification_results` and `decision_makers` are never imported/queried by `backend/verification/`. |

---

## 18. Implementation Order (safest sequence)

Each step ends with its own verification; nothing proceeds on a red bar.

1. **Deps + config** — add `phonenumbers` / `dnspython` to `requirements.txt`, `pip install`; add
   the 4 settings to `config.py` (flag `False`). → app still boots; `pytest -q` still 698.
2. **DB layer** — append the 2 tables + indexes to `_SCHEMA_SQL`; add the ~8 helper fns;
   `tests/verification/test_db*` (creation, UPSERT, progress increment, cascade). → fresh-DB +
   existing-DB migration clean; `PRAGMA` diff test green.
3. **Pure check modules (TDD)** — `status_rules.py`, `disposable_domains.py`, `dns_lookup.py`
   (resolver mocked), `email_checks.py`, `phone_checks.py`. Full unit coverage incl. every §4.3
   / §4.4 case. No wiring yet.
4. **Engine + job** — `engine.verify_contact` + `run_verification_job`; `test_verification_run.py`
   (real `JobQueue`, mocked DNS): lifecycle, progress, cancel, per-lead isolation, `not_found`.
5. **Safety test first** — `test_verification_never_touches_a_send_path.py` **before** the router,
   so the boundary is locked as the surface grows.
6. **Router + feature flag** — `routers/verification.py`, `require_feature_enabled`, register in
   `main.py`; `test_verification_router.py` + `test_feature_flag_off.py`.
7. **Settings exposure** — `settings_router.py` + `Settings.jsx` toggle. → toggling on/off works.
8. **Frontend — Lead Search** — `verificationApi`; wire "Verify Selected"; poll + progress +
   cancel; results merge; `Verification` column; `VerificationBadge`; `VerificationDetailDrawer`;
   `verificationBadges.js`. → `npm run build`; browser E2E steps 1–6.
9. **Frontend — Leads page** — read-only badge column + drawer. → browser E2E step 7.
10. **5B handoff fold-in** — `email_campaigns/service.py` guarded snapshot;
    `test_5b_handoff_with_verification.py`; re-run all 21 `test_campaign_from_search.py`. →
    browser E2E step 8.
11. **Full regression** — `pytest -q` green (698 + 5C); `npm run build` clean; full browser E2E
    (incl. restart smoke, steps 9–11); send-path log audit; `git status` clean-except-intended.
12. **Docs** — finalise `docs/superpowers/plans/2026-09-01-lead-contact-verification.md`.
    (Commit staging left entirely to the user.)

---

## Appendix A — WhatsApp hint wording (locked)

The only permitted surface text for the hint:

- Heading: **"WhatsApp capability hint — format-based only"**
- Values: `possible` / `unlikely` / `unknown`
- Explainer: *"Inferred from the phone number's line type. This is not a check that the number
  has WhatsApp."*

**Forbidden** anywhere (UI, API field docs, DB comments, logs, tests, this spec): "WhatsApp
verified", "WhatsApp reachable", "WhatsApp active", "WhatsApp account confirmed", "WhatsApp number
confirmed". A test asserts these strings do not appear in the verification package or the two new
frontend components.
