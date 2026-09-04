# Lead Search Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a backend-driven Lead Search Automation feature: upload a CSV/XLSX of locations + niches, and the server searches every niche×location combination sequentially, collecting deduplicated leads, stopping each day at a configurable limit or time window, persisting exact position, and auto-resuming the next day.

**Architecture:** New `backend/automation/` package (mirrors `backend/discovery/` / `backend/research_agent/`). A `LeadSearchService` wraps the existing discovery pipeline. An APScheduler 2-minute `automation_tick` decides when a daily run is due and enqueues `run_automation_slice` on the existing `JobQueue`. The slice loops the persisted `automation_queue`, checkpointing progress atomically after every item. All state lives in SQLite; the browser only polls.

**Tech Stack:** Python 3.11 / FastAPI / `aiosqlite` (raw SQL via `backend/database.py`), APScheduler `AsyncIOScheduler`, `backend/queue_worker.py::JobQueue`, `openpyxl` (new). React 18 + Vite + `@tanstack/react-query` + `@tanstack/react-virtual` frontend.

## Global Constraints

- **No outreach anywhere in `backend/automation/`** — no import of `email_sender`, `whatsapp_sender`, `ai_brain` message generation, `marketing_agent`, `followup_engine`. This feature only discovers and stores leads.
- **No fabrication** — leads store only what the scrapers return; a missing email/phone/social stays `NULL`.
- **Dedup reuses `backend/database.py::create_or_merge_lead`** (via `backend/discovery/merge_dedup.py::merge_and_save`) — no second dedup path.
- **Backend-only scheduling** — APScheduler + `JobQueue`. Zero `setTimeout`/`setInterval` for scheduling in the frontend; the frontend only polls `GET /api/automation/status`.
- **Scraper internals untouched** — call `backend/discovery/adapters.py::SourceRegistry` / `_dispatch_source` as-is.
- **Existing 465 backend tests must stay green.** Run `venv/Scripts/python.exe -m pytest -q` from repo root (Windows) — the project venv, not `backend/.venv`.
- **Additive DB only** — new tables in `_SCHEMA_SQL` **and** a matching `_run_migrations` block (`CREATE TABLE IF NOT EXISTS` + `_add_col_if_missing`); dev DBs already exist and must upgrade cleanly on boot.
- **Frontend has no test framework** — frontend tasks verify with `cd frontend && npm run build` + manual browser check. Never claim a frontend change is "tested".
- **Settings layering pattern** — every config value: `app_settings` DB override > `.env`/`config.py` pydantic default > hardcoded fallback. Mirror `backend/research_agent/config.py::get_research_config`.
- **`source = "AUTOMATION"`** on every automation-collected lead.
- Spec: `docs/superpowers/specs/2026-08-30-lead-search-automation-design.md`.

---

## File Structure

**New — `backend/automation/`:**
| File | Responsibility |
|---|---|
| `__init__.py` | empty package marker |
| `config.py` | `get_automation_settings()` — the 7 `automation_*` settings with DB-override layering |
| `file_import.py` | `parse_upload(filename, data)` — CSV/XLSX → `ParsedImport` (locations, niches, layout, warnings). No persistence. |
| `queue_builder.py` | `build_queue(locations, niches)` — niche-outer × location-inner list of `QueueItem` |
| `lead_search_service.py` | `LeadSearchService` ABC + `DiscoveryLeadSearch` — one niche×location search via the discovery pipeline, returns `SearchResult` |
| `runner.py` | `run_automation_slice(payload)` — JobQueue handler: loop the queue, checkpoint after each item, stop at limit/deadline/pause/stop |
| `scheduler_hooks.py` | `automation_tick()`, `resume_running_slice(queue)`, `kick_slice_now(queue)`, stale-slice detection |

**New — routers / tests / frontend:**
- `backend/routers/automation.py` — `/api/automation/*`
- `tests/automation/{__init__,test_automation_db,test_file_import,test_queue_builder,test_lead_search_service,test_runner,test_scheduler_hooks,test_automation_router}.py`
- `frontend/src/components/automation/{AutomationUpload,AutomationDashboard,AutomationQueueTable,AutomationLog,AutomationSettings}.jsx`

**Modified:**
- `backend/database.py` — 4 tables + `_run_migrations` block + ~12 helper functions
- `backend/config.py` — 7 `automation_*` defaults on `Settings`
- `backend/main.py` — register router; call `resume_running_slice` in the existing `_reconcile_interrupted_jobs`
- `backend/scheduler.py` — register `automation_tick` in `start_scheduler`
- `backend/requirements.txt` — `+ openpyxl>=3.1`
- `frontend/src/api/client.js` — `automationApi`
- `frontend/src/lib/badges.js` — `AUTOMATION` badge + label
- `frontend/src/pages/LeadSearch.jsx` — mount the automation section

---

## Task 1: Database schema + helpers

**Files:**
- Modify: `backend/database.py` (`_SCHEMA_SQL` ~line 636; `_run_migrations` ~line 793; helpers appended near the research-session helpers ~line 2760)
- Create: `tests/automation/__init__.py` (empty)
- Test: `tests/automation/test_automation_db.py`

**Interfaces:**
- Produces:
  - `get_automation_state() -> dict` — returns row `id=1`, creating it (`status='IDLE'`) on first call.
  - `update_automation_state(data: dict) -> bool` — writable-field allow-list.
  - `bulk_insert_automation_queue(items: list[dict]) -> int` — items are `{position, niche, city, state}`; returns count.
  - `get_automation_queue(status: str | None = None, offset: int = 0, limit: int = 100) -> list[dict]`
  - `count_automation_queue_by_status() -> dict[str, int]`
  - `update_automation_queue_item(item_id: int, data: dict) -> bool`
  - `checkpoint_automation_progress(item_id: int, item_data: dict, state_data: dict) -> None` — updates the queue item AND `automation_state` in one `transaction()`.
  - `reset_automation_queue() -> None` — all items → `PENDING`, clears `leads_found`/`new_leads`/`attempts`/`error_message`/timestamps.
  - `append_automation_log(level: str, message: str) -> None` — inserts + prunes to last 2000 rows.
  - `get_automation_log(limit: int = 100) -> list[dict]` — newest first.
  - `create_automation_import(data: dict) -> int` — `{filename, layout, n_locations, n_niches, n_combinations}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/automation/test_automation_db.py
import pytest

pytestmark = pytest.mark.asyncio


async def test_automation_state_autocreates_row_one(clean_db):
    db = clean_db
    state = await db.get_automation_state()
    assert state["id"] == 1
    assert state["status"] == "IDLE"
    assert state["current_position"] == 0
    assert state["today_count"] == 0
    assert state["total_count"] == 0


async def test_update_automation_state_allow_list(clean_db):
    db = clean_db
    await db.get_automation_state()
    assert await db.update_automation_state({"status": "RUNNING", "today_count": 5})
    assert (await db.get_automation_state())["status"] == "RUNNING"
    # unknown / non-writable key is ignored, not an error
    assert not await db.update_automation_state({"nonsense_col": 1})


async def test_queue_bulk_insert_and_paginate(clean_db):
    db = clean_db
    items = [{"position": i, "niche": "Hair Salon", "city": f"City{i}", "state": "TX"} for i in range(5)]
    assert await db.bulk_insert_automation_queue(items) == 5
    page = await db.get_automation_queue(offset=0, limit=3)
    assert [p["position"] for p in page] == [0, 1, 2]
    counts = await db.count_automation_queue_by_status()
    assert counts["PENDING"] == 5


async def test_checkpoint_progress_is_atomic(clean_db):
    db = clean_db
    await db.get_automation_state()
    await db.bulk_insert_automation_queue([{"position": 0, "niche": "n", "city": "c", "state": "s"}])
    item = (await db.get_automation_queue())[0]
    await db.checkpoint_automation_progress(
        item["id"],
        {"status": "COMPLETED", "new_leads": 3, "leads_found": 4},
        {"current_position": 1, "today_count": 3, "total_count": 3, "queue_completed": 1},
    )
    assert (await db.get_automation_queue(status="COMPLETED"))[0]["new_leads"] == 3
    state = await db.get_automation_state()
    assert state["current_position"] == 1 and state["today_count"] == 3


async def test_reset_automation_queue(clean_db):
    db = clean_db
    await db.bulk_insert_automation_queue([{"position": 0, "niche": "n", "city": "c", "state": "s"}])
    item = (await db.get_automation_queue())[0]
    await db.update_automation_queue_item(item["id"], {"status": "COMPLETED", "new_leads": 9})
    await db.reset_automation_queue()
    reset = (await db.get_automation_queue())[0]
    assert reset["status"] == "PENDING" and reset["new_leads"] == 0


async def test_automation_log_prunes_to_cap(clean_db):
    db = clean_db
    for i in range(2010):
        await db.append_automation_log("INFO", f"line {i}")
    rows = await db.get_automation_log(limit=5000)
    assert len(rows) == 2000
    assert rows[0]["message"] == "line 2009"  # newest first
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/Scripts/python.exe -m pytest tests/automation/test_automation_db.py -q`
Expected: FAIL — `AttributeError: module 'backend.database' has no attribute 'get_automation_state'`

- [ ] **Step 3: Add the schema to `_SCHEMA_SQL`**

In `backend/database.py`, immediately after the `lead_research_evidence` table + its index (~line 647), inside the `_SCHEMA_SQL` string:

```sql

-- ── Lead Search Automation (Phase 1 — single config + single queue) ──────────
-- See docs/superpowers/specs/2026-08-30-lead-search-automation-design.md
-- Discovery-only: collects deduplicated leads, never sends outreach.
CREATE TABLE IF NOT EXISTS automation_state (
    id                    INTEGER PRIMARY KEY CHECK (id = 1),
    status                TEXT DEFAULT 'IDLE',
    current_position      INTEGER DEFAULT 0,
    today_count           INTEGER DEFAULT 0,
    total_count           INTEGER DEFAULT 0,
    today_date            TEXT,
    duration_deadline     TIMESTAMP,
    next_run_at           TIMESTAMP,
    queue_total           INTEGER DEFAULT 0,
    queue_completed       INTEGER DEFAULT 0,
    last_niche            TEXT,
    last_location         TEXT,
    last_query            TEXT,
    last_success_at       TIMESTAMP,
    last_run_started_at   TIMESTAMP,
    last_run_finished_at  TIMESTAMP,
    paused_at             TIMESTAMP,
    import_id             INTEGER REFERENCES automation_imports(id) ON DELETE SET NULL,
    updated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS automation_queue (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    position       INTEGER NOT NULL UNIQUE,
    niche          TEXT NOT NULL,
    city           TEXT,
    state          TEXT,
    status         TEXT DEFAULT 'PENDING',
    leads_found    INTEGER DEFAULT 0,
    new_leads      INTEGER DEFAULT 0,
    attempts       INTEGER DEFAULT 0,
    error_message  TEXT,
    started_at     TIMESTAMP,
    finished_at    TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_automation_queue_status   ON automation_queue (status);
CREATE INDEX IF NOT EXISTS idx_automation_queue_position ON automation_queue (position);

CREATE TABLE IF NOT EXISTS automation_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    level    TEXT DEFAULT 'INFO',
    message  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_automation_log_ts ON automation_log (id);

CREATE TABLE IF NOT EXISTS automation_imports (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    filename       TEXT,
    layout         TEXT,
    n_locations    INTEGER DEFAULT 0,
    n_niches       INTEGER DEFAULT 0,
    n_combinations INTEGER DEFAULT 0,
    imported_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

- [ ] **Step 4: Add the migration block to `_run_migrations`**

In `backend/database.py::_run_migrations`, after the `lead_research_results` `_add_col_if_missing` line (added in the prior session, ~line 793):

```python
    # Lead Search Automation — new tables are created by executescript(_SCHEMA_SQL)
    # above; this block only adds columns to an already-created table on dev DBs.
    for col, typedef in [
        ("import_id", "INTEGER"),
        ("next_run_at", "TIMESTAMP"),
    ]:
        await _add_col_if_missing(raw, "automation_state", col, typedef)
```

(No other automation columns need `_add_col_if_missing` on first release — they ship in `_SCHEMA_SQL`. This block exists so future column additions have a home.)

- [ ] **Step 5: Add the helper functions**

In `backend/database.py`, after `save_research_result` (~line 2800), add:

```python
# ─────────────────────────────────────────────────────────────────────────────
# Lead Search Automation (see 2026-08-30-lead-search-automation-design.md)
# ─────────────────────────────────────────────────────────────────────────────

_AUTOMATION_STATE_WRITABLE = frozenset({
    "status", "current_position", "today_count", "total_count", "today_date",
    "duration_deadline", "next_run_at", "queue_total", "queue_completed",
    "last_niche", "last_location", "last_query", "last_success_at",
    "last_run_started_at", "last_run_finished_at", "paused_at", "import_id",
})

_AUTOMATION_QUEUE_WRITABLE = frozenset({
    "status", "leads_found", "new_leads", "attempts", "error_message",
    "started_at", "finished_at",
})

_AUTOMATION_LOG_CAP = 2000


async def get_automation_state() -> Dict[str, Any]:
    """The single automation config/progress row (id=1). Created on first call."""
    async with get_db() as conn:
        row = await conn.fetchrow("SELECT * FROM automation_state WHERE id = 1")
        if row is None:
            await conn.execute("INSERT INTO automation_state (id, status) VALUES (1, 'IDLE')")
            row = await conn.fetchrow("SELECT * FROM automation_state WHERE id = 1")
    return dict(row)


