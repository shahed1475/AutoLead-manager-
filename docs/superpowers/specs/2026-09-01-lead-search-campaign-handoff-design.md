# Phase 5B — Lead Search → Email Campaign Handoff (Design)

**Date:** 2026-09-01
**Branch:** `feat/email-campaigns`
**Checkpoint:** 5B (first implementation phase of Checkpoint 5 — see `2026-09-01` audit in the conversation record / Checkpoint 5A).
**Status:** approved for implementation planning.

---

## 1. Context

`LeadSearch.jsx` (`/lead-search`) runs Quick Search: a planner-driven, 1–2 source
discovery pass that persists results as `leads` rows via
`database.create_or_merge_lead` (+ `lead_sources` provenance) and shows them in a
**read-only** table (Business / Contact / Website / Score). There is no row
selection and no action a user can take on the results.

Checkpoint 5 turns Lead Search into a qualification + handoff workflow
(selection → verification → intelligence reports → email-campaign handoff).
**Phase 5B is the first and lowest-risk slice: selection + the campaign handoff.**
Verification (5C) and reports (5D/5E) are separate later phases.

### Why this is low-risk

- Quick Search results are **already** `leads` rows. The handoff is a pure
  read of `leads` → write of `email_campaign_leads`, linked by `lead_id`.
- Both DB primitives already exist: `database.get_leads_by_ids` (`database.py:1541`)
  and `database.bulk_insert_email_campaign_leads` (`database.py:3369`,
  `INSERT OR IGNORE` on `(campaign_id, lead_key)`).
- No new dependency, no schema change, no migration.
- Nothing in this phase touches a send path, a transport, the TEST_MODE gate,
  or n8n.

---

## 2. Goals

1. Select one / several / all Quick Search result leads, deselect, see a count,
   with selection surviving the 1.5 s status poll.
2. Send the selected leads into a **new** Email Campaign in one action, choosing
   a campaign name and (optionally) a Send From sender profile.
3. Preserve every selected lead's original discovered data; never modify the
   source `leads` row; never silently drop a selected lead.
4. Reuse the existing `EmailCampaignService` create + persistence flow and the
   Checkpoint 4 sender-profile system unchanged.

## 3. Non-goals (explicit)

- No contact verification (Phase 5C).
- No lead intelligence reports / PDF / DOCX / ZIP (Phase 5D/5E).
- No send, prepare, or "start campaign" action from Lead Search — the campaign
  lands `DRAFT` and the user drives prepare → ready → start on `/email-campaigns`
  as today.
- No "add to an existing campaign" path — every handoff creates a new campaign
  (decision: keeps one code path; user merges/renames later if needed).
- No selection on `Leads.jsx` — the same endpoint is reusable there later.
- No n8n involvement of any kind.
- No change to `email_campaigns_enabled` default, TEST_MODE, `safety.py`,
  `senders.resolve_transport`, or any transport.

---

## 4. Architecture

```
LeadSearch.jsx
  results (≤100, in browser + in `leads`)
      │  select rows  → selected: Set<lead_id>  (page-level React state)
      ▼
  action bar (visible when selected.size > 0)
   [N selected] [Clear] [Verify Selected 🔒] [Generate Reports 🔒] [Send to Email Campaign]
      │
      ▼  handoff modal: { name (prefilled), sender_profile_id? }
  emailCampaignsApi.createFromSearch({ name, sender_profile_id?, reply_to?, lead_ids })
      │
      ▼  POST /api/email-campaigns/from-search   (router: email_campaigns, _gated)
  EmailCampaignService.create_campaign({name, sender_profile_id, reply_to})   ── existing
      → always DRAFT, test_mode forced true, validates sender_profile_id
  EmailCampaignService.add_leads_from_db(campaign_id, lead_ids)               ── NEW
      → db.get_leads_by_ids                                                   ── existing
      → build normalized rows (see §6)
      → db.bulk_insert_email_campaign_leads  (INSERT OR IGNORE)               ── existing
      → db.recount_email_campaign                                             ── existing
  db.log_email_campaign_activity(cid, "leads_added_from_search", summary)     ── existing
      │
      ▼  201 { campaign_id, name, total_requested, added, skipped_existing,
               missing_email, invalid_email, not_found: [int] }
  toast "Created campaign 'X' with N leads" + "Open campaign →" (/email-campaigns)
```

