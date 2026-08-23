# CRM Deal-Stage Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Kanban-style deal-stage pipeline (NEW → CONTACTED → REPLIED → INTERESTED → MEETING → PROPOSAL → WON/LOST) on top of the existing lead-status system, with an audit log of every stage change and a new board page to manage it from.

**Architecture:** Five new `LeadStatus` values plus a `lead_stage_history` table, both purely additive. One choke-point DB function (`set_lead_stage`) is the only thing allowed to write a board-relevant status change, called from both the existing `reply_detector.py` automation and a new manual-move endpoint — so the history log can never drift from reality. New backend router for the board query; the manual-move and history endpoints join the existing `routers/leads.py` next to the pre-existing `PATCH /{lead_id}/status`. New frontend page at `/pipeline`; the existing Leads table page is untouched.

**Tech Stack:** Python/FastAPI/aiosqlite (existing), React/Vite/Tailwind/`@tanstack/react-query` (existing) + `@dnd-kit/core` + `@dnd-kit/sortable` (new frontend deps — no other frontend testing infra exists in this repo, confirmed no `*.test.jsx` files present, so frontend tasks verify via `npm run build` + manual walkthrough, matching how Phase 4's own frontend task was verified).

## Global Constraints

- No existing `LeadStatus` value is renamed or removed. `PENDING`/`SENT`/`REPLIED`/`SKIPPED`/`DO_NOT_CONTACT` and every endpoint/query that depends on them keep working exactly as today.
- `STOP_CAMPAIGN`'s existing behavior (in `reply_detector.py`) for a lead that never advanced past `REPLIED` must stay **byte-for-byte identical** to Phase 4's tested behavior — it still sets `SKIPPED`. Only leads that already reached `INTERESTED`/`MEETING`/`PROPOSAL` get the new `LOST` destination.
- Every board-relevant status write goes through `set_lead_stage()` — no other function/endpoint in this plan writes `leads.status` directly for any of the 5 new values.
- A `DO_NOT_CONTACT` lead can never be manually moved into a board stage (`POST /{lead_id}/stage` rejects it) — mirrors the guard convention Phase 4 established everywhere else.
- The startup status-normalization migration (`backend/database.py`, the `UPDATE leads SET status = 'PENDING' WHERE status NOT IN (...)` query) must allow-list all 5 new values, or any lead sitting in one of them reverts to `PENDING` on the next restart — this exact class of bug was the headline fix Phase 4 needed for `DO_NOT_CONTACT`.
- No automated test may perform a real network/LLM call.

---

### Task 1: Data model — new statuses, history table, `set_lead_stage`

**Files:**
- Modify: `backend/models.py` (`LeadStatus` enum)
- Modify: `backend/database.py` (schema, migration allow-list, new functions)
- Test: `tests/test_database_pipeline.py` (new)

**Interfaces:**
- Produces: `async def set_lead_stage(lead_id: int, to_status: str, changed_by: str, reason: Optional[str] = None) -> bool` (True if a change was written, False if the lead doesn't exist or `to_status` already equals the current status — no-op, no history row). `async def get_stage_history(lead_id: int) -> List[Dict[str, Any]]` (newest first). `async def get_board_leads() -> Dict[str, List[Dict[str, Any]]]` (keyed by the 8 board-column names, each lead dict including `id`, `business_name`, `score`, `score_label`, `channel`, `days_in_stage`). Consumed by Task 2 (`set_lead_stage`) and Task 3 (all three).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_database_pipeline.py
import pytest
from datetime import datetime, timedelta, timezone

pytestmark = pytest.mark.asyncio


async def test_set_lead_stage_writes_status_and_history(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Pipeline Co", "status": "REPLIED"})

    changed = await db.set_lead_stage(lead_id, "INTERESTED", "system", "SCHEDULE_MEETING recommended")

    assert changed is True
    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] == "INTERESTED"
    history = await db.get_stage_history(lead_id)
    assert len(history) == 1
    assert history[0]["from_status"] == "REPLIED"
    assert history[0]["to_status"] == "INTERESTED"
    assert history[0]["changed_by"] == "system"
    assert history[0]["reason"] == "SCHEDULE_MEETING recommended"


async def test_set_lead_stage_noop_when_same_status(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Same Stage Co", "status": "MEETING"})

    changed = await db.set_lead_stage(lead_id, "MEETING", "operator", "drag onto same column")

    assert changed is False
    history = await db.get_stage_history(lead_id)
    assert history == []


async def test_set_lead_stage_returns_false_for_missing_lead(clean_db):
    db = clean_db
    changed = await db.set_lead_stage(999999, "INTERESTED", "operator")
    assert changed is False


async def test_get_stage_history_newest_first(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "History Co", "status": "REPLIED"})

    await db.set_lead_stage(lead_id, "INTERESTED", "system", "first")
    await db.set_lead_stage(lead_id, "MEETING", "operator", "second")

    history = await db.get_stage_history(lead_id)
    assert len(history) == 2
    assert history[0]["reason"] == "second"
    assert history[1]["reason"] == "first"


async def test_get_board_leads_groups_by_stage_and_excludes_non_board_statuses(clean_db):
    db = clean_db
    pending_id  = await db.create_lead({"business_name": "New Co", "status": "PENDING"})
    sent_id     = await db.create_lead({"business_name": "Contacted Co", "status": "SENT"})
    replied_id  = await db.create_lead({"business_name": "Replied Co", "status": "REPLIED"})
    meeting_id  = await db.create_lead({"business_name": "Meeting Co", "status": "MEETING"})
    skipped_id  = await db.create_lead({"business_name": "Skipped Co", "status": "SKIPPED"})
    dnc_id      = await db.create_lead({"business_name": "DNC Co", "status": "DO_NOT_CONTACT"})

    board = await db.get_board_leads()

    board_ids = {lead["id"] for leads in board.values() for lead in leads}
    assert pending_id in board_ids
    assert sent_id in board_ids
    assert replied_id in board_ids
    assert meeting_id in board_ids
    assert skipped_id not in board_ids
    assert dnc_id not in board_ids

    new_ids = {lead["id"] for lead in board["NEW"]}
    assert pending_id in new_ids
    contacted_ids = {lead["id"] for lead in board["CONTACTED"]}
    assert sent_id in contacted_ids
    meeting_ids = {lead["id"] for lead in board["MEETING"]}
    assert meeting_id in meeting_ids


async def test_get_board_leads_includes_days_in_stage(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Aged Co", "status": "REPLIED"})

    board = await db.get_board_leads()
    replied = next(l for l in board["REPLIED"] if l["id"] == lead_id)
    assert "days_in_stage" in replied
    assert replied["days_in_stage"] >= 0


async def test_all_five_new_statuses_survive_restart_migration(clean_db):
    """Mirrors test_do_not_contact_status_survives_restart_migration from Phase 4 —
    every new status must be in the startup allow-list or it silently reverts
    to PENDING on the next process restart."""
    db = clean_db
    lead_ids = {}
    for status in ("INTERESTED", "MEETING", "PROPOSAL", "WON", "LOST"):
        lead_ids[status] = await db.create_lead({"business_name": f"{status} Co", "status": status})

    await db.init_db()  # simulates a process restart re-running migrations

    for status, lead_id in lead_ids.items():
        lead = await db.get_lead_by_id(lead_id)
        assert lead["status"] == status, f"{status} did not survive restart migration"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_database_pipeline.py -v`
Expected: FAIL — `set_lead_stage`/`get_stage_history`/`get_board_leads` don't exist yet (`AttributeError`), and `create_lead({"status": "INTERESTED", ...})` would currently get silently reset to `PENDING` by the unmodified migration allow-list even before that.

- [ ] **Step 3: Add the 5 new `LeadStatus` values**

In `backend/models.py`, find:

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
```

Replace with:

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

- [ ] **Step 4: Add the `lead_stage_history` table and update the migration allow-list**

In `backend/database.py`, find the schema block that defines `generated_messages` (search for `CREATE TABLE IF NOT EXISTS generated_messages`) and add immediately after its closing `CREATE INDEX` line:

```sql
CREATE TABLE IF NOT EXISTS lead_stage_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id      INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    from_status  TEXT,
    to_status    TEXT NOT NULL,
    changed_by   TEXT NOT NULL,
    reason       TEXT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_lead_stage_history_lead ON lead_stage_history (lead_id);
```

Find the migration's status-normalization query (search for `UPDATE leads SET status = 'PENDING'`):

```python
    await raw.execute("""
        UPDATE leads SET status = 'PENDING'
        WHERE status IS NULL
           OR status NOT IN ('PENDING','SENT','REPLIED','SKIPPED','MESSAGES_READY','ENRICHED','SCORED','DO_NOT_CONTACT')
    """)
```

Replace with:

```python
    await raw.execute("""
        UPDATE leads SET status = 'PENDING'
        WHERE status IS NULL
           OR status NOT IN (
               'PENDING','SENT','REPLIED','SKIPPED','MESSAGES_READY','ENRICHED','SCORED','DO_NOT_CONTACT',
               'INTERESTED','MEETING','PROPOSAL','WON','LOST'
           )
    """)
```

- [ ] **Step 5: Implement `set_lead_stage`, `get_stage_history`, `get_board_leads`**

In `backend/database.py`, add near the other lead-status-adjacent functions (immediately after `mark_lead_replied` is a good spot — search for `async def mark_lead_replied`):

```python
# ─────────────────────────────────────────────────────────────────────────────
# CRM deal-stage pipeline — set_lead_stage is the single choke point for every
# board-relevant status write (automatic from reply_detector.py, manual from
# the pipeline router), so lead_stage_history can never drift from leads.status.
# ─────────────────────────────────────────────────────────────────────────────

_BOARD_COLUMNS: Dict[str, List[str]] = {
    "NEW":        ["PENDING", "ENRICHED", "SCORED", "MESSAGES_READY"],
    "CONTACTED":  ["SENT"],
    "REPLIED":    ["REPLIED"],
    "INTERESTED": ["INTERESTED"],
    "MEETING":    ["MEETING"],
    "PROPOSAL":   ["PROPOSAL"],
    "WON":        ["WON"],
    "LOST":       ["LOST"],
}


async def set_lead_stage(
    lead_id: int, to_status: str, changed_by: str, reason: Optional[str] = None,
) -> bool:
    """Atomically write leads.status and a lead_stage_history row. No-op
    (returns False, writes nothing) if the lead doesn't exist or to_status
    already equals the current status."""
    async with transaction() as tx:
        current = await tx.fetchrow("SELECT status FROM leads WHERE id = $1", lead_id)
        if not current:
            return False
        from_status = current["status"]
        if (from_status or "").upper() == to_status.upper():
            return False
        await tx.execute("UPDATE leads SET status = $1 WHERE id = $2", to_status, lead_id)
        await tx.execute(
            """INSERT INTO lead_stage_history (lead_id, from_status, to_status, changed_by, reason)
               VALUES ($1, $2, $3, $4, $5)""",
            lead_id, from_status, to_status, changed_by, reason,
        )
    return True


async def get_stage_history(lead_id: int) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT * FROM lead_stage_history WHERE lead_id = $1 ORDER BY created_at DESC, id DESC",
            lead_id,
        )
    return [dict(r) for r in rows]


async def get_board_leads() -> Dict[str, List[Dict[str, Any]]]:
    """Every lead currently in one of the 8 board columns, grouped by column
    name. days_in_stage is computed from the most recent lead_stage_history
    row for that lead, or the lead's created_at if it has no history yet
    (true for every pre-existing lead and every NEW/CONTACTED lead that has
    never had an automatic or manual stage change)."""
    all_statuses = [s for statuses in _BOARD_COLUMNS.values() for s in statuses]
    placeholders = ", ".join(f"${i + 1}" for i in range(len(all_statuses)))
    async with get_db() as conn:
        rows = await conn.fetch(
            f"""SELECT l.*,
                       (SELECT h.created_at FROM lead_stage_history h
                        WHERE h.lead_id = l.id ORDER BY h.created_at DESC, h.id DESC LIMIT 1) AS last_stage_change
                FROM leads l WHERE l.status IN ({placeholders})""",
            *all_statuses,
        )

    board: Dict[str, List[Dict[str, Any]]] = {col: [] for col in _BOARD_COLUMNS}
    status_to_col = {s: col for col, statuses in _BOARD_COLUMNS.items() for s in statuses}
    now = datetime.now(timezone.utc)
    for row in rows:
        lead = dict(row)
        col = status_to_col.get((lead.get("status") or "").upper())
        if not col:
            continue
        anchor_raw = lead.pop("last_stage_change", None) or lead.get("created_at")
        try:
            anchor = datetime.fromisoformat(str(anchor_raw).replace("Z", "+00:00"))
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=timezone.utc)
            lead["days_in_stage"] = max(0, (now - anchor).days)
        except (ValueError, TypeError):
            lead["days_in_stage"] = 0
        board[col].append(lead)
    return board
```

`backend/database.py` already imports `datetime`, `timezone` at module level (used throughout the file, e.g. in `get_recent_send_info`) and already has `transaction`, `get_db`, `Dict`, `List`, `Any`, `Optional` imported/defined — no new imports needed.

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_database_pipeline.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 7: Run the full suite for regressions**

Run: `python -m pytest tests/ -q`
Expected: all previously-passing tests still PASS (274 baseline + 7 new = 281).

- [ ] **Step 8: Commit**

```bash
git add backend/models.py backend/database.py tests/test_database_pipeline.py
git commit -m "feat: add CRM deal-stage statuses, history log, and set_lead_stage"
```

---

### Task 2: Wire automatic stage transitions into `reply_detector.py`

**Files:**
- Modify: `backend/reply_detector.py`
- Test: `tests/test_reply_detector.py` (extend)

**Interfaces:**
- Consumes: `db.set_lead_stage(lead_id, to_status, changed_by, reason) -> bool` (Task 1).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_reply_detector.py` (reuse the file's existing `_imap_message`/`_patch_imap`/`_patch_legacy_intent`/`_patch_rich_intent`/`_CONFIG` helpers):

```python
async def test_schedule_meeting_advances_replied_lead_to_interested(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Meeting Bound Co", "email": "lead@company.com", "status": "REPLIED"})

    _patch_imap(monkeypatch, [_imap_message(body="Sounds great, tell me more")])
    _patch_legacy_intent(monkeypatch, "interested")
    _patch_rich_intent(monkeypatch, "INTERESTED", "SCHEDULE_MEETING", draft="Happy to share more.")

    await reply_detector.check_for_replies(_CONFIG)

    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] == "INTERESTED"
    history = await db.get_stage_history(lead_id)
    assert len(history) == 1
    assert history[0]["to_status"] == "INTERESTED"
    assert history[0]["changed_by"] == "system"


async def test_schedule_meeting_does_not_downgrade_lead_already_past_interested(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Already Meeting Co", "email": "lead@company.com", "status": "MEETING"})

    _patch_imap(monkeypatch, [_imap_message(body="Looking forward to our call")])
    _patch_legacy_intent(monkeypatch, "interested")
    _patch_rich_intent(monkeypatch, "INTERESTED", "SCHEDULE_MEETING", draft="See you then.")

    await reply_detector.check_for_replies(_CONFIG)

    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] == "MEETING"
    history = await db.get_stage_history(lead_id)
    assert history == []


async def test_stop_campaign_from_interested_goes_to_lost_not_skipped(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Lost Deal Co", "email": "lead@company.com", "status": "INTERESTED"})

    _patch_imap(monkeypatch, [_imap_message(body="Actually we've decided to go another direction")])
    _patch_legacy_intent(monkeypatch, "unknown")
    _patch_rich_intent(monkeypatch, "NOT_INTERESTED", "STOP_CAMPAIGN")

    await reply_detector.check_for_replies(_CONFIG)

    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] == "LOST"
    history = await db.get_stage_history(lead_id)
    assert history[0]["to_status"] == "LOST"


async def test_stop_campaign_from_meeting_goes_to_lost(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Lost After Meeting Co", "email": "lead@company.com", "status": "MEETING"})

    _patch_imap(monkeypatch, [_imap_message(body="Wrong contact, I no longer work here")])
    _patch_legacy_intent(monkeypatch, "unknown")
    _patch_rich_intent(monkeypatch, "WRONG_CONTACT", "STOP_CAMPAIGN")

    await reply_detector.check_for_replies(_CONFIG)

    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] == "LOST"


async def test_stop_campaign_from_replied_still_goes_to_skipped_unchanged(clean_db, monkeypatch):
    """Regression: the pre-existing Phase 4 behavior for a lead that never
    advanced past REPLIED must not change."""
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Still Skipped Co", "email": "lead@company.com", "status": "REPLIED"})

    _patch_imap(monkeypatch, [_imap_message(body="Not interested, thanks")])
    _patch_legacy_intent(monkeypatch, "not_interested")
    _patch_rich_intent(monkeypatch, "NOT_INTERESTED", "STOP_CAMPAIGN")

    await reply_detector.check_for_replies(_CONFIG)

    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] == "SKIPPED"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_reply_detector.py -v`
Expected: the first four new tests FAIL (no `INTERESTED`/`LOST` transitions exist yet); `test_stop_campaign_from_replied_still_goes_to_skipped_unchanged` PASSES already (confirms the regression baseline before you touch anything).

- [ ] **Step 3: Wire the transitions**

In `backend/reply_detector.py`, find the block starting at `action = rich["recommended_action"]` (inside the `if reply_id and lead:` try block):

```python
                action = rich["recommended_action"]
                if rich["intent"] == "OPT_OUT":
                    await db.update_lead(lead_id, {"status": "DO_NOT_CONTACT"})
                    cancelled = await db.cancel_pending_followups(lead_id)
                    discarded = await db.discard_pending_drafts_for_lead(lead_id)
                    await _log(
                        f"Reply detector: {biz} → marked DO_NOT_CONTACT (opt-out detected), "
                        f"{cancelled} pending follow-up(s) cancelled, {discarded} pending draft(s) discarded"
                    )
                elif action == "STOP_CAMPAIGN":
                    cancelled = await db.cancel_pending_followups(lead_id)
                    current_status = (lead.get("status") or "").upper()
                    if current_status not in _LOCKED_STATUSES and current_status != "DO_NOT_CONTACT":
                        await db.update_lead(lead_id, {"status": "SKIPPED"})
                    await _log(
                        f"Reply detector: {biz} → campaign stopped ({rich['intent']}), "
                        f"{cancelled} pending follow-up(s) cancelled"
                    )
                elif rich["draft_response"]:
```

Replace with:

```python
                action = rich["recommended_action"]
                current_status = (lead.get("status") or "").upper()
                if rich["intent"] == "OPT_OUT":
                    await db.update_lead(lead_id, {"status": "DO_NOT_CONTACT"})
                    cancelled = await db.cancel_pending_followups(lead_id)
                    discarded = await db.discard_pending_drafts_for_lead(lead_id)
                    await _log(
                        f"Reply detector: {biz} → marked DO_NOT_CONTACT (opt-out detected), "
                        f"{cancelled} pending follow-up(s) cancelled, {discarded} pending draft(s) discarded"
                    )
                elif action == "STOP_CAMPAIGN":
                    cancelled = await db.cancel_pending_followups(lead_id)
                    # A lead that already reached the deal-stage pipeline (INTERESTED and
                    # beyond) is LOST when the campaign stops — it got further than a plain
                    # SKIPPED implies. A lead that never advanced past REPLIED keeps the
                    # exact pre-existing SKIPPED behavior, unchanged.
                    if current_status in ("INTERESTED", "MEETING", "PROPOSAL"):
                        await db.set_lead_stage(lead_id, "LOST", "system", f"{rich['intent']} — campaign stopped")
                    elif current_status not in _LOCKED_STATUSES and current_status != "DO_NOT_CONTACT":
                        await db.update_lead(lead_id, {"status": "SKIPPED"})
                    await _log(
                        f"Reply detector: {biz} → campaign stopped ({rich['intent']}), "
                        f"{cancelled} pending follow-up(s) cancelled"
                    )
                elif action == "SCHEDULE_MEETING" and current_status == "REPLIED":
                    await db.set_lead_stage(lead_id, "INTERESTED", "system", f"{rich['intent']} — SCHEDULE_MEETING recommended")
                    if rich["draft_response"]:
                        draft_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}" if subject else "Re: your message"
                        await db.set_reply_draft(reply_id, draft_subject, rich["draft_response"])
                        await _log(f"Reply detector: {biz} → advanced to INTERESTED, reply draft queued for approval")
                    else:
                        await _log(f"Reply detector: {biz} → advanced to INTERESTED")
                elif rich["draft_response"]:
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_reply_detector.py -v`
Expected: all tests PASS, including the pre-existing ones from Task 1 of the Phase 4 plan.

- [ ] **Step 5: Run the full suite for regressions**

Run: `python -m pytest tests/ -q`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/reply_detector.py tests/test_reply_detector.py
git commit -m "feat: advance leads through INTERESTED/LOST via reply intelligence"
```

---

### Task 3: Backend endpoints — board query, manual move, history

**Files:**
- Modify: `backend/models.py` (new `StageUpdate` model)
- Modify: `backend/routers/leads.py` (two new endpoints)
- Create: `backend/routers/pipeline.py`
- Modify: `backend/main.py` (register the new router)
- Test: `tests/test_pipeline_router.py` (new)

**Interfaces:**
- Consumes: `db.get_board_leads()`, `db.set_lead_stage()`, `db.get_stage_history()` (Task 1).
- Produces: `GET /api/pipeline/board`, `POST /api/leads/{lead_id}/stage`, `GET /api/leads/{lead_id}/stage-history`. Consumed by Task 4/5's frontend.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pipeline_router.py
import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


async def test_board_groups_leads_by_stage(clean_db):
    from backend.main import app
    db = clean_db
    await db.create_lead({"business_name": "New Co", "status": "PENDING"})
    await db.create_lead({"business_name": "Meeting Co", "status": "MEETING"})
    await db.create_lead({"business_name": "Skipped Co", "status": "SKIPPED"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/pipeline/board")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["NEW"]) == 1
    assert len(body["MEETING"]) == 1
    assert "SKIPPED" not in body


async def test_manual_stage_move_writes_history(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Movable Co", "status": "REPLIED"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/leads/{lead_id}/stage", json={"to_status": "INTERESTED", "reason": "manual drag"})

    assert resp.status_code == 200
    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] == "INTERESTED"
    history = await db.get_stage_history(lead_id)
    assert history[0]["changed_by"] == "operator"
    assert history[0]["reason"] == "manual drag"


async def test_manual_stage_move_400_for_do_not_contact_lead(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Suppressed Co", "status": "DO_NOT_CONTACT"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/leads/{lead_id}/stage", json={"to_status": "MEETING"})

    assert resp.status_code == 400
    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] == "DO_NOT_CONTACT"


async def test_manual_stage_move_400_for_non_board_status(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Bad Target Co", "status": "REPLIED"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/leads/{lead_id}/stage", json={"to_status": "ENRICHED"})

    assert resp.status_code == 400
    lead = await db.get_lead_by_id(lead_id)
    assert lead["status"] == "REPLIED"


async def test_manual_stage_move_404_for_missing_lead(clean_db):
    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/leads/999999/stage", json={"to_status": "MEETING"})
    assert resp.status_code == 404


async def test_stage_history_endpoint_returns_newest_first(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "History Endpoint Co", "status": "REPLIED"})
    await db.set_lead_stage(lead_id, "INTERESTED", "system", "first")
    await db.set_lead_stage(lead_id, "MEETING", "operator", "second")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/leads/{lead_id}/stage-history")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["reason"] == "second"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_pipeline_router.py -v`
