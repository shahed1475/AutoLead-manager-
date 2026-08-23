# CRM Deal-Stage Pipeline — Design Spec

## Context

Phase 4 (Intelligent Outreach + Reply + Follow-Up, merged `8358505`) wired the Reply Intelligence Agent's `recommended_action` into the existing lead-status pipeline in the smallest way that satisfied its own scope: `SCHEDULE_MEETING` just leaves a lead at `REPLIED` with the action visible on the drafts queue, `STOP_CAMPAIGN` sets `SKIPPED`. The final whole-branch review flagged the absence of a real deal-stage taxonomy as a deliberate, in-scope deferral, not an oversight.

This spec is that deferred work: a visual pipeline (Kanban board) with real forward stages, replacing "check the drafts queue and squint at `recommended_action`" with a board the user actually manages leads from.

## Goals

- A Kanban board showing every lead currently in an active sales conversation, grouped by stage.
- Reply intelligence keeps automatically advancing leads through the early stages (unchanged mechanism, new destination).
- The user can drag a card to correct a stage, and can manually advance a lead through stages nothing can infer automatically (a meeting actually happening, a proposal actually sent, a deal actually won).
- Every stage change — automatic or manual — is logged, so "why did this lead move" is always answerable.
- Zero behavior change for `PENDING`/`SENT`/`SKIPPED`/`DO_NOT_CONTACT` leads or any endpoint that already depends on today's `LeadStatus` values. This is additive.

## Non-goals

- No change to the existing Leads table page (`frontend/src/pages/Leads.jsx`) — it keeps showing every lead including `PENDING`/`SKIPPED`/`DO_NOT_CONTACT`, unrelated to this board.
- No revenue/deal-value tracking, no forecasting, no reporting dashboards on the pipeline. Just stage + history.
- No change to `STOP_CAMPAIGN`'s existing behavior for the common case (a lead that never got past `REPLIED`) — that path is fully tested by Phase 4 and stays exactly as-is.

## Data model

### `LeadStatus` — five new values

`backend/models.py`'s `LeadStatus` enum gains:

```python
class LeadStatus(str, Enum):
    PENDING        = "PENDING"
    ENRICHED       = "ENRICHED"
    SCORED         = "SCORED"
    MESSAGES_READY = "MESSAGES_READY"
    SENT           = "SENT"
    REPLIED        = "REPLIED"
    SKIPPED        = "SKIPPED"
    DO_NOT_CONTACT = "DO_NOT_CONTACT"
    INTERESTED     = "INTERESTED"
    MEETING        = "MEETING"
    PROPOSAL       = "PROPOSAL"
    WON            = "WON"
    LOST           = "LOST"
```

No existing value is renamed or removed. `backend/database.py`'s startup status-normalization allow-list (the query that resets any unrecognized `status` to `PENDING` on every restart — the same list `DO_NOT_CONTACT` had to be added to in Phase 4) must include all five new values, or a lead sitting in `MEETING` would silently revert to `PENDING` on the next app restart. This is the single most important correctness detail in this spec, mirrored directly from a real Phase 4 lesson (`test_do_not_contact_status_survives_restart_migration`).

The board itself never shows `PENDING`, `ENRICHED`, `SCORED`, `MESSAGES_READY`, `SKIPPED`, or `DO_NOT_CONTACT` leads — the eight board columns are:

```
NEW (=PENDING/ENRICHED/SCORED/MESSAGES_READY)  →  CONTACTED (=SENT)  →  REPLIED  →  INTERESTED  →  MEETING  →  PROPOSAL  →  WON
                                                                                                                          →  LOST
```

`NEW` and `CONTACTED` are presentation-layer labels only — no new DB values, no change to what `PENDING`/`SENT` mean anywhere else in the codebase. The board's `NEW` column groups every pre-send status together (a lead can be `PENDING`, `ENRICHED`, or `SCORED` while still waiting to be contacted — they're all "not yet contacted" from a pipeline point of view).

### `lead_stage_history` table

```sql
CREATE TABLE IF NOT EXISTS lead_stage_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id      INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    from_status  TEXT,
    to_status    TEXT NOT NULL,
    changed_by   TEXT NOT NULL,             -- 'system' | 'operator'
    reason       TEXT,                       -- e.g. "SCHEDULE_MEETING recommended" or "manual drag"
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_lead_stage_history_lead ON lead_stage_history (lead_id);
```

Every write to `leads.status` that goes through the new stage-transition path (see below) writes one history row in the same transaction. This table is purely additive — nothing reads it except the new history endpoint and the drawer UI; no existing code changes behavior because this table exists.

## Transition rules

All stage changes go through one new function, `backend/pipeline.py`'s `set_lead_stage(lead_id, to_status, changed_by, reason=None)`, which writes both `leads.status` and the `lead_stage_history` row atomically. This is the single choke point for every stage change in this feature — automatic and manual both call it, so the history log can never be inconsistent with the actual status.

**Automatic (system-triggered):**