### Units

| Unit | Responsibility | Interface | Depends on |
|---|---|---|---|
| `EmailCampaignService.add_leads_from_db` | map `leads` rows → campaign lead rows, persist via the existing bulk insert, return a summary | `add_leads_from_db(campaign_id: int, lead_ids: list[int]) -> dict` | `db.get_leads_by_ids`, `db.bulk_insert_email_campaign_leads`, `db.recount_email_campaign`, `validators.is_valid_email` |
| `POST /api/email-campaigns/from-search` handler | request validation, orchestrate create + add, shape the response | FastAPI route, `_gated` | `EmailCampaignService`, a new Pydantic model `CampaignFromSearchRequest` |
| `_lead_row_to_campaign_row(campaign_id, lead)` (module-level helper in `service.py`) | pure mapping of one lead dict → one normalized campaign-lead dict (with `raw_json` already serialised) | `(campaign_id: int, lead: dict) -> dict` | `validators.is_valid_email` |
| `LeadSearch.jsx` selection state + action bar | track `Set<lead_id>`, render the bar, open the modal | React state + props into `ResultsTable` | — |
| Handoff modal component | collect name + sender profile, call the mutation, report the result | props: `{ leadIds, defaultName, onDone }` | `emailCampaignsApi.createFromSearch`, `emailSendersApi.list` |

---

## 5. API contract

### `POST /api/email-campaigns/from-search`

Dependencies: `[Depends(auth.require_session), Depends(_require_feature_enabled)]`
(the existing `_gated` list on this router).

**Request body** — `CampaignFromSearchRequest`:

| field | type | rules |
|---|---|---|
| `name` | `str` | `Field(min_length=1, max_length=120)`; `create_campaign` then trims and rejects a whitespace-only name |
| `sender_profile_id` | `int \| None` | optional; must exist (validated by `create_campaign`) |
| `reply_to` | `str \| None` | optional; valid email if present (validated by `create_campaign`) |
| `lead_ids` | `list[int]` | required, 1..2000 items, de-duplicated server-side before processing |

**Responses**

- `201` — `{ campaign_id, name, total_requested, added, skipped_existing, missing_email, invalid_email, not_found }`
  - `total_requested` = count of distinct `lead_ids` after de-duplicating the input list
  - `added` = rows actually inserted (`bulk_insert_email_campaign_leads` return value)
  - `skipped_existing` = `len(built_rows) - added` — rows dropped by `INSERT OR IGNORE` because two selected leads produced the same `lead_key` (same email). A fresh campaign starts empty, so this is only ever same-email collisions within the selection.
  - `missing_email` / `invalid_email` = counts over the built rows, by resulting status
  - `not_found` = `list[int]` of requested ids not present in `leads`
- `422` — empty `lead_ids`, `> 2000` ids, or empty `name`
- `400` — `EmailCampaignError` (bad `sender_profile_id`, bad `reply_to`, name empty after trim)
- `503` — `email_campaigns_enabled` is off (existing `_require_feature_enabled`)

**Idempotency:** none at the endpoint level — each call creates a new campaign.
Within a call, `bulk_insert_email_campaign_leads` is idempotent on
`(campaign_id, lead_key)`, so a partial retry into the *same* campaign id (not
exposed by this endpoint, but possible via a future "add to existing") would not
double-insert.

**Rate limit:** `@limiter.limit("20/minute")` on the route (matches the
discovery/search precedent; a batch of 2000 is a heavy write).

---