Expected: FAIL — `/api/pipeline/board` and the two new `/api/leads/{id}/...` routes don't exist yet (404s).

- [ ] **Step 3: Add the `StageUpdate` model**

In `backend/models.py`, find `class StatusUpdate(BaseModel):` and add immediately after its block:

```python
class StageUpdate(BaseModel):
    to_status: LeadStatus
    reason:    Optional[str] = None
```

- [ ] **Step 4: Add the two lead-scoped endpoints to `routers/leads.py`**

In `backend/routers/leads.py`, add `StageUpdate` to the existing models import (find `from ..models import Lead, LeadCreate, LeadUpdate, LeadListResponse, StatusUpdate` and add `StageUpdate` to that list). Then find the `patch_status` endpoint and add these two new endpoints immediately after it:

```python
# Manual moves may only target a real, single-valued board stage. NEW and
# CONTACTED are display-only aggregates (NEW groups PENDING/ENRICHED/SCORED/
# MESSAGES_READY) with no single underlying status to move a lead "back" to
# other than their canonical member — PENDING and SENT respectively. ENRICHED/
# SCORED/MESSAGES_READY/SKIPPED/DO_NOT_CONTACT are reachable only through their
# own dedicated flows (scoring, opt-out), never through this endpoint.
_MANUAL_STAGE_TARGETS = frozenset({
    "PENDING", "SENT", "REPLIED", "INTERESTED", "MEETING", "PROPOSAL", "WON", "LOST",
})


@router.post("/{lead_id}/stage")
async def move_lead_stage(lead_id: int, payload: StageUpdate):
    """Manual deal-stage move (drag-and-drop on the pipeline board, or a
    button on the lead detail drawer). Rejects a DO_NOT_CONTACT lead — an
    opted-out lead cannot be pulled back into an active pipeline stage
    through this endpoint."""
    lead = await db.get_lead_by_id(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    if (lead.get("status") or "").upper() == "DO_NOT_CONTACT":
        raise HTTPException(400, "Lead is marked DO_NOT_CONTACT — cannot move to a pipeline stage")
    if payload.to_status.value not in _MANUAL_STAGE_TARGETS:
        raise HTTPException(400, f"{payload.to_status.value} is not a valid manual stage target")
    await db.set_lead_stage(lead_id, payload.to_status.value, "operator", payload.reason)
    return await db.get_lead_by_id(lead_id)


@router.get("/{lead_id}/stage-history")
async def lead_stage_history(lead_id: int):
    lead = await db.get_lead_by_id(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    return await db.get_stage_history(lead_id)
```