async def update_automation_state(data: Dict[str, Any]) -> bool:
    clean = {k: v for k, v in data.items() if k in _AUTOMATION_STATE_WRITABLE and v is not None}
    if not clean:
        return False
    clean["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    set_clause = ", ".join(f"{c} = ?" for c in clean)
    async with get_db() as conn:
        result = await conn.execute(
            f"UPDATE automation_state SET {set_clause} WHERE id = 1", *clean.values()
        )
    return _rows_affected(result) > 0


async def bulk_insert_automation_queue(items: List[Dict[str, Any]]) -> int:
    if not items:
        return 0
    async with transaction() as conn:
        for it in items:
            await conn.execute(
                "INSERT INTO automation_queue (position, niche, city, state) VALUES ($1, $2, $3, $4)",
                it["position"], it["niche"], it.get("city"), it.get("state"),
            )
    return len(items)


async def get_automation_queue(
    status: Optional[str] = None, offset: int = 0, limit: int = 100,
) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        if status:
            rows = await conn.fetch(
                "SELECT * FROM automation_queue WHERE status = $1 ORDER BY position ASC LIMIT $2 OFFSET $3",
                status, limit, offset,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM automation_queue ORDER BY position ASC LIMIT $1 OFFSET $2",
                limit, offset,
            )
    return [dict(r) for r in rows]


async def count_automation_queue_by_status() -> Dict[str, int]:
    async with get_db() as conn:
        rows = await conn.fetch("SELECT status, COUNT(*) AS n FROM automation_queue GROUP BY status")
    return {r["status"]: r["n"] for r in rows}


async def update_automation_queue_item(item_id: int, data: Dict[str, Any]) -> bool:
    clean = {k: v for k, v in data.items() if k in _AUTOMATION_QUEUE_WRITABLE and v is not None}
    if not clean:
        return False
    set_clause = ", ".join(f"{c} = ?" for c in clean)
    async with get_db() as conn:
        result = await conn.execute(
            f"UPDATE automation_queue SET {set_clause} WHERE id = ?", *clean.values(), item_id
        )
    return _rows_affected(result) > 0


async def checkpoint_automation_progress(
    item_id: int, item_data: Dict[str, Any], state_data: Dict[str, Any],
) -> None:
    """Atomic recovery point: the queue item and automation_state commit together
    so a crash can never advance the position without recording the item, or
    vice versa."""
    item_clean = {k: v for k, v in item_data.items() if k in _AUTOMATION_QUEUE_WRITABLE and v is not None}
    state_clean = {k: v for k, v in state_data.items() if k in _AUTOMATION_STATE_WRITABLE and v is not None}
    state_clean["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    async with transaction() as conn:
        if item_clean:
            set_i = ", ".join(f"{c} = ?" for c in item_clean)
            await conn.execute(f"UPDATE automation_queue SET {set_i} WHERE id = ?", *item_clean.values(), item_id)
        if state_clean:
            set_s = ", ".join(f"{c} = ?" for c in state_clean)
            await conn.execute(f"UPDATE automation_state SET {set_s} WHERE id = 1", *state_clean.values())


async def reset_automation_queue() -> None:
    async with get_db() as conn:
        await conn.execute(
            "UPDATE automation_queue SET status = 'PENDING', leads_found = 0, new_leads = 0, "
            "attempts = 0, error_message = NULL, started_at = NULL, finished_at = NULL"
        )


async def append_automation_log(level: str, message: str) -> None:
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO automation_log (level, message) VALUES ($1, $2)", level, message[:2000]
        )
        await conn.execute(
            "DELETE FROM automation_log WHERE id <= "
            "(SELECT MAX(id) FROM automation_log) - $1", _AUTOMATION_LOG_CAP,
        )


async def get_automation_log(limit: int = 100) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT * FROM automation_log ORDER BY id DESC LIMIT $1", limit
        )
    return [dict(r) for r in rows]


async def create_automation_import(data: Dict[str, Any]) -> int:
    async with get_db() as conn:
        return await conn.fetchval(
            "INSERT INTO automation_imports (filename, layout, n_locations, n_niches, n_combinations) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING id",
            data.get("filename"), data.get("layout"),
            data.get("n_locations", 0), data.get("n_niches", 0), data.get("n_combinations", 0),
        )
```

Note: verify `transaction()` and `_rows_affected` exist in `database.py` (they do — used by `create_or_merge_lead` and `update_research_session`). `datetime` / `timezone` are imported at the top of `database.py`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/automation/test_automation_db.py -q`
Expected: PASS (6 tests)

- [ ] **Step 7: Run the full suite for regressions**

Run: `venv/Scripts/python.exe -m pytest -q`
Expected: PASS (471 = 465 + 6)

- [ ] **Step 8: Commit**

```bash
git add backend/database.py tests/automation/
git commit -m "feat(automation): DB schema + helpers for Lead Search Automation"
```

---

## Task 2: Automation settings config

**Files:**
- Create: `backend/automation/__init__.py` (empty), `backend/automation/config.py`
- Modify: `backend/config.py` (`Settings` class, after `daily_whatsapp_limit` ~line 49)
- Test: `tests/automation/test_config.py`

**Interfaces:**
- Consumes: `backend.database.get_all_settings`, `backend.config.get_settings`
- Produces: `backend.automation.config.get_automation_settings() -> dict` with keys `automation_enabled: bool`, `automation_daily_limit: int`, `automation_start_time: str` (`"HH:MM"`), `automation_timezone: str`, `automation_duration_hours: int`, `automation_per_item_target: int`, `automation_max_retries: int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/automation/test_config.py
import pytest
from backend.automation import config as autocfg

pytestmark = pytest.mark.asyncio


async def test_defaults_when_no_db_override(clean_db):
    cfg = await autocfg.get_automation_settings()
    assert cfg["automation_enabled"] is False
    assert cfg["automation_daily_limit"] == 500
    assert cfg["automation_start_time"] == "07:00"
    assert cfg["automation_timezone"] == "America/New_York"
    assert cfg["automation_duration_hours"] == 4
    assert cfg["automation_per_item_target"] == 100
    assert cfg["automation_max_retries"] == 2


async def test_db_override_wins_and_types_coerced(clean_db):
    db = clean_db
    await db.set_setting("automation_enabled", "true")
    await db.set_setting("automation_daily_limit", "250")
    await db.set_setting("automation_start_time", "06:30")
    cfg = await autocfg.get_automation_settings()
    assert cfg["automation_enabled"] is True
    assert cfg["automation_daily_limit"] == 250
    assert cfg["automation_start_time"] == "06:30"
```

(Check the real setting-writer name in `database.py` — it is `set_setting(key, value)`; adjust if the suite uses a different helper.)

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/Scripts/python.exe -m pytest tests/automation/test_config.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.automation'`

- [ ] **Step 3: Add defaults to `backend/config.py`**

In the `Settings` class, after `daily_whatsapp_limit: int = 20`:

```python

    # ── Lead Search Automation ───────────────────────────────────────────────
    automation_enabled:          bool = False
    automation_daily_limit:      int  = 500
    automation_start_time:       str  = "07:00"
    automation_timezone:         str  = "America/New_York"
    automation_duration_hours:   int  = 4
    automation_per_item_target:  int  = 100
    automation_max_retries:      int  = 2
```

- [ ] **Step 4: Create `backend/automation/__init__.py`** (empty file)

- [ ] **Step 5: Create `backend/automation/config.py`**

```python
"""
config.py — Lead Search Automation settings.

Layering: app_settings DB override > .env/pydantic default > this module's
fallback — identical to backend/research_agent/config.py and _scraper_cfg.
"""
from __future__ import annotations

from typing import Any, Dict

from .. import database as db
from ..config import get_settings

_env = get_settings()

_DEFAULTS: Dict[str, Any] = {
    "automation_enabled":         False,
    "automation_daily_limit":     500,
    "automation_start_time":      "07:00",
    "automation_timezone":        "America/New_York",
    "automation_duration_hours":  4,
    "automation_per_item_target": 100,
    "automation_max_retries":     2,
}


async def get_automation_settings() -> Dict[str, Any]:
    stored = await db.get_all_settings()
    cfg: Dict[str, Any] = {}
    for key, hardcoded in _DEFAULTS.items():
        default = getattr(_env, key, hardcoded)
        raw = stored.get(key)
        if raw is None:
            cfg[key] = default
        elif isinstance(default, bool):
            cfg[key] = str(raw).strip().lower() == "true"
        elif isinstance(default, int):
            try:
                cfg[key] = int(raw)
            except (TypeError, ValueError):
                cfg[key] = default
        else:
            cfg[key] = str(raw)
    return cfg
```

- [ ] **Step 6: Run tests**

Run: `venv/Scripts/python.exe -m pytest tests/automation/test_config.py -q`
Expected: PASS (2 tests)

- [ ] **Step 7: Commit**

```bash
git add backend/automation/__init__.py backend/automation/config.py backend/config.py tests/automation/test_config.py
git commit -m "feat(automation): settings config with DB-override layering"
```

---

## Task 3: File import (CSV / XLSX parsing)

**Files:**
- Create: `backend/automation/file_import.py`
- Modify: `backend/requirements.txt` (`+ openpyxl>=3.1`)
- Test: `tests/automation/test_file_import.py`

**Interfaces:**
- Produces:
  - `parse_upload(filename: str, data: bytes, second_filename: str | None = None, second_data: bytes | None = None) -> ParsedImport`
  - `@dataclass ParsedImport { locations: list[dict], niches: list[str], combinations: int, layout: str, warnings: list[str] }` where each location is `{"city": str, "state": str | None}`.
  - Raises `ValueError` (caught by the router → 422) on: unsupported extension, no niche column found, empty result.

- [ ] **Step 1: Install openpyxl**

Add `openpyxl>=3.1` to `backend/requirements.txt`, then:
Run: `venv/Scripts/python.exe -m pip install "openpyxl>=3.1"`

- [ ] **Step 2: Write the failing test**

```python
# tests/automation/test_file_import.py
import io
import pytest
from openpyxl import Workbook

from backend.automation.file_import import parse_upload

# ── helpers ──────────────────────────────────────────────────────────────

def _xlsx(sheets: dict[str, list[list]]) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

def _csv(text: str) -> bytes:
    return text.encode("utf-8")

# ── combined layout ──────────────────────────────────────────────────────

def test_combined_csv():
    data = _csv("City/Town,State,Niche\nNew York,New York,Hair Salon\nMiami,Florida,Beauty Salon\n")
    p = parse_upload("leads.csv", data)
    assert p.layout == "combined"
    assert {n for n in p.niches} == {"Hair Salon", "Beauty Salon"}
    assert {loc["city"] for loc in p.locations} == {"New York", "Miami"}
    assert p.combinations == len(p.niches) * len(p.locations)

def test_combined_xlsx_with_aliased_headers():
    data = _xlsx({"Sheet1": [["town", "region", "industry"],
                             ["Chicago", "IL", "Dental Clinic"],
                             ["Houston", "TX", "Restaurant"]]})
    p = parse_upload("data.xlsx", data)
    assert p.layout == "combined"
    assert len(p.locations) == 2 and len(p.niches) == 2

# ── separate sheets ──────────────────────────────────────────────────────

def test_separate_sheets_in_one_xlsx():
    data = _xlsx({
        "Locations": [["City/Town", "State"], ["New York", "NY"], ["LA", "CA"], ["Chicago", "IL"]],
        "Niches":    [["Niche"], ["Hair Salon"], ["Barber Shop"]],
    })
    p = parse_upload("book.xlsx", data)
    assert p.layout == "separate"
    assert len(p.locations) == 3 and len(p.niches) == 2
    assert p.combinations == 6

def test_separate_two_files():
    locs = _csv("City,State\nNew York,NY\nMiami,FL\n")
    niches = _csv("Niche\nHair Salon\n")
    p = parse_upload("locations.csv", locs, "niches.csv", niches)
    assert p.layout == "separate"
    assert len(p.locations) == 2 and p.niches == ["Hair Salon"]

# ── validation + hygiene ─────────────────────────────────────────────────

def test_missing_niche_column_raises():
    with pytest.raises(ValueError, match="niche"):
        parse_upload("x.csv", _csv("City,State\nNY,NY\n"))

def test_unsupported_extension_raises():
    with pytest.raises(ValueError, match="csv.*xlsx|xlsx.*csv|supported"):
        parse_upload("old.xls", b"\x00\x01")

def test_whitespace_and_blank_rows_cleaned():
    data = _csv("City,State,Niche\n  New York  , NY ,  Hair Salon \n\n,,\nMiami,FL,Hair Salon\n")
    p = parse_upload("x.csv", data)
    assert {loc["city"] for loc in p.locations} == {"New York", "Miami"}
    assert p.niches == ["Hair Salon"]  # de-duped, trimmed

def test_duplicate_collapse_warns():
    data = _csv("City,State,Niche\nNY,NY,Hair Salon\nNY,NY,Hair Salon\n")
    p = parse_upload("x.csv", data)
    assert len(p.locations) == 1 and len(p.niches) == 1
    assert any("duplicate" in w.lower() for w in p.warnings)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `venv/Scripts/python.exe -m pytest tests/automation/test_file_import.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: Implement `backend/automation/file_import.py`**

