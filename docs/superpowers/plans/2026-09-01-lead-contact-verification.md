# Lead Contact Verification (Checkpoint 5C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the disabled "Verify Selected" control on Lead Search to a deterministic, DNS-backed contact-verification workflow that classifies each selected lead as `VALID` / `RISKY` / `INVALID` / `UNVERIFIED` and surfaces the result as a badge + detail drawer — with zero outbound-communication capability.

**Architecture:** A new `backend/verification/` package behind a `contact_verification_enabled` feature flag (default OFF). "Verify Selected" creates a `verification_runs` row and enqueues a `CONTACT_VERIFICATION` job on the existing `JobQueue`; the job runs deterministic email checks (syntax → role → disposable → MX → A-fallback via `dnspython`) and phone checks (`phonenumbers`) per lead, writing one row per lead to `lead_contact_verification` (UPSERT, `UNIQUE(lead_id)`). The frontend polls run status (mirrors Quick Search), then renders badges. No `leads` row is ever mutated; no sender/scheduler/n8n is ever reachable.

**Tech Stack:** FastAPI + `aiosqlite`, `dnspython` (async resolver), `phonenumbers` (offline), React 18 + Vite + React Query, `pytest`/`pytest-asyncio`.

**Design spec:** `docs/superpowers/specs/2026-09-01-lead-contact-verification-design.md` — read it before starting; this plan implements it section-by-section.

## Global Constraints

- **SQLite only**, single file; schema source of truth is `backend/database.py::_SCHEMA_SQL`. New tables are additive `CREATE TABLE IF NOT EXISTS` — **no `ALTER`, no change to any existing table**. `PRAGMA foreign_keys=ON` is already set in `get_db()` / `transaction()`.
- **`leads` row is NEVER written by 5C** (no `update_lead`, no `create_lead`, no status write).
- **No outbound path, ever:** verification must never import or call `send_email`, `_send_smtp`, `send_whatsapp`, `_send_whatsapp_desktop`, `whatsapp_sender`, `pyautogui`, `email_campaigns.senders.resolve_transport`, `email_campaigns.n8n_client`, `scheduler` dispatch, `smtplib`, `aiosmtplib`, or campaign start/prepare.
- **No SMTP probing** (no port-25 traffic, no `RCPT TO`). **No paid API, no API key, no LLM.**
- **DNS verification MUST NOT be described as proving a mailbox exists.** Distinguish: syntactically valid / live domain / role-based / disposable / invalid / no MX-or-A / valid phone / invalid phone.
- **WhatsApp hint copy is locked** (design Appendix A): heading exactly `"WhatsApp capability hint — format-based only"`; values `possible` / `unlikely` / `unknown`; explainer `"Inferred from the phone number's line type. This is not a check that the number has WhatsApp."` **Forbidden strings anywhere** (code, UI, tests, comments): "WhatsApp verified", "WhatsApp reachable", "WhatsApp active", "WhatsApp account confirmed", "WhatsApp number confirmed".
- **Lead status is exactly one of** `VALID` / `RISKY` / `INVALID` / `UNVERIFIED` — no other value reaches the DB or API.
- **Feature flag OFF ⇒ zero behavior change** anywhere: router `503` before any work, no run row, no verification row, no job enqueued, `LeadSearch` button disabled, badge columns hidden, `_lead_row_to_campaign_row` unchanged.
- Feature-flag resolution mirrors `email_campaigns`: `config.contact_verification_enabled` **OR** truthy `app_settings['contact_verification_enabled']`.
- Run tests from repo root with the project venv: `venv/Scripts/python.exe -m pytest -q`. Baseline is **698 passing**. Frontend: `cd frontend && npm run build` (no frontend test framework — build + manual browser check is the bar).
- Commit after every task. Do **not** commit the pre-existing uncommitted Checkpoint 3A–4 working-tree changes; stage only the files each task names.
- Reuse existing helpers: `db._now_naive_iso()`, `db.get_leads_by_ids()`, `db.get_db()` / `db.transaction()`, `validators.is_valid_email` / `validators._SPAM_LOCALS`, `rate_limit.limiter`, the `_FakeQueue` test pattern from `tests/discovery/test_discovery_router.py`.

---

## File Structure

**Backend — new package `backend/verification/`**

| File | Responsibility |
|---|---|
| `__init__.py` | package marker; no logic |
| `status_rules.py` | pure functions: `email_status_from(...)`, `roll_up(email_status, phone_status)` |
| `disposable_domains.py` | load `data/disposable_domains.txt` once → `is_disposable(domain) -> bool` |
| `data/disposable_domains.txt` | vendored newline-delimited blocklist (dated header comment) |
| `dns_lookup.py` | `async resolve_domain(domain, cache) -> str` — MX then A/AAAA, timeout, per-run cache |
| `email_checks.py` | `async check_email(email, dns_cache) -> dict` |
| `phone_checks.py` | `check_phone(phone, default_region) -> dict` + `whatsapp_hint_from(line_type, phone_status)` |
| `engine.py` | `async verify_contact(lead, dns_cache) -> dict`; `async run_verification_job(payload) -> None` |
| `service.py` | `is_feature_enabled()`, `start_run(lead_ids)`, `get_run_public(id)`, `get_results(id)`, `get_active_run()`, `request_cancel(id)`, `get_lead_states(ids)` |

**Backend — modified**

| File | Change |
|---|---|
| `backend/database.py` | append 2 `CREATE TABLE` + 3 `CREATE INDEX` to `_SCHEMA_SQL`; add helper fns |
| `backend/config.py` | 4 new settings |
| `backend/requirements.txt` | `phonenumbers>=8.13`, `dnspython>=2.6` |
| `backend/main.py` | import + register `verification` router |
| `backend/routers/verification.py` | **new** — 6 routes + `VerifySelectedRequest` + `require_feature_enabled` |
| `backend/email_campaigns/service.py` | `add_leads_from_db` + `_lead_row_to_campaign_row` — guarded, additive verification snapshot |

**Frontend — new**

| File | Responsibility |
|---|---|
| `frontend/src/lib/verificationBadges.js` | `status -> { label, className, Icon }` |
| `frontend/src/components/lead-search/VerificationBadge.jsx` | presentational badge (+ spinner state) |
| `frontend/src/components/lead-search/VerificationDetailDrawer.jsx` | read-only per-check breakdown |

**Frontend — modified**

| File | Change |
|---|---|
| `frontend/src/api/client.js` | `verificationApi` object |
| `frontend/src/pages/LeadSearch.jsx` | wire "Verify Selected": run state, poll, progress, cancel, results merge, badge column, drawer, flag gate + enable affordance |
| `frontend/src/pages/Leads.jsx` | display-only badge column (flag-gated) + drawer |
| `frontend/src/pages/Settings.jsx` | `contact_verification_enabled` in `DEFAULTS` + a `Toggle` |

**Tests — new (`tests/verification/`)**

`__init__.py`, `test_db.py`, `test_status_rules.py`, `test_disposable_domains.py`, `test_dns_lookup.py`, `test_email_checks.py`, `test_phone_checks.py`, `test_verification_engine.py`, `test_verification_run.py`, `test_verification_never_touches_a_send_path.py`, `test_verification_router.py`, `test_feature_flag_off.py`, `test_5b_handoff_with_verification.py`

---

## Task 1: Dependencies + config settings

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/config.py:88` (after the `queue_workers` block, before `# ── Lead Discovery` — anywhere in the class body is fine; keep it grouped)
- Test: `tests/verification/__init__.py` (create empty), `tests/verification/test_config.py`

**Interfaces:**
- Produces: `config.get_settings().contact_verification_enabled: bool` (default `False`),
  `.verification_dns_timeout_seconds: float` (3.0), `.verification_dns_lifetime_seconds: float` (5.0),
  `.verification_max_leads_per_run: int` (2000).

- [ ] **Step 1: Add dependencies**

In `backend/requirements.txt`, under `# ── Data validation ───`, add:

```
# ── Contact verification (Checkpoint 5C) — deterministic, offline/DNS only ───
phonenumbers>=8.13
dnspython>=2.6
```

- [ ] **Step 2: Install**

Run: `venv/Scripts/python.exe -m pip install "phonenumbers>=8.13" "dnspython>=2.6"`
Expected: both install (dnspython likely already satisfied). Run
`venv/Scripts/python.exe -c "import phonenumbers, dns.asyncresolver; print('ok')"` → `ok`.

- [ ] **Step 3: Write the failing test**

Create `tests/verification/__init__.py` (empty). Create `tests/verification/test_config.py`:

```python
from backend.config import get_settings


def test_contact_verification_settings_exist_with_safe_defaults():
    s = get_settings()
    assert s.contact_verification_enabled is False
    assert s.verification_dns_timeout_seconds == 3.0
    assert s.verification_dns_lifetime_seconds == 5.0
    assert s.verification_max_leads_per_run == 2000
```

- [ ] **Step 4: Run test to verify it fails**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_config.py -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'contact_verification_enabled'`.

- [ ] **Step 5: Add the settings**

In `backend/config.py`, inside `class Settings(BaseSettings):`, add (place after the
`queue_workers: int = 4` block):

```python
    # ── Contact Verification (Checkpoint 5C) ────────────────────────────────
    # Deterministic email + phone verification (syntax / role / disposable /
    # MX+A DNS lookup / phonenumbers). No SMTP probe, no paid API, no LLM.
    # DB override: app_settings 'contact_verification_enabled'. Default OFF.
    contact_verification_enabled:      bool  = False
    verification_dns_timeout_seconds:  float = 3.0     # per DNS query
    verification_dns_lifetime_seconds: float = 5.0     # total incl. retries
    verification_max_leads_per_run:    int   = 2000
```

- [ ] **Step 6: Run test to verify it passes**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_config.py -q`
Expected: PASS.

- [ ] **Step 7: Full suite still green**

Run: `venv/Scripts/python.exe -m pytest -q`
Expected: `699 passed` (698 baseline + this test).

- [ ] **Step 8: Commit**

```bash
git add backend/requirements.txt backend/config.py tests/verification/__init__.py tests/verification/test_config.py
git commit -m "feat(5c): add phonenumbers/dnspython deps + contact-verification config"
```

---

## Task 2: Database tables + cascade

**Files:**
- Modify: `backend/database.py` — append to `_SCHEMA_SQL` (the big DDL string; add near the end, after the last `CREATE TABLE`/`CREATE INDEX`, before the closing `"""`)
- Test: `tests/verification/test_db.py`

**Interfaces:**
- Produces: tables `verification_runs` and `lead_contact_verification` (schema per design §6.1/§6.2), created on `db.init_db()`.

- [ ] **Step 1: Write the failing test**

Create `tests/verification/test_db.py`:

```python
import pytest

pytestmark = pytest.mark.asyncio


async def test_verification_tables_exist(clean_db):
    db = clean_db
    async with db.get_db() as conn:
        rows = await conn.fetch(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('verification_runs', 'lead_contact_verification')"
        )
    names = {r["name"] for r in rows}
    assert names == {"verification_runs", "lead_contact_verification"}


async def test_lead_contact_verification_cascades_on_lead_delete(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Acme", "email": "a@acme.com"})
    async with db.get_db() as conn:
        await conn.execute(
            "INSERT INTO lead_contact_verification (lead_id, status) VALUES ($1, $2)",
            lead_id, "VALID",
        )
    assert await db.delete_lead(lead_id) is True
    async with db.get_db() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM lead_contact_verification WHERE lead_id = $1", lead_id
        )
    assert row is None  # cascade removed it


async def test_lead_contact_verification_unique_on_lead_id(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Acme", "email": "a@acme.com"})
    async with db.get_db() as conn:
        await conn.execute(
            "INSERT INTO lead_contact_verification (lead_id, status) VALUES ($1, 'VALID')", lead_id)
        with pytest.raises(Exception):
            await conn.execute(
                "INSERT INTO lead_contact_verification (lead_id, status) VALUES ($1, 'RISKY')", lead_id)


async def test_init_db_is_idempotent(clean_db):
    # clean_db already ran init_db once; run again — must not raise
    await clean_db.init_db()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_db.py -q`
Expected: FAIL — `no such table: verification_runs`.

- [ ] **Step 3: Add the DDL**

In `backend/database.py`, find the end of `_SCHEMA_SQL` (just before the closing `"""`). Append:

```sql

-- Checkpoint 5C — Contact Verification. Additive; no existing table touched.
-- verification_runs: one row per "Verify Selected" click (mirrors lead_discovery_runs).
CREATE TABLE IF NOT EXISTS verification_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    status            TEXT    NOT NULL DEFAULT 'QUEUED',   -- QUEUED|RUNNING|COMPLETED|FAILED|CANCEL_REQUESTED|CANCELLED
    requested_count   INTEGER NOT NULL DEFAULT 0,
    processed_count   INTEGER NOT NULL DEFAULT 0,
    valid_count       INTEGER NOT NULL DEFAULT 0,
    risky_count       INTEGER NOT NULL DEFAULT 0,
    invalid_count     INTEGER NOT NULL DEFAULT 0,
    unverified_count  INTEGER NOT NULL DEFAULT 0,
    error_count       INTEGER NOT NULL DEFAULT 0,
    lead_ids_json     TEXT    NOT NULL DEFAULT '[]',
    not_found_json    TEXT,
    error_message     TEXT,
    started_at        TIMESTAMP,
    finished_at       TIMESTAMP,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_verification_runs_status ON verification_runs (status);

-- lead_contact_verification: one CURRENT row per lead (UPSERT on lead_id).
CREATE TABLE IF NOT EXISTS lead_contact_verification (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id              INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    run_id               INTEGER REFERENCES verification_runs(id) ON DELETE SET NULL,
    status               TEXT NOT NULL,          -- VALID | RISKY | INVALID | UNVERIFIED
    email                TEXT,
    email_status         TEXT,                   -- VALID | RISKY | INVALID | MISSING
    email_syntax_ok      INTEGER,
    email_is_role        INTEGER,
    email_is_disposable  INTEGER,
    email_domain         TEXT,
    email_dns_result     TEXT,                   -- MX_FOUND | A_ONLY | NO_RECORDS | TIMEOUT | DNS_ERROR | SKIPPED
    email_mx_hosts       TEXT,                   -- JSON array, <=5
    phone                TEXT,
    phone_status         TEXT,                   -- VALID | INVALID | MISSING
    phone_e164           TEXT,
    phone_region         TEXT,
    phone_line_type      TEXT,
    whatsapp_hint        TEXT,                   -- possible | unlikely | unknown  (format-based only)
    checked_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    duration_ms          INTEGER,
    error                TEXT,
    raw_json             TEXT,
    UNIQUE (lead_id)
);
CREATE INDEX IF NOT EXISTS idx_lcv_status ON lead_contact_verification (status);
CREATE INDEX IF NOT EXISTS idx_lcv_run    ON lead_contact_verification (run_id);
```