- [ ] **Step 5: Create `routers/pipeline.py`**

```python
# backend/routers/pipeline.py
"""
routers/pipeline.py — CRM deal-stage board query.

The two lead-scoped mutations (manual stage move, stage history) live in
routers/leads.py next to the existing PATCH /{lead_id}/status, since they're
lead-scoped under the /api/leads prefix. This router only holds the
board-wide GET, which doesn't fit that prefix.
"""
from fastapi import APIRouter

from .. import database as db

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


@router.get("/board")
async def get_board():
    return await db.get_board_leads()
```

- [ ] **Step 6: Register the router in `main.py`**

In `backend/main.py`, find `from .routers import marketing as marketing_router` and add immediately after it:

```python
from .routers import pipeline as pipeline_router
```

Find `app.include_router(marketing_router.router, dependencies=_authed)` and add immediately after it:

```python
app.include_router(pipeline_router.router,  dependencies=_authed)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python -m pytest tests/test_pipeline_router.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 8: Run the full suite for regressions**

Run: `python -m pytest tests/ -q`
Expected: all tests PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/models.py backend/routers/leads.py backend/routers/pipeline.py backend/main.py tests/test_pipeline_router.py
git commit -m "feat: add pipeline board, manual stage move, and stage history endpoints"
```

---