## 6. Data mapping: `leads` row → `email_campaign_leads` row

`_lead_row_to_campaign_row(campaign_id, lead)`:

The returned dict uses the keys `bulk_insert_email_campaign_leads` reads:
`lead_key, lead_id, email, first_name, last_name, company, raw_json,
body_source, provided_body, status, status_detail`.

| key | value |
|---|---|
| `lead_key` | `f"{campaign_id}::{email_lc}"` when `is_valid_email(email)`, else `f"{campaign_id}::lead::{lead['id']}"` |
| `lead_id` | `lead["id"]` — the link back to the global lead |
| `email` | `(lead.get("email") or "").strip().lower() or None` |
| `first_name` | `""` (the `leads` table has no person-name field) |
| `last_name` | `""` |
| `company` | `lead.get("business_name")` |
| `raw_json` | `json.dumps({<full lead row>, "_handoff": {"source": "lead_search", "lead_id": lead["id"], "added_at": <iso>}}, default=str)` — a pre-serialised string, passed through by the bulk insert's `raw_json` branch (not the `raw` dict branch, which would `json.dumps` without `default=str`) |
| `body_source` | `"ai"` |
| `provided_body` | `None` |
| `status` | `"VALIDATED"` if `is_valid_email(email)`; `"MISSING_EMAIL"` if no email; `"INVALID_EMAIL"` if an email is present but fails `is_valid_email` |
| `status_detail` | `""` / `"no email on the discovered lead"` / `"email format is invalid"` |

Notes:

- **`lead_key` scheme** — email-keyed when possible so the same email selected
  twice, or a later file import of the same address, collapses via
  `INSERT OR IGNORE`. No-email leads key on `lead::{id}` — stable and unique per
  global lead.
- **`raw_json`** — the campaign's audit/immutability record. It carries the
  original discovered values (`business_name`, `phone`, `website`, `niche`,
  `city`, `source`, `score`, …) verbatim. Non-serialisable values (datetimes)
  are coerced with `default=str`. This is where Phase 5C will add a
  `verification` snapshot.
- The send loop already only acts on `VALIDATED` / `GENERATED`
  (`_SENDABLE_LEAD_STATUSES`), so `MISSING_EMAIL` / `INVALID_EMAIL` leads are
  visible in the campaign UI but never emailed.
- Build one row per found lead and pass them all to
  `bulk_insert_email_campaign_leads` unchanged — its `INSERT OR IGNORE` on
  `(campaign_id, lead_key)` drops same-email collisions, and its return value
  is `added`. Do **not** pre-de-duplicate the rows (that would make
  `skipped_existing` always 0).

---

## 7. Frontend

### `LeadSearch.jsx`

**Selection state** — in the `LeadSearch` component:

```js
const [selected, setSelected] = useState(() => new Set())   // lead ids
```

- Passed to `ResultsTable` as `{ selected, onToggle, onToggleAll }`.
- Cleared in `handleSubmit` (a new search) and by the action bar's "Clear".
- Survives the `statusQuery` / `resultsQuery` refetch — the results list only
  grows or stays; toggling operates on ids, not indices.

**`ResultsTable`** gains a leading `<th>`/`<td>` checkbox column:

- Header checkbox: `checked` when every rendered lead id is in `selected`;
  `indeterminate` (via a ref) when some but not all are.
- Row checkbox: `checked={selected.has(lead.id)}`, `onChange` → `onToggle(lead.id)`.
- Checkbox click must not trigger the row's link navigation (stop propagation).