Note: `_SCHEMA_SQL` runs via `executescript` on **every** startup (`_run_migrations`), and both
tables are brand new, so `CREATE TABLE IF NOT EXISTS` creates them on the existing
`backend/data/leads.db` too — no `_add_col_if_missing` entry is needed for initial creation.

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_db.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Verify against the real dev DB (manual)**

Run: `venv/Scripts/python.exe -c "import asyncio; from backend import database as db; asyncio.run(db.init_db())"`
Expected: no error ("Schema migrations applied"). Then:
`venv/Scripts/python.exe -c "import sqlite3; c=sqlite3.connect(r'backend/data/leads.db'); print([r[0] for r in c.execute(\"select name from sqlite_master where name like 'verification%' or name='lead_contact_verification'\")])"`
Expected: `['verification_runs', 'lead_contact_verification']`.

- [ ] **Step 6: Full suite**

Run: `venv/Scripts/python.exe -m pytest -q`
Expected: `703 passed`.

- [ ] **Step 7: Commit**

```bash
git add backend/database.py tests/verification/test_db.py
git commit -m "feat(5c): add verification_runs + lead_contact_verification tables"
```

---

## Task 3: Database helper functions

**Files:**
- Modify: `backend/database.py` — add a `# ── Contact verification (5C) ──` section near the other run helpers (after `get_leads_for_discovery_run`, ~line 2830)
- Test: `tests/verification/test_db.py` (append)

**Interfaces:**
- Produces:
  - `async create_verification_run(lead_ids: list[int]) -> int` — inserts a `QUEUED` run, `requested_count = len(lead_ids)`, `lead_ids_json = json.dumps(lead_ids)`; returns id.
  - `async get_verification_run(run_id: int) -> dict | None` — row as dict, with `lead_ids` and `not_found` decoded from JSON.
  - `async update_verification_run(run_id: int, data: dict) -> bool` — writes only `_VERIFICATION_RUN_WRITABLE` keys; list values JSON-encoded.
  - `async get_latest_verification_run() -> dict | None` — most recent by `id DESC`.
  - `async bump_verification_run(run_id: int, status_bucket: str, *, processed: bool = True, error: bool = False) -> None` — atomic `processed_count += 1` (if `processed`) and the matching `{valid,risky,invalid,unverified}_count += 1` and `error_count += 1` (if `error`).
  - `async upsert_lead_contact_verification(lead_id: int, run_id: int | None, result: dict) -> int` — `INSERT … ON CONFLICT(lead_id) DO UPDATE SET … RETURNING id`.
  - `async get_verification_results_for_run(run_id: int) -> list[dict]` — join `lead_contact_verification` + `leads.business_name` for rows with this `run_id`, `ORDER BY lead_id`.
  - `async get_lead_contact_verifications_by_ids(lead_ids: list[int]) -> dict[int, dict]` — `{lead_id: row_dict}` for the given ids (missing ids absent).

- [ ] **Step 1: Write the failing tests**

Append to `tests/verification/test_db.py`:

```python
async def test_create_and_get_verification_run(clean_db):
    db = clean_db
    rid = await db.create_verification_run([3, 1, 2])
    run = await db.get_verification_run(rid)
    assert run["status"] == "QUEUED"
    assert run["requested_count"] == 3
    assert run["lead_ids"] == [3, 1, 2]
    assert run["processed_count"] == 0
    assert await db.get_verification_run(999999) is None


async def test_update_and_latest_verification_run(clean_db):
    db = clean_db
    rid = await db.create_verification_run([1])
    await db.update_verification_run(rid, {"status": "RUNNING", "started_at": db._now_naive_iso()})
    await db.update_verification_run(rid, {"not_found": [42]})
    run = await db.get_verification_run(rid)
    assert run["status"] == "RUNNING" and run["started_at"] and run["not_found"] == [42]
    rid2 = await db.create_verification_run([2])
    assert (await db.get_latest_verification_run())["id"] == rid2


async def test_bump_verification_run(clean_db):
    db = clean_db
    rid = await db.create_verification_run([1, 2, 3])
    await db.bump_verification_run(rid, "VALID")
    await db.bump_verification_run(rid, "RISKY")
    await db.bump_verification_run(rid, "UNVERIFIED", error=True)
    run = await db.get_verification_run(rid)
    assert run["processed_count"] == 3
    assert run["valid_count"] == 1 and run["risky_count"] == 1
    assert run["unverified_count"] == 1 and run["error_count"] == 1


async def test_upsert_lead_contact_verification_overwrites(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Acme", "email": "a@acme.com"})
    rid = await db.create_verification_run([lead_id])
    await db.upsert_lead_contact_verification(lead_id, rid, {
        "status": "RISKY", "email_status": "RISKY", "email": "a@acme.com",
        "email_dns_result": "TIMEOUT", "raw_json": "{}",
    })
    await db.upsert_lead_contact_verification(lead_id, rid, {
        "status": "VALID", "email_status": "VALID", "email": "a@acme.com",
        "email_dns_result": "MX_FOUND", "email_mx_hosts": '["mx.acme.com"]', "raw_json": "{}",
    })
    rows = await db.get_verification_results_for_run(rid)
    assert len(rows) == 1
    assert rows[0]["status"] == "VALID"
    assert rows[0]["email_dns_result"] == "MX_FOUND"
    assert rows[0]["business_name"] == "Acme"


async def test_get_lead_contact_verifications_by_ids(clean_db):
    db = clean_db
    a = await db.create_lead({"business_name": "A", "email": "a@a.com"})
    b = await db.create_lead({"business_name": "B", "email": "b@b.com"})
    await db.upsert_lead_contact_verification(a, None, {"status": "VALID", "raw_json": "{}"})
    got = await db.get_lead_contact_verifications_by_ids([a, b, 999999])
    assert set(got.keys()) == {a}
    assert got[a]["status"] == "VALID"
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_db.py -q`
Expected: FAIL — `AttributeError: module 'backend.database' has no attribute 'create_verification_run'`.

- [ ] **Step 3: Implement the helpers**

In `backend/database.py`, add a new section (mirrors the discovery-run helpers):

```python
# ─────────────────────────────────────────────────────────────────────────────
# Contact verification (Checkpoint 5C) — additive, no existing table touched
# ─────────────────────────────────────────────────────────────────────────────

_VERIFICATION_RUN_WRITABLE = frozenset({
    "status", "requested_count", "processed_count", "valid_count", "risky_count",
    "invalid_count", "unverified_count", "error_count", "not_found", "error_message",
    "started_at", "finished_at",
})

_LCV_WRITABLE = (
    "run_id", "status", "email", "email_status", "email_syntax_ok", "email_is_role",
    "email_is_disposable", "email_domain", "email_dns_result", "email_mx_hosts",
    "phone", "phone_status", "phone_e164", "phone_region", "phone_line_type",
    "whatsapp_hint", "duration_ms", "error", "raw_json",
)

_VERIFY_BUCKET_COL = {
    "VALID": "valid_count", "RISKY": "risky_count",
    "INVALID": "invalid_count", "UNVERIFIED": "unverified_count",
}


async def create_verification_run(lead_ids: List[int]) -> int:
    async with get_db() as conn:
        return await conn.fetchval(
            "INSERT INTO verification_runs (status, requested_count, lead_ids_json) "
            "VALUES ('QUEUED', $1, $2) RETURNING id",
            len(lead_ids), json.dumps(list(lead_ids)),
        )


def _decode_verification_run(row) -> Dict[str, Any]:
    d = dict(row)
    d["lead_ids"] = json.loads(d.pop("lead_ids_json") or "[]")
    d["not_found"] = json.loads(d.pop("not_found_json") or "null") or []
    return d


async def get_verification_run(run_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        row = await conn.fetchrow("SELECT * FROM verification_runs WHERE id = $1", run_id)
    return _decode_verification_run(row) if row else None


async def get_latest_verification_run() -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        row = await conn.fetchrow("SELECT * FROM verification_runs ORDER BY id DESC LIMIT 1")
    return _decode_verification_run(row) if row else None


async def update_verification_run(run_id: int, data: Dict[str, Any]) -> bool:
    clean: Dict[str, Any] = {}
    for k, v in data.items():
        if k not in _VERIFICATION_RUN_WRITABLE or v is None:
            continue
        if k == "not_found":
            clean["not_found_json"] = json.dumps(v)
        else:
            clean[k] = v
    if not clean:
        return False
    set_clause = ", ".join(f"{c} = ?" for c in clean)
    async with get_db() as conn:
        res = await conn.execute(
            f"UPDATE verification_runs SET {set_clause} WHERE id = ?",
            *clean.values(), run_id,
        )
    return _rows_affected(res) > 0


async def bump_verification_run(run_id: int, status_bucket: str, *,
                                processed: bool = True, error: bool = False) -> None:
    bucket_col = _VERIFY_BUCKET_COL.get(status_bucket, "unverified_count")
    parts = [f"{bucket_col} = {bucket_col} + 1"]
    if processed:
        parts.append("processed_count = processed_count + 1")
    if error:
        parts.append("error_count = error_count + 1")
    async with get_db() as conn:
        await conn.execute(
            f"UPDATE verification_runs SET {', '.join(parts)} WHERE id = ?", run_id
        )


async def upsert_lead_contact_verification(lead_id: int, run_id: Optional[int],
                                           result: Dict[str, Any]) -> int:
    data = {"run_id": run_id, **{k: result.get(k) for k in _LCV_WRITABLE if k != "run_id"}}
    data = {k: v for k, v in data.items() if v is not None}
    data["status"] = result["status"]  # NOT NULL — always present
    cols = ", ".join(["lead_id"] + list(data.keys()))
    ph = ", ".join("?" for _ in range(len(data) + 1))
    update_set = ", ".join(f"{c} = excluded.{c}" for c in data.keys())
    update_set += ", checked_at = CURRENT_TIMESTAMP"
    async with get_db() as conn:
        return await conn.fetchval(
            f"INSERT INTO lead_contact_verification ({cols}) VALUES ({ph}) "
            f"ON CONFLICT (lead_id) DO UPDATE SET {update_set} RETURNING id",
            lead_id, *data.values(),
        )


async def get_verification_results_for_run(run_id: int) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT lcv.*, l.business_name FROM lead_contact_verification lcv "
            "JOIN leads l ON l.id = lcv.lead_id WHERE lcv.run_id = $1 ORDER BY lcv.lead_id",
            run_id,
        )
    return [dict(r) for r in rows]


async def get_lead_contact_verifications_by_ids(lead_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    if not lead_ids:
        return {}
    ph = ", ".join("?" for _ in lead_ids)
    async with get_db() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM lead_contact_verification WHERE lead_id IN ({ph})", *lead_ids
        )
    return {r["lead_id"]: dict(r) for r in rows}
```

- [ ] **Step 4: Run to verify pass**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_db.py -q`
Expected: PASS (9 tests total in the file).

- [ ] **Step 5: Full suite**

Run: `venv/Scripts/python.exe -m pytest -q`
Expected: `708 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/database.py tests/verification/test_db.py
git commit -m "feat(5c): database helpers for verification runs + results"
```

---

## Task 4: `status_rules.py` — the deterministic roll-up

**Files:**
- Create: `backend/verification/__init__.py` (empty), `backend/verification/status_rules.py`
- Test: `tests/verification/test_status_rules.py`

**Interfaces:**
- Produces:
  - `email_status_from(*, present: bool, syntax_ok: bool, is_role: bool, is_disposable: bool, dns_result: str) -> str` → `"MISSING" | "INVALID" | "RISKY" | "VALID"`
  - `roll_up(email_status: str, phone_status: str) -> str` → `"VALID" | "RISKY" | "INVALID" | "UNVERIFIED"`
  - constants `EMAIL_STATUSES`, `PHONE_STATUSES`, `LEAD_STATUSES`, `DNS_RESULTS`

- [ ] **Step 1: Write the failing test** — `tests/verification/test_status_rules.py`:

```python
import pytest
from backend.verification.status_rules import email_status_from, roll_up, LEAD_STATUSES


def E(present=True, syntax_ok=True, is_role=False, is_disposable=False, dns_result="MX_FOUND"):
    return email_status_from(present=present, syntax_ok=syntax_ok, is_role=is_role,
                             is_disposable=is_disposable, dns_result=dns_result)


@pytest.mark.parametrize("kw,expected", [
    (dict(present=False),                              "MISSING"),
    (dict(syntax_ok=False),                            "INVALID"),
    (dict(is_disposable=True),                         "INVALID"),
    (dict(dns_result="NO_RECORDS"),                    "INVALID"),
    (dict(is_role=True),                               "RISKY"),
    (dict(dns_result="A_ONLY"),                        "RISKY"),
    (dict(dns_result="TIMEOUT"),                       "RISKY"),
    (dict(dns_result="DNS_ERROR"),                     "RISKY"),
    (dict(),                                           "VALID"),
])
def test_email_status_from(kw, expected):
    assert E(**kw) == expected


@pytest.mark.parametrize("email_s,phone_s,expected", [
    ("VALID",   "VALID",   "VALID"),
    ("VALID",   "MISSING", "VALID"),
    ("RISKY",   "VALID",   "RISKY"),
    ("RISKY",   "MISSING", "RISKY"),
    ("INVALID", "VALID",   "RISKY"),
    ("INVALID", "INVALID", "INVALID"),
    ("INVALID", "MISSING", "INVALID"),
    ("MISSING", "VALID",   "RISKY"),
    ("MISSING", "INVALID", "INVALID"),
    ("MISSING", "MISSING", "INVALID"),
])
def test_roll_up_grid(email_s, phone_s, expected):
    result = roll_up(email_s, phone_s)
    assert result == expected
    assert result in LEAD_STATUSES