```python
"""
file_import.py — parse an uploaded CSV/XLSX of locations + niches into a
ParsedImport. No persistence, no side effects. .xls (old binary format) is
not supported.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

_LOCATION_HEADERS = ("city", "town", "location", "city/town")
_STATE_HEADERS = ("state", "province", "region", "st")
_NICHE_HEADERS = ("niche", "industry", "category", "keyword", "service", "type")


@dataclass
class ParsedImport:
    locations: List[Dict[str, Optional[str]]] = field(default_factory=list)
    niches: List[str] = field(default_factory=list)
    combinations: int = 0
    layout: str = "combined"          # "combined" | "separate"
    warnings: List[str] = field(default_factory=list)


def _norm(h: Any) -> str:
    return str(h or "").strip().lower().replace("_", " ").replace("-", " ")


def _match(header: str, candidates: Tuple[str, ...]) -> bool:
    h = _norm(header)
    return any(c == h or c in h.split() or h in c for c in candidates)


def _rows_from_csv(data: bytes) -> List[List[str]]:
    text = data.decode("utf-8-sig", errors="replace")
    return [row for row in csv.reader(io.StringIO(text))]


def _sheets_from_xlsx(data: bytes) -> Dict[str, List[List[Any]]]:
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out: Dict[str, List[List[Any]]] = {}
    for ws in wb.worksheets:
        out[ws.title] = [list(r) for r in ws.iter_rows(values_only=True)]
    wb.close()
    return out


def _extract_table(rows: List[List[Any]]) -> Tuple[Optional[int], Optional[int], Optional[int], List[List[str]]]:
    """Returns (city_idx, state_idx, niche_idx, body_rows). Indices are None if
    the column is absent. body_rows are the non-header rows as trimmed strings."""
    if not rows:
        return None, None, None, []
    header = rows[0]
    city_idx = state_idx = niche_idx = None
    for i, h in enumerate(header):
        if city_idx is None and _match(h, _LOCATION_HEADERS):
            city_idx = i
        elif state_idx is None and _match(h, _STATE_HEADERS):
            state_idx = i
        elif niche_idx is None and _match(h, _NICHE_HEADERS):
            niche_idx = i
    body = []
    for r in rows[1:]:
        cells = [("" if c is None else str(c)).strip() for c in r]
        if any(cells):
            body.append(cells)
    return city_idx, state_idx, niche_idx, body


def _dedupe(seq: List[str]) -> Tuple[List[str], int]:
    seen, out, dups = set(), [], 0
    for s in seq:
        k = s.lower()
        if k in seen:
            dups += 1
        else:
            seen.add(k)
            out.append(s)
    return out, dups


def _dedupe_locations(locs: List[Dict[str, Optional[str]]]) -> Tuple[List[Dict[str, Optional[str]]], int]:
    seen, out, dups = set(), [], 0
    for loc in locs:
        k = (loc["city"].lower(), (loc["state"] or "").lower())
        if k in seen:
            dups += 1
        else:
            seen.add(k)
            out.append(loc)
    return out, dups


def parse_upload(
    filename: str, data: bytes,
    second_filename: Optional[str] = None, second_data: Optional[bytes] = None,
) -> ParsedImport:
    def _load(name: str, blob: bytes):
        low = (name or "").lower()
        if low.endswith(".csv"):
            return {"__csv__": _rows_from_csv(blob)}
        if low.endswith(".xlsx"):
            return _sheets_from_xlsx(blob)
        raise ValueError(f"Unsupported file type '{name}'. Supported: .csv, .xlsx")

    sheets: Dict[str, List[List[Any]]] = _load(filename, data)
    if second_data is not None:
        for k, v in _load(second_filename or "second", second_data).items():
            sheets[f"{k}#2"] = v

    warnings: List[str] = []
    # Try each sheet as a combined table first.
    for name, rows in sheets.items():
        ci, si, ni, body = _extract_table(rows)
        if ci is not None and ni is not None:
            locs = [{"city": r[ci], "state": (r[si] if si is not None and si < len(r) else None) or None}
                    for r in body if ci < len(r) and r[ci]]
            niches = [r[ni] for r in body if ni < len(r) and r[ni]]
            locs, dl = _dedupe_locations(locs)
            niches, dn = _dedupe(niches)
            if dl or dn:
                warnings.append(f"Collapsed {dl + dn} duplicate row(s).")
            if not locs or not niches:
                raise ValueError("File parsed but produced no usable locations or niches.")
            return ParsedImport(locs, niches, len(locs) * len(niches), "combined", warnings)

    # Separate: one table has locations (city, no niche), another has niches only.
    loc_rows: List[Dict[str, Optional[str]]] = []
    niche_vals: List[str] = []
    for name, rows in sheets.items():
        ci, si, ni, body = _extract_table(rows)
        if ci is not None and ni is None:
            loc_rows += [{"city": r[ci], "state": (r[si] if si is not None and si < len(r) else None) or None}
                         for r in body if ci < len(r) and r[ci]]
        elif ni is not None:
            niche_vals += [r[ni] for r in body if ni < len(r) and r[ni]]
        elif ci is None and ni is None and body and len(body[0]) == 1:
            # a bare single-column sheet with no recognised header — treat the
            # header cell as data too (common for a hand-made niche list)
            first = _norm(rows[0][0]) if rows and rows[0] else ""
            vals = ([rows[0][0]] if first and not _match(first, _NICHE_HEADERS) else []) + [r[0] for r in body]
            niche_vals += [v for v in (str(x).strip() for x in vals) if v]

    loc_rows, dl = _dedupe_locations(loc_rows)
    niche_vals, dn = _dedupe(niche_vals)
    if dl or dn:
        warnings.append(f"Collapsed {dl + dn} duplicate row(s).")
    if not niche_vals:
        raise ValueError("No niche column found. Expected a column named niche/industry/category.")
    if not loc_rows:
        raise ValueError("No location column found. Expected a column named city/town/location.")
    return ParsedImport(loc_rows, niche_vals, len(loc_rows) * len(niche_vals), "separate", warnings)
```

- [ ] **Step 5: Run tests**

Run: `venv/Scripts/python.exe -m pytest tests/automation/test_file_import.py -q`
Expected: PASS (9 tests). Fix `_match`/`_extract_table` if the aliased-header or single-column cases miss.

- [ ] **Step 6: Commit**

```bash
git add backend/automation/file_import.py backend/requirements.txt tests/automation/test_file_import.py
git commit -m "feat(automation): CSV/XLSX file import with layout detection"
```

---

## Task 4: Queue builder

**Files:**
- Create: `backend/automation/queue_builder.py`
- Test: `tests/automation/test_queue_builder.py`

**Interfaces:**
- Consumes: `ParsedImport` shape from Task 3 (`locations: list[{city, state}]`, `niches: list[str]`)
- Produces: `build_queue(locations: list[dict], niches: list[str]) -> list[dict]` where each item is `{"position": int, "niche": str, "city": str, "state": str | None}`, ordered niche-outer then location-inner, positions contiguous from 0.

- [ ] **Step 1: Write the failing test**

```python
# tests/automation/test_queue_builder.py
from backend.automation.queue_builder import build_queue


def test_niche_outer_location_inner_ordering():
    locs = [{"city": "New York", "state": "NY"}, {"city": "LA", "state": "CA"}]
    niches = ["Hair Salon", "Barber Shop"]
    q = build_queue(locs, niches)
    assert [(i["niche"], i["city"]) for i in q] == [
        ("Hair Salon", "New York"), ("Hair Salon", "LA"),
        ("Barber Shop", "New York"), ("Barber Shop", "LA"),
    ]
    assert [i["position"] for i in q] == [0, 1, 2, 3]


def test_count_is_product():
    q = build_queue([{"city": f"c{i}", "state": None} for i in range(4)], ["a", "b", "c"])
    assert len(q) == 12


def test_empty_inputs_give_empty_queue():
    assert build_queue([], ["a"]) == []
    assert build_queue([{"city": "x", "state": None}], []) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/Scripts/python.exe -m pytest tests/automation/test_queue_builder.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `backend/automation/queue_builder.py`**

```python
"""
queue_builder.py — turn parsed locations + niches into an ordered search queue.
Niche-outer, location-inner (spec §2): all locations for niche #1, then all
locations for niche #2, and so on.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_queue(
    locations: List[Dict[str, Optional[str]]], niches: List[str],
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    pos = 0
    for niche in niches:
        for loc in locations:
            items.append({
                "position": pos,
                "niche": niche,
                "city": loc.get("city"),
                "state": loc.get("state"),
            })
            pos += 1
    return items
```

- [ ] **Step 4: Run tests**

Run: `venv/Scripts/python.exe -m pytest tests/automation/test_queue_builder.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/automation/queue_builder.py tests/automation/test_queue_builder.py
git commit -m "feat(automation): niche-outer queue builder"
```

---

## Task 5: LeadSearchService

**Files:**
- Create: `backend/automation/lead_search_service.py`
- Test: `tests/automation/test_lead_search_service.py`

**Interfaces:**
- Consumes: `backend.discovery.planner.DiscoveryPlanner`, `backend.discovery.adapters.get_registry`, `backend.discovery.merge_dedup.merge_and_save`, `backend.scraper._scraper_cfg`
- Produces:
  - `class LeadSearchService(ABC)` with `async def search_leads(self, niche: str, city: str, state: str | None, country: str | None, target: int) -> SearchResult`
  - `@dataclass SearchResult { new_leads: int, total_found: int, lead_ids: list[int], sources_used: list[str], error: str | None }`
  - `class DiscoveryLeadSearch(LeadSearchService)` — the Phase-1 implementation.
  - `get_lead_search_service() -> LeadSearchService` factory (returns `DiscoveryLeadSearch()`).

- [ ] **Step 1: Write the failing test**

```python
# tests/automation/test_lead_search_service.py
import pytest

from backend.automation import lead_search_service as svc

pytestmark = pytest.mark.asyncio


class _FakePlan:
    recommended_sources = ["GOOGLE_MAPS", "YELLOW_PAGES"]
    query_variants = ["dental clinic"]
    intent = "LOCAL_BUSINESS"


class _FakePlanner:
    async def plan(self, **kw):
        return _FakePlan()


class _FakeRegistry:
    def __init__(self, per_source):
        self._per_source = per_source
    def get(self, name):
        return object() if name in self._per_source else None
    async def execute(self, name, niche, city, country, budget, cfg, log_fn=None):
        from backend.discovery.adapters import AdapterResult
        return AdapterResult(source=name, leads=self._per_source.get(name, []))


async def test_counts_new_leads_from_merge(monkeypatch, clean_db):
    monkeypatch.setattr(svc, "DiscoveryPlanner", lambda: _FakePlanner())
    monkeypatch.setattr(svc, "get_registry", lambda: _FakeRegistry({
        "GOOGLE_MAPS": [{"business_name": "A Dental", "phone": "5551110000", "city": "Akron"}],
        "YELLOW_PAGES": [{"business_name": "B Dental", "phone": "5552220000", "city": "Akron"}],
    }))
    monkeypatch.setattr(svc, "_scraper_cfg", lambda: _async({}))

    result = await svc.DiscoveryLeadSearch().search_leads("dental clinic", "Akron", "OH", "USA", 50)
    assert result.error is None
    assert result.new_leads == 2
    assert set(result.sources_used) == {"GOOGLE_MAPS", "YELLOW_PAGES"}


async def test_source_exception_becomes_error_not_raise(monkeypatch, clean_db):
    monkeypatch.setattr(svc, "DiscoveryPlanner", lambda: _FakePlanner())

    class _Boom:
        def get(self, name): return object()
        async def execute(self, *a, **kw): raise RuntimeError("scraper died")
    monkeypatch.setattr(svc, "get_registry", lambda: _Boom())
    monkeypatch.setattr(svc, "_scraper_cfg", lambda: _async({}))

    result = await svc.DiscoveryLeadSearch().search_leads("dental", "Akron", "OH", "USA", 50)
    assert result.new_leads == 0
    assert result.error and "scraper died" in result.error


async def _async(v):
    return v
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/Scripts/python.exe -m pytest tests/automation/test_lead_search_service.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `backend/automation/lead_search_service.py`**

```python
"""
lead_search_service.py — the swappable search layer (spec §14).

Phase 1: DiscoveryLeadSearch runs one niche×location Quick Search through the
existing discovery pipeline (planner → source registry → merge/dedup). A paid
provider can be added later as another LeadSearchService subclass behind a
settings key without touching the runner.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

from ..discovery.adapters import get_registry
from ..discovery.merge_dedup import merge_and_save
from ..discovery.planner import DiscoveryPlanner
from ..scraper import _scraper_cfg

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    new_leads: int = 0
    total_found: int = 0
    lead_ids: List[int] = field(default_factory=list)
    sources_used: List[str] = field(default_factory=list)
    error: Optional[str] = None


class LeadSearchService(ABC):
    @abstractmethod
    async def search_leads(
        self, niche: str, city: str, state: Optional[str],
        country: Optional[str], target: int,
    ) -> SearchResult:
        ...


class DiscoveryLeadSearch(LeadSearchService):
    """Runs the built-in discovery pipeline for a single niche×location."""

    async def search_leads(
        self, niche: str, city: str, state: Optional[str],
        country: Optional[str], target: int,
    ) -> SearchResult:
        try:
            planner = DiscoveryPlanner()
            plan = await planner.plan(query=niche, niche=niche, city=city,
                                      country=country or "", mode="QUICK")
            search_text = plan.query_variants[0] if plan.query_variants else niche
            registry = get_registry()
            cfg = await _scraper_cfg()

            candidates: list = []
            used: List[str] = []
            for source_name in plan.recommended_sources:
                adapter = registry.get(source_name)
                if adapter is None:
                    continue
                res = await registry.execute(
                    source_name, search_text, city, country or "", target, cfg, log_fn=None,
                )
                used.append(source_name)
                candidates.extend(res.leads or [])
                if len(candidates) >= target:
                    break

            for c in candidates:
                c.setdefault("source", "AUTOMATION")
                c.setdefault("niche", niche)
                if state and not c.get("state"):
                    c["state"] = state

            save = await merge_and_save(candidates, run_id=None, default_source="AUTOMATION")
            return SearchResult(
                new_leads=save["new_count"],
                total_found=len(candidates),
                lead_ids=save.get("unique_saved_ids", []),
                sources_used=used,
            )
        except Exception as exc:  # never raises — the runner treats .error as a soft failure
            logger.warning("LeadSearchService failed for %s / %s: %s", niche, city, exc, exc_info=True)
            return SearchResult(error=str(exc)[:300])