**Action bar** — a `div` rendered above `ResultsTable` only when `selected.size > 0`,
using existing card / button classes:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  12 selected   [Clear]        [Verify Selected 🔒] [Generate Reports 🔒]      │
│                               [Send to Email Campaign]                        │
└──────────────────────────────────────────────────────────────────────────────┘
```

- `Verify Selected`, `Generate Reports`: `disabled`, `title="Coming in a later step"`,
  muted styling (`opacity-50 cursor-not-allowed`), a small lock icon.
- `Send to Email Campaign`: opens the handoff modal.

**Handoff modal** — new component `frontend/src/components/lead-search/SendToCampaignModal.jsx`:

- Props: `{ leadIds: number[], leads: object[], defaultName: string, onClose, onDone }`.
- Fields:
  - **Campaign name** — text input, pre-filled with `defaultName`
    (`` `${query.trim()} — ${location.trim()}` `` truncated to 120; falls back to
    `"Lead Search campaign"` if both empty).
  - **Send From** — `<select className="input">`: first option
    `"Default / global SMTP"` (value `""`), then one option per profile from
    `useQuery(['email-senders'], emailSendersApi.list)` labelled
    `` `${p.provider === 'gmail' ? 'Gmail' : 'SMTP'} — ${p.email_address}${p.status !== 'connected' ? ' (' + p.status + ')' : ''}` ``.
    Mirrors `EmailCampaigns.jsx:107-118`.
- Live summary line computed from `leads`:
  `"{N} leads · {M} have no email (added but not emailed)"`.
- If `N > 50`: a required checkbox `"Yes, add {N} leads"` gating the submit button.
- Submit → `useMutation` → `emailCampaignsApi.createFromSearch({ name, sender_profile_id: senderId ? Number(senderId) : undefined, lead_ids: leadIds })`.
  - Success: `toast.success(\`Created "${name}" with ${data.added} lead(s)\`)`;
    if `data.missing_email + data.invalid_email > 0` a second info toast; call
    `onDone()` (clears selection, closes modal); render an "Open campaign →"
    link (`<Link to="/email-campaigns">`).
  - Error: `toast.error(err.message)`; if the message matches `/not enabled|503/i`
    show `"Enable Email Campaigns first (Email Campaigns page → Enable)."`; modal
    stays open.

### `frontend/src/api/client.js`

Add to `emailCampaignsApi`:

```js
createFromSearch: (payload) =>
  api.post('/email-campaigns/from-search', payload).then((r) => r.data),
```

No new frontend dependency. No change to Sidebar / routing / other pages.

---

## 8. Error handling & edge cases

| Case | Behaviour |
|---|---|
| `lead_ids` empty | `422` "no leads selected" |
| `lead_ids` length > 2000 | `422` "too many leads (max 2000)" |
| Duplicate ids in `lead_ids` | De-duplicated before processing; `total_requested` is the distinct count |
| A `lead_id` was deleted between select and submit | Skipped; returned in `not_found`; not fatal |
| Two selected leads share an email | One campaign lead (same `lead_key`); counted in `skipped_existing` |
| Selected lead has no email | Added, `status = MISSING_EMAIL` |
| Selected lead has a malformed email | Added, `status = INVALID_EMAIL` |
| `sender_profile_id` does not exist | `create_campaign` raises `EmailCampaignError` → `400` |
| `reply_to` malformed | `create_campaign` raises → `400` |
| `name` empty / whitespace | `422` (Pydantic) or `400` (`create_campaign`) |
| `email_campaigns_enabled` off | `503` (existing `_require_feature_enabled`) |
| Campaign created but `add_leads_from_db` then fails | The empty `DRAFT` campaign remains; error returned; user can retry or delete it. (No transaction spanning both — acceptable: an empty draft is harmless and visible.) |

The campaign is `DRAFT` with `test_mode` truthy after the handoff. Prepare / mark-ready / start are unchanged and user-driven on `/email-campaigns`.

---

## 9. Security

- Endpoint behind the existing `_gated` (session + feature flag). No new auth surface.
- `@limiter.limit("20/minute")` — a 2000-lead batch is a heavy write; matches the `discovery/search` rate-limit precedent.
- `lead_ids` hard-capped at 2000 per call (DoS bound).
- No secret handling. `sender_profile_id` is an integer reference; credentials
  are never touched by this phase.
- `raw_json` is built from `leads` rows only (no scraped HTML rendered, no
  external fetch). It is stored, not returned to the browser in this phase
  (the campaign leads list endpoint already governs what campaign-lead fields
  are exposed).
- No file generation, no path handling, no n8n.

---

## 10. Testing

### `tests/test_campaign_from_search.py` (new)

Fixtures: reuse `clean_db`; a helper to insert N `leads` rows with controllable
email validity; enable `email_campaigns_enabled`.

1. **single valid lead** → `201`; campaign exists, status `DRAFT`, `test_mode` truthy;
   exactly 1 `email_campaign_leads` row; `lead_id` links to the global lead;
   status `VALIDATED`; summary `added=1`.
2. **mixed batch** — valid email / no email / malformed email → statuses
   `VALIDATED` / `MISSING_EMAIL` / `INVALID_EMAIL`; summary counts match.
3. **`raw_json` preservation** — parse `raw_json`, assert `business_name`,
   `phone`, `website`, `source`, `score` equal the original lead values and
   `_handoff.lead_id` is set.
4. **source lead untouched** — snapshot the `leads` row before, re-fetch after,
   assert byte-equal.
5. **duplicate email in selection** — two leads, same email → 1 campaign lead,
   `skipped_existing=1`.
6. **non-existent id** — `lead_ids=[real, 999999]` → `not_found=[999999]`,
   `added=1`, no error.
7. **bounds** — `lead_ids=[]` → `422`; `lead_ids` of length 2001 → `422`.
8. **sender profile** — create an SMTP profile, pass its id → campaign
   `sender_profile_id` persisted; pass a non-existent id → `400`.
9. **feature flag off** — disable `email_campaigns_enabled` → `503`.
10. **no-send guard** — patch `email_sender.send_email`,
    `email_campaigns.senders.resolve_transport`, and
    `email_campaigns.n8n_client` with a `_NeverCalled` sentinel (pattern from
    `tests/test_send_guards.py` / `tests/test_campaign_send_from.py`); run the
    full handoff; assert none were called.
11. **activity log** — a `leads_added_from_search` row exists with the summary
    in `detail` and no secret-shaped content.

### Regression

- `venv/Scripts/python.exe -m pytest -q` — green (adds to the current 677).
- `cd frontend && npm run build` — clean.
- Manual: start backend + frontend, run a Quick Search, select rows (individual
  + select-all + clear), open the modal, create a campaign, confirm it appears
  on `/email-campaigns` with the correct lead count, `DRAFT` status, and the
  chosen sender; confirm no email is sent.

---

## 11. Out of scope / carried to later phases

- **5C** — `backend/verification/`, `lead_contact_verification` table, "Verify
  Selected" wired, verification snapshot folded into the handoff `raw_json`,
  verification columns in the results table + detail drawer.
- **5D/5E** — `backend/lead_reports/`, single + bulk PDF/DOCX, ZIP bundle,
  "Generate Reports" wired.
- **Later** — "add to an existing campaign" option; selection on `Leads.jsx`
  using the same endpoint(s); a lead detail drawer.

---

## 12. Files touched (anticipated)

**Backend**
- `backend/routers/email_campaigns.py` — new route + `CampaignFromSearchRequest` import
- `backend/models.py` — `CampaignFromSearchRequest`
- `backend/email_campaigns/service.py` — `add_leads_from_db`, `_lead_row_to_campaign_row`
- `tests/test_campaign_from_search.py` — new

**Frontend**
- `frontend/src/pages/LeadSearch.jsx` — selection state, checkbox column, action bar
- `frontend/src/components/lead-search/SendToCampaignModal.jsx` — new
- `frontend/src/api/client.js` — `emailCampaignsApi.createFromSearch`

**No** schema change, **no** migration, **no** dependency change, **no** change
to `safety.py` / `senders.py` / any transport / `scheduler.py` / n8n.