def test_named_combinations_from_brief():
    # valid email + disposable domain  -> email INVALID; with no phone -> INVALID
    assert roll_up(E(is_disposable=True), "MISSING") == "INVALID"
    # ...same but a valid phone rescues it to RISKY
    assert roll_up(E(is_disposable=True), "VALID") == "RISKY"
    # valid syntax + dead domain
    assert roll_up(E(dns_result="NO_RECORDS"), "MISSING") == "INVALID"
    # DNS timeout / failure -> RISKY regardless of phone
    assert roll_up(E(dns_result="TIMEOUT"), "MISSING") == "RISKY"
    assert roll_up(E(dns_result="DNS_ERROR"), "VALID") == "RISKY"
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_status_rules.py -q`
Expected: FAIL — `ModuleNotFoundError: backend.verification`.

- [ ] **Step 3: Implement**

Create `backend/verification/__init__.py` (empty). Create `backend/verification/status_rules.py`:

```python
"""status_rules.py — pure, deterministic roll-up of contact checks to a lead status.

No I/O, no side effects. Total function over the finite status product (design §4.3/§5).
"""

EMAIL_STATUSES = ("MISSING", "INVALID", "RISKY", "VALID")
PHONE_STATUSES = ("MISSING", "INVALID", "VALID")
LEAD_STATUSES  = ("VALID", "RISKY", "INVALID", "UNVERIFIED")
DNS_RESULTS    = ("MX_FOUND", "A_ONLY", "NO_RECORDS", "TIMEOUT", "DNS_ERROR", "SKIPPED")

_DNS_RISKY  = frozenset({"A_ONLY", "TIMEOUT", "DNS_ERROR"})
_DNS_DEAD   = frozenset({"NO_RECORDS"})


def email_status_from(*, present: bool, syntax_ok: bool, is_role: bool,
                      is_disposable: bool, dns_result: str) -> str:
    if not present:
        return "MISSING"
    if not syntax_ok or is_disposable or dns_result in _DNS_DEAD:
        return "INVALID"
    if is_role or dns_result in _DNS_RISKY:
        return "RISKY"
    return "VALID"          # syntax OK, non-role, non-disposable, MX_FOUND


def roll_up(email_status: str, phone_status: str) -> str:
    if email_status == "VALID":
        return "VALID"
    if email_status == "RISKY":
        return "RISKY"
    # email is INVALID or MISSING here
    if phone_status == "VALID":
        return "RISKY"
    return "INVALID"
```

- [ ] **Step 4: Run to verify pass**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_status_rules.py -q`
Expected: PASS (all parametrized cases).

- [ ] **Step 5: Commit**

```bash
git add backend/verification/__init__.py backend/verification/status_rules.py tests/verification/test_status_rules.py
git commit -m "feat(5c): deterministic status roll-up rules"
```

---

## Task 5: `disposable_domains.py` + vendored list

**Files:**
- Create: `backend/verification/disposable_domains.py`, `backend/verification/data/disposable_domains.txt`
- Test: `tests/verification/test_disposable_domains.py`

**Interfaces:**
- Produces: `is_disposable(domain: str) -> bool` — exact match **or** registrable-suffix match against the loaded set (case-insensitive). Empty/None → `False`.

- [ ] **Step 1: Create the data file**

Create `backend/verification/data/disposable_domains.txt`. First line a comment, then a curated
list (start with this seed set of well-known throwaway providers; ~1 per line, no blank lines
between). This is vendored — no runtime fetch.

```
# Disposable / throwaway email domains — vendored 2026-09-01. Refresh = edit this file.
# Source: curated from the public "disposable-email-domains" project, point-in-time snapshot.
0-mail.com
10minutemail.com
20minutemail.com
33mail.com
guerrillamail.com
guerrillamail.net
guerrillamail.org
mailinator.com
mailinator.net
maildrop.cc
temp-mail.org
tempmail.com
tempmailo.com
throwawaymail.com
trashmail.com
trashmail.de
yopmail.com
yopmail.fr
getnada.com
dispostable.com
fakeinbox.com
sharklasers.com
spam4.me
mohmal.com
mailnesia.com
tmpmail.org
tmpmail.net
emailondeck.com
mailcatch.com
inboxbear.com
```

(The implementer may extend this list; do not shrink it.)

- [ ] **Step 2: Write the failing test** — `tests/verification/test_disposable_domains.py`:

```python
from backend.verification.disposable_domains import is_disposable


def test_known_disposable_matches():
    assert is_disposable("mailinator.com") is True
    assert is_disposable("MAILINATOR.COM") is True
    assert is_disposable("guerrillamail.org") is True


def test_subdomain_of_disposable_matches():
    assert is_disposable("smtp.mailinator.com") is True


def test_real_business_domain_does_not_match():
    assert is_disposable("popupgenix.com") is False
    assert is_disposable("acme-dental.co.uk") is False


def test_empty_input():
    assert is_disposable("") is False
    assert is_disposable(None) is False
```

- [ ] **Step 3: Run to verify failure**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_disposable_domains.py -q`
Expected: FAIL — module not found.

- [ ] **Step 4: Implement** — `backend/verification/disposable_domains.py`:

```python
"""disposable_domains.py — offline blocklist of throwaway email domains.

Loaded once at import. Data file is vendored; no runtime network fetch.
"""
from pathlib import Path
from typing import Optional

_DATA = Path(__file__).parent / "data" / "disposable_domains.txt"


def _load() -> frozenset[str]:
    out = set()
    try:
        for line in _DATA.read_text(encoding="utf-8").splitlines():
            line = line.strip().lower()
            if line and not line.startswith("#"):
                out.add(line)
    except OSError:
        pass
    return frozenset(out)


_DOMAINS = _load()


def is_disposable(domain: Optional[str]) -> bool:
    if not domain:
        return False
    d = domain.strip().lower().rstrip(".")
    if d in _DOMAINS:
        return True
    # suffix match: sub.mailinator.com -> mailinator.com
    parts = d.split(".")
    for i in range(1, len(parts) - 1):
        if ".".join(parts[i:]) in _DOMAINS:
            return True
    return False
```

- [ ] **Step 5: Run to verify pass**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_disposable_domains.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/verification/disposable_domains.py backend/verification/data/disposable_domains.txt tests/verification/test_disposable_domains.py
git commit -m "feat(5c): vendored disposable-domain blocklist"
```

---

## Task 6: `dns_lookup.py` — async MX/A resolution

**Files:**
- Create: `backend/verification/dns_lookup.py`
- Test: `tests/verification/test_dns_lookup.py`

**Interfaces:**
- Produces: `async resolve_domain(domain: str, cache: dict, *, timeout: float = 3.0, lifetime: float = 5.0) -> tuple[str, list[str]]`
  → `(dns_result, mx_hosts)` where `dns_result ∈ {MX_FOUND, A_ONLY, NO_RECORDS, TIMEOUT, DNS_ERROR}` and `mx_hosts` is ≤5 host strings (empty unless `MX_FOUND`).
  `cache` is a caller-owned dict keyed by lowercase domain → the returned tuple (per-run reuse).

- [ ] **Step 1: Write the failing test** — `tests/verification/test_dns_lookup.py`:

```python
import pytest
import dns.resolver
from backend.verification import dns_lookup

pytestmark = pytest.mark.asyncio


class _Ans:
    def __init__(self, items): self._items = items
    def __iter__(self): return iter(self._items)


class _MX:
    def __init__(self, host): self.exchange = host
    def to_text(self): return self.host if False else str(self.exchange)


def _patch_resolver(monkeypatch, behavior):
    """behavior: dict of rdtype -> ('ok', answer) | ('nxdomain',) | ('noanswer',) | ('timeout',) | ('error',)"""
    class _FakeResolver:
        def __init__(self): self.timeout = self.lifetime = 0
        async def resolve(self, domain, rdtype):
            b = behavior.get(rdtype, ("noanswer",))
            if b[0] == "ok":
                return b[1]
            if b[0] == "nxdomain":
                raise dns.resolver.NXDOMAIN
            if b[0] == "noanswer":
                raise dns.resolver.NoAnswer
            if b[0] == "timeout":
                raise dns.exception.Timeout
            raise dns.resolver.NoNameservers
    monkeypatch.setattr(dns_lookup.dns.asyncresolver, "Resolver", _FakeResolver)


async def test_mx_found(monkeypatch):
    _patch_resolver(monkeypatch, {"MX": ("ok", _Ans([_MX("mx1.acme.com."), _MX("mx2.acme.com.")]))})
    result, hosts = await dns_lookup.resolve_domain("acme.com", {})
    assert result == "MX_FOUND"
    assert hosts[:2] == ["mx1.acme.com.", "mx2.acme.com."]


async def test_a_only(monkeypatch):
    _patch_resolver(monkeypatch, {"MX": ("noanswer",), "A": ("ok", _Ans(["1.2.3.4"]))})
    result, hosts = await dns_lookup.resolve_domain("acme.com", {})
    assert result == "A_ONLY" and hosts == []


async def test_no_records(monkeypatch):
    _patch_resolver(monkeypatch, {"MX": ("nxdomain",), "A": ("nxdomain",), "AAAA": ("nxdomain",)})
    result, _ = await dns_lookup.resolve_domain("nope.invalid", {})
    assert result == "NO_RECORDS"


async def test_timeout(monkeypatch):
    _patch_resolver(monkeypatch, {"MX": ("timeout",)})
    result, _ = await dns_lookup.resolve_domain("slow.com", {})
    assert result == "TIMEOUT"


async def test_dns_error(monkeypatch):
    _patch_resolver(monkeypatch, {"MX": ("error",)})
    result, _ = await dns_lookup.resolve_domain("broken.com", {})
    assert result == "DNS_ERROR"


async def test_cache_hit_skips_second_lookup(monkeypatch):
    calls = {"n": 0}
    class _FakeResolver:
        def __init__(self): self.timeout = self.lifetime = 0
        async def resolve(self, domain, rdtype):
            calls["n"] += 1
            return _Ans([_MX("mx.acme.com.")])
    monkeypatch.setattr(dns_lookup.dns.asyncresolver, "Resolver", _FakeResolver)
    cache = {}
    await dns_lookup.resolve_domain("acme.com", cache)
    await dns_lookup.resolve_domain("acme.com", cache)
    assert calls["n"] == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_dns_lookup.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement** — `backend/verification/dns_lookup.py`:

```python
"""dns_lookup.py — deterministic MX/A(AAAA) resolution for email verification.

The ONLY outbound traffic 5C generates: standard DNS (port 53) for the domain
of an email already on the lead. No SMTP, no HTTP.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Tuple

import dns.asyncresolver
import dns.exception
import dns.resolver

logger = logging.getLogger(__name__)

_MX_FOUND, _A_ONLY, _NO_RECORDS, _TIMEOUT, _DNS_ERROR = (
    "MX_FOUND", "A_ONLY", "NO_RECORDS", "TIMEOUT", "DNS_ERROR",
)


async def resolve_domain(domain: str, cache: Dict[str, Tuple[str, List[str]]], *,
                         timeout: float = 3.0, lifetime: float = 5.0
                         ) -> Tuple[str, List[str]]:
    key = (domain or "").strip().lower().rstrip(".")
    if not key:
        return _NO_RECORDS, []
    if key in cache:
        return cache[key]
    result = await _resolve(key, timeout, lifetime)
    cache[key] = result
    return result


async def _resolve(domain: str, timeout: float, lifetime: float) -> Tuple[str, List[str]]:
    resolver = dns.asyncresolver.Resolver()
    resolver.timeout = timeout
    resolver.lifetime = lifetime
    # 1. MX
    try:
        answer = await resolver.resolve(domain, "MX")
        hosts = [str(r.exchange) for r in answer][:5]
        if hosts:
            return _MX_FOUND, hosts
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
        pass
    except dns.exception.Timeout:
        return _TIMEOUT, []
    except (dns.resolver.NoNameservers, dns.exception.DNSException, OSError) as exc:
        logger.debug("DNS MX lookup failed for %s: %s", domain, type(exc).__name__)
        return _DNS_ERROR, []
    # 2. A / AAAA fallback
    for rdtype in ("A", "AAAA"):
        try:
            answer = await resolver.resolve(domain, rdtype)
            if list(answer):
                return _A_ONLY, []
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            continue
        except dns.exception.Timeout:
            return _TIMEOUT, []
        except (dns.resolver.NoNameservers, dns.exception.DNSException, OSError):
            return _DNS_ERROR, []
    return _NO_RECORDS, []
```

- [ ] **Step 4: Run to verify pass**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_dns_lookup.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/verification/dns_lookup.py tests/verification/test_dns_lookup.py
git commit -m "feat(5c): async MX/A DNS resolver with per-run cache"
```

---

## Task 7: `email_checks.py`

**Files:**
- Create: `backend/verification/email_checks.py`
- Test: `tests/verification/test_email_checks.py`

**Interfaces:**
- Consumes: `validators.is_valid_email`, `disposable_domains.is_disposable`, `dns_lookup.resolve_domain`, `status_rules.email_status_from`
- Produces: `async check_email(email: str | None, dns_cache: dict, *, timeout=3.0, lifetime=5.0) -> dict` with keys:
  `email` (normalised or None), `email_status`, `email_syntax_ok` (0/1), `email_is_role` (0/1),
  `email_is_disposable` (0/1), `email_domain`, `email_dns_result`, `email_mx_hosts` (JSON string or None).
- Produces: `ROLE_LOCALS: frozenset[str]`

- [ ] **Step 1: Write the failing test** — `tests/verification/test_email_checks.py`:

```python
import json
import pytest
from backend.verification import email_checks, dns_lookup

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mx(monkeypatch):
    async def _fake(domain, cache, **kw):
        return ("MX_FOUND", ["mx.%s." % domain])
    monkeypatch.setattr(email_checks, "resolve_domain", _fake)


@pytest.fixture
def dns_as(monkeypatch):
    def _set(result, hosts=None):
        async def _fake(domain, cache, **kw):
            return (result, hosts or [])
        monkeypatch.setattr(email_checks, "resolve_domain", _fake)
    return _set


async def test_missing_email():
    r = await email_checks.check_email(None, {})
    assert r["email_status"] == "MISSING" and r["email"] is None


async def test_bad_syntax(dns_as):
    dns_as("MX_FOUND")
    r = await email_checks.check_email("not-an-email", {})
    assert r["email_status"] == "INVALID" and r["email_syntax_ok"] == 0


async def test_valid_with_mx(mx):
    r = await email_checks.check_email("Owner@Acme.com", {})
    assert r["email"] == "owner@acme.com"
    assert r["email_status"] == "VALID"
    assert r["email_domain"] == "acme.com"
    assert r["email_is_role"] == 0
    assert json.loads(r["email_mx_hosts"]) == ["mx.acme.com."]


async def test_role_account_is_risky(mx):
    r = await email_checks.check_email("info@acme.com", {})
    assert r["email_is_role"] == 1
    assert r["email_status"] == "RISKY"


async def test_disposable_is_invalid(dns_as):
    dns_as("MX_FOUND")
    r = await email_checks.check_email("x@mailinator.com", {})
    assert r["email_is_disposable"] == 1
    assert r["email_status"] == "INVALID"


@pytest.mark.parametrize("dns_result,expected", [
    ("MX_FOUND",   "VALID"),
    ("A_ONLY",     "RISKY"),
    ("NO_RECORDS", "INVALID"),
    ("TIMEOUT",    "RISKY"),
    ("DNS_ERROR",  "RISKY"),
])
async def test_dns_result_maps_to_status(dns_as, dns_result, expected):
    dns_as(dns_result)
    r = await email_checks.check_email("hello@acme.com", {})
    assert r["email_dns_result"] == dns_result
    assert r["email_status"] == expected
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_email_checks.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement** — `backend/verification/email_checks.py`:

```python
"""email_checks.py — deterministic email verification (design §4.1).