def get_lead_search_service() -> LeadSearchService:
    return DiscoveryLeadSearch()
```

- [ ] **Step 4: Run tests**

Run: `venv/Scripts/python.exe -m pytest tests/automation/test_lead_search_service.py -q`
Expected: PASS (2 tests). If `merge_and_save`'s return key differs, align the test + code to the real key (`new_count`).

- [ ] **Step 5: Full suite**

Run: `venv/Scripts/python.exe -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/automation/lead_search_service.py tests/automation/test_lead_search_service.py
git commit -m "feat(automation): LeadSearchService over the discovery pipeline"
```

---

## Task 6: Runner (`run_automation_slice`)

**Files:**
- Create: `backend/automation/runner.py`
- Test: `tests/automation/test_runner.py`

**Interfaces:**
- Consumes: all Task 1 DB helpers; `get_automation_settings` (Task 2); `get_lead_search_service` + `SearchResult` (Task 5)
- Produces:
  - `async def run_automation_slice(payload: dict | None = None) -> None` — the JobQueue handler. Never raises.
  - `DURATION_UNLIMITED = 0`
  - `_STALE_SLICE_SECONDS = 1200`

- [ ] **Step 1: Write the failing test**

```python
# tests/automation/test_runner.py
from datetime import datetime, timedelta, timezone

import pytest

from backend.automation import runner as runner_mod
from backend.automation.lead_search_service import SearchResult

pytestmark = pytest.mark.asyncio


def _utc_iso(dt):
    return dt.replace(tzinfo=None).isoformat()


async def _seed_queue(db, n, niche="Hair Salon"):
    await db.get_automation_state()
    await db.bulk_insert_automation_queue(
        [{"position": i, "niche": niche, "city": f"City{i}", "state": "TX"} for i in range(n)]
    )
    await db.update_automation_state({"queue_total": n})


def _fake_service(per_call_new=3, error_on=None):
    calls = {"n": 0}

    class _S:
        async def search_leads(self, niche, city, state, country, target):
            calls["n"] += 1
            if error_on and calls["n"] in error_on:
                return SearchResult(error="transient")
            return SearchResult(new_leads=per_call_new, total_found=per_call_new, sources_used=["GOOGLE_MAPS"])

    return _S(), calls


async def test_stops_at_daily_limit_final_item_overshoots(clean_db, monkeypatch):
    db = clean_db
    await _seed_queue(db, 10)
    await db.update_automation_state({"status": "SCHEDULED", "today_count": 0})
    svc, calls = _fake_service(per_call_new=4)
    monkeypatch.setattr(runner_mod, "get_lead_search_service", lambda: svc)
    monkeypatch.setattr(runner_mod, "get_automation_settings",
                        lambda: _async({"automation_daily_limit": 10, "automation_duration_hours": 0,
                                        "automation_per_item_target": 100, "automation_max_retries": 0,
                                        "automation_enabled": True}))
    await runner_mod.run_automation_slice()
    state = await db.get_automation_state()
    assert state["status"] == "LIMIT_REACHED"
    assert state["today_count"] == 12          # 3 items * 4, final overshoots past 10
    assert calls["n"] == 3
    assert state["current_position"] == 3


async def test_stops_at_duration_deadline(clean_db, monkeypatch):
    db = clean_db
    await _seed_queue(db, 10)
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db.update_automation_state({"status": "SCHEDULED", "duration_deadline": _utc_iso(past)})
    svc, calls = _fake_service()
    monkeypatch.setattr(runner_mod, "get_lead_search_service", lambda: svc)
    monkeypatch.setattr(runner_mod, "get_automation_settings",
                        lambda: _async({"automation_daily_limit": 999, "automation_duration_hours": 4,
                                        "automation_per_item_target": 100, "automation_max_retries": 0,
                                        "automation_enabled": True}))
    await runner_mod.run_automation_slice()
    assert (await db.get_automation_state())["status"] == "SCHEDULED"
    assert calls["n"] == 0                       # deadline already passed → no work


async def test_position_persisted_after_every_item(clean_db, monkeypatch):
    db = clean_db
    await _seed_queue(db, 3)
    await db.update_automation_state({"status": "SCHEDULED"})
    positions_seen = []
    svc, _ = _fake_service()
    real = svc.search_leads

    async def spy(*a, **kw):
        positions_seen.append((await db.get_automation_state())["current_position"])
        return await real(*a, **kw)
    svc.search_leads = spy
    monkeypatch.setattr(runner_mod, "get_lead_search_service", lambda: svc)
    monkeypatch.setattr(runner_mod, "get_automation_settings",
                        lambda: _async({"automation_daily_limit": 999, "automation_duration_hours": 0,
                                        "automation_per_item_target": 100, "automation_max_retries": 0,
                                        "automation_enabled": True}))
    await runner_mod.run_automation_slice()
    assert positions_seen == [0, 1, 2]
    assert (await db.get_automation_state())["status"] == "COMPLETED"


async def test_pause_stops_at_next_boundary(clean_db, monkeypatch):
    db = clean_db
    await _seed_queue(db, 5)
    await db.update_automation_state({"status": "SCHEDULED"})

    class _S:
        n = 0
        async def search_leads(self, *a, **kw):
            _S.n += 1
            if _S.n == 2:
                await db.update_automation_state({"status": "PAUSED"})
            return SearchResult(new_leads=1)
    svc = _S()
    monkeypatch.setattr(runner_mod, "get_lead_search_service", lambda: svc)
    monkeypatch.setattr(runner_mod, "get_automation_settings",
                        lambda: _async({"automation_daily_limit": 999, "automation_duration_hours": 0,
                                        "automation_per_item_target": 100, "automation_max_retries": 0,
                                        "automation_enabled": True}))
    await runner_mod.run_automation_slice()
    state = await db.get_automation_state()
    assert state["status"] == "PAUSED"
    assert state["current_position"] == 2      # item 0 and 1 done, paused before 2


async def test_transient_error_retried_then_item_failed_and_advances(clean_db, monkeypatch):
    db = clean_db
    await _seed_queue(db, 2)
    await db.update_automation_state({"status": "SCHEDULED"})

    class _S:
        async def search_leads(self, niche, city, *a, **kw):
            return SearchResult(error="always") if city == "City0" else SearchResult(new_leads=2)
    monkeypatch.setattr(runner_mod, "get_lead_search_service", lambda: _S())
    monkeypatch.setattr(runner_mod, "asyncio", runner_mod.asyncio)  # keep real asyncio
    monkeypatch.setattr(runner_mod, "get_automation_settings",
                        lambda: _async({"automation_daily_limit": 999, "automation_duration_hours": 0,
                                        "automation_per_item_target": 100, "automation_max_retries": 2,
                                        "automation_enabled": True}))
    await runner_mod.run_automation_slice()
    q = await db.get_automation_queue()
    assert q[0]["status"] == "FAILED" and q[0]["attempts"] == 3
    assert q[1]["status"] == "COMPLETED"
    assert (await db.get_automation_state())["status"] == "COMPLETED"


async def test_never_raises_on_unexpected_error(clean_db, monkeypatch):
    db = clean_db
    await _seed_queue(db, 1)
    await db.update_automation_state({"status": "SCHEDULED"})

    class _S:
        async def search_leads(self, *a, **kw):
            raise KeyError("boom inside service call site")
    monkeypatch.setattr(runner_mod, "get_lead_search_service", lambda: _S())
    monkeypatch.setattr(runner_mod, "get_automation_settings",
                        lambda: _async({"automation_daily_limit": 999, "automation_duration_hours": 0,
                                        "automation_per_item_target": 100, "automation_max_retries": 0,
                                        "automation_enabled": True}))
    await runner_mod.run_automation_slice()          # must NOT raise
    assert (await db.get_automation_state())["status"] in ("SCHEDULED", "COMPLETED")


async def test_guard_exits_are_noops(clean_db, monkeypatch):
    db = clean_db
    await _seed_queue(db, 2)
    await db.update_automation_state({"status": "STOPPED"})
    monkeypatch.setattr(runner_mod, "get_automation_settings",
                        lambda: _async({"automation_daily_limit": 999, "automation_duration_hours": 0,
                                        "automation_per_item_target": 100, "automation_max_retries": 0,
                                        "automation_enabled": True}))
    await runner_mod.run_automation_slice()
    assert (await db.get_automation_state())["status"] == "STOPPED"  # untouched


async def _async(v):
    return v
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/Scripts/python.exe -m pytest tests/automation/test_runner.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `backend/automation/runner.py`**

```python
"""
runner.py — run_automation_slice: the JobQueue handler that walks
automation_queue from current_position, one niche×location at a time,
checkpointing progress atomically after each item and stopping when the daily
limit or time window is hit, or the user pauses/stops. Never raises out.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .. import database as db
from .config import get_automation_settings
from .lead_search_service import get_lead_search_service

logger = logging.getLogger(__name__)

DURATION_UNLIMITED = 0
_STALE_SLICE_SECONDS = 1200
_RETRY_BACKOFF_SECONDS = 5

_STOP_STATUSES = {"PAUSED", "STOPPED"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().replace(tzinfo=None).isoformat()


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", ""))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


async def _log(level: str, msg: str) -> None:
    try:
        await db.append_automation_log(level, msg)
    except Exception:
        logger.debug("automation_log write failed", exc_info=True)
    logger.info("[AUTOMATION] %s", msg)


async def run_automation_slice(payload: Optional[Dict[str, Any]] = None) -> None:
    try:
        await _run_slice()
    except Exception as exc:  # last-resort — a slice must never crash the worker
        logger.error("run_automation_slice crashed: %s", exc, exc_info=True)
        try:
            await db.update_automation_state({"status": "SCHEDULED", "last_run_finished_at": _now_iso()})
            await _log("ERROR", f"Automation run stopped unexpectedly: {exc}")
        except Exception:
            logger.error("could not persist automation failure state", exc_info=True)


async def _run_slice() -> None:
    cfg = await get_automation_settings()
    state = await db.get_automation_state()

    # ── Guards ───────────────────────────────────────────────────────────
    if not cfg["automation_enabled"] and state["status"] != "SCHEDULED":
        return
    if state["status"] in _STOP_STATUSES:
        return
    if state["status"] == "RUNNING":
        started = _parse_ts(state.get("last_run_started_at"))
        updated = _parse_ts(state.get("updated_at"))
        fresh = max([t for t in (started, updated) if t], default=None)
        if fresh and (_now() - fresh).total_seconds() < _STALE_SLICE_SECONDS:
            return  # another slice genuinely owns this run
        await _log("WARN", "Previous run looked stalled — taking over.")

    limit = int(cfg["automation_daily_limit"])
    if int(state["today_count"] or 0) >= limit:
        await db.update_automation_state({"status": "LIMIT_REACHED"})
        return

    deadline = _parse_ts(state.get("duration_deadline"))
    duration_h = int(cfg["automation_duration_hours"])
    if duration_h != DURATION_UNLIMITED and deadline and _now() >= deadline:
        await db.update_automation_state({"status": "SCHEDULED"})
        return

    queue = await db.get_automation_queue(offset=state["current_position"], limit=100000)
    queue = [q for q in queue if q["position"] >= state["current_position"]]
    if not queue:
        await db.update_automation_state({"status": "COMPLETED"})
        return

    # ── Run ──────────────────────────────────────────────────────────────
    service = get_lead_search_service()
    target = int(cfg["automation_per_item_target"])
    max_retries = int(cfg["automation_max_retries"])
    await db.update_automation_state({"status": "RUNNING", "last_run_started_at": _now_iso()})
    await _log("INFO", f"Automation run started at position {state['current_position']}")

    today_count = int(state["today_count"] or 0)
    total_count = int(state["total_count"] or 0)
    completed = int(state["queue_completed"] or 0)

    for item in queue:
        live = await db.get_automation_state()
        if live["status"] in _STOP_STATUSES:
            await _log("INFO", f"Run {live['status'].lower()} at position {item['position']}")
            return
        if today_count >= limit:
            await db.update_automation_state({"status": "LIMIT_REACHED"})
            await _log("INFO", f"Daily limit {limit} reached — {today_count} leads today.")
            return
        if duration_h != DURATION_UNLIMITED and deadline and _now() >= deadline:
            await db.update_automation_state({"status": "SCHEDULED"})
            await _log("INFO", "Daily time window ended — resuming at the next scheduled run.")
            return

        loc = ", ".join(p for p in (item["city"], item["state"]) if p)
        await db.update_automation_queue_item(item["id"], {"status": "SEARCHING", "started_at": _now_iso()})
        await _log("INFO", f"Searching {item['niche']} / {loc}")

        result = None
        attempts = 0
        for attempt in range(max_retries + 1):
            attempts = attempt + 1
            result = await service.search_leads(
                item["niche"], item["city"], item.get("state"), None, target,
            )
            if not result.error:
                break
            if attempt < max_retries:
                await _log("WARN", f"{item['niche']} / {loc}: {result.error} — retry {attempt + 1}/{max_retries}")
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS)

        if result.error and not result.new_leads:
            item_status = "FAILED"
        elif result.error:
            item_status = "PARTIAL"
        else:
            item_status = "COMPLETED"

        today_count += result.new_leads
        total_count += result.new_leads
        completed += 1

        await db.checkpoint_automation_progress(
            item["id"],
            {
                "status": item_status, "leads_found": result.total_found,
                "new_leads": result.new_leads, "attempts": attempts,
                "error_message": result.error, "finished_at": _now_iso(),
            },
            {
                "current_position": item["position"] + 1,
                "today_count": today_count, "total_count": total_count,
                "queue_completed": completed,
                "last_niche": item["niche"], "last_location": loc,
                "last_query": f"{item['niche']} {loc}".strip(),
                "last_success_at": _now_iso() if result.new_leads else None,
            },
        )
        await _log(
            "INFO" if item_status != "FAILED" else "ERROR",
            f"{item['niche']} / {loc}: {result.total_found} found, {result.new_leads} new"
            + (f" ({result.error})" if result.error else ""),
        )

    await db.update_automation_state({"status": "COMPLETED", "last_run_finished_at": _now_iso()})
    await _log("INFO", "Queue complete.")
```