- `reply_detector.py`'s existing `recommended_action == "SCHEDULE_MEETING"` branch (today: no-op beyond staging a draft) now also calls `set_lead_stage(lead_id, "INTERESTED", "system", "SCHEDULE_MEETING recommended")` — but only when the lead's current status is `REPLIED` (i.e., don't downgrade a lead already at `MEETING`/`PROPOSAL`/`WON` just because they replied again positively).
- `reply_detector.py`'s existing `STOP_CAMPAIGN` branch changes its destination status based on current stage: if `current_status in ("INTERESTED", "MEETING", "PROPOSAL")`, call `set_lead_stage(lead_id, "LOST", "system", f"{rich['intent']} — campaign stopped after reaching {current_status}")` instead of `SKIPPED`. If `current_status` is anything earlier (`PENDING` through `REPLIED`), behavior is **byte-for-byte unchanged** — still sets `SKIPPED` exactly as Phase 4 built and tested it. This is the one piece of genuinely new branching logic in `reply_detector.py`; everything else in this spec is new files/endpoints.

**Manual (operator-triggered), all via `POST /api/leads/{id}/stage`:**

- Drag-and-drop on the board to any of the 8 columns.
- A "Mark as Meeting Scheduled" / "Mark as Proposal Sent" / "Mark as Won" / "Mark as Lost" button set on the lead detail drawer (same drawer `EnrichmentDrawer.jsx` already opens from, or a new small component reusing its shell) for operators who prefer not to drag.
- No restriction on which column a manual move can target (including moving backward) — the operator is the authority once they're touching the board directly, and every move is logged regardless.

`DO_NOT_CONTACT` interaction: opting out already sets `DO_NOT_CONTACT` via the existing Phase 4 path and removes the lead from every board column (it's excluded regardless of which of the 8 statuses it was in). No change needed to the opt-out code itself — the board's query simply excludes `DO_NOT_CONTACT` leads, the same way it excludes `SKIPPED` ones.

## API

New router `backend/routers/pipeline.py`, mounted at `/api/pipeline`:

- `GET /api/pipeline/board` → `{stage: [lead, ...]}` for all 8 columns, each lead including `id`, `business_name`, `score`, `score_label`, `channel`, and `days_in_stage` (computed from the most recent `lead_stage_history` row for that lead, or `created_at`/`sent_at` for leads with no history row yet).
- `POST /api/leads/{id}/stage` — body `{"to_status": "MEETING", "reason": "optional"}`. Validates `to_status` is one of the 8 board statuses, calls `set_lead_stage(lead_id, to_status, "operator", reason)`. Rejects (400) if the lead is currently `DO_NOT_CONTACT` — a suppressed lead cannot be manually pulled back into an active pipeline stage; the operator must go through the existing status/opt-out override paths deliberately if they truly want to re-engage.
- `GET /api/leads/{id}/stage-history` → the `lead_stage_history` rows for one lead, newest first, for the drawer's history list.

## Frontend

New page `frontend/src/pages/Pipeline.jsx`, routed at `/pipeline` in `frontend/src/App.jsx`, new nav entry in `frontend/src/components/Sidebar.jsx` (placed after "Leads", before "Campaign" — it's a lead-management view, belongs next to the leads list conceptually).

Board: 8 columns matching the stage list. Each card: business name, score badge (reusing existing `SCORE_STYLES` pattern from `Leads.jsx`), channel icon, `days_in_stage` as a small muted label ("3d in stage"). Drag-and-drop library: evaluate `@dnd-kit/core` (actively maintained, no legacy React 18 concerns, small bundle) vs a hand-rolled HTML5 drag API — implementation task will decide based on what's already a dependency; no new heavy dependency either way given the existing `@tanstack/react-query` + `@tanstack/react-virtual` stack already in `package.json`.

Clicking a card opens the same lead detail drawer used elsewhere (extended with a small "Stage History" section calling the new history endpoint, and the manual-advance buttons).

## Error handling & edge cases

- A lead can appear on the board with zero `lead_stage_history` rows (e.g., a lead that was `SENT` before this feature existed and later replied, landing at `REPLIED` — the automatic-INTERESTED path requires a fresh `SCHEDULE_MEETING` recommendation, so pre-existing `REPLIED` leads just show up in the `REPLIED` column with no history, which is correct — nothing false is implied).
- `set_lead_stage` is a no-op (returns without writing) if `to_status == current_status` — dragging a card back onto its own column doesn't create a spurious history row.
- The `POST /api/leads/{id}/stage` endpoint's `DO_NOT_CONTACT` rejection reuses the same status-string comparison convention already established in `routers/marketing.py`/`routers/campaigns.py`/`routers/replies.py` from Phase 4 — no new pattern introduced.

## Testing

- Backend: `set_lead_stage` unit tests (writes both status and history atomically, no-op on same-status, correct `changed_by`/`reason`). `reply_detector.py` integration tests for the two new branches (SCHEDULE_MEETING on REPLIED → INTERESTED; STOP_CAMPAIGN from INTERESTED/MEETING/PROPOSAL → LOST; STOP_CAMPAIGN from REPLIED → SKIPPED, unchanged — this last one is a regression test against Phase 4's existing behavior, not new behavior). Migration test mirroring `test_do_not_contact_status_survives_restart_migration` for all 5 new statuses. Router tests for board grouping, manual move, DO_NOT_CONTACT rejection, history listing.
- Frontend: no automated test suite currently exists for pages in this repo (confirmed — `frontend/` has no `*.test.jsx` files); verification is a manual walkthrough via the `run` skill (start the dev server, drag a card, confirm the move persists and history updates) plus `npm run build` succeeding, consistent with how Task 7 of the Phase 4 plan verified its frontend change.

## Explicitly deferred (out of scope for this spec)

- The bulk export/write-manually/upload/auto-send workflow discussed alongside this feature — separate spec, separate plan, built after this one lands.
- Any deal-value/revenue field on `WON` — nothing in the current schema tracks a dollar amount for a lead; adding one is a bigger, separate decision.