Never proves a mailbox exists. Distinguishes: syntax / role / disposable /
live domain (MX or A) / dead domain.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from ..validators import is_valid_email, _SPAM_LOCALS
from .disposable_domains import is_disposable
from .dns_lookup import resolve_domain
from .status_rules import email_status_from

ROLE_LOCALS = frozenset(_SPAM_LOCALS) | frozenset({
    "info", "sales", "contact", "hello", "hi", "admin", "support", "office",
    "team", "marketing", "enquiries", "inquiries", "help", "mail", "billing",
    "accounts", "accounting", "hr", "jobs", "careers", "recruitment", "press",
    "media", "pr", "general", "reception", "frontdesk", "service",
    "customerservice", "orders", "booking", "bookings",
})


async def check_email(email: Optional[str], dns_cache: Dict[str, Any], *,
                      timeout: float = 3.0, lifetime: float = 5.0) -> Dict[str, Any]:
    base = {
        "email": None, "email_status": "MISSING", "email_syntax_ok": 0,
        "email_is_role": 0, "email_is_disposable": 0, "email_domain": None,
        "email_dns_result": None, "email_mx_hosts": None,
    }
    if not email or not str(email).strip():
        return base

    raw = str(email).strip().lower()
    syntax_ok = is_valid_email(raw)
    base["email"] = raw
    base["email_syntax_ok"] = 1 if syntax_ok else 0

    if not syntax_ok:
        base["email_status"] = email_status_from(
            present=True, syntax_ok=False, is_role=False, is_disposable=False,
            dns_result="SKIPPED")
        return base

    local, domain = raw.rsplit("@", 1)
    base["email_domain"] = domain
    is_role = local in ROLE_LOCALS
    disp = is_disposable(domain)
    base["email_is_role"] = 1 if is_role else 0
    base["email_is_disposable"] = 1 if disp else 0

    if disp:
        base["email_dns_result"] = "SKIPPED"
        base["email_status"] = email_status_from(
            present=True, syntax_ok=True, is_role=is_role, is_disposable=True,
            dns_result="SKIPPED")
        return base

    dns_result, mx_hosts = await resolve_domain(
        domain, dns_cache, timeout=timeout, lifetime=lifetime)
    base["email_dns_result"] = dns_result
    base["email_mx_hosts"] = json.dumps(mx_hosts) if mx_hosts else None
    base["email_status"] = email_status_from(
        present=True, syntax_ok=True, is_role=is_role, is_disposable=False,
        dns_result=dns_result)
    return base
```

- [ ] **Step 4: Run to verify pass**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_email_checks.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/verification/email_checks.py tests/verification/test_email_checks.py
git commit -m "feat(5c): deterministic email checks (syntax/role/disposable/DNS)"
```

---

## Task 8: `phone_checks.py`

**Files:**
- Create: `backend/verification/phone_checks.py`
- Test: `tests/verification/test_phone_checks.py`

**Interfaces:**
- Produces: `check_phone(phone: str | None, default_region: str | None) -> dict` with keys:
  `phone` (raw or None), `phone_status` (`VALID|INVALID|MISSING`), `phone_e164`, `phone_region`,
  `phone_line_type` (str name), `whatsapp_hint` (`possible|unlikely|unknown`).
- Produces: `whatsapp_hint_from(line_type: str, phone_status: str) -> str`
- Produces: `region_for_country(country: str | None) -> str | None`

- [ ] **Step 1: Write the failing test** — `tests/verification/test_phone_checks.py`:

```python
import pytest
from backend.verification.phone_checks import check_phone, whatsapp_hint_from, region_for_country


def test_missing_phone():
    r = check_phone(None, "US")
    assert r["phone_status"] == "MISSING"
    assert r["whatsapp_hint"] == "unknown"


def test_unparseable_phone():
    r = check_phone("not a phone", "US")
    assert r["phone_status"] == "INVALID"
    assert r["whatsapp_hint"] == "unknown"


def test_valid_us_number():
    r = check_phone("+1 202 456 1111", None)     # White House
    assert r["phone_status"] == "VALID"
    assert r["phone_e164"] == "+12024561111"
    assert r["phone_region"] == "US"
    assert r["phone_line_type"] in {"FIXED_LINE", "FIXED_LINE_OR_MOBILE"}


def test_valid_number_via_default_region():
    r = check_phone("(202) 456-1111", "US")
    assert r["phone_status"] == "VALID"
    assert r["phone_e164"] == "+12024561111"


def test_invalid_but_parseable():
    r = check_phone("+1 000 000 0000", None)
    assert r["phone_status"] == "INVALID"


@pytest.mark.parametrize("line_type,expected", [
    ("MOBILE", "possible"),
    ("FIXED_LINE_OR_MOBILE", "possible"),
    ("FIXED_LINE", "unlikely"),
    ("VOIP", "unlikely"),
    ("TOLL_FREE", "unlikely"),
])
def test_whatsapp_hint_from(line_type, expected):
    assert whatsapp_hint_from(line_type, "VALID") == expected


def test_whatsapp_hint_unknown_when_not_valid():
    assert whatsapp_hint_from("MOBILE", "INVALID") == "unknown"
    assert whatsapp_hint_from("MOBILE", "MISSING") == "unknown"


def test_region_for_country():
    assert region_for_country("United States") == "US"
    assert region_for_country("united kingdom") == "GB"
    assert region_for_country(None) is None
    assert region_for_country("Nowhereland") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_phone_checks.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement** — `backend/verification/phone_checks.py`:

```python
"""phone_checks.py — deterministic phone verification via phonenumbers (offline).

The WhatsApp value is a FORMAT-BASED HINT ONLY. It is never a reachability check.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import phonenumbers
from phonenumbers import PhoneNumberType, NumberParseException

_TYPE_NAME = {
    PhoneNumberType.MOBILE: "MOBILE",
    PhoneNumberType.FIXED_LINE: "FIXED_LINE",
    PhoneNumberType.FIXED_LINE_OR_MOBILE: "FIXED_LINE_OR_MOBILE",
    PhoneNumberType.VOIP: "VOIP",
    PhoneNumberType.TOLL_FREE: "TOLL_FREE",
    PhoneNumberType.PREMIUM_RATE: "PREMIUM_RATE",
    PhoneNumberType.SHARED_COST: "SHARED_COST",
    PhoneNumberType.PAGER: "PAGER",
    PhoneNumberType.UAN: "UAN",
    PhoneNumberType.VOICEMAIL: "VOICEMAIL",
    PhoneNumberType.PERSONAL_NUMBER: "PERSONAL_NUMBER",
    PhoneNumberType.UNKNOWN: "UNKNOWN",
}

_WHATSAPP_POSSIBLE = frozenset({"MOBILE", "FIXED_LINE_OR_MOBILE"})

# Minimal country-name -> ISO-3166 alpha-2 map for the lead.country values this app produces.
_COUNTRY_TO_REGION = {
    "united states": "US", "usa": "US", "us": "US", "united states of america": "US",
    "united kingdom": "GB", "uk": "GB", "england": "GB", "great britain": "GB",
    "canada": "CA", "australia": "AU", "ireland": "IE", "new zealand": "NZ",
    "india": "IN", "germany": "DE", "france": "FR", "spain": "ES", "italy": "IT",
    "netherlands": "NL", "united arab emirates": "AE", "uae": "AE",
    "singapore": "SG", "south africa": "ZA", "mexico": "MX", "brazil": "BR",
}


def region_for_country(country: Optional[str]) -> Optional[str]:
    if not country:
        return None
    return _COUNTRY_TO_REGION.get(country.strip().lower())


def whatsapp_hint_from(line_type: str, phone_status: str) -> str:
    if phone_status != "VALID":
        return "unknown"
    return "possible" if line_type in _WHATSAPP_POSSIBLE else "unlikely"


def check_phone(phone: Optional[str], default_region: Optional[str]) -> Dict[str, Any]:
    base = {
        "phone": None, "phone_status": "MISSING", "phone_e164": None,
        "phone_region": None, "phone_line_type": None, "whatsapp_hint": "unknown",
    }
    if not phone or not str(phone).strip():
        return base
    raw = str(phone).strip()
    base["phone"] = raw
    try:
        parsed = phonenumbers.parse(raw, default_region)
    except NumberParseException:
        base["phone_status"] = "INVALID"
        return base
    if not phonenumbers.is_valid_number(parsed):
        base["phone_status"] = "INVALID"
        return base
    line_type = _TYPE_NAME.get(phonenumbers.number_type(parsed), "UNKNOWN")
    base.update({
        "phone_status": "VALID",
        "phone_e164": phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164),
        "phone_region": phonenumbers.region_code_for_number(parsed),
        "phone_line_type": line_type,
        "whatsapp_hint": whatsapp_hint_from(line_type, "VALID"),
    })
    return base
```

- [ ] **Step 4: Run to verify pass**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_phone_checks.py -q`
Expected: PASS. (If a specific number's `line_type` assertion is brittle across `phonenumbers`
versions, relax that one assertion to `in {…}` — do not change the hint logic.)

- [ ] **Step 5: Commit**

```bash
git add backend/verification/phone_checks.py tests/verification/test_phone_checks.py
git commit -m "feat(5c): deterministic phone checks + format-based WhatsApp hint"
```

---

## Task 9: `engine.verify_contact` — one lead, end to end

**Files:**
- Create: `backend/verification/engine.py` (this task adds `verify_contact` only; the job handler is Task 10)
- Test: `tests/verification/test_verification_engine.py`

**Interfaces:**
- Consumes: `email_checks.check_email`, `phone_checks.check_phone` + `region_for_country`, `status_rules.roll_up`, `config.get_settings`
- Produces: `async verify_contact(lead: dict, dns_cache: dict) -> dict` — merges email + phone check dicts, adds `status` (rolled up), `duration_ms`, `raw_json` (json string of the full result). Never raises for normal data; a checker exception propagates to the caller (job handler isolates it).

- [ ] **Step 1: Write the failing test** — `tests/verification/test_verification_engine.py`:

```python
import json
import pytest
from backend.verification import engine, email_checks

pytestmark = pytest.mark.asyncio


@pytest.fixture
def dns(monkeypatch):
    def _set(result, hosts=None):
        async def _fake(domain, cache, **kw):
            return (result, hosts or [])
        monkeypatch.setattr(email_checks, "resolve_domain", _fake)
    return _set


async def test_valid_email_and_phone(dns):
    dns("MX_FOUND", ["mx.acme.com."])
    lead = {"id": 1, "email": "owner@acme.com", "phone": "+1 202 456 1111", "country": "United States"}
    r = await engine.verify_contact(lead, {})
    assert r["status"] == "VALID"
    assert r["email_status"] == "VALID"
    assert r["phone_status"] == "VALID"
    assert isinstance(r["duration_ms"], int)
    parsed = json.loads(r["raw_json"])
    assert parsed["email_domain"] == "acme.com"


async def test_missing_email_valid_phone_is_risky(dns):
    dns("MX_FOUND")
    lead = {"id": 2, "email": None, "phone": "+1 202 456 1111", "country": None}
    r = await engine.verify_contact(lead, {})
    assert r["status"] == "RISKY"


async def test_dead_domain_no_phone_is_invalid(dns):
    dns("NO_RECORDS")
    lead = {"id": 3, "email": "x@dead.example", "phone": None, "country": None}
    r = await engine.verify_contact(lead, {})
    assert r["status"] == "INVALID"


async def test_disposable_no_phone_is_invalid(dns):
    dns("MX_FOUND")
    lead = {"id": 4, "email": "a@mailinator.com", "phone": "", "country": None}
    r = await engine.verify_contact(lead, {})
    assert r["status"] == "INVALID"
    assert r["email_is_disposable"] == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_verification_engine.py -q`
Expected: FAIL — `engine` has no attribute `verify_contact` / module missing.

- [ ] **Step 3: Implement** — `backend/verification/engine.py`:

```python
"""engine.py — per-lead verification + the CONTACT_VERIFICATION JobQueue handler.

Imports NOTHING that can send: no email_sender, no whatsapp_sender, no
email_campaigns.senders/n8n_client, no scheduler, no pyautogui, no smtplib.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict

from ..config import get_settings
from .email_checks import check_email
from .phone_checks import check_phone, region_for_country
from .status_rules import roll_up

logger = logging.getLogger(__name__)


async def verify_contact(lead: Dict[str, Any], dns_cache: Dict[str, Any]) -> Dict[str, Any]:
    cfg = get_settings()
    started = time.monotonic()
    email_res = await check_email(
        lead.get("email"), dns_cache,
        timeout=cfg.verification_dns_timeout_seconds,
        lifetime=cfg.verification_dns_lifetime_seconds,
    )
    phone_res = check_phone(lead.get("phone"), region_for_country(lead.get("country")))
    status = roll_up(email_res["email_status"], phone_res["phone_status"])
    result: Dict[str, Any] = {
        **email_res, **phone_res,
        "status": status,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
    result["raw_json"] = json.dumps(result, default=str)
    return result
```

- [ ] **Step 4: Run to verify pass**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_verification_engine.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/verification/engine.py tests/verification/test_verification_engine.py
git commit -m "feat(5c): per-lead verify_contact engine"
```

---

## Task 10: `run_verification_job` + `service.py`

**Files:**
- Modify: `backend/verification/engine.py` — add `run_verification_job`
- Create: `backend/verification/service.py`
- Test: `tests/verification/test_verification_run.py`

**Interfaces:**
- Consumes: `db.get_verification_run`, `db.update_verification_run`, `db.get_leads_by_ids`, `db.upsert_lead_contact_verification`, `db.bump_verification_run`
- Produces:
  - `async run_verification_job(payload: dict) -> None` — JobQueue handler; `payload = {"run_id": int}`. Lifecycle QUEUED→RUNNING→COMPLETED/CANCELLED/FAILED. Per-lead exception → `UNVERIFIED` row + `error_count`, run continues.
  - `service.is_feature_enabled() -> bool`
  - `service.start_run(lead_ids: list[int]) -> dict` — `{run_id, status, requested_count}`; raises `VerificationQueueError` if queue unavailable/full (router maps to 503) after marking the run FAILED.
  - `service.get_run_public(run_id)`, `service.get_results(run_id)`, `service.get_active_run()`, `service.request_cancel(run_id)`, `service.get_lead_states(lead_ids)`
  - `service.VerificationError`, `service.VerificationQueueError`

- [ ] **Step 1: Write the failing test** — `tests/verification/test_verification_run.py`:

```python
import pytest
from backend.verification import engine, email_checks

pytestmark = pytest.mark.asyncio


@pytest.fixture
def dns_ok(monkeypatch):
    async def _fake(domain, cache, **kw):
        return ("MX_FOUND", ["mx.%s." % domain])
    monkeypatch.setattr(email_checks, "resolve_domain", _fake)


async def _lead(db, **over):
    d = {"business_name": "Biz", "email": None, "phone": None}
    d.update(over)
    return await db.create_lead(d)


async def test_run_completes_with_counts(clean_db, dns_ok):
    db = clean_db
    a = await _lead(db, email="a@acme.com")                 # VALID
    b = await _lead(db, email=None, phone="+1 202 456 1111")  # MISSING email + VALID phone -> RISKY
    c = await _lead(db, email=None, phone=None)             # INVALID
    rid = await db.create_verification_run([a, b, c])

    await engine.run_verification_job({"run_id": rid})

    run = await db.get_verification_run(rid)
    assert run["status"] == "COMPLETED"
    assert run["processed_count"] == 3
    assert run["valid_count"] == 1 and run["risky_count"] == 1 and run["invalid_count"] == 1
    rows = {r["lead_id"]: r for r in await db.get_verification_results_for_run(rid)}
    assert rows[a]["status"] == "VALID"
    assert rows[b]["status"] == "RISKY"
    assert rows[c]["status"] == "INVALID"


async def test_reverify_upserts(clean_db, dns_ok):
    db = clean_db
    a = await _lead(db, email="a@acme.com")
    r1 = await db.create_verification_run([a])
    await engine.run_verification_job({"run_id": r1})
    r2 = await db.create_verification_run([a])
    await engine.run_verification_job({"run_id": r2})
    async with db.get_db() as conn:
        n = await conn.fetchval("SELECT COUNT(*) FROM lead_contact_verification WHERE lead_id = $1", a)
    assert n == 1


async def test_not_found_ids_reported(clean_db, dns_ok):
    db = clean_db
    a = await _lead(db, email="a@acme.com")
    rid = await db.create_verification_run([a, 999999])
    await engine.run_verification_job({"run_id": rid})
    run = await db.get_verification_run(rid)
    assert run["status"] == "COMPLETED"
    assert run["not_found"] == [999999]
    assert run["processed_count"] == 1


async def test_cancel_requested_stops_between_leads(clean_db, dns_ok):
    db = clean_db
    ids = [await _lead(db, email=f"a{i}@acme.com") for i in range(5)]
    rid = await db.create_verification_run(ids)
    await db.update_verification_run(rid, {"status": "CANCEL_REQUESTED"})
    await engine.run_verification_job({"run_id": rid})
    run = await db.get_verification_run(rid)
    assert run["status"] == "CANCELLED"
    assert run["processed_count"] < 5


async def test_per_lead_exception_is_isolated(clean_db, monkeypatch):
    db = clean_db
    a = await _lead(db, email="a@acme.com")
    b = await _lead(db, email="b@acme.com")
    calls = {"n": 0}
    real = engine.verify_contact
    async def flaky(lead, cache):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return await real(lead, cache)
    monkeypatch.setattr(engine, "verify_contact", flaky)
    async def _fake(domain, cache, **kw):
        return ("MX_FOUND", [])
    monkeypatch.setattr(email_checks, "resolve_domain", _fake)

    rid = await db.create_verification_run([a, b])
    await engine.run_verification_job({"run_id": rid})
    run = await db.get_verification_run(rid)
    assert run["status"] == "COMPLETED"
    assert run["error_count"] == 1
    rows = {r["lead_id"]: r for r in await db.get_verification_results_for_run(rid)}
    assert rows[a]["status"] == "UNVERIFIED" and rows[a]["error"]
    assert rows[b]["status"] in {"VALID", "RISKY"}


async def test_missing_run_returns_quietly(clean_db):
    await engine.run_verification_job({"run_id": 123456})   # must not raise
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_verification_run.py -q`
Expected: FAIL — `engine` has no attribute `run_verification_job`.

- [ ] **Step 3: Implement the job handler** — append to `backend/verification/engine.py`:

```python
from .. import database as db

_ACTIVE = frozenset({"QUEUED", "RUNNING", "CANCEL_REQUESTED"})


async def run_verification_job(payload: Dict[str, Any]) -> None:
    """JobQueue handler. payload = {"run_id": int}. Never raises out."""
    run_id = payload.get("run_id")
    run = await db.get_verification_run(run_id) if run_id else None
    if not run:
        logger.warning("run_verification_job: no run for run_id=%s", run_id)
        return
    if run["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
        return

    try:
        await db.update_verification_run(run_id, {"status": "RUNNING", "started_at": db._now_naive_iso()})
        lead_ids = run["lead_ids"]
        leads_map = await db.get_leads_by_ids(lead_ids)
        not_found = [lid for lid in lead_ids if lid not in leads_map]
        dns_cache: Dict[str, Any] = {}

        for lid in lead_ids:
            fresh = await db.get_verification_run(run_id)
            if fresh and fresh["status"] == "CANCEL_REQUESTED":
                await db.update_verification_run(run_id, {
                    "status": "CANCELLED", "finished_at": db._now_naive_iso(),
                    "not_found": not_found,
                })
                return
            if lid not in leads_map:
                continue
            try:
                result = await verify_contact(dict(leads_map[lid]), dns_cache)
                errored = False
            except Exception as exc:  # per-lead isolation
                logger.warning("verify_contact failed for lead %s: %s", lid, type(exc).__name__)
                result = {"status": "UNVERIFIED", "error": repr(exc)[:500], "raw_json": "{}"}
                errored = True
            await db.upsert_lead_contact_verification(lid, run_id, result)
            await db.bump_verification_run(run_id, result["status"], error=errored)

        await db.update_verification_run(run_id, {
            "status": "COMPLETED", "finished_at": db._now_naive_iso(), "not_found": not_found,
        })
    except Exception as exc:
        logger.error("run_verification_job fatal for run %s: %s", run_id, exc, exc_info=True)
        await db.update_verification_run(run_id, {
            "status": "FAILED", "error_message": repr(exc)[:500],
            "finished_at": db._now_naive_iso(),
        })
```

- [ ] **Step 4: Run to verify pass**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_verification_run.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Write `service.py`**

Create `backend/verification/service.py`:

```python
"""service.py — feature flag + run orchestration for contact verification.

Holds no verification logic (that's engine.py); shapes requests/responses and
talks to the JobQueue + DB. No sender/scheduler/n8n import.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .. import database as db
from ..config import get_settings
from ..queue_worker import get_queue
from .engine import run_verification_job

logger = logging.getLogger(__name__)


class VerificationError(RuntimeError):
    pass


class VerificationQueueError(RuntimeError):
    pass


async def is_feature_enabled() -> bool:
    if get_settings().contact_verification_enabled:
        return True
    return str(await db.get_setting("contact_verification_enabled") or "").lower() in ("1", "true", "yes")


async def start_run(lead_ids: List[int]) -> Dict[str, Any]:
    distinct = list(dict.fromkeys(int(x) for x in lead_ids))
    run_id = await db.create_verification_run(distinct)
    queue = get_queue()
    if queue is None:
        await db.update_verification_run(run_id, {"status": "FAILED", "error_message": "Job queue unavailable"})
        raise VerificationQueueError("Verification queue is not available")
    ok = queue.enqueue_nowait("CONTACT_VERIFICATION", {"run_id": run_id}, run_verification_job)
    if not ok:
        await db.update_verification_run(run_id, {"status": "FAILED", "error_message": "Job queue full"})
        raise VerificationQueueError("Verification queue is full — try again shortly")
    return {"run_id": run_id, "status": "QUEUED", "requested_count": len(distinct)}


_PUBLIC_RUN_FIELDS = (
    "id", "status", "requested_count", "processed_count", "valid_count", "risky_count",
    "invalid_count", "unverified_count", "error_count", "not_found", "error_message",
    "started_at", "finished_at", "created_at",
)


def _run_public(run: Dict[str, Any]) -> Dict[str, Any]:
    return {k: run.get(k) for k in _PUBLIC_RUN_FIELDS}


async def get_run_public(run_id: int) -> Optional[Dict[str, Any]]:
    run = await db.get_verification_run(run_id)
    return _run_public(run) if run else None


async def get_active_run() -> Optional[Dict[str, Any]]:
    run = await db.get_latest_verification_run()
    return _run_public(run) if run else None


async def request_cancel(run_id: int) -> Optional[Dict[str, Any]]:
    run = await db.get_verification_run(run_id)
    if not run:
        return None
    if run["status"] in ("QUEUED", "RUNNING"):
        await db.update_verification_run(run_id, {"status": "CANCEL_REQUESTED"})
        run = await db.get_verification_run(run_id)
    return _run_public(run)


_RESULT_FIELDS = (
    "lead_id", "business_name", "status", "email", "email_status", "email_syntax_ok",
    "email_is_role", "email_is_disposable", "email_domain", "email_dns_result",
    "email_mx_hosts", "phone", "phone_status", "phone_e164", "phone_region",
    "phone_line_type", "whatsapp_hint", "checked_at", "duration_ms", "error",
)


async def get_results(run_id: int) -> Optional[Dict[str, Any]]:
    run = await db.get_verification_run(run_id)
    if not run:
        return None
    rows = await db.get_verification_results_for_run(run_id)
    return {
        "run_id": run_id,
        "status": run["status"],
        "not_found": run["not_found"],
        "results": [{k: r.get(k) for k in _RESULT_FIELDS} for r in rows],
    }


async def get_lead_states(lead_ids: List[int]) -> Dict[str, Any]:
    by_id = await db.get_lead_contact_verifications_by_ids([int(x) for x in lead_ids])
    out: Dict[str, Any] = {}
    for lid in lead_ids:
        row = by_id.get(int(lid))
        out[str(lid)] = ({k: row.get(k) for k in _RESULT_FIELDS if k != "business_name"}
                         if row else None)
    return {"verifications": out}
```

- [ ] **Step 6: Run full suite**

Run: `venv/Scripts/python.exe -m pytest -q`
Expected: all green (baseline + all new verification tests).

- [ ] **Step 7: Commit**

```bash
git add backend/verification/engine.py backend/verification/service.py tests/verification/test_verification_run.py
git commit -m "feat(5c): CONTACT_VERIFICATION job handler + service layer"
```

---

## Task 11: Send-path safety regression test

**Files:**
- Test: `tests/verification/test_verification_never_touches_a_send_path.py`

**Interfaces:**
- Consumes: `engine.run_verification_job`, `email_checks.resolve_domain` (mocked)

- [ ] **Step 1: Write the test** (this is the deliverable — it should PASS immediately if the
  engine is clean; if it fails, the engine imports/uses a forbidden path and must be fixed):

```python
"""Verification must never reach an outbound path. Equivalent to
tests/test_campaign_from_search.py::test_handoff_never_touches_a_send_path.
"""
import sys
import hashlib
import pytest
from backend.verification import engine, email_checks

pytestmark = pytest.mark.asyncio


def _boom(*a, **k):
    raise AssertionError("verification reached a send / transport / outbound path")


async def _lead(db, **over):
    d = {"business_name": "Biz", "email": None, "phone": None}
    d.update(over)
    return await db.create_lead(d)


def _hash_row(row) -> str:
    return hashlib.sha256(repr(sorted(dict(row).items())).encode()).hexdigest()


async def test_verification_job_never_touches_a_send_path(clean_db, monkeypatch):
    db = clean_db

    # 1. Raising sentinels on every known outbound entrypoint.
    import backend.email_sender as email_sender
    import backend.whatsapp_sender as whatsapp_sender
    from backend.email_campaigns import senders as ec_senders
    from backend.email_campaigns import n8n_client
    import smtplib
    import aiosmtplib

    monkeypatch.setattr(email_sender, "send_email", _boom, raising=False)
    monkeypatch.setattr(email_sender, "_send_smtp", _boom, raising=False)
    monkeypatch.setattr(whatsapp_sender, "send_whatsapp", _boom, raising=False)
    monkeypatch.setattr(whatsapp_sender, "_send_whatsapp_desktop", _boom, raising=False)
    monkeypatch.setattr(ec_senders, "resolve_transport", _boom, raising=False)
    monkeypatch.setattr(n8n_client, "describe", _boom, raising=False)
    monkeypatch.setattr(smtplib, "SMTP", _boom, raising=False)
    monkeypatch.setattr(aiosmtplib, "send", _boom, raising=False)
    try:
        import backend.scheduler as scheduler
        monkeypatch.setattr(scheduler, "_dispatch_send", _boom, raising=False)
    except Exception:
        pass

    # 2. The verification package must not have imported a sender / pyautogui / smtplib itself.
    banned = ("backend.email_sender", "backend.whatsapp_sender", "pyautogui",
              "backend.email_campaigns.senders", "backend.email_campaigns.n8n_client",
              "backend.scheduler")
    vmods = [m for m in sys.modules if m.startswith("backend.verification")]
    for vm in vmods:
        mod = sys.modules[vm]
        for attr in dir(mod):
            assert not any(b.split(".")[-1] == attr for b in banned), \
                f"{vm} references {attr}"

    # 3. DNS mocked — no real network.
    async def _fake_dns(domain, cache, **kw):
        return ("MX_FOUND", ["mx.%s." % domain])
    monkeypatch.setattr(email_checks, "resolve_domain", _fake_dns)

    # 4. Mixed batch, one lead forced to raise.
    a = await _lead(db, email="owner@acme.com", phone="+1 202 456 1111", country="United States")
    b = await _lead(db, email="info@acme.com")
    c = await _lead(db, email="x@mailinator.com")
    d = await _lead(db, email="x@dead.example")
    e = await _lead(db, email=None, phone="not a phone")
    f = await _lead(db, email=None, phone=None)

    before = {lid: _hash_row(await db.get_lead_by_id(lid)) for lid in (a, b, c, d, e, f)}
    async with db.get_db() as conn:
        camp_runs_before = await conn.fetchval("SELECT COUNT(*) FROM campaign_runs")
        ecr_before = await conn.fetchval("SELECT COUNT(*) FROM email_campaign_runs")

    rid = await db.create_verification_run([a, b, c, d, e, f])
    await engine.run_verification_job({"run_id": rid})   # must not raise / not fire a sentinel

    run = await db.get_verification_run(rid)
    assert run["status"] == "COMPLETED"

    # 5. Nothing outbound happened; leads untouched; no runs created.
    after = {lid: _hash_row(await db.get_lead_by_id(lid)) for lid in (a, b, c, d, e, f)}
    assert after == before, "verification mutated a leads row"
    async with db.get_db() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM campaign_runs") == camp_runs_before
        assert await conn.fetchval("SELECT COUNT(*) FROM email_campaign_runs") == ecr_before
```

- [ ] **Step 2: Run it**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_verification_never_touches_a_send_path.py -q`
Expected: PASS. If it FAILS on the import/attr check, remove the offending import from the
verification package (it must not need any sender). If a sentinel fires, trace and remove that
call path — do not weaken the test.

- [ ] **Step 3: Commit**

```bash
git add tests/verification/test_verification_never_touches_a_send_path.py
git commit -m "test(5c): prove verification never touches a send/outbound path"
```

---

## Task 12: Router + feature gate + registration

**Files:**
- Create: `backend/routers/verification.py`
- Modify: `backend/main.py:34` (import) and `:155` (register, after `research_agent_router`)
- Test: `tests/verification/test_verification_router.py`, `tests/verification/test_feature_flag_off.py`

**Interfaces:**
- Consumes: `service.*`, `rate_limit.limiter`
- Produces: routes under `/api/verification` (design §7).

- [ ] **Step 1: Write the failing tests** — `tests/verification/test_verification_router.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


class _FakeQueue:
    def __init__(self, full=False): self.full = full; self.enqueued = []
    def enqueue_nowait(self, job_type, payload, handler):
        if self.full:
            return False
        self.enqueued.append((job_type, payload))
        return True


def _client():
    from backend.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def enabled(clean_db):
    await clean_db.upsert_setting("contact_verification_enabled", "true")
    return clean_db


@pytest.fixture(autouse=True)
def _fake_queue(monkeypatch):
    import backend.routers.verification as vr
    monkeypatch.setattr(vr, "get_queue", lambda: _FakeQueue())


async def _lead(db, **over):
    d = {"business_name": "Biz", "email": "a@acme.com"}
    d.update(over)
    return await db.create_lead(d)


async def test_start_run_201(enabled):
    lid = await _lead(enabled)
    async with _client() as c:
        r = await c.post("/api/verification/runs", json={"lead_ids": [lid, lid]})
    assert r.status_code == 201
    body = r.json()
    assert body["requested_count"] == 1
    assert body["status"] == "QUEUED"


async def test_empty_lead_ids_422(enabled):
    async with _client() as c:
        r = await c.post("/api/verification/runs", json={"lead_ids": []})
    assert r.status_code == 422


async def test_too_many_lead_ids_422(enabled):
    async with _client() as c:
        r = await c.post("/api/verification/runs", json={"lead_ids": list(range(1, 2002))})
    assert r.status_code == 422


async def test_get_run_and_results_and_active(enabled):
    db = enabled
    lid = await _lead(db)
    rid = await db.create_verification_run([lid])
    await db.upsert_lead_contact_verification(lid, rid, {"status": "VALID", "email_status": "VALID",
                                                        "phone_status": "MISSING", "raw_json": "{}"})
    await db.update_verification_run(rid, {"status": "COMPLETED"})
    async with _client() as c:
        s = await c.get(f"/api/verification/runs/{rid}")
        res = await c.get(f"/api/verification/runs/{rid}/results")
        act = await c.get("/api/verification/runs/active")
    assert s.status_code == 200 and s.json()["status"] == "COMPLETED"
    assert res.json()["results"][0]["status"] == "VALID"
    assert act.json()["id"] == rid


async def test_unknown_run_404(enabled):
    async with _client() as c:
        r = await c.get("/api/verification/runs/999999")
    assert r.status_code == 404


async def test_cancel(enabled):
    db = enabled
    lid = await _lead(db)
    rid = await db.create_verification_run([lid])
    await db.update_verification_run(rid, {"status": "RUNNING"})
    async with _client() as c:
        r = await c.post(f"/api/verification/runs/{rid}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "CANCEL_REQUESTED"


async def test_lead_states_batch(enabled):
    db = enabled
    a = await _lead(db)
    b = await _lead(db, email="b@b.com")
    await db.upsert_lead_contact_verification(a, None, {"status": "RISKY", "raw_json": "{}"})
    async with _client() as c:
        r = await c.get(f"/api/verification/leads?lead_ids={a},{b}")
    body = r.json()["verifications"]
    assert body[str(a)]["status"] == "RISKY"
    assert body[str(b)] is None


async def test_lead_states_too_many_422(enabled):
    ids = ",".join(str(i) for i in range(1, 502))
    async with _client() as c:
        r = await c.get(f"/api/verification/leads?lead_ids={ids}")
    assert r.status_code == 422
```

And `tests/verification/test_feature_flag_off.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


def _client():
    from backend.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_disabled_returns_503_and_creates_nothing(clean_db, monkeypatch):
    db = clean_db
    lid = await db.create_lead({"business_name": "Acme", "email": "a@acme.com"})

    import backend.routers.verification as vr
    enqueued = []
    class _Q:
        def enqueue_nowait(self, *a, **k): enqueued.append(a); return True
    monkeypatch.setattr(vr, "get_queue", lambda: _Q())

    async with _client() as c:
        r = await c.post("/api/verification/runs", json={"lead_ids": [lid]})
        s = await c.get("/api/verification/runs/active")
        g = await c.get(f"/api/verification/leads?lead_ids={lid}")
    assert r.status_code == 503
    assert s.status_code == 503
    assert g.status_code == 503
    assert enqueued == []
    async with db.get_db() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM verification_runs") == 0
        assert await conn.fetchval("SELECT COUNT(*) FROM lead_contact_verification") == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_verification_router.py tests/verification/test_feature_flag_off.py -q`
Expected: FAIL — 404s everywhere (router not registered).

- [ ] **Step 3: Implement the router** — `backend/routers/verification.py`:

```python
"""routers/verification.py — Contact Verification (Checkpoint 5C) REST API.

Behind the app session dependency (main.py) AND contact_verification_enabled
(default OFF -> 503). No business logic here — delegates to verification.service.
This router CANNOT send anything: it only creates verification runs + reads results.
"""
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..queue_worker import get_queue  # noqa: F401  (imported so tests can monkeypatch here)
from ..rate_limit import limiter
from ..verification import service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/verification", tags=["verification"])


async def require_feature_enabled() -> None:
    if not await service.is_feature_enabled():
        raise HTTPException(
            status_code=503,
            detail="Contact Verification is not enabled on this instance "
                   "(app_settings 'contact_verification_enabled').",
        )


_gated = [Depends(require_feature_enabled)]


class VerifySelectedRequest(BaseModel):
    lead_ids: List[int] = Field(min_length=1, max_length=2000)


@router.post("/runs", dependencies=_gated, status_code=201)
@limiter.limit("10/minute")
async def start_verification_run(request: Request, payload: VerifySelectedRequest):
    try:
        return await service.start_run(payload.lead_ids)
    except service.VerificationQueueError as exc:
        raise HTTPException(503, str(exc))


@router.get("/runs/active", dependencies=_gated)
async def active_run():
    return await service.get_active_run()


@router.get("/runs/{run_id}", dependencies=_gated)
async def get_run(run_id: int):
    run = await service.get_run_public(run_id)
    if not run:
        raise HTTPException(404, "Verification run not found")
    return run


@router.get("/runs/{run_id}/results", dependencies=_gated)
async def get_run_results(run_id: int):
    res = await service.get_results(run_id)
    if res is None:
        raise HTTPException(404, "Verification run not found")
    return res


@router.post("/runs/{run_id}/cancel", dependencies=_gated)
async def cancel_run(run_id: int):
    run = await service.request_cancel(run_id)
    if run is None:
        raise HTTPException(404, "Verification run not found")
    return run


@router.get("/leads", dependencies=_gated)
async def lead_states(lead_ids: str = Query(...)):
    try:
        ids = [int(x) for x in lead_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(422, "lead_ids must be comma-separated integers")
    if not ids or len(ids) > 500:
        raise HTTPException(422, "lead_ids must contain 1..500 ids")
    return await service.get_lead_states(ids)
```

**Note on `get_queue` import:** `service.start_run` calls `get_queue()` from `queue_worker`. The
router tests monkeypatch `backend.routers.verification.get_queue`; to make that effective,
`service.start_run` must read the queue via a module attr the test patches. Simplest: in
`service.py` change `from ..queue_worker import get_queue` to `from .. import queue_worker` and
call `queue_worker.get_queue()`, and have the router test monkeypatch
`backend.verification.service.queue_worker.get_queue` — OR keep the router-level fake by having
the router pass the queue in. **Chosen approach:** monkeypatch at the service module in tests.
Update the two router tests' `_fake_queue` fixture to
`monkeypatch.setattr("backend.verification.service.get_queue", lambda: _FakeQueue())` and drop
the unused import note. (Adjust Step 1 fixtures accordingly before running.)

- [ ] **Step 4: Register in main.py**

`backend/main.py` line ~34, add to the router imports:
```python
from .routers import verification as verification_router
```
Line ~155, after the `research_agent_router` line:
```python
app.include_router(verification_router.router, dependencies=_authed)  # feature-flagged (contact_verification_enabled, default OFF)
```

- [ ] **Step 5: Run to verify pass**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_verification_router.py tests/verification/test_feature_flag_off.py -q`
Expected: PASS.

- [ ] **Step 6: Full suite**

Run: `venv/Scripts/python.exe -m pytest -q`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add backend/routers/verification.py backend/main.py tests/verification/test_verification_router.py tests/verification/test_feature_flag_off.py
git commit -m "feat(5c): /api/verification router + feature gate"
```

---

## Task 13: Frontend — API client + badge + drawer components

**Files:**
- Modify: `frontend/src/api/client.js` (after `emailSendersApi`, ~line 289)
- Create: `frontend/src/lib/verificationBadges.js`, `frontend/src/components/lead-search/VerificationBadge.jsx`, `frontend/src/components/lead-search/VerificationDetailDrawer.jsx`

**Interfaces:**
- Produces: `verificationApi` (`startRun`, `runStatus`, `runResults`, `activeRun`, `cancelRun`, `leadStates`), `<VerificationBadge status running onClick />`, `<VerificationDetailDrawer verification onClose />`, `verificationBadges.js` default export map.

- [ ] **Step 1: Add `verificationApi`** to `frontend/src/api/client.js`:

```js
// Contact Verification (Checkpoint 5C) — deterministic email/phone checks.
// Feature-flagged (contact_verification_enabled). 503 while the flag is off.
export const verificationApi = {
  startRun:   (payload) => api.post('/verification/runs', payload).then((r) => r.data),
  runStatus:  (id)      => api.get(`/verification/runs/${id}`).then((r) => r.data),
  runResults: (id)      => api.get(`/verification/runs/${id}/results`).then((r) => r.data),
  activeRun:  ()        => api.get('/verification/runs/active').then((r) => r.data),
  cancelRun:  (id)      => api.post(`/verification/runs/${id}/cancel`).then((r) => r.data),
  leadStates: (ids)     => api.get('/verification/leads', { params: { lead_ids: ids.join(',') } }).then((r) => r.data),
}
```

- [ ] **Step 2: Create `frontend/src/lib/verificationBadges.js`**:

```js
import { CheckCircle, AlertTriangle, XCircle, MinusCircle } from 'lucide-react'

// status -> visual treatment. UNVERIFIED covers "no result yet" and "check errored".
const MAP = {
  VALID:      { label: 'Valid',      className: 'text-emerald-300 bg-emerald-500/10 border-emerald-500/30', Icon: CheckCircle },
  RISKY:      { label: 'Risky',      className: 'text-amber-300 bg-amber-500/10 border-amber-500/30',       Icon: AlertTriangle },
  INVALID:    { label: 'Invalid',    className: 'text-red-300 bg-red-500/10 border-red-500/30',             Icon: XCircle },
  UNVERIFIED: { label: 'Unverified', className: 'text-slate-400 bg-slate-500/10 border-slate-600/40',       Icon: MinusCircle },
}

export function badgeFor(status) {
  return MAP[status] || MAP.UNVERIFIED
}

export default MAP
```

- [ ] **Step 3: Create `frontend/src/components/lead-search/VerificationBadge.jsx`**:

```jsx
import { Loader2 } from 'lucide-react'
import { badgeFor } from '../../lib/verificationBadges'

// `verification`: the per-lead row from /verification/leads or run results (or undefined).
// `running`: a verify run for this results set is in flight -> show a spinner for unresolved rows.
export default function VerificationBadge({ verification, running, onClick }) {
  if (!verification && running) {
    return <Loader2 size={13} className="animate-spin text-slate-500" aria-label="Verifying" />
  }
  const status = verification?.status || 'UNVERIFIED'
  const { label, className, Icon } = badgeFor(status)
  const stale = verification?.checked_at &&
    (Date.now() - new Date(verification.checked_at).getTime()) > 14 * 864e5
  return (
    <button
      type="button"
      onClick={onClick}
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${className}`}
      title={verification ? 'View verification detail' : 'Not verified yet'}
    >
      <Icon size={11} />
      {label}{stale ? ' ·' : ''}
    </button>
  )
}
```

- [ ] **Step 4: Create `frontend/src/components/lead-search/VerificationDetailDrawer.jsx`**:

```jsx
import { X } from 'lucide-react'

function Row({ k, v }) {
  return (
    <div className="flex justify-between gap-4 py-1 text-xs">
      <span className="text-slate-500">{k}</span>
      <span className="text-slate-200 text-right">{v ?? '—'}</span>
    </div>
  )
}

const DNS_LABEL = {
  MX_FOUND: 'MX record found', A_ONLY: 'A record only (no MX)', NO_RECORDS: 'No MX or A record',
  TIMEOUT: 'DNS lookup timed out', DNS_ERROR: 'DNS lookup failed', SKIPPED: 'Not checked',
}

export default function VerificationDetailDrawer({ verification, businessName, onClose }) {
  const v = verification || {}
  let mx = []
  try { mx = JSON.parse(v.email_mx_hosts || '[]') } catch { /* ignore */ }
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50" onClick={onClose}>
      <div className="h-full w-full max-w-sm overflow-y-auto bg-slate-900 border-l border-slate-800 p-5 space-y-4"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-100">Verification · {businessName || `lead ${v.lead_id}`}</h3>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-300"><X size={16} /></button>
        </div>

        <div className="rounded-lg border border-slate-800 p-3">
          <p className="text-[11px] font-bold uppercase tracking-wide text-slate-500 mb-1">Email</p>
          <Row k="Address" v={v.email} />
          <Row k="Overall" v={v.email_status} />
          <Row k="Syntax valid" v={v.email_syntax_ok ? 'yes' : 'no'} />
          <Row k="Role account" v={v.email_is_role ? 'yes (info@, sales@…)' : 'no'} />
          <Row k="Disposable domain" v={v.email_is_disposable ? 'yes' : 'no'} />
          <Row k="Domain" v={v.email_domain} />
          <Row k="DNS" v={DNS_LABEL[v.email_dns_result] || v.email_dns_result} />
          {mx.length > 0 && <Row k="MX hosts" v={mx.join(', ')} />}
        </div>

        <div className="rounded-lg border border-slate-800 p-3">
          <p className="text-[11px] font-bold uppercase tracking-wide text-slate-500 mb-1">Phone</p>
          <Row k="Overall" v={v.phone_status} />
          <Row k="E.164" v={v.phone_e164} />
          <Row k="Region" v={v.phone_region} />
          <Row k="Line type" v={v.phone_line_type} />
        </div>

        <div className="rounded-lg border border-slate-800 p-3">
          <p className="text-[11px] font-bold uppercase tracking-wide text-slate-500 mb-1">
            WhatsApp capability hint — format-based only
          </p>
          <Row k="Hint" v={v.whatsapp_hint} />
          <p className="mt-1 text-[10px] text-slate-500">
            Inferred from the phone number&apos;s line type. This is not a check that the number has WhatsApp.
          </p>
        </div>

        <div className="text-[10px] text-slate-600">
          Checked {v.checked_at || '—'}{v.duration_ms != null ? ` · ${v.duration_ms} ms` : ''}
          {v.error ? ` · error: ${v.error}` : ''}
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Build**

Run: `cd frontend && npm run build`
Expected: clean (components compile; not yet wired anywhere).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.js frontend/src/lib/verificationBadges.js frontend/src/components/lead-search/VerificationBadge.jsx frontend/src/components/lead-search/VerificationDetailDrawer.jsx
git commit -m "feat(5c): verificationApi + badge + detail drawer components"
```

---

## Task 14: Frontend — wire "Verify Selected" on Lead Search

**Files:**
- Modify: `frontend/src/pages/LeadSearch.jsx`

**Interfaces:**
- Consumes: `verificationApi`, `settingsApi.getAll`, `VerificationBadge`, `VerificationDetailDrawer`

- [ ] **Step 1: Add imports + constants**

At the top of `LeadSearch.jsx` add:
```jsx
import { verificationApi, settingsApi } from '../api/client'   // extend existing import line
import VerificationBadge from '../components/lead-search/VerificationBadge'
import VerificationDetailDrawer from '../components/lead-search/VerificationDetailDrawer'
```
Near the other status sets:
```jsx
const VERIFY_ACTIVE = new Set(['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'])
const LS_VERIFY_KEY = 'autolead_verification_run'
function loadVerifyRunId() { try { return localStorage.getItem(LS_VERIFY_KEY) || null } catch { return null } }
function storeVerifyRunId(id) { try { id ? localStorage.setItem(LS_VERIFY_KEY, String(id)) : localStorage.removeItem(LS_VERIFY_KEY) } catch {} }
```

- [ ] **Step 2: Add state + feature flag + queries** inside `LeadSearch()`:

```jsx
const { data: appSettings } = useQuery({ queryKey: ['settings'], queryFn: settingsApi.getAll })
const verifyEnabled = appSettings?.contact_verification_enabled === 'true'

const [verifyRunId, setVerifyRunId] = useState(loadVerifyRunId)
const [drawerLeadId, setDrawerLeadId] = useState(null)

const verifyStatusQuery = useQuery({
  queryKey: ['verify-status', verifyRunId],
  queryFn: () => verificationApi.runStatus(verifyRunId),
  enabled: !!verifyRunId,
  retry: false,
  refetchInterval: (q) => (VERIFY_ACTIVE.has(q.state.data?.status) ? 1500 : false),
  refetchIntervalInBackground: true,
})
const verifyRun = verifyStatusQuery.data
const verifyActive = !!verifyRun && VERIFY_ACTIVE.has(verifyRun.status)

useEffect(() => {
  if (verifyStatusQuery.error?.response?.status === 404) { storeVerifyRunId(null); setVerifyRunId(null) }
}, [verifyStatusQuery.error])

// reconnect on mount
useEffect(() => {
  if (verifyRunId || !verifyEnabled) return
  let cancelled = false
  verificationApi.activeRun().then((run) => {
    if (!cancelled && run?.id) { setVerifyRunId(String(run.id)); storeVerifyRunId(run.id) }
  }).catch(() => {})
  return () => { cancelled = true }
}, [verifyRunId, verifyEnabled])

const verifyResultsQuery = useQuery({
  queryKey: ['verify-results', verifyRunId],
  queryFn: () => verificationApi.runResults(verifyRunId),
  enabled: !!verifyRunId && !!verifyRun && !VERIFY_ACTIVE.has(verifyRun.status),
})

// baseline states for leads verified in an earlier run
const visibleIds = leads.map((l) => l.id)
const leadStatesQuery = useQuery({
  queryKey: ['verify-leadstates', verifyEnabled, visibleIds.join(',')],
  queryFn: () => verificationApi.leadStates(visibleIds),
  enabled: verifyEnabled && visibleIds.length > 0,
})

const verificationByLeadId = useMemo(() => {
  const m = {}
  const base = leadStatesQuery.data?.verifications || {}
  Object.entries(base).forEach(([k, v]) => { if (v) m[Number(k)] = v })
  ;(verifyResultsQuery.data?.results || []).forEach((r) => { m[r.lead_id] = r })
  return m
}, [leadStatesQuery.data, verifyResultsQuery.data])

const enableVerifyMut = useMutation({
  mutationFn: () => settingsApi.update('contact_verification_enabled', 'true'),
  onSuccess: () => { toast.success('Contact Verification enabled'); queryClient.invalidateQueries({ queryKey: ['settings'] }) },
})

const verifyMut = useMutation({
  mutationFn: () => verificationApi.startRun({ lead_ids: [...selected] }),
  onSuccess: (data) => { setVerifyRunId(String(data.run_id)); storeVerifyRunId(data.run_id) },
  onError: (err) => toast.error(/503|not enabled/i.test(err?.message || '')
    ? 'Enable Contact Verification first.' : (err?.response?.data?.detail || err.message || 'Could not start verification')),
})

function handleVerify() {
  if (selected.size > 100 && !window.confirm(`Verify ${selected.size} leads? Runs in the background.`)) return
  verifyMut.mutate()
}
const cancelVerifyMut = useMutation({ mutationFn: () => verificationApi.cancelRun(verifyRunId) })
```

- [ ] **Step 3: Update `SelectionActionBar`** — replace the disabled "Verify Selected" button:

```jsx
function SelectionActionBar({ count, onClear, onSend, onVerify, verifyEnabled, verifyBusy, onEnableVerify }) {
  if (count === 0) return null
  return (
    <div className="sticky top-2 z-10 flex flex-wrap items-center gap-2 rounded-xl border border-brand-500/40 bg-slate-900/95 px-4 py-2.5 shadow-lg">
      <span className="text-xs font-semibold text-brand-300">{count} selected</span>
      <button className="btn-secondary text-xs" onClick={onClear}>Clear</button>
      <div className="flex-1" />
      {verifyEnabled ? (
        <button className="btn-secondary text-xs" onClick={onVerify} disabled={verifyBusy}>
          <ShieldCheck size={12} /> Verify Selected
        </button>
      ) : (
        <button className="btn-secondary text-xs opacity-60" onClick={onEnableVerify}
                title="Contact Verification is off — click to enable">
          <ShieldCheck size={12} /> Verify Selected (enable)
        </button>
      )}
      <button className="btn-secondary text-xs opacity-50 cursor-not-allowed" disabled title="Coming in a later step">
        <FileText size={12} /> Generate Reports
      </button>
      <button className="btn-primary text-xs" onClick={onSend}>
        <SendIcon size={12} /> Send to Email Campaign
      </button>
    </div>
  )
}
```

Wire it where `<SelectionActionBar ... />` is rendered:
```jsx
<SelectionActionBar
  count={selected.size}
  onClear={clearSelection}
  onSend={() => setShowSendModal(true)}
  onVerify={handleVerify}
  verifyEnabled={verifyEnabled}
  verifyBusy={verifyActive || verifyMut.isPending}
  onEnableVerify={() => enableVerifyMut.mutate()}
/>
```

**Do not clear selection after verify** — selection only clears on a new search / "Clear".

- [ ] **Step 4: Add the progress panel** — directly under the action bar, inside the
  `leads.length > 0` block:

```jsx
{verifyActive && verifyRun && (
  <div className="flex items-center gap-3 rounded-xl border border-slate-800 bg-slate-900/40 px-4 py-2.5">
    <Loader2 size={14} className="animate-spin text-brand-400" />
    <span className="text-xs text-slate-300">
      Verifying… {verifyRun.processed_count} / {verifyRun.requested_count}
    </span>
    <button className="btn-secondary text-[11px] ml-auto" onClick={() => cancelVerifyMut.mutate()}>Cancel</button>
  </div>
)}
{verifyRun?.status === 'COMPLETED' && (verifyRun.risky_count + verifyRun.invalid_count + verifyRun.error_count) >= 0 && verifyResultsQuery.data && (
  <p className="text-[11px] text-slate-500">
    Verified {verifyRun.processed_count}: {verifyRun.valid_count} valid · {verifyRun.risky_count} risky · {verifyRun.invalid_count} invalid
    {verifyRun.error_count ? ` · ${verifyRun.error_count} errored` : ''}
  </p>
)}
```

- [ ] **Step 5: Add the badge column to `ResultsTable`**

Add a `verificationByLeadId`, `verifyActive`, `onBadgeClick` prop. In `<thead>` add
`<th className="px-4 py-2.5">Verification</th>` after Score. In each row, after the Score `<td>`:

```jsx
<td className="px-4 py-3">
  <VerificationBadge
    verification={verificationByLeadId[lead.id]}
    running={verifyActive}
    onClick={() => onBadgeClick(lead.id)}
  />
</td>
```

Update the `<ResultsTable ... />` call to pass `verificationByLeadId={verificationByLeadId}`,
`verifyActive={verifyActive}`, `onBadgeClick={setDrawerLeadId}`. Also update the loading skeleton
`cols={5}` → `cols={6}`.

- [ ] **Step 6: Mount the drawer** — near the `SendToCampaignModal` mount:

```jsx
{drawerLeadId != null && (
  <VerificationDetailDrawer
    verification={verificationByLeadId[drawerLeadId]}
    businessName={leads.find((l) => l.id === drawerLeadId)?.business_name}
    onClose={() => setDrawerLeadId(null)}
  />
)}
```

- [ ] **Step 7: Build**

Run: `cd frontend && npm run build`
Expected: clean.

- [ ] **Step 8: Manual browser check** (backend + frontend running, flag ON via Settings):
  select leads → "Verify Selected" → progress counts → badges render → click badge → drawer
  shows checks incl. the WhatsApp hint label → selection preserved → refresh mid-run reconnects.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/pages/LeadSearch.jsx
git commit -m "feat(5c): wire Verify Selected on Lead Search (job, poll, badges, drawer)"
```

---

## Task 15: Frontend — Leads page column + Settings toggle

**Files:**
- Modify: `frontend/src/pages/Leads.jsx`, `frontend/src/pages/Settings.jsx`

- [ ] **Step 1: Settings toggle** — in `Settings.jsx`, add to `DEFAULTS`:
```js
  contact_verification_enabled: 'false',
```
In the "Engine & Limits" `SectionCard` (line ~625), add:
```jsx
<Toggle
  label="Contact Verification"
  description="Deterministic email (syntax / role / disposable / MX-DNS) + phone checks on Lead Search results. No email is sent."
  checked={values.contact_verification_enabled === 'true'}
  onChange={() => set('contact_verification_enabled', values.contact_verification_enabled === 'true' ? 'false' : 'true')}
/>
```

- [ ] **Step 2: Leads page column** — in `Leads.jsx`:
  - import `verificationApi`, `settingsApi`, `VerificationBadge`, `VerificationDetailDrawer`.
  - `const { data: s } = useQuery({ queryKey:['settings'], queryFn: settingsApi.getAll })`
    → `const verifyEnabled = s?.contact_verification_enabled === 'true'`.
  - `const pageIds = (leads || []).map(l => l.id)` (use whatever the page's lead array is named).
  - `const { data: vstate } = useQuery({ queryKey:['verify-leadstates','leads', pageIds.join(',')], queryFn: () => verificationApi.leadStates(pageIds), enabled: verifyEnabled && pageIds.length > 0 })`
  - `const vById = vstate?.verifications || {}`
  - When `verifyEnabled`, render an extra `<th>Verification</th>` and per row
    `<td><VerificationBadge verification={vById[String(lead.id)]} onClick={() => setDrawerLead(lead.id)} /></td>`.
  - Mount `VerificationDetailDrawer` when `drawerLead != null`.
  - **No "Verify Selected" / no selection on this page.**

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: clean.

- [ ] **Step 4: Manual check** — `/leads` with flag on shows the column read-only; drawer opens;
  with flag off the column is absent.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Leads.jsx frontend/src/pages/Settings.jsx
git commit -m "feat(5c): verification badge on Leads page + Settings toggle"
```

---

## Task 16: 5B handoff — fold verification snapshot into `raw_json`

**Files:**
- Modify: `backend/email_campaigns/service.py` — `_lead_row_to_campaign_row` (line ~88) and `add_leads_from_db` (line ~317)
- Test: `tests/verification/test_5b_handoff_with_verification.py`

**Interfaces:**
- Consumes: `db.get_lead_contact_verifications_by_ids`, `config.contact_verification_enabled` (via a local `_verification_enabled()` mirroring the existing `_feature_enabled`)
- Produces: `_lead_row_to_campaign_row(campaign_id, lead, verification=None)` — when `verification` is a dict, adds `raw["_handoff"]["verification"] = {status, email_status, phone_status, whatsapp_hint, checked_at}`.

- [ ] **Step 1: Write the failing test** — `tests/verification/test_5b_handoff_with_verification.py`:

```python
import json
import pytest
from backend.email_campaigns.service import _lead_row_to_campaign_row, get_email_campaign_service

pytestmark = pytest.mark.asyncio


def test_helper_without_verification_has_no_verification_key():
    row = _lead_row_to_campaign_row(1, {"id": 5, "business_name": "Acme", "email": "a@acme.com"})
    raw = json.loads(row["raw_json"])
    assert "verification" not in raw["_handoff"]


def test_helper_with_verification_folds_snapshot():
    v = {"status": "VALID", "email_status": "VALID", "phone_status": "MISSING",
         "whatsapp_hint": "unknown", "checked_at": "2026-09-01T00:00:00"}
    row = _lead_row_to_campaign_row(1, {"id": 5, "business_name": "Acme", "email": "a@acme.com"}, verification=v)
    raw = json.loads(row["raw_json"])
    assert raw["_handoff"]["verification"] == {
        "status": "VALID", "email_status": "VALID", "phone_status": "MISSING",
        "whatsapp_hint": "unknown", "checked_at": "2026-09-01T00:00:00",
    }


@pytest.fixture
async def enabled(clean_db):
    await clean_db.upsert_setting("email_campaigns_enabled", "true")
    await clean_db.upsert_setting("contact_verification_enabled", "true")
    return clean_db


async def test_add_leads_from_db_includes_snapshot_when_flag_on(enabled):
    db = enabled
    svc = get_email_campaign_service()
    lid = await db.create_lead({"business_name": "Acme", "email": "a@acme.com"})
    await db.upsert_lead_contact_verification(lid, None, {
        "status": "RISKY", "email_status": "RISKY", "phone_status": "MISSING",
        "whatsapp_hint": "unknown", "raw_json": "{}",
    })
    camp = await svc.create_campaign({"name": "H"})
    await svc.add_leads_from_db(camp["id"], [lid])
    row = (await db.get_email_campaign_leads(camp["id"]))[0]
    assert json.loads(row["raw_json"])["_handoff"]["verification"]["status"] == "RISKY"


async def test_add_leads_from_db_no_snapshot_when_flag_off(clean_db):
    db = clean_db
    await db.upsert_setting("email_campaigns_enabled", "true")   # campaigns on, verification OFF
    svc = get_email_campaign_service()
    lid = await db.create_lead({"business_name": "Acme", "email": "a@acme.com"})
    await db.upsert_lead_contact_verification(lid, None, {"status": "VALID", "raw_json": "{}"})
    camp = await svc.create_campaign({"name": "H"})
    await svc.add_leads_from_db(camp["id"], [lid])
    row = (await db.get_email_campaign_leads(camp["id"]))[0]
    assert "verification" not in json.loads(row["raw_json"])["_handoff"]
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_5b_handoff_with_verification.py -q`
Expected: FAIL — `_lead_row_to_campaign_row() got an unexpected keyword argument 'verification'`.

- [ ] **Step 3: Implement** — in `backend/email_campaigns/service.py`:

Add near the other feature-flag helper:
```python
async def _verification_enabled() -> bool:
    if get_settings().contact_verification_enabled:
        return True
    return _truthy(await db.get_setting("contact_verification_enabled"))
```

Change `_lead_row_to_campaign_row` signature and body:
```python
def _lead_row_to_campaign_row(campaign_id: int, lead: Dict[str, Any],
                              verification: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ...
    raw = dict(lead)
    raw["_handoff"] = {
        "source": "lead_search",
        "lead_id": lead_id,
        "added_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }
    if verification:
        raw["_handoff"]["verification"] = {
            "status":       verification.get("status"),
            "email_status": verification.get("email_status"),
            "phone_status": verification.get("phone_status"),
            "whatsapp_hint": verification.get("whatsapp_hint"),
            "checked_at":   verification.get("checked_at"),
        }
    ...
```

In `add_leads_from_db`, after `leads_map = await db.get_leads_by_ids(ordered_ids)`:
```python
        v_by_id: Dict[int, Any] = {}
        if await _verification_enabled():
            v_by_id = await db.get_lead_contact_verifications_by_ids(ordered_ids)

        rows = [
            _lead_row_to_campaign_row(campaign_id, leads_map[lid], v_by_id.get(lid))
            for lid in ordered_ids if lid in leads_map
        ]
```

- [ ] **Step 4: Run to verify pass**

Run: `venv/Scripts/python.exe -m pytest tests/verification/test_5b_handoff_with_verification.py -q`
Expected: PASS.

- [ ] **Step 5: Re-run all 5B tests — no regression**

Run: `venv/Scripts/python.exe -m pytest tests/test_campaign_from_search.py -q`
Expected: `21 passed` (unchanged).

- [ ] **Step 6: Commit**

```bash
git add backend/email_campaigns/service.py tests/verification/test_5b_handoff_with_verification.py
git commit -m "feat(5c): fold verification snapshot into the 5B campaign handoff (flag-gated)"
```

---

## Task 17: Full regression + browser E2E + restart + git audit

**Files:** none (verification only)

- [ ] **Step 1: Full backend suite**

Run: `venv/Scripts/python.exe -m pytest -q`
Expected: all green; count = 698 baseline + every new `tests/verification/` test, 0 failures.
Record the exact number.

- [ ] **Step 2: Targeted regression**

Run: `venv/Scripts/python.exe -m pytest tests/test_campaign_from_search.py tests/test_send_guards.py tests/test_followup_engine.py tests/test_replies_router.py tests/test_reply_detector.py tests/test_marketing_router.py -q`
Expected: all green, unchanged counts.

- [ ] **Step 3: Frontend build**

Run: `cd frontend && npm run build`
Expected: clean, no new warnings vs baseline.

- [ ] **Step 4: Schema-integrity check**

Run:
```
venv/Scripts/python.exe -c "import sqlite3; c=sqlite3.connect(r'backend/data/leads.db'); \
print('leads cols:', len([r for r in c.execute('PRAGMA table_info(leads)')])); \
print('ecl cols:', len([r for r in c.execute('PRAGMA table_info(email_campaign_leads)')]))"
```
Expected: unchanged from pre-5C (leads unchanged, email_campaign_leads unchanged).

- [ ] **Step 5: Browser E2E** (backend on :8001, frontend on :5173; use the browser tool)

Walk design §12.4 steps 1–11:
1. Flag OFF → "Verify Selected (enable)" affordance; no `/api/verification/*` call on load.
2. Enable via Settings toggle (or the inline affordance).
3. Quick Search → select 3–5 → Verify Selected → progress → badges (Valid/Risky/Invalid).
4. Click a badge → drawer shows email checks, DNS result, MX hosts, phone line type,
   **"WhatsApp capability hint — format-based only"**.
5. Start a run → refresh mid-run → reconnects, progress resumes.
6. Cancel a run → `Cancelled`, partial badges.
7. `/leads` → read-only badge column + drawer.
8. Hand off a `Valid` lead to a campaign (5B) → campaign `DRAFT`; check its
   `email_campaign_leads.raw_json._handoff.verification` snapshot in the DB.
9. Network tab: only `/api/verification/*` (+ existing) — **no** send-path request.
10. Backend log during the run: **no** `send_email` / `send_whatsapp` / `smtp` / `pyautogui`.
11. Stop + restart both servers → `/api/health` ok → repeat one verify smoke test.

Record PASS/FAIL per step with evidence.

- [ ] **Step 6: Send-path log audit**

Run: `grep -icE "send_email|send_whatsapp|smtp|pyautogui|_dispatch_send" <backend log>`
Expected: `0`.

- [ ] **Step 7: Git audit**

Run: `git status` and `git diff --stat`
Expected: only the §"File Structure" files changed/created; the pre-existing uncommitted
Checkpoint 3A–4 working-tree changes untouched; no stray files.

- [ ] **Step 8: Final commit (docs)**

```bash
git add docs/superpowers/specs/2026-09-01-lead-contact-verification-design.md docs/superpowers/plans/2026-09-01-lead-contact-verification.md
git commit -m "docs(5c): contact verification design + implementation plan"
```

(If the design/plan docs were already committed earlier, skip.)

---

## Self-Review

**1. Spec coverage**

| Spec section | Task(s) |
|---|---|
| §1 Scope / non-goals | enforced across all tasks (Global Constraints) |
| §2 User flow | Tasks 14, 15 |
| §3 Architecture / units | Tasks 4–12 (units), Task 14 (frontend flow) |
| §4 Verification rules (email/DNS/phone) | Tasks 5, 6, 7, 8 |
| §4.4 named combinations | Task 4 `test_named_combinations_from_brief` + Task 9 |
| §5 Status calculation (4 states) | Task 4 (`roll_up`, `LEAD_STATUSES`) |
| §4.5 honesty constraints | Task 7 (distinct fields), Task 8 (hint wording), Task 13 (drawer copy) |
| §6 DB schema (both tables, fields, cascade, UNIQUE) | Tasks 2, 3 |
| §7 API contract (6 endpoints, 422/404/503) | Task 12 |
| §8 Background job (lifecycle, isolation, cancel, retry) | Task 10 |
| §9 Frontend (button, poll, progress, badge, drawer, Leads, Settings) | Tasks 13, 14, 15 |
| §10 Security (DNS-only, no keys, log discipline) | Task 6 (no HTTP/SMTP), Task 10 (log types only) |
| §11 Safety (no send path) | Task 11 (+ Task 9/10 construction) |
| §12 Testing (unit/integration/router/flag-off/safety/5B/regression/E2E) | Tasks 4–12, 16, 17 |
| §13 Migration & deps | Tasks 1, 2 |
| §14 File-level plan | "File Structure" section + per-task Files blocks |
| §15 Regression protection | Task 16 (5B), Task 17 (schema-integrity, full suite, git audit) |
| §16 Acceptance criteria | Task 17 walks them |
| §17 Risks | mitigations baked into Tasks 6 (timeout/cache), 8 (hint informational), 10 (isolation), 14 (`refetchIntervalInBackground`) |
| §18 Implementation order | this task numbering **is** §18's order |

**2. Placeholder scan** — no "TBD"/"handle edge cases"/"similar to Task N"; every code step has real code.

**3. Type consistency** — `roll_up`/`email_status_from` signatures match between Task 4 def and Tasks 9/16 use. `check_email` returns the exact `email_*` keys the DB `_LCV_WRITABLE` tuple (Task 3) and `_RESULT_FIELDS` (Task 10) read. `run_verification_job` payload `{"run_id": int}` consistent between Task 10, Task 11, Task 12 service. `verificationApi` method names consistent between Task 13 and Task 14. DB helper names (`create_verification_run`, `bump_verification_run`, `upsert_lead_contact_verification`, `get_verification_results_for_run`, `get_lead_contact_verifications_by_ids`, `get_latest_verification_run`, `update_verification_run`, `get_verification_run`) consistent across Tasks 3, 10, 12, 16.

**Known adjustment for the implementer:** Task 12 Step 3 notes the queue-monkeypatch seam — in
`service.py` import `from ..queue_worker import get_queue` and in the router tests patch
`backend.verification.service.get_queue`. Align the Task 12 test fixtures to that before running.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-09-01-lead-contact-verification.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