- [ ] **Step 4: Run tests**

Run: `venv/Scripts/python.exe -m pytest tests/automation/test_runner.py -q`
Expected: PASS (8 tests). The retry test uses a 5s backoff — if it makes the test slow, add `monkeypatch.setattr(runner_mod, "_RETRY_BACKOFF_SECONDS", 0)` in that test.

- [ ] **Step 5: Full suite**

Run: `venv/Scripts/python.exe -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/automation/runner.py tests/automation/test_runner.py
git commit -m "feat(automation): slice runner with atomic checkpointing + limit/deadline/pause stops"
```

---

## Task 7: Scheduler hooks

**Files:**
- Create: `backend/automation/scheduler_hooks.py`
- Test: `tests/automation/test_scheduler_hooks.py`

**Interfaces:**
- Consumes: Task 1 helpers; `get_automation_settings`; `run_automation_slice` (Task 6)
- Produces:
  - `async def automation_tick() -> None` — APScheduler entry point (no args).
  - `async def resume_running_slice(queue) -> None` — startup reconciler.
  - `async def kick_slice_now(queue) -> bool` — daily-reset-if-new-day then enqueue; returns False if it couldn't enqueue.
  - `_ENQUEUE(queue)` helper wrapping `queue.enqueue_nowait("AUTOMATION", {}, run_automation_slice)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/automation/test_scheduler_hooks.py
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend.automation import scheduler_hooks as hooks

pytestmark = pytest.mark.asyncio


class _FakeQueue:
    def __init__(self): self.jobs = []
    def enqueue_nowait(self, jt, payload, handler): self.jobs.append(jt); return True


def _settings(**over):
    base = {"automation_enabled": True, "automation_daily_limit": 500,
            "automation_start_time": "07:00", "automation_timezone": "America/New_York",
            "automation_duration_hours": 4, "automation_per_item_target": 100,
            "automation_max_retries": 2}
    base.update(over)
    async def _f(): return base
    return _f


async def test_tick_noop_when_disabled(clean_db, monkeypatch):
    q = _FakeQueue()
    monkeypatch.setattr(hooks, "get_automation_settings", _settings(automation_enabled=False))
    monkeypatch.setattr(hooks, "get_queue", lambda: q)
    await hooks.automation_tick()
    assert q.jobs == []


async def test_tick_noop_before_start_time(clean_db, monkeypatch):
    q = _FakeQueue()
    tz = ZoneInfo("America/New_York")
    early = datetime.now(tz).replace(hour=5, minute=0).strftime("%H:%M")
    monkeypatch.setattr(hooks, "get_automation_settings", _settings(automation_start_time="23:59"))
    monkeypatch.setattr(hooks, "get_queue", lambda: q)
    monkeypatch.setattr(hooks, "_now_local", lambda tzname: datetime.now(ZoneInfo(tzname)).replace(hour=5, minute=0))
    await hooks.automation_tick()
    assert q.jobs == []


async def test_tick_enqueues_and_resets_today_count_on_new_day(clean_db, monkeypatch):
    db = clean_db
    await db.get_automation_state()
    await db.update_automation_state({"today_count": 123, "today_date": "2000-01-01",
                                     "current_position": 7, "total_count": 900, "queue_total": 20})
    q = _FakeQueue()
    monkeypatch.setattr(hooks, "get_automation_settings", _settings(automation_start_time="00:00"))
    monkeypatch.setattr(hooks, "get_queue", lambda: q)
    await hooks.automation_tick()
    assert q.jobs == ["AUTOMATION"]
    state = await db.get_automation_state()
    assert state["today_count"] == 0
    assert state["current_position"] == 7        # position preserved — resume, not restart
    assert state["total_count"] == 900


async def test_tick_noop_if_already_ran_today(clean_db, monkeypatch):
    db = clean_db
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    await db.get_automation_state()
    await db.update_automation_state({"today_date": today, "status": "COMPLETED"})
    q = _FakeQueue()
    monkeypatch.setattr(hooks, "get_automation_settings", _settings(automation_start_time="00:00"))
    monkeypatch.setattr(hooks, "get_queue", lambda: q)
    await hooks.automation_tick()
    assert q.jobs == []


async def test_resume_running_slice_requeues_stuck_run(clean_db, monkeypatch):
    db = clean_db
    await db.get_automation_state()
    await db.update_automation_state({"status": "RUNNING"})
    q = _FakeQueue()
    n = await hooks.resume_running_slice(q)
    assert n == 1 and q.jobs == ["AUTOMATION"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/Scripts/python.exe -m pytest tests/automation/test_scheduler_hooks.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `backend/automation/scheduler_hooks.py`**

```python
"""
scheduler_hooks.py — decides WHEN the automation runs.

automation_tick() is registered on APScheduler (IntervalTrigger, 2 min). It
never does search work itself — it only enqueues run_automation_slice on the
JobQueue when a daily run is due in the configured timezone.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from .. import database as db
from ..queue_worker import get_queue
from .config import get_automation_settings
from .runner import run_automation_slice

logger = logging.getLogger(__name__)


def _now_local(tzname: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(tzname))
    except Exception:
        return datetime.now(ZoneInfo("UTC"))


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        h, m = str(value).split(":")
        return max(0, min(23, int(h))), max(0, min(59, int(m)))
    except (ValueError, AttributeError):
        return 7, 0


def _enqueue(queue) -> bool:
    if queue is None:
        return False
    return bool(queue.enqueue_nowait("AUTOMATION", {}, run_automation_slice))


async def _daily_reset_and_deadline(cfg: dict, today_local: str) -> None:
    duration_h = int(cfg["automation_duration_hours"])
    deadline = None
    if duration_h > 0:
        deadline = (datetime.now(timezone.utc) + timedelta(hours=duration_h)).replace(tzinfo=None).isoformat()
    await db.update_automation_state({
        "today_date": today_local, "today_count": 0, "status": "SCHEDULED",
        "duration_deadline": deadline,
    })


async def automation_tick() -> None:
    try:
        cfg = await get_automation_settings()
        if not cfg["automation_enabled"]:
            return
        state = await db.get_automation_state()
        if state["status"] in ("RUNNING", "STOPPED"):
            return
        if int(state.get("queue_total") or 0) == 0:
            return

        tzname = cfg["automation_timezone"]
        now_local = _now_local(tzname)
        today_local = now_local.date().isoformat()
        if state.get("today_date") == today_local:
            return  # already ran (or is running) today

        start_h, start_m = _parse_hhmm(cfg["automation_start_time"])
        if (now_local.hour, now_local.minute) < (start_h, start_m):
            return

        await _daily_reset_and_deadline(cfg, today_local)
        if _enqueue(get_queue()):
            await db.append_automation_log("INFO", f"Scheduled run started ({tzname} {cfg['automation_start_time']}).")
        else:
            await db.update_automation_state({"status": "SCHEDULED"})
            await db.append_automation_log("ERROR", "Could not enqueue scheduled run — queue unavailable.")
    except Exception as exc:
        logger.warning("automation_tick failed: %s", exc, exc_info=True)


async def resume_running_slice(queue) -> int:
    """Startup reconciler — a RUNNING state means the previous worker died."""
    state = await db.get_automation_state()
    if state["status"] != "RUNNING":
        return 0
    if _enqueue(queue):
        await db.append_automation_log("WARN", "Resumed an interrupted automation run after restart.")
        return 1
    await db.update_automation_state({"status": "SCHEDULED"})
    return 0


async def kick_slice_now(queue) -> bool:
    """POST /start and /resume: reset the daily counter if it's a new day,
    then enqueue a slice immediately (still bounded by the daily limit)."""
    cfg = await get_automation_settings()
    state = await db.get_automation_state()
    today_local = _now_local(cfg["automation_timezone"]).date().isoformat()
    if state.get("today_date") != today_local:
        await _daily_reset_and_deadline(cfg, today_local)
    else:
        await db.update_automation_state({"status": "SCHEDULED"})
    return _enqueue(queue)
```

- [ ] **Step 4: Run tests**

Run: `venv/Scripts/python.exe -m pytest tests/automation/test_scheduler_hooks.py -q`
Expected: PASS (5 tests). The `_now_local` monkeypatch in `test_tick_noop_before_start_time` requires `_now_local` to be a module-level function taking `tzname` — it is.

- [ ] **Step 5: Commit**

```bash
git add backend/automation/scheduler_hooks.py tests/automation/test_scheduler_hooks.py
git commit -m "feat(automation): timezone-aware scheduler tick + startup reconciler"
```

---

## Task 8: API router

**Files:**
- Create: `backend/routers/automation.py`
- Test: `tests/automation/test_automation_router.py`

**Interfaces:**
- Consumes: Task 1 helpers; `parse_upload` (Task 3); `build_queue` (Task 4); `get_automation_settings` (Task 2); `kick_slice_now` (Task 7); `get_queue`
- Produces: `router` (APIRouter, prefix `/api/automation`). Endpoints per spec §6.

- [ ] **Step 1: Write the failing test**

```python
# tests/automation/test_automation_router.py
import io
import pytest
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook

from backend.routers import automation as auto_router

pytestmark = pytest.mark.asyncio


class _FakeQueue:
    def __init__(self): self.jobs = []
    def enqueue_nowait(self, jt, payload, handler): self.jobs.append(jt); return True


async def _client():
    from backend.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _combined_csv() -> bytes:
    return b"City,State,Niche\nNew York,NY,Hair Salon\nMiami,FL,Barber Shop\n"


async def test_preview_does_not_persist(clean_db):
    db = clean_db
    async with await _client() as c:
        resp = await c.post("/api/automation/import/preview",
                            files={"file": ("l.csv", _combined_csv(), "text/csv")})
    assert resp.status_code == 200
    body = resp.json()
    assert body["combinations"] == 4
    assert body["n_locations"] == 2 and body["n_niches"] == 2
    assert await db.get_automation_queue() == []          # nothing written


async def test_preview_422_on_bad_file(clean_db):
    async with await _client() as c:
        resp = await c.post("/api/automation/import/preview",
                            files={"file": ("x.csv", b"City,State\nNY,NY\n", "text/csv")})
    assert resp.status_code == 422


async def test_confirm_builds_queue_and_resets_position(clean_db, monkeypatch):
    db = clean_db
    await db.get_automation_state()
    await db.update_automation_state({"current_position": 9, "total_count": 40})
    payload = {"locations": [{"city": "NY", "state": "NY"}, {"city": "LA", "state": "CA"}],
               "niches": ["Hair Salon"], "filename": "l.csv", "layout": "combined"}
    async with await _client() as c:
        resp = await c.post("/api/automation/import/confirm", json=payload)
    assert resp.status_code == 200
    q = await db.get_automation_queue()
    assert [i["position"] for i in q] == [0, 1]
    state = await db.get_automation_state()
    assert state["current_position"] == 0 and state["today_count"] == 0
    assert state["total_count"] == 40                      # preserved


async def test_start_respects_daily_limit(clean_db, monkeypatch):
    db = clean_db
    monkeypatch.setattr(auto_router, "get_queue", lambda: _FakeQueue())
    await db.get_automation_state()
    await db.bulk_insert_automation_queue([{"position": 0, "niche": "n", "city": "c", "state": "s"}])
    await db.update_automation_state({"today_count": 500, "queue_total": 1})
    monkeypatch.setattr(auto_router, "get_automation_settings",
                        lambda: _async({"automation_daily_limit": 500, "automation_enabled": True,
                                        "automation_timezone": "UTC", "automation_start_time": "07:00",
                                        "automation_duration_hours": 4}))
    async with await _client() as c:
        resp = await c.post("/api/automation/start")
    assert resp.status_code == 409


async def test_reset_requires_confirm(clean_db):
    async with await _client() as c:
        no = await c.post("/api/automation/reset")
        yes = await c.post("/api/automation/reset?confirm=true")
    assert no.status_code == 400
    assert yes.status_code == 200


async def test_pause_resume_stop_transitions(clean_db, monkeypatch):
    db = clean_db
    monkeypatch.setattr(auto_router, "get_queue", lambda: _FakeQueue())
    monkeypatch.setattr(auto_router, "kick_slice_now", lambda q: _async(True))
    await db.get_automation_state()
    await db.update_automation_state({"status": "RUNNING"})
    async with await _client() as c:
        assert (await c.post("/api/automation/pause")).json()["status"] == "PAUSED"
        assert (await c.post("/api/automation/resume")).json()["status"] in ("SCHEDULED", "RUNNING")
        assert (await c.post("/api/automation/stop")).json()["status"] == "STOPPED"


async def test_status_has_derived_fields(clean_db):
    db = clean_db
    await db.get_automation_state()
    await db.update_automation_state({"today_count": 65, "queue_total": 10, "queue_completed": 4})
    async with await _client() as c:
        body = (await c.get("/api/automation/status")).json()
    assert "progress_pct" in body and "searches_remaining" in body
    assert body["searches_remaining"] == 6


async def _async(v):
    return v
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/Scripts/python.exe -m pytest tests/automation/test_automation_router.py -q`
Expected: FAIL — `ImportError` (router not registered yet) or `ModuleNotFoundError`

- [ ] **Step 3: Implement `backend/routers/automation.py`**

```python
"""
routers/automation.py — Lead Search Automation API (spec §6). Discovery-only:
no outreach. All routes behind the existing single-user session dependency
(applied in main.py). Scheduling is backend-only — the frontend just polls
GET /status.
"""
import logging
import math
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from .. import database as db
from ..automation.config import get_automation_settings
from ..automation.file_import import parse_upload
from ..automation.queue_builder import build_queue
from ..automation.scheduler_hooks import kick_slice_now
from ..queue_worker import get_queue
from ..rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/automation", tags=["automation"])

_ACTIVE = {"RUNNING", "SCHEDULED"}
_SETTING_KEYS = (
    "automation_enabled", "automation_daily_limit", "automation_start_time",
    "automation_timezone", "automation_duration_hours", "automation_per_item_target",
    "automation_max_retries",
)


class ConfirmImport(BaseModel):
    locations: List[Dict[str, Optional[str]]]
    niches: List[str]
    filename: Optional[str] = None
    layout: str = "combined"


class SettingsUpdate(BaseModel):
    automation_enabled: Optional[bool] = None
    automation_daily_limit: Optional[int] = None
    automation_start_time: Optional[str] = None
    automation_timezone: Optional[str] = None
    automation_duration_hours: Optional[int] = None
    automation_per_item_target: Optional[int] = None
    automation_max_retries: Optional[int] = None


async def _status_body() -> Dict[str, Any]:
    state = await db.get_automation_state()
    cfg = await get_automation_settings()
    counts = await db.count_automation_queue_by_status()
    total = int(state.get("queue_total") or sum(counts.values()))
    done = sum(counts.get(s, 0) for s in ("COMPLETED", "PARTIAL", "FAILED", "SKIPPED"))
    limit = int(cfg["automation_daily_limit"]) or 1
    return {
        **state,
        "settings": cfg,
        "counts_by_status": counts,
        "progress_pct": round(min(100.0, 100.0 * int(state.get("today_count") or 0) / limit), 1),
        "searches_completed": done,
        "searches_remaining": max(0, total - done),
        "current_niche": state.get("last_niche"),
        "current_location": state.get("last_location"),
    }


@router.post("/import/preview")
@limiter.limit("10/minute")
async def import_preview(request: Request, file: UploadFile = File(...), file2: Optional[UploadFile] = File(None)):
    data = await file.read()
    d2 = await file2.read() if file2 is not None else None
    try:
        parsed = parse_upload(file.filename, data, file2.filename if file2 else None, d2)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    cfg = await get_automation_settings()
    limit = int(cfg["automation_daily_limit"]) or 1
    return {
        "locations": parsed.locations,
        "niches": parsed.niches,
        "n_locations": len(parsed.locations),
        "n_niches": len(parsed.niches),
        "combinations": parsed.combinations,
        "layout": parsed.layout,
        "warnings": parsed.warnings,
        "estimated_days": math.ceil(parsed.combinations / limit),
        "estimate_note": "Rough minimum — assumes at least one new lead per search; actual results vary.",
    }


@router.post("/import/confirm")
async def import_confirm(payload: ConfirmImport):
    if not payload.locations or not payload.niches:
        raise HTTPException(status_code=422, detail="Need at least one location and one niche.")
    items = build_queue(payload.locations, payload.niches)
    cfg = await get_automation_settings()
    import_id = await db.create_automation_import({
        "filename": payload.filename, "layout": payload.layout,
        "n_locations": len(payload.locations), "n_niches": len(payload.niches),
        "n_combinations": len(items),
    })
    await db.reset_automation_queue()
    async with db.transaction() as conn:
        await conn.execute("DELETE FROM automation_queue")
    await db.bulk_insert_automation_queue(items)
    await db.update_automation_state({
        "current_position": 0, "today_count": 0, "queue_total": len(items),
        "queue_completed": 0, "import_id": import_id,
        "status": "SCHEDULED" if cfg["automation_enabled"] else "IDLE",
        "last_niche": None, "last_location": None, "last_query": None,
    })
    await db.append_automation_log("INFO", f"Imported {len(payload.locations)} locations × {len(payload.niches)} niches → {len(items)} searches.")
    return await _status_body()


@router.get("/status")
async def get_status():
    return await _status_body()


@router.get("/queue")
async def get_queue_items(status: Optional[str] = None, offset: int = 0, limit: int = 100):
    items = await db.get_automation_queue(status=status, offset=offset, limit=min(limit, 500))
    return {"items": items, "counts_by_status": await db.count_automation_queue_by_status()}


@router.get("/log")
async def get_log(limit: int = 100):
    return {"lines": await db.get_automation_log(limit=min(limit, 500))}


@router.put("/settings")
async def update_settings(payload: SettingsUpdate):
    data = payload.model_dump(exclude_none=True)
    if "automation_start_time" in data:
        try:
            h, m = data["automation_start_time"].split(":")
            int(h); int(m)
        except (ValueError, AttributeError):
            raise HTTPException(status_code=422, detail="start_time must be HH:MM")
    if "automation_timezone" in data:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(data["automation_timezone"])
        except Exception:
            raise HTTPException(status_code=422, detail="Unknown timezone")
    for k, v in data.items():
        await db.set_setting(k, "true" if v is True else "false" if v is False else str(v))
    return await _status_body()


@router.post("/start")
async def start_now(request: Request):
    state = await db.get_automation_state()
    if int(state.get("queue_total") or 0) == 0:
        raise HTTPException(status_code=503, detail="No search queue imported yet.")
    cfg = await get_automation_settings()
    if int(state.get("today_count") or 0) >= int(cfg["automation_daily_limit"]):
        raise HTTPException(status_code=409, detail="Daily lead limit already reached — try again tomorrow.")
    if not await kick_slice_now(get_queue()):
        raise HTTPException(status_code=503, detail="Job queue unavailable.")
    return await _status_body()


@router.post("/pause")
async def pause():
    state = await db.get_automation_state()
    if state["status"] not in _ACTIVE:
        raise HTTPException(status_code=409, detail=f"Automation is {state['status']}, cannot pause.")
    await db.update_automation_state({"status": "PAUSED"})
    await db.append_automation_log("INFO", "Paused by user.")
    return await db.get_automation_state()


@router.post("/resume")
async def resume():
    await db.update_automation_state({"status": "SCHEDULED"})
    await kick_slice_now(get_queue())
    await db.append_automation_log("INFO", "Resumed by user.")
    return await db.get_automation_state()


@router.post("/stop")
async def stop():
    await db.update_automation_state({"status": "STOPPED"})
    await db.append_automation_log("INFO", "Stopped by user.")
    return await db.get_automation_state()


@router.post("/reset")
async def reset(confirm: bool = False):
    if not confirm:
        raise HTTPException(status_code=400, detail="Pass ?confirm=true — this resets the search position.")
    await db.reset_automation_queue()
    await db.update_automation_state({
        "current_position": 0, "today_count": 0, "queue_completed": 0,
        "status": "IDLE", "last_niche": None, "last_location": None, "last_query": None,
    })
    await db.append_automation_log("WARN", "Progress reset by user (total leads preserved).")
    return await _status_body()
```

Note: confirm `db.set_setting` is the real setting-writer name (grep `def set_setting` in `database.py`); if it's `upsert_setting` or similar, use that.

- [ ] **Step 4: Register the router (needed for the test client)**

In `backend/main.py`, with the other router imports (~line 31):
```python
from .routers import automation as automation_router
```
And with the other `include_router` calls (~line 113):
```python
app.include_router(automation_router.router, dependencies=_authed)
```

- [ ] **Step 5: Run tests**

Run: `venv/Scripts/python.exe -m pytest tests/automation/test_automation_router.py -q`
Expected: PASS (8 tests)

- [ ] **Step 6: Full suite**

Run: `venv/Scripts/python.exe -m pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/routers/automation.py backend/main.py tests/automation/test_automation_router.py
git commit -m "feat(automation): REST API — import/preview/confirm, status, queue, log, controls"
```

---

## Task 9: Scheduler + startup wiring

**Files:**
- Modify: `backend/scheduler.py` (`start_scheduler` ~line 811), `backend/main.py` (`_reconcile_interrupted_jobs` ~line 60)
- Test: `tests/automation/test_startup_wiring.py`

**Interfaces:**
- Consumes: `automation_tick`, `resume_running_slice` (Task 7)
- Produces: an APScheduler job `id="automation_tick"` on `IntervalTrigger(minutes=2)`; a `resume_running_slice(queue)` call inside the existing startup reconcile task.

- [ ] **Step 1: Write the failing test**

```python
# tests/automation/test_startup_wiring.py
import pytest

pytestmark = pytest.mark.asyncio


async def test_automation_tick_job_registered():
    from backend import scheduler
    sch = scheduler.start_scheduler(9)
    try:
        assert sch.get_job("automation_tick") is not None
    finally:
        scheduler.stop_scheduler()


async def test_reconcile_calls_resume(clean_db, monkeypatch):
    """The startup reconcile task must invoke resume_running_slice."""
    called = {"n": 0}
    from backend.automation import scheduler_hooks

    async def fake_resume(queue):
        called["n"] += 1
        return 0
    monkeypatch.setattr(scheduler_hooks, "resume_running_slice", fake_resume)

    # import the reconcile coroutine factory from main and run it directly
    import backend.main as main_mod
    # _reconcile_interrupted_jobs is defined inside lifespan; test via the
    # public helper added in Step 3 instead:
    await main_mod._reconcile_automation(object())
    assert called["n"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/Scripts/python.exe -m pytest tests/automation/test_startup_wiring.py -q`
Expected: FAIL — no `automation_tick` job / no `_reconcile_automation`

- [ ] **Step 3: Wire the scheduler**

In `backend/scheduler.py`, add near the top with the other imports:
```python
from .automation.scheduler_hooks import automation_tick
```
In `start_scheduler`, after the `followup_sequence` job registration:
```python
    _scheduler.add_job(
        automation_tick,
        IntervalTrigger(minutes=2),
        id                 = "automation_tick",
        replace_existing   = True,
        misfire_grace_time = 300,
    )
```

- [ ] **Step 4: Wire the startup reconciler**

In `backend/main.py`, extract a module-level helper so it's testable, and call it from the existing `_reconcile_interrupted_jobs`:

```python
async def _reconcile_automation(queue) -> None:
    from .automation.scheduler_hooks import resume_running_slice
    try:
        await resume_running_slice(queue)
    except Exception as exc:
        logger.warning("Startup automation reconcile failed: %s", exc)
```

Inside `_reconcile_interrupted_jobs` (the existing startup task), add a line:
```python
        await _reconcile_automation(queue)
```

- [ ] **Step 5: Run tests + full suite**

Run: `venv/Scripts/python.exe -m pytest tests/automation/ -q && venv/Scripts/python.exe -m pytest -q`
Expected: PASS (all automation tests + full 465+new green)

- [ ] **Step 6: Commit**

```bash
git add backend/scheduler.py backend/main.py tests/automation/test_startup_wiring.py
git commit -m "feat(automation): register 2-min tick job + startup reconcile"
```

---

## Task 10: Frontend API client + source badge

**Files:**
- Modify: `frontend/src/api/client.js` (after `researchAgentApi`), `frontend/src/lib/badges.js` (`SOURCE_BADGE` + `SOURCE_LABEL`)

**Interfaces:**
- Produces: `automationApi` with `previewImport`, `confirmImport`, `status`, `queue`, `log`, `saveSettings`, `start`, `pause`, `resume`, `stop`, `reset`.

- [ ] **Step 1: Add `automationApi` to `client.js`**

```javascript
// Lead Search Automation — bulk niche×location scheduled discovery
export const automationApi = {
  previewImport: (formData) =>
    api.post('/automation/import/preview', formData, { headers: { 'Content-Type': 'multipart/form-data' } }).then((r) => r.data),
  confirmImport: (payload) => api.post('/automation/import/confirm', payload).then((r) => r.data),
  status:        ()        => api.get('/automation/status').then((r) => r.data),
  queue:         (params)  => api.get('/automation/queue', { params }).then((r) => r.data),
  log:           (limit)   => api.get('/automation/log', { params: { limit } }).then((r) => r.data),
  saveSettings:  (payload) => api.put('/automation/settings', payload).then((r) => r.data),
  start:         ()        => api.post('/automation/start').then((r) => r.data),
  pause:         ()        => api.post('/automation/pause').then((r) => r.data),
  resume:        ()        => api.post('/automation/resume').then((r) => r.data),
  stop:          ()        => api.post('/automation/stop').then((r) => r.data),
  reset:         ()        => api.post('/automation/reset', null, { params: { confirm: true } }).then((r) => r.data),
}
```

- [ ] **Step 2: Add the `AUTOMATION` badge to `badges.js`**

In `SOURCE_BADGE`: `AUTOMATION: 'badge bg-cyan-500/20 text-cyan-400 border border-cyan-500/30',`
In `SOURCE_LABEL`: `AUTOMATION: '🤖 Auto',`

- [ ] **Step 3: Build check**

Run: `cd frontend && npm run build`
Expected: builds clean (no new components imported yet, so this only checks syntax).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/client.js frontend/src/lib/badges.js
git commit -m "feat(automation): frontend API client + AUTOMATION source badge"
```

---

## Task 11: Upload + import preview component

**Files:**
- Create: `frontend/src/components/automation/AutomationUpload.jsx`

**Interfaces:**
- Consumes: `automationApi.previewImport`, `automationApi.confirmImport`
- Produces: `<AutomationUpload onImported={(status) => void} />` — renders a file dropzone; on file → preview modal; on Confirm → calls `confirmImport` and fires `onImported`.

- [ ] **Step 1: Implement the component**

```jsx
import { useRef, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Upload, FileSpreadsheet, X, AlertTriangle, Check } from 'lucide-react'
import toast from 'react-hot-toast'
import { automationApi } from '../../api/client'

export default function AutomationUpload({ onImported }) {
  const fileRef = useRef(null)
  const [preview, setPreview] = useState(null)

  const previewMut = useMutation({
    mutationFn: (files) => {
      const fd = new FormData()
      fd.append('file', files[0])
      if (files[1]) fd.append('file2', files[1])
      return automationApi.previewImport(fd)
    },
    onSuccess: setPreview,
    onError: (err) => toast.error(err?.response?.data?.detail || 'Could not read that file'),
  })

  const confirmMut = useMutation({
    mutationFn: () => automationApi.confirmImport({
      locations: preview.locations, niches: preview.niches,
      filename: preview.filename || 'upload', layout: preview.layout,
    }),
    onSuccess: (status) => { setPreview(null); toast.success('Queue created'); onImported?.(status) },
    onError: (err) => toast.error(err?.response?.data?.detail || 'Import failed'),
  })

  function onPick(e) {
    const files = [...e.target.files]
    if (files.length) previewMut.mutate(files)
    e.target.value = ''
  }

  return (
    <div className="rounded-xl border border-dashed border-slate-700 bg-slate-900/30 p-8 text-center">
      <FileSpreadsheet size={28} className="mx-auto text-brand-400" />
      <p className="mt-2 text-sm font-medium text-slate-200">Upload a locations & niches file</p>
      <p className="mt-1 text-xs text-slate-500">
        CSV or XLSX. Columns: <code>City/Town</code>, <code>State</code>, <code>Niche</code> — or
        separate location and niche sheets. (.xls is not supported.)
      </p>
      <button onClick={() => fileRef.current?.click()} disabled={previewMut.isPending}
        className="btn-primary mt-4 inline-flex items-center gap-2 text-sm">
        <Upload size={14} /> {previewMut.isPending ? 'Reading…' : 'Choose file(s)'}
      </button>
      <input ref={fileRef} type="file" accept=".csv,.xlsx" multiple hidden onChange={onPick} />

      {preview && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => setPreview(null)}>
          <div className="card w-full max-w-lg p-5 space-y-4 text-left" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold text-slate-100">Import preview</h3>
              <button onClick={() => setPreview(null)} className="p-1 text-slate-400 hover:bg-slate-800 rounded"><X size={15} /></button>
            </div>
            <ul className="text-xs text-slate-300 space-y-1">
              <li><Check size={11} className="inline text-emerald-400" /> {preview.n_locations} locations</li>
              <li><Check size={11} className="inline text-emerald-400" /> {preview.n_niches} niches</li>
              <li><Check size={11} className="inline text-emerald-400" /> {preview.combinations.toLocaleString()} search combinations</li>
              <li className="text-slate-500">Estimated ≥ {preview.estimated_days} day(s) — {preview.estimate_note}</li>
            </ul>
            {preview.warnings?.length > 0 && (
              <div className="text-xs text-amber-400 flex items-start gap-1.5">
                <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                <span>{preview.warnings.join(' ')}</span>
              </div>
            )}
            <div className="grid grid-cols-2 gap-3 text-xs">
              <div>
                <p className="font-semibold text-slate-400 mb-1">Locations</p>
                <div className="max-h-40 overflow-y-auto text-slate-400 space-y-0.5">
                  {preview.locations.slice(0, 100).map((l, i) => <div key={i}>{[l.city, l.state].filter(Boolean).join(', ')}</div>)}
                </div>
              </div>
              <div>
                <p className="font-semibold text-slate-400 mb-1">Niches</p>
                <div className="max-h-40 overflow-y-auto text-slate-400 space-y-0.5">
                  {preview.niches.map((n, i) => <div key={i}>{n}</div>)}
                </div>
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={() => setPreview(null)} className="btn-secondary text-xs">Cancel</button>
              <button onClick={() => confirmMut.mutate()} disabled={confirmMut.isPending} className="btn-primary text-xs">
                {confirmMut.isPending ? 'Importing…' : 'Confirm Import'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Build check**

Run: `cd frontend && npm run build`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/automation/AutomationUpload.jsx
git commit -m "feat(automation): file upload + import preview modal"
```

---

## Task 12: Automation dashboard + controls

**Files:**
- Create: `frontend/src/components/automation/AutomationDashboard.jsx`

**Interfaces:**
- Consumes: `automationApi.{status,start,pause,resume,stop,reset}`; the `/status` body shape from Task 8.
- Produces: `<AutomationDashboard />` — polls `status` (3s while RUNNING/SCHEDULED, else 15s), renders status pill + progress bar + stat tiles + the 5 control buttons (Reset behind a confirm dialog).

- [ ] **Step 1: Implement the component**

```jsx
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Play, Pause, Square, RotateCcw, Loader2 } from 'lucide-react'
import toast from 'react-hot-toast'
import { automationApi } from '../../api/client'

const PILL = {
  RUNNING:        ['🟢', 'Running',            'text-emerald-400'],
  SCHEDULED:      ['🟡', 'Scheduled',          'text-amber-400'],
  PAUSED:         ['⏸', 'Paused',              'text-slate-300'],
  LIMIT_REACHED:  ['✅', 'Daily limit reached', 'text-emerald-400'],
  STOPPED:        ['🔴', 'Stopped',            'text-red-400'],
  COMPLETED:      ['✔', 'Queue complete',      'text-emerald-400'],
  IDLE:           ['⚪', 'Idle',                'text-slate-400'],
}
const ACTIVE = new Set(['RUNNING', 'SCHEDULED'])

function Tile({ label, value }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-widest text-slate-500">{label}</p>
      <p className="text-sm font-medium text-slate-200 truncate">{value ?? '—'}</p>
    </div>
  )
}

function fmtDuration(sec) {
  if (sec == null) return '—'
  const s = Math.max(0, Math.floor(sec))
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

export default function AutomationDashboard() {
  const qc = useQueryClient()
  const [confirmReset, setConfirmReset] = useState(false)

  const { data: s } = useQuery({
    queryKey: ['automation-status'],
    queryFn: automationApi.status,
    refetchInterval: (q) => (q.state.data && ACTIVE.has(q.state.data.status) ? 3000 : 15000),
  })

  const act = (fn, msg) => useMutation({
    mutationFn: fn,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['automation-status'] }); qc.invalidateQueries({ queryKey: ['automation-queue'] }); msg && toast.success(msg) },
    onError: (e) => toast.error(e?.response?.data?.detail || 'Action failed'),
  })
  const startM = act(automationApi.start, 'Started')
  const pauseM = act(automationApi.pause)
  const resumeM = act(automationApi.resume)
  const stopM = act(automationApi.stop)
  const resetM = act(automationApi.reset, 'Progress reset')

  if (!s) return <div className="rounded-xl border border-slate-800 bg-slate-900/30 p-5 text-sm text-slate-500">Loading automation…</div>

  const [emoji, label, cls] = PILL[s.status] || PILL.IDLE
  const limit = s.settings?.automation_daily_limit || 0
  const pct = s.progress_pct ?? 0
  const timeLeftSec = s.duration_deadline ? (new Date(s.duration_deadline + 'Z') - Date.now()) / 1000 : null

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/30 p-5 space-y-4">
      <div className="flex items-center justify-between">
        <span className={`text-sm font-semibold ${cls}`}>{emoji} {label}</span>
        <span className="text-xs text-slate-500">Next run: {s.next_run_at ? new Date(s.next_run_at + 'Z').toLocaleString() : `tomorrow ${s.settings?.automation_start_time}`}</span>
      </div>

      <div>
        <div className="flex justify-between text-xs text-slate-400 mb-1">
          <span>Today's leads</span><span>{s.today_count} / {limit}</span>
        </div>
        <div className="h-2 rounded-full bg-slate-800 overflow-hidden">
          <div className="h-full bg-brand-500 transition-all" style={{ width: `${Math.min(100, pct)}%` }} />
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Tile label="Total leads (all time)" value={s.total_count} />
        <Tile label="Current niche" value={s.current_niche} />
        <Tile label="Current city" value={s.current_location} />
        <Tile label="Searches done / left" value={`${s.searches_completed} / ${s.searches_remaining}`} />
        <Tile label="Duration" value={`${s.settings?.automation_duration_hours || 0}h`} />
        <Tile label="Time left today" value={fmtDuration(timeLeftSec)} />
        <Tile label="Last success" value={s.last_success_at ? new Date(s.last_success_at + 'Z').toLocaleTimeString() : '—'} />
        <Tile label="Timezone" value={s.settings?.automation_timezone} />
      </div>

      <div className="flex flex-wrap gap-2 pt-1">
        <button onClick={() => startM.mutate()} disabled={startM.isPending || s.status === 'RUNNING'} className="btn-primary text-xs flex items-center gap-1.5"><Play size={13} /> Start Now</button>
        <button onClick={() => pauseM.mutate()} disabled={!ACTIVE.has(s.status)} className="btn-secondary text-xs flex items-center gap-1.5"><Pause size={13} /> Pause</button>
        <button onClick={() => resumeM.mutate()} disabled={s.status !== 'PAUSED'} className="btn-secondary text-xs flex items-center gap-1.5"><Play size={13} /> Resume</button>
        <button onClick={() => stopM.mutate()} disabled={s.status === 'STOPPED'} className="btn-secondary text-xs flex items-center gap-1.5"><Square size={13} /> Stop</button>
        <button onClick={() => setConfirmReset(true)} className="btn-secondary text-xs flex items-center gap-1.5 ml-auto"><RotateCcw size={13} /> Reset Progress</button>
      </div>

      {confirmReset && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => setConfirmReset(false)}>
          <div className="card w-full max-w-sm p-5 space-y-3" onClick={(e) => e.stopPropagation()}>
            <p className="text-sm text-slate-200">Reset the search position to the start? Collected leads and the all-time total are kept; every queue item goes back to Pending.</p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirmReset(false)} className="btn-secondary text-xs">Cancel</button>
              <button onClick={() => { resetM.mutate(); setConfirmReset(false) }} className="btn-danger text-xs">Reset</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Build check** — `cd frontend && npm run build` → clean.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/automation/AutomationDashboard.jsx
git commit -m "feat(automation): status dashboard + pause/resume/stop/reset controls"
```

---

## Task 13: Queue table + activity log

**Files:**
- Create: `frontend/src/components/automation/AutomationQueueTable.jsx`, `frontend/src/components/automation/AutomationLog.jsx`

**Interfaces:**
- Consumes: `automationApi.queue`, `automationApi.log`
- Produces: `<AutomationQueueTable />`, `<AutomationLog />` — both self-fetching, poll every 5s.

- [ ] **Step 1: Implement `AutomationQueueTable.jsx`**

```jsx
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'

const STATUS_CLS = {
  PENDING:   'text-slate-500',
  SEARCHING: 'text-amber-400',
  COMPLETED: 'text-emerald-400',
  PARTIAL:   'text-amber-400',
  FAILED:    'text-red-400',
  SKIPPED:   'text-slate-600',
}
const FILTERS = ['ALL', 'PENDING', 'SEARCHING', 'COMPLETED', 'PARTIAL', 'FAILED']

export default function AutomationQueueTable() {
  const [filter, setFilter] = useState('ALL')
  const { data } = useQuery({
    queryKey: ['automation-queue', filter],
    queryFn: () => automationApi.queue({ status: filter === 'ALL' ? undefined : filter, limit: 500 }),
    refetchInterval: 5000,
  })
  const items = data?.items || []
  const counts = data?.counts_by_status || {}

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/30 overflow-hidden">
      <div className="flex flex-wrap gap-1.5 p-3 border-b border-slate-800">
        {FILTERS.map((f) => (
          <button key={f} onClick={() => setFilter(f)}
            className={`text-[10px] font-bold uppercase px-2 py-1 rounded ${filter === f ? 'bg-brand-500/20 text-brand-300' : 'text-slate-500 hover:text-slate-300'}`}>
            {f}{f !== 'ALL' && counts[f] ? ` ${counts[f]}` : ''}
          </button>
        ))}
      </div>
      <div className="max-h-[420px] overflow-y-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-slate-900">
            <tr className="text-left text-[10px] font-bold uppercase tracking-widest text-slate-500 border-b border-slate-800">
              <th className="px-4 py-2">#</th><th className="px-4 py-2">Niche</th>
              <th className="px-4 py-2">City</th><th className="px-4 py-2">State</th>
              <th className="px-4 py-2">Status</th><th className="px-4 py-2">New</th>
            </tr>
          </thead>
          <tbody>
            {items.map((it) => (
              <tr key={it.id} className="border-b border-slate-800/50 last:border-0">
                <td className="px-4 py-2 text-slate-500">{it.position + 1}</td>
                <td className="px-4 py-2 text-slate-200">{it.niche}</td>
                <td className="px-4 py-2 text-slate-400">{it.city}</td>
                <td className="px-4 py-2 text-slate-400">{it.state || '—'}</td>
                <td className={`px-4 py-2 text-[11px] font-semibold ${STATUS_CLS[it.status] || ''}`}>{it.status}</td>
                <td className="px-4 py-2 text-slate-400">{it.new_leads || 0}</td>
              </tr>
            ))}
            {items.length === 0 && <tr><td colSpan={6} className="px-4 py-6 text-center text-xs text-slate-600">No items</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
import { automationApi } from '../../api/client'
```

(Move the `import { automationApi }` line to the top with the other imports — shown last only for clarity in this plan.)

- [ ] **Step 2: Implement `AutomationLog.jsx`**

```jsx
import { useQuery } from '@tanstack/react-query'
import { automationApi } from '../../api/client'

const LEVEL_CLS = { ERROR: 'text-red-400', WARN: 'text-amber-400', INFO: 'text-slate-400' }

export default function AutomationLog() {
  const { data } = useQuery({
    queryKey: ['automation-log'],
    queryFn: () => automationApi.log(100),
    refetchInterval: 5000,
  })
  const lines = data?.lines || []
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950 p-3 max-h-64 overflow-y-auto font-mono text-[11px] leading-relaxed">
      {lines.length === 0 && <p className="text-slate-600">No activity yet.</p>}
      {lines.map((l) => (
        <div key={l.id} className={LEVEL_CLS[l.level] || 'text-slate-400'}>
          <span className="text-slate-600">{new Date(l.ts + 'Z').toLocaleTimeString()} </span>
          {l.message}
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 3: Build check** — `cd frontend && npm run build` → clean.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/automation/AutomationQueueTable.jsx frontend/src/components/automation/AutomationLog.jsx
git commit -m "feat(automation): search queue table + activity log"
```

---

## Task 14: Automation settings panel

**Files:**
- Create: `frontend/src/components/automation/AutomationSettings.jsx`

**Interfaces:**
- Consumes: `automationApi.{status,saveSettings}`
- Produces: `<AutomationSettings />` — form for the 7 settings; Save → `PUT /automation/settings`.

- [ ] **Step 1: Implement the component**

```jsx
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { automationApi } from '../../api/client'

const ZONES = [
  'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
  'America/Phoenix', 'America/Anchorage', 'Pacific/Honolulu', 'America/Toronto',
  'America/Sao_Paulo', 'Europe/London', 'Europe/Paris', 'Europe/Berlin',
  'Europe/Madrid', 'Europe/Rome', 'Europe/Amsterdam', 'Europe/Moscow',
  'Africa/Johannesburg', 'Asia/Dubai', 'Asia/Karachi', 'Asia/Kolkata',
  'Asia/Dhaka', 'Asia/Bangkok', 'Asia/Singapore', 'Asia/Hong_Kong',
  'Asia/Shanghai', 'Asia/Tokyo', 'Australia/Sydney', 'Pacific/Auckland', 'UTC',
]

export default function AutomationSettings() {
  const qc = useQueryClient()
  const { data: s } = useQuery({ queryKey: ['automation-status'], queryFn: automationApi.status })
  const [form, setForm] = useState(null)

  useEffect(() => {
    if (s?.settings && !form) setForm({ ...s.settings })
  }, [s, form])

  const saveM = useMutation({
    mutationFn: () => automationApi.saveSettings({
      automation_enabled: !!form.automation_enabled,
      automation_daily_limit: Number(form.automation_daily_limit) || 500,
      automation_start_time: form.automation_start_time || '07:00',
      automation_timezone: form.automation_timezone,
      automation_duration_hours: Number(form.automation_duration_hours) || 0,
      automation_per_item_target: Number(form.automation_per_item_target) || 100,
      automation_max_retries: Number(form.automation_max_retries) || 0,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['automation-status'] }); toast.success('Settings saved') },
    onError: (e) => toast.error(e?.response?.data?.detail || 'Save failed'),
  })

  if (!form) return null
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }))

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/30 p-5 space-y-4">
      <h3 className="text-sm font-bold text-slate-100">Automation Settings</h3>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
        <label className="block">
          <span className="text-xs text-slate-400">Daily Lead Limit</span>
          <input type="number" min={1} value={form.automation_daily_limit} onChange={set('automation_daily_limit')}
            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-slate-200 focus:border-brand-500 focus:outline-none" />
        </label>
        <label className="block">
          <span className="text-xs text-slate-400">Daily Start Time</span>
          <input type="time" value={form.automation_start_time} onChange={set('automation_start_time')}
            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-slate-200 focus:border-brand-500 focus:outline-none" />
        </label>
        <label className="block">
          <span className="text-xs text-slate-400">Search Duration (hours, 0 = no limit)</span>
          <input type="number" min={0} value={form.automation_duration_hours} onChange={set('automation_duration_hours')}
            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-slate-200 focus:border-brand-500 focus:outline-none" />
        </label>
        <label className="block">
          <span className="text-xs text-slate-400">Timezone</span>
          <select value={form.automation_timezone} onChange={set('automation_timezone')}
            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-slate-200 focus:border-brand-500 focus:outline-none">
            {ZONES.includes(form.automation_timezone) ? null : <option value={form.automation_timezone}>{form.automation_timezone}</option>}
            {ZONES.map((z) => <option key={z} value={z}>{z}</option>)}
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-slate-400">Leads per search (bound)</span>
          <input type="number" min={1} max={100} value={form.automation_per_item_target} onChange={set('automation_per_item_target')}
            className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-slate-200 focus:border-brand-500 focus:outline-none" />
        </label>
        <label className="flex items-center gap-2 mt-5">
          <input type="checkbox" checked={!!form.automation_enabled} onChange={set('automation_enabled')} />
          <span className="text-xs text-slate-300">Enable Daily Automation</span>
        </label>
      </div>
      <button onClick={() => saveM.mutate()} disabled={saveM.isPending} className="btn-primary text-xs">
        {saveM.isPending ? 'Saving…' : 'Save Settings'}
      </button>
    </div>
  )
}
```

- [ ] **Step 2: Build check** — `cd frontend && npm run build` → clean.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/automation/AutomationSettings.jsx
git commit -m "feat(automation): settings panel (limit / start time / timezone / duration / enable)"
```

---

## Task 15: Mount in Lead Search page + live smoke test

**Files:**
- Modify: `frontend/src/pages/LeadSearch.jsx` (add the automation section above the manual form)

**Interfaces:**
- Consumes: all Task 11–14 components; `automationApi.status`

- [ ] **Step 1: Add the automation section to `LeadSearch.jsx`**

At the top of the file, add imports:
```jsx
import { useQuery } from '@tanstack/react-query'   // already imported — keep one
import AutomationUpload from '../components/automation/AutomationUpload'
import AutomationDashboard from '../components/automation/AutomationDashboard'
import AutomationQueueTable from '../components/automation/AutomationQueueTable'
import AutomationLog from '../components/automation/AutomationLog'
import AutomationSettings from '../components/automation/AutomationSettings'
import { automationApi } from '../api/client'
```

Add a small section component inside the file (above `export default function LeadSearch`):
```jsx
function AutomationSection() {
  const [open, setOpen] = useState(true)
  const { data: status } = useQuery({
    queryKey: ['automation-status'],
    queryFn: automationApi.status,
    refetchInterval: (q) => (['RUNNING', 'SCHEDULED'].includes(q.state.data?.status) ? 3000 : 15000),
  })
  const hasQueue = (status?.queue_total || 0) > 0

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/20">
      <button onClick={() => setOpen((o) => !o)} className="w-full flex items-center justify-between px-5 py-3 text-sm font-semibold text-slate-200">
        <span>⚙️ Lead Search Automation {hasQueue ? '' : '(not set up)'}</span>
        <span className="text-slate-500">{open ? '−' : '+'}</span>
      </button>
      {open && (
        <div className="p-5 pt-0 space-y-4">
          {!hasQueue && <AutomationUpload onImported={() => window.location.reload()} />}
          {hasQueue && (
            <>
              <AutomationDashboard />
              <AutomationSettings />
              <details className="rounded-xl border border-slate-800 bg-slate-900/30">
                <summary className="px-4 py-2 text-xs font-semibold text-slate-400 cursor-pointer">Search queue</summary>
                <div className="p-3 pt-0"><AutomationQueueTable /></div>
              </details>
              <details className="rounded-xl border border-slate-800 bg-slate-900/30">
                <summary className="px-4 py-2 text-xs font-semibold text-slate-400 cursor-pointer">Activity log</summary>
                <div className="p-3 pt-0"><AutomationLog /></div>
              </details>
              <div className="text-right">
                <AutomationUpload onImported={() => window.location.reload()} />
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
```

Render `<AutomationSection />` at the top of the returned JSX in `LeadSearch`, right after the `<h1>Lead Search</h1>` header block and before the manual `<form>`.

- [ ] **Step 2: Build check**

Run: `cd frontend && npm run build`
Expected: clean.

- [ ] **Step 3: Full backend regression**

Run: `venv/Scripts/python.exe -m pytest -q`
Expected: PASS (465 + all new automation tests).

- [ ] **Step 4: Live smoke test**

Start both servers (`venv/Scripts/python.exe run_server_8001.py`, `cd frontend && npm run dev`). Then:
1. Open Lead Search → the "Lead Search Automation (not set up)" panel shows the dropzone.
2. Upload a small CSV (3 niches × 3 cities) → preview modal shows `9 combinations` → Confirm.
3. Panel now shows the dashboard (status IDLE, 0/500), the queue table (9 PENDING rows, niche-outer order), the settings panel.
4. In settings: set Daily Lead Limit `10`, tick **Enable Daily Automation**, Save.
5. Click **Start Now** → status → RUNNING, log lines appear, queue rows flip PENDING → SEARCHING → COMPLETED, `today_count` climbs.
6. Navigate to Dashboard and back → the panel reconnects and still shows live progress (state is server-side).
7. When `today_count ≥ 10` → status → ✅ Daily limit reached, run stops, `current_position` sits mid-queue.
8. Check `/api/leads` (or the Leads page) → new leads with `source` badge `🤖 Auto`; re-run a Start Now and confirm overlapping combinations don't create duplicates (`total_count` grows less than `leads_found` sum).
9. **Pause** mid-run → stops at the next item; **Resume** → continues from the same position.
10. Restart the backend while RUNNING → on boot it re-enqueues and continues from `current_position` (check the log for "Resumed an interrupted automation run").

Record the actual observed results (statuses, counts, dedup behavior, restart recovery) in the completion report — do not claim it works without running these.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/LeadSearch.jsx
git commit -m "feat(automation): mount automation panel on the Lead Search page"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task(s) |
|---|---|
| 1 Upload CSV/Excel; combined + separate sheets; auto-map; preview | 3 (parse), 8 (`/import/preview`), 11 (UI) |
| 2 Sequential niche-outer order; remember position | 4 (builder), 6 (runner loop + checkpoint) |
| 3 Daily lead limit, configurable, final-item overshoot | 2 (setting), 6 (`today_count >= limit`) |
| 4 Automatic daily schedule, configurable time + timezone | 2, 7 (`automation_tick`), 9 (registration) |
| 5 Search duration window | 2 (setting), 6 (`duration_deadline`), 7 (deadline set on tick) |
| 6 Persistent progress across restart | 1 (`automation_state`, atomic checkpoint), 6, 7 (`resume_running_slice`), 9 |
| 7 Dashboard / progress UI | 12 |
| 8 Search queue view | 13 |
| 9 Pause / Resume / Stop / Reset | 8 (endpoints), 12 (buttons + confirm) |
| 10 Start Now (limit still applies) | 8 (`/start` 409 on limit), 12 |
| 11 Daily reset (today=0, total + position preserved) | 7 (`_daily_reset_and_deadline`), test in 7 |
| 12 Duplicate prevention | 5 (`merge_and_save` → `create_or_merge_lead`) |
| 13 Lead data; no fabrication | 5 (passes through scraper fields only), Global Constraints |
| 14 `LeadSearchService` seam | 5 |
| 15 Background automation, browser-independent | 6 (JobQueue handler), 7, 12 (poll only) |
| 16 Job safety: retries, atomic progress, restart recovery | 1 (transaction), 6 (retry loop, never-raises), 7, 9 |
| 17 Settings UI | 14 |
| 18 Upload validation + estimate | 3 (raises), 8 (`estimated_days` + note), 11 |
| 19 Import preview → Confirm | 8, 11 |
| 20 Multiple uploads | Phase 1: re-upload = replace (Task 8 `/import/confirm` wipes + rebuilds); the New/Add/Replace choice is Phase 2 (spec §11) |
| 21 Campaign system | Phase 2 (spec §11) — Phase 1 is a single implicit campaign |
| 22 Backend scheduling only | 7, 9; Global Constraints |
| 23 DB tables + indexes | 1 |
| 24 Activity log | 1 (`automation_log` + prune), 6 (`_log` calls), 13 (UI) |
| 25 Inspect-first; reuse; don't break | Done in brainstorm; Global Constraints; every task runs the full suite |

**Placeholder scan:** none — every code step is complete runnable code.

**Type consistency:** `SearchResult` fields (`new_leads`, `total_found`, `lead_ids`, `sources_used`, `error`) are used identically in Tasks 5 and 6. `checkpoint_automation_progress(item_id, item_data, state_data)` signature matches between Task 1 definition and Task 6 call. `automationApi` method names match between Task 10 and Tasks 11–15. `automation_tick` / `resume_running_slice` / `kick_slice_now` names match between Tasks 7, 8, 9.

**Known follow-ups (not blockers):**
- Verify `db.set_setting` is the real writer name (Task 2/8) — grep before implementing.
- `merge_and_save` return key is assumed `new_count` / `unique_saved_ids` (matches `backend/discovery/merge_dedup.py` from the prior session) — confirm in Task 5.
- The `AutomationQueueTable` uses a plain scroll container, not `@tanstack/react-virtual`; a 5,000-row queue renders 500 max (server `limit` cap) so virtualization isn't required for Phase 1. If a user imports a very large file and filters to ALL, add virtualization then.