### Task 4: Frontend — pipeline board page

**Files:**
- Modify: `frontend/package.json` (add `@dnd-kit/core`, `@dnd-kit/sortable`, `@dnd-kit/utilities`)
- Modify: `frontend/src/api/client.js` (new `pipelineApi` module)
- Create: `frontend/src/pages/Pipeline.jsx`
- Modify: `frontend/src/App.jsx` (new route)
- Modify: `frontend/src/components/Sidebar.jsx` (new nav entry)

**Interfaces:**
- Consumes: `GET /api/pipeline/board`, `POST /api/leads/{id}/stage` (Task 3).

- [ ] **Step 1: Add the drag-and-drop dependencies**

In `frontend/src/../package.json` (i.e. `frontend/package.json`), find:

```json
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.28.0",
    "@tanstack/react-query": "^5.62.3",
    "@tanstack/react-virtual": "^3.10.9",
    "axios": "^1.7.9",
    "recharts": "^2.13.3",
    "lucide-react": "^0.468.0",
    "clsx": "^2.1.1",
    "react-hot-toast": "^2.4.1"
  },
```

Replace with:

```json
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.28.0",
    "@tanstack/react-query": "^5.62.3",
    "@tanstack/react-virtual": "^3.10.9",
    "@dnd-kit/core": "^6.1.0",
    "@dnd-kit/sortable": "^8.0.0",
    "@dnd-kit/utilities": "^3.2.2",
    "axios": "^1.7.9",
    "recharts": "^2.13.3",
    "lucide-react": "^0.468.0",
    "clsx": "^2.1.1",
    "react-hot-toast": "^2.4.1"
  },
```

Then run `cd frontend && npm install` to fetch the new packages and update `package-lock.json`.

- [ ] **Step 2: Add `pipelineApi` to the API client**

In `frontend/src/api/client.js`, find the `marketingApi` export block and add immediately after it:

```javascript
export const pipelineApi = {
  board: () => api.get('/pipeline/board').then((r) => r.data),
  moveStage: (leadId, toStatus, reason) =>
    api.post(`/leads/${leadId}/stage`, { to_status: toStatus, reason }).then((r) => r.data),
  stageHistory: (leadId) => api.get(`/leads/${leadId}/stage-history`).then((r) => r.data),
}
```

- [ ] **Step 3: Create the Pipeline page**

```jsx
// frontend/src/pages/Pipeline.jsx
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  DndContext, DragOverlay, closestCorners, PointerSensor, useSensor, useSensors,
  useDroppable, useDraggable,
} from '@dnd-kit/core'
import { GitBranch, Mail, MessageCircle } from 'lucide-react'
import toast from 'react-hot-toast'
import { pipelineApi } from '../api/client'

const COLUMNS = [
  { key: 'NEW',        label: 'New' },
  { key: 'CONTACTED',  label: 'Contacted' },
  { key: 'REPLIED',    label: 'Replied' },
  { key: 'INTERESTED', label: 'Interested' },
  { key: 'MEETING',    label: 'Meeting' },
  { key: 'PROPOSAL',   label: 'Proposal' },
  { key: 'WON',        label: 'Won' },
  { key: 'LOST',       label: 'Lost' },
]

// NEW and CONTACTED are display-only aggregates on the backend (NEW groups
// PENDING/ENRICHED/SCORED/MESSAGES_READY) — a manual move targeting either
// column must send the single canonical status the backend's
// _MANUAL_STAGE_TARGETS allow-list actually accepts, not the column label.
const COLUMN_TO_STATUS = {
  NEW:        'PENDING',
  CONTACTED:  'SENT',
  REPLIED:    'REPLIED',
  INTERESTED: 'INTERESTED',
  MEETING:    'MEETING',
  PROPOSAL:   'PROPOSAL',
  WON:        'WON',
  LOST:       'LOST',
}

const SCORE_STYLES = {
  HOT:  'border-red-500/50 bg-red-500/15 text-red-300',
  WARM: 'border-amber-500/50 bg-amber-500/15 text-amber-300',
  COLD: 'border-blue-500/50 bg-blue-500/15 text-blue-300',
}

function LeadCard({ lead }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({ id: String(lead.id) })
  const style = transform
    ? { transform: `translate(${transform.x}px, ${transform.y}px)`, opacity: isDragging ? 0.5 : 1 }
    : undefined
  const ChannelIcon = lead.channel === 'WHATSAPP' ? MessageCircle : Mail

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...listeners}
      {...attributes}
      className="rounded-lg border border-slate-700/50 bg-slate-900/60 p-2.5 space-y-1.5 cursor-grab active:cursor-grabbing"
    >
      <p className="text-xs font-medium text-slate-200 truncate">{lead.business_name}</p>
      <div className="flex items-center justify-between">
        <span className={`text-[9px] font-bold uppercase px-1.5 py-0.5 rounded-full border ${SCORE_STYLES[lead.score_label] || SCORE_STYLES.COLD}`}>
          {lead.score_label || 'COLD'}
        </span>
        <div className="flex items-center gap-1 text-[10px] text-slate-500">
          <ChannelIcon size={11} />
          <span>{lead.days_in_stage}d</span>
        </div>
      </div>
    </div>
  )
}

function Column({ column, leads }) {
  const { setNodeRef, isOver } = useDroppable({ id: column.key })
  return (
    <div
      ref={setNodeRef}
      className={`flex-1 min-w-[220px] rounded-xl border p-2.5 space-y-2 transition-colors ${
        isOver ? 'border-brand-500/50 bg-brand-600/5' : 'border-slate-800 bg-slate-900/30'
      }`}
    >
      <div className="flex items-center justify-between px-1 pb-1">
        <span className="text-[10px] font-bold uppercase tracking-widest text-slate-400">{column.label}</span>
        <span className="text-[10px] text-slate-600">{leads.length}</span>
      </div>
      <div className="space-y-2 min-h-[40px]">
        {leads.map((lead) => <LeadCard key={lead.id} lead={lead} />)}
      </div>
    </div>
  )
}

export default function Pipeline() {
  const queryClient = useQueryClient()
  const [activeLead, setActiveLead] = useState(null)
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }))

  const { data: board = {}, isLoading } = useQuery({
    queryKey: ['pipeline-board'],
    queryFn: pipelineApi.board,
    staleTime: 15_000,
  })

  const moveMutation = useMutation({
    mutationFn: ({ leadId, toStatus }) => pipelineApi.moveStage(leadId, toStatus, 'manual drag'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['pipeline-board'] }),
    onError: (err) => toast.error(err?.message || 'Could not move lead'),
  })

  function handleDragStart(event) {
    const id = Number(event.active.id)
    const lead = Object.values(board).flat().find((l) => l.id === id)
    setActiveLead(lead || null)
  }

  function handleDragEnd(event) {
    setActiveLead(null)
    const { active, over } = event
    if (!over) return
    const leadId = Number(active.id)
    const targetColumn = over.id
    const currentColumn = Object.entries(board).find(([, leads]) => leads.some((l) => l.id === leadId))?.[0]
    if (currentColumn === targetColumn) return
    const toStatus = COLUMN_TO_STATUS[targetColumn]
    if (!toStatus) return
    moveMutation.mutate({ leadId, toStatus })
  }

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center gap-2">
        <GitBranch size={18} className="text-brand-400" />
        <h1 className="text-lg font-bold text-slate-100">Pipeline</h1>
      </div>

      {isLoading ? (
        <p className="text-sm text-slate-600">Loading pipeline…</p>
      ) : (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCorners}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
        >
          <div className="flex gap-3 overflow-x-auto pb-4">
            {COLUMNS.map((column) => (
              <Column key={column.key} column={column} leads={board[column.key] || []} />
            ))}
          </div>
          <DragOverlay>{activeLead ? <LeadCard lead={activeLead} /> : null}</DragOverlay>
        </DndContext>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Register the route**

In `frontend/src/App.jsx`, find:

```javascript
const Dashboard = lazy(() => import('./pages/Dashboard'))
const Leads     = lazy(() => import('./pages/Leads'))
const Campaign  = lazy(() => import('./pages/Campaign'))
```

Replace with:

```javascript
const Dashboard = lazy(() => import('./pages/Dashboard'))
const Leads     = lazy(() => import('./pages/Leads'))
const Pipeline  = lazy(() => import('./pages/Pipeline'))
const Campaign  = lazy(() => import('./pages/Campaign'))
```

Find:

```javascript
          <Route path="/leads"     element={<Leads />} />
          <Route path="/campaign"  element={<Campaign />} />
```

Replace with:

```javascript
          <Route path="/leads"     element={<Leads />} />
          <Route path="/pipeline"  element={<Pipeline />} />
          <Route path="/campaign"  element={<Campaign />} />
```

- [ ] **Step 5: Add the nav entry**

In `frontend/src/components/Sidebar.jsx`, find:

```javascript
import {
  LayoutDashboard, Users, Send, Cpu, Settings, Inbox,
} from 'lucide-react'
```

Replace with:

```javascript
import {
  LayoutDashboard, Users, GitBranch, Send, Cpu, Settings, Inbox,
} from 'lucide-react'
```

Find:

```javascript
const nav = [
  { to: '/dashboard', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/leads',     icon: Users,           label: 'Leads'     },
  { to: '/campaign',  icon: Send,            label: 'Campaign'  },
```

Replace with:

```javascript
const nav = [
  { to: '/dashboard', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/leads',     icon: Users,           label: 'Leads'     },
  { to: '/pipeline',  icon: GitBranch,       label: 'Pipeline'  },
  { to: '/campaign',  icon: Send,            label: 'Campaign'  },
```

- [ ] **Step 6: Verify the build**

Run: `cd frontend && npm run build`
Expected: clean build, no errors. (No automated frontend test suite exists in this repo — this build check plus the manual walkthrough in Step 7 are the verification for this task, matching how Phase 4's Task 7 was verified.)

- [ ] **Step 7: Manual verification**

Run the dev server via the `run` skill, open `/pipeline`, confirm: the 8 columns render, a lead created via the existing Leads page or campaign flow appears in the correct column, dragging a card to a different column persists (reload the page, the card stays in its new column) and the backend log shows a `lead_stage_history` row was written (check via `sqlite3` or a quick `GET /api/leads/{id}/stage-history` call).

- [ ] **Step 8: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/api/client.js frontend/src/pages/Pipeline.jsx frontend/src/App.jsx frontend/src/components/Sidebar.jsx
git commit -m "feat: add CRM pipeline board page with drag-and-drop stage moves"
```

---

### Task 5: Frontend — stage history panel in the lead detail drawer

**Files:**
- Create: `frontend/src/components/StageHistoryPanel.jsx`
- Modify: `frontend/src/components/EnrichmentDrawer.jsx`

**Interfaces:**
- Consumes: `pipelineApi.stageHistory`, `pipelineApi.moveStage` (Task 4).

- [ ] **Step 1: Create the panel**

```jsx
// frontend/src/components/StageHistoryPanel.jsx
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { GitBranch, Loader2 } from 'lucide-react'
import toast from 'react-hot-toast'
import { pipelineApi } from '../api/client'

const MANUAL_STAGES = ['MEETING', 'PROPOSAL', 'WON', 'LOST']

const STAGE_LABEL = {
  MEETING:  'Mark as Meeting Scheduled',
  PROPOSAL: 'Mark as Proposal Sent',
  WON:      'Mark as Won',
  LOST:     'Mark as Lost',
}

export default function StageHistoryPanel({ leadId, currentStatus }) {
  const queryClient = useQueryClient()

  const { data: history = [], isLoading } = useQuery({
    queryKey: ['stage-history', leadId],
    queryFn: () => pipelineApi.stageHistory(leadId),
    enabled: Boolean(leadId),
    staleTime: 15_000,
  })

  const moveMutation = useMutation({
    mutationFn: (toStatus) => pipelineApi.moveStage(leadId, toStatus, 'manual (drawer button)'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['stage-history', leadId] })
      queryClient.invalidateQueries({ queryKey: ['pipeline-board'] })
    },
    onError: (err) => toast.error(err?.message || 'Could not move lead'),
  })

  if (!leadId || currentStatus === 'DO_NOT_CONTACT') return null

  return (
    <div className="mt-1 pt-4 border-t border-slate-800 space-y-3">
      <div className="flex items-center gap-1.5">
        <GitBranch size={12} className="text-brand-400" />
        <span className="text-[10px] font-bold uppercase tracking-widest text-brand-400">Deal Stage</span>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {MANUAL_STAGES.filter((s) => s !== currentStatus).map((stage) => (
          <button
            key={stage}
            onClick={() => moveMutation.mutate(stage)}
            disabled={moveMutation.isPending}
            className="btn-secondary text-[10px] py-1 px-2"
          >
            {moveMutation.isPending ? <Loader2 size={10} className="animate-spin" /> : null}
            {STAGE_LABEL[stage]}
          </button>
        ))}
      </div>

      {isLoading ? (
        <p className="text-[10px] text-slate-600">Loading history…</p>
      ) : history.length === 0 ? (
        <p className="text-[10px] text-slate-600 italic">No stage changes yet.</p>
      ) : (
        <ul className="space-y-1.5">
          {history.map((h) => (
            <li key={h.id} className="text-[10px] text-slate-500 flex items-baseline gap-1.5">
              <span className="text-slate-300 font-semibold">{h.to_status}</span>
              <span className="text-slate-600">·</span>
              <span>{h.changed_by === 'system' ? 'auto' : 'you'}</span>
              {h.reason && <span className="text-slate-600 truncate">— {h.reason}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Mount it in the drawer**

In `frontend/src/components/EnrichmentDrawer.jsx`, find the import block (search for `import MarketingMessagesPanel from './MarketingMessagesPanel'`) and add immediately after it:

```javascript
import StageHistoryPanel from './StageHistoryPanel'
```

Find `<MarketingMessagesPanel leadId={lead?.id} />` and add immediately after it:

```jsx
          <StageHistoryPanel leadId={lead?.id} currentStatus={lead?.status} />
```

- [ ] **Step 3: Verify the build**

Run: `cd frontend && npm run build`
Expected: clean build, no errors.

- [ ] **Step 4: Manual verification**

Open a lead's detail drawer (from the Leads page or the Pipeline board), confirm the "Deal Stage" section appears with the manual-move buttons (except the button matching the lead's current stage) and an empty "No stage changes yet" message for a lead with no history; click a button, confirm a history row appears and the button disappears from the available list (since it's now the current stage).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/StageHistoryPanel.jsx frontend/src/components/EnrichmentDrawer.jsx
git commit -m "feat: add stage history and manual stage-move buttons to the lead drawer"
```
