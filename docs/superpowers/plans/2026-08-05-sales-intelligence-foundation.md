# AI Sales Intelligence Engine — Sub-project 1: Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Lead Qualification Agent and a Company Research Agent behind an opt-in settings
toggle, with a durable evidence-tracked schema, a resumable orchestrator, and a read API —
without changing any existing campaign behavior unless explicitly enabled.

**Architecture:** A new `backend/intelligence/` package holds two small agents behind a shared
`ResearchAgent` protocol, orchestrated by `run_research_pipeline`/`run_pending_research`. The
Company Research Agent reuses the existing `analyze_website`/`enrich_lead_with_ai` functions
rather than duplicating them — `enriched_data`/`scores` keep populating exactly as today. A new
`sales_intelligence_enabled` app_setting (default off) gates the pipeline wiring in
`routers/campaigns.py` and `scheduler.py`.

**Tech Stack:** Python 3.11+, FastAPI, aiosqlite, httpx, pytest + pytest-asyncio + respx (new
dev dependencies).

## Global Constraints

- Every new DB write path must go through the same `_normalize_lead_fields`-style discipline
  already established in `database.py` — reuse existing helpers, don't bypass them.
- No behavior change to any existing campaign flow when `sales_intelligence_enabled` is unset
  or `"false"` (the default). This is the single most important constraint in this plan.
- No new external network dependency beyond what already exists (httpx, the existing
  Ollama/cloud-LLM dispatch). No LinkedIn/search-engine scraping in this sub-project.
- Reuse `ai_brain._call_llm_raw`/`_ollama_cfg` for all LLM calls — never call a provider SDK
  directly, never hardcode Ollama's `/api/generate` shape.
- Every agent method takes `(lead: dict, campaign: Optional[dict])` and returns an
  `AgentResult` — never raises out of `.run()`.
- All new tests run via: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest <path> -v`

---

## File Structure

```
backend/
  intelligence/
    __init__.py              # public exports + intelligence_enabled() toggle helper
    base.py                  # AgentResult, EvidenceItem, ResearchAgent protocol
    techstack.py              # pure heuristic tech-stack detector
    qualification_agent.py    # Lead Qualification Agent
    company_research_agent.py # Company Research Agent
    orchestrator.py           # run_research_pipeline, run_pending_research
  routers/
    intelligence.py           # GET /api/leads/{lead_id}/research
  database.py                 # + 6 new tables, migration sweep, CRUD helpers (edited)
  enrichment/website_analyzer.py  # + raw_html field on analyze_website() result (edited)
  routers/campaigns.py         # ENRICHING stage: toggle branch (edited)
  scheduler.py                 # per-lead enrichment: toggle branch (edited)
  main.py                      # register intelligence router (edited)
  requirements.txt             # + pytest, pytest-asyncio, respx (edited)
tests/
  conftest.py                  # temp-DB fixture, sys.path setup
  test_database_intelligence_schema.py
  intelligence/
    test_base.py
    test_techstack.py
    test_qualification_agent.py
    test_company_research_agent.py
    test_orchestrator.py
    test_settings_toggle.py
    test_intelligence_router.py
pytest.ini                     # new, repo root
```

---

### Task 1: Test harness

**Files:**
- Create: `pytest.ini` (repo root)
- Create: `tests/conftest.py`
- Create: `tests/test_smoke_harness.py`
- Modify: `backend/requirements.txt`

**Interfaces:**
- Produces: a `clean_db` pytest-asyncio fixture (function-scoped) that wipes and re-initializes
  a throwaway SQLite database before each test, yielding the `backend.database` module itself
  (call it `db` in tests). Every later task's tests depend on this fixture existing exactly as
  named.

- [ ] **Step 1: Add test dependencies**

Edit `backend/requirements.txt`, after the `# ── Auth & rate limiting` section, add:

```
# ── Testing (dev) ──────────────────────────────────────────────────────────
pytest>=8.0.0
pytest-asyncio>=0.23.0
respx>=0.21.0
```

- [ ] **Step 2: Install them**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pip install pytest pytest-asyncio respx`

- [ ] **Step 3: Write pytest.ini**

Create `pytest.ini` at repo root:

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 4: Write the conftest fixture**

Create `tests/conftest.py`:

```python
"""
conftest.py — must set DATABASE_PATH before any `backend.*` module is imported,
because backend/database.py captures DB_PATH as a module-level global at import
time (from backend.config.get_settings(), which is @lru_cache'd). This module
runs before any test file in this directory tree is collected, so it's the one
safe place to do this.
"""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_TEST_DB_PATH = REPO_ROOT / "backend" / "data" / "test_autolead.db"
os.environ["DATABASE_PATH"] = str(_TEST_DB_PATH)

import pytest_asyncio  # noqa: E402

from backend import database as db  # noqa: E402


@pytest_asyncio.fixture
async def clean_db():
    """Function-scoped: wipe and reinitialize the test SQLite DB before each test.
    DB_PATH itself is fixed (set once above); the file at that path is recreated
    per test so tests never see each other's data."""
    if _TEST_DB_PATH.exists():
        _TEST_DB_PATH.unlink()
    await db.init_db()
    yield db
    if _TEST_DB_PATH.exists():
        _TEST_DB_PATH.unlink()
```

- [ ] **Step 5: Write the harness smoke test**

Create `tests/test_smoke_harness.py`:

```python
import pytest

pytestmark = pytest.mark.asyncio


async def test_clean_db_creates_schema_and_supports_basic_write(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Harness Test Co", "email": "hi@harnesstest.io"})
    assert lead_id

    lead = await db.get_lead_by_id(lead_id)
    assert lead["business_name"] == "Harness Test Co"


async def test_clean_db_is_actually_clean_between_tests(clean_db):
    db = clean_db
    total = await db.get_dashboard_stats()
    assert total["total_leads"] == 0
```

- [ ] **Step 6: Run and verify both pass**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/test_smoke_harness.py -v`
Expected: 2 passed. (If test 2 fails because test 1's lead is still present, the fixture teardown/setup is wrong — fix before proceeding; every later task depends on this isolation being real.)

- [ ] **Step 7: Commit**

```bash
git add pytest.ini tests/conftest.py tests/test_smoke_harness.py backend/requirements.txt
git commit -m "test: add pytest harness with isolated temp-DB fixture"
```

---

### Task 2: Database schema — company_profiles, research_evidence + scaffolding tables

**Files:**
- Modify: `backend/database.py`
- Create: `tests/test_database_intelligence_schema.py`

**Interfaces:**
- Produces (new `database.py` functions, all async):
  - `upsert_company_profile(lead_id: int, data: dict) -> int` — insert-or-update by `lead_id`,
    returns `company_profiles.id`. List values are JSON-encoded automatically.
  - `get_company_profile(lead_id: int) -> Optional[dict]`
  - `add_research_evidence(company_profile_id: int, items: list[dict]) -> None` — each item is
    `{"agent_name", "field_name", "source_type", "source_url", "snippet"}`.
  - `get_research_evidence(company_profile_id: int) -> list[dict]`
  - `get_leads_without_company_profile(limit: int = 50) -> list[dict]`
  - `get_pending_company_profiles(limit: int = 50) -> list[dict]`
- Consumes: existing `_JSON_ARRAY_COLS`, `_row_to_dict`, `get_db()`, `create_lead`,
  `get_leads_by_ids` (all already in `database.py`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_database_intelligence_schema.py`:

```python
import pytest

pytestmark = pytest.mark.asyncio


async def test_company_profile_upsert_and_get(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Acme Corp", "email": "a@acmecorp.io"})

    profile_id = await db.upsert_company_profile(lead_id, {
        "status": "RESEARCHING",
        "industry": "SaaS",
        "services": ["consulting", "support"],
    })
    assert profile_id

    profile = await db.get_company_profile(lead_id)
    assert profile["status"] == "RESEARCHING"
    assert profile["industry"] == "SaaS"
    assert profile["services"] == ["consulting", "support"]  # JSON round-trip

    profile_id2 = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    assert profile_id2 == profile_id
    updated = await db.get_company_profile(lead_id)
    assert updated["status"] == "DONE"
    assert updated["industry"] == "SaaS"  # untouched fields preserved


async def test_get_company_profile_returns_none_when_absent(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "No Profile Co"})
    assert await db.get_company_profile(lead_id) is None


async def test_research_evidence_roundtrip(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Beta LLC"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})

    await db.add_research_evidence(profile_id, [
        {"agent_name": "qualification", "field_name": "qualification_status",
         "source_type": "heuristic", "source_url": None, "snippet": "all checks passed"},
    ])
    evidence = await db.get_research_evidence(profile_id)
    assert len(evidence) == 1
    assert evidence[0]["agent_name"] == "qualification"
    assert evidence[0]["snippet"] == "all checks passed"


async def test_cascade_delete_removes_profile_and_evidence(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Gamma Inc"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    await db.add_research_evidence(profile_id, [
        {"agent_name": "qualification", "field_name": "x", "source_type": "heuristic",
         "source_url": None, "snippet": None},
    ])

    deleted = await db.delete_lead(lead_id)
    assert deleted
    assert await db.get_company_profile(lead_id) is None


async def test_get_leads_without_company_profile(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Delta Co"})
    leads = await db.get_leads_without_company_profile(limit=10)
    assert any(l["id"] == lead_id for l in leads)

    await db.upsert_company_profile(lead_id, {"status": "PENDING"})
    leads = await db.get_leads_without_company_profile(limit=10)
    assert not any(l["id"] == lead_id for l in leads)


async def test_get_pending_company_profiles(clean_db):
    db = clean_db
    lead_id_1 = await db.create_lead({"business_name": "Pending Co"})
    lead_id_2 = await db.create_lead({"business_name": "Done Co"})
    await db.upsert_company_profile(lead_id_1, {"status": "PENDING"})
    await db.upsert_company_profile(lead_id_2, {"status": "DONE"})

    pending = await db.get_pending_company_profiles(limit=10)
    assert {p["lead_id"] for p in pending} == {lead_id_1}


async def test_restart_sweep_resets_stuck_statuses(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Epsilon Ltd"})
    await db.upsert_company_profile(lead_id, {"status": "RESEARCHING"})

    await db.init_db()  # simulates a process restart re-running migrations

    profile = await db.get_company_profile(lead_id)
    assert profile["status"] == "PENDING"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/test_database_intelligence_schema.py -v`
Expected: FAIL — `AttributeError: module 'backend.database' has no attribute 'upsert_company_profile'`

- [ ] **Step 3: Add JSON array columns**

Read `backend/database.py`, find the `_JSON_ARRAY_COLS` frozenset near the top (currently
`{"marketing_gaps", "issues", "conversion_gaps", "seo_gaps", "pitch_angles", "key_problems", "sources"}`).
Add the 5 new list-valued columns:

```python
_JSON_ARRAY_COLS = frozenset({
    "marketing_gaps", "issues", "conversion_gaps", "seo_gaps",
    "pitch_angles", "key_problems", "sources",
    "services", "products", "social_profiles", "tech_stack", "partnerships",
})
```

- [ ] **Step 4: Add the schema DDL**

In `_SCHEMA_SQL`, find the `CREATE TABLE IF NOT EXISTS replies (...)` block (already edited in
the prior hardening pass to have `ON DELETE CASCADE`) and its trailing `CREATE INDEX
idx_replies_lead` line. Immediately after that index statement and before `CREATE TABLE IF NOT
EXISTS campaigns (`, insert:

```sql

CREATE TABLE IF NOT EXISTS company_profiles (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id                  INTEGER UNIQUE REFERENCES leads(id) ON DELETE CASCADE,
    status                   TEXT DEFAULT 'PENDING',
    qualification_status     TEXT,
    qualification_reason     TEXT,
    qualification_confidence REAL,
    industry                 TEXT,
    services                 TEXT,
    products                 TEXT,
    company_description      TEXT,
    social_profiles          TEXT,
    tech_stack               TEXT,
    company_size_estimate    TEXT,
    maturity_estimate        TEXT,
    hiring_signal            INTEGER,
    recent_activity_summary  TEXT,
    partnerships             TEXT,
    research_confidence      REAL,
    researched_at            TIMESTAMP,
    created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_company_profiles_status ON company_profiles (status);

CREATE TABLE IF NOT EXISTS research_evidence (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    company_profile_id INTEGER REFERENCES company_profiles(id) ON DELETE CASCADE,
    agent_name         TEXT,
    field_name         TEXT,
    source_type        TEXT,
    source_url         TEXT,
    snippet            TEXT,
    collected_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_research_evidence_profile ON research_evidence (company_profile_id);

CREATE TABLE IF NOT EXISTS decision_makers (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_profile_id  INTEGER REFERENCES company_profiles(id) ON DELETE CASCADE,
    full_name           TEXT,
    role_title           TEXT,
    seniority_rank       INTEGER,
    email                TEXT,
    phone                TEXT,
    linkedin_url         TEXT,
    source_type          TEXT,
    source_url           TEXT,
    confidence           REAL,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_decision_makers_profile ON decision_makers (company_profile_id);

CREATE TABLE IF NOT EXISTS verification_results (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_maker_id  INTEGER REFERENCES decision_makers(id) ON DELETE CASCADE,
    check_name         TEXT,
    passed             INTEGER,
    detail             TEXT,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sales_scores (
    id                           INTEGER PRIMARY KEY AUTOINCREMENT,
    company_profile_id           INTEGER UNIQUE REFERENCES company_profiles(id) ON DELETE CASCADE,
    company_quality_score        REAL,
    decision_maker_quality_score REAL,
    contact_confidence_score     REAL,
    icp_match_score              REAL,
    outreach_readiness_score     REAL,
    overall_prospect_score       REAL,
    briefing                     TEXT,
    scored_at                    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS personalization_context (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_profile_id  INTEGER UNIQUE REFERENCES company_profiles(id) ON DELETE CASCADE,
    outreach_angle       TEXT,
    value_proposition    TEXT,
    talking_points        TEXT,
    email_tone            TEXT,
    whatsapp_tone         TEXT,
    recommended_cta       TEXT,
    created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

- [ ] **Step 5: Add the restart sweep**

In `_run_migrations`, find:

```python
    await raw.execute("""
        UPDATE campaign_runs SET status = 'FAILED', stage = 'FAILED', finished_at = CURRENT_TIMESTAMP
        WHERE status = 'RUNNING'
    """)
```

Immediately after it, add:

```python
    await raw.execute("""
        UPDATE company_profiles SET status = 'PENDING'
        WHERE status IN ('QUALIFYING', 'RESEARCHING')
    """)
```

- [ ] **Step 6: Add the CRUD functions**

Find the `get_score` function (end of the "Scores" section, just before the "Messages" section
comment block). Immediately after `get_score`, add:

```python
# ─────────────────────────────────────────────────────────────────────────────
# Sales Intelligence — company research
# ─────────────────────────────────────────────────────────────────────────────

_COMPANY_PROFILE_WRITABLE = frozenset({
    "status", "qualification_status", "qualification_reason", "qualification_confidence",
    "industry", "services", "products", "company_description", "social_profiles",
    "tech_stack", "company_size_estimate", "maturity_estimate", "hiring_signal",
    "recent_activity_summary", "partnerships", "research_confidence", "researched_at",
})


async def upsert_company_profile(lead_id: int, data: Dict[str, Any]) -> int:
    """Insert or update the company_profiles row for lead_id. Returns its id."""
    clean = {
        k: (json.dumps(v) if isinstance(v, list) else v)
        for k, v in data.items()
        if k in _COMPANY_PROFILE_WRITABLE and v is not None
    }
    if not clean:
        existing = await get_company_profile(lead_id)
        if existing:
            return existing["id"]
        clean = {"status": "PENDING"}
    cols         = ", ".join(["lead_id"] + list(clean.keys()))
    placeholders = ", ".join("?" for _ in range(len(clean) + 1))
    update_set   = ", ".join(f"{c} = excluded.{c}" for c in clean.keys())
    async with get_db() as conn:
        row_id = await conn.fetchval(
            f"""INSERT INTO company_profiles ({cols}) VALUES ({placeholders})
                ON CONFLICT (lead_id) DO UPDATE SET {update_set}
                RETURNING id""",
            lead_id, *clean.values(),
        )
    return row_id


async def get_company_profile(lead_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        row = await conn.fetchrow("SELECT * FROM company_profiles WHERE lead_id = $1", lead_id)
    return dict(row) if row else None


async def add_research_evidence(company_profile_id: int, items: List[Dict[str, Any]]) -> None:
    if not items:
        return
    async with get_db() as conn:
        for item in items:
            await conn.execute(
                """INSERT INTO research_evidence
                   (company_profile_id, agent_name, field_name, source_type, source_url, snippet)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                company_profile_id, item.get("agent_name"), item.get("field_name"),
                item.get("source_type"), item.get("source_url"), item.get("snippet"),
            )


async def get_research_evidence(company_profile_id: int) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT * FROM research_evidence WHERE company_profile_id = $1 ORDER BY collected_at",
            company_profile_id,
        )
    return [dict(r) for r in rows]


async def get_leads_without_company_profile(limit: int = 50) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            """SELECT l.* FROM leads l
               LEFT JOIN company_profiles cp ON cp.lead_id = l.id
               WHERE cp.id IS NULL
               ORDER BY l.created_at DESC LIMIT $1""",
            limit,
        )
    return [dict(r) for r in rows]


async def get_pending_company_profiles(limit: int = 50) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT * FROM company_profiles WHERE status = 'PENDING' ORDER BY created_at LIMIT $1",
            limit,
        )
    return [dict(r) for r in rows]
```

- [ ] **Step 7: Run to verify all pass**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/test_database_intelligence_schema.py -v`
Expected: 7 passed.

- [ ] **Step 8: Regression check — full existing test + compile suite still clean**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/ -v && "./backend/.venv/Scripts/python.exe" -m compileall backend`
Expected: all tests pass, compileall reports no errors.

- [ ] **Step 9: Commit**

```bash
git add backend/database.py tests/test_database_intelligence_schema.py
git commit -m "feat: add sales-intelligence schema (company_profiles, research_evidence, scaffolding tables)"
```

---

### Task 3: Agent framework base types

**Files:**
- Create: `backend/intelligence/__init__.py` (empty for now — populated in Task 9)
- Create: `backend/intelligence/base.py`
- Create: `tests/intelligence/__init__.py` (empty)
- Create: `tests/intelligence/test_base.py`

**Interfaces:**
- Produces: `EvidenceItem(field_name, source_type, source_url, snippet)`,
  `AgentResult(status, data, evidence, confidence, reason)`, `ResearchAgent` protocol —
  imported by every agent and by the orchestrator in later tasks.

- [ ] **Step 1: Write the failing test**

Create `tests/intelligence/__init__.py` (empty file).

Create `tests/intelligence/test_base.py`:

```python
from backend.intelligence.base import AgentResult, EvidenceItem


def test_agent_result_defaults():
    result = AgentResult(status="ok")
    assert result.data == {}
    assert result.evidence == []
    assert result.confidence == 0.0
    assert result.reason is None


def test_agent_result_with_values():
    ev = EvidenceItem(field_name="industry", source_type="website",
                       source_url="https://x.com", snippet="hello")
    result = AgentResult(status="rejected", data={"a": 1}, evidence=[ev],
                          confidence=0.3, reason="missing name")
    assert result.evidence[0].field_name == "industry"
    assert result.reason == "missing name"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/intelligence/test_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.intelligence'`

- [ ] **Step 3: Implement**

Create `backend/intelligence/__init__.py` (empty for now):

```python
```

Create `backend/intelligence/base.py`:

```python
"""
base.py — shared types every research agent and the orchestrator depend on.

Deliberately minimal: no plugin registry, no discovery magic. Adding a new
agent later means implementing this Protocol and adding one line to the
orchestrator's ordered agent list — that's the whole "extensibility" story
this sub-project needs for 2 agents.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Protocol


@dataclass
class EvidenceItem:
    field_name: str
    source_type: str            # "website" | "ai_inference" | "heuristic"
    source_url: Optional[str]
    snippet: Optional[str]


@dataclass
class AgentResult:
    status: Literal["ok", "rejected", "failed"]
    data: Dict[str, Any] = field(default_factory=dict)
    evidence: List[EvidenceItem] = field(default_factory=list)
    confidence: float = 0.0
    reason: Optional[str] = None


class ResearchAgent(Protocol):
    name: str

    async def run(self, lead: Dict[str, Any], campaign: Optional[Dict[str, Any]] = None) -> AgentResult:
        ...
```

- [ ] **Step 4: Run to verify pass**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/intelligence/test_base.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/intelligence/__init__.py backend/intelligence/base.py tests/intelligence/__init__.py tests/intelligence/test_base.py
git commit -m "feat: add sales-intelligence agent framework base types"
```

---

### Task 4: Tech-stack heuristic detector

**Files:**
- Create: `backend/intelligence/techstack.py`
- Create: `tests/intelligence/test_techstack.py`

**Interfaces:**
- Produces: `detect_tech_stack(html: Optional[str]) -> List[str]` — pure function, no I/O.
  Consumed by the Company Research Agent in Task 6.

- [ ] **Step 1: Write the failing tests**

Create `tests/intelligence/test_techstack.py`:

```python
from backend.intelligence.techstack import detect_tech_stack


def test_detects_wordpress():
    html = '<html><head><link rel="stylesheet" href="/wp-content/themes/x/style.css"></head></html>'
    assert "WordPress" in detect_tech_stack(html)


def test_detects_shopify():
    html = '<script src="https://cdn.shopify.com/s/files/1/foo.js"></script>'
    assert "Shopify" in detect_tech_stack(html)


def test_detects_multiple_signatures():
    html = '<div id="wp-content"></div><script>gtag("config")</script>'
    stack = detect_tech_stack(html)
    assert "WordPress" in stack
    assert "Google Analytics" in stack


def test_empty_or_none_html_returns_empty_list():
    assert detect_tech_stack("") == []
    assert detect_tech_stack(None) == []


def test_no_matches_returns_empty_list():
    assert detect_tech_stack("<html><body>Plain site, no known signatures</body></html>") == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/intelligence/test_techstack.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.intelligence.techstack'`

- [ ] **Step 3: Implement**

Create `backend/intelligence/techstack.py`:

```python
"""
techstack.py — zero-extra-network-call heuristic tech-stack detection.

Operates on HTML already fetched by analyze_website() — never fetches
anything itself. Pure regex signature matching, easy to extend.
"""
import re
from typing import Dict, List, Optional

_SIGNATURES: Dict[str, List[str]] = {
    "WordPress":        [r"wp-content", r"wp-includes", r'name="generator"\s+content="WordPress'],
    "Shopify":           [r"cdn\.shopify\.com", r"Shopify\.theme"],
    "Wix":                [r"static\.wixstatic\.com", r"\bwix\.com\b"],
    "Squarespace":        [r"squarespace\.com", r"static1\.squarespace\.com"],
    "Webflow":            [r"webflow\.com", r"\bwf-"],
    "Next.js/React":      [r"__NEXT_DATA__", r"data-reactroot", r"_next/static"],
    "Google Analytics":   [r"google-analytics\.com", r"gtag\("],
}


def detect_tech_stack(html: Optional[str]) -> List[str]:
    if not html:
        return []
    found: List[str] = []
    for name, patterns in _SIGNATURES.items():
        if any(re.search(p, html, re.I) for p in patterns):
            found.append(name)
    return found
```

- [ ] **Step 4: Run to verify pass**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/intelligence/test_techstack.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/intelligence/techstack.py tests/intelligence/test_techstack.py
git commit -m "feat: add pure heuristic tech-stack detector"
```

---

### Task 5: Lead Qualification Agent

**Files:**
- Create: `backend/intelligence/qualification_agent.py`
- Create: `tests/intelligence/test_qualification_agent.py`

**Interfaces:**
- Consumes: `backend.validators.clean_business_name/is_valid_email/is_valid_phone` (existing),
  `backend.intelligence.base.AgentResult/EvidenceItem` (Task 3).
- Produces: `QualificationAgent` class with `name = "qualification"` and
  `async def run(lead, campaign=None) -> AgentResult`. `AgentResult.data` contains
  `qualification_status` ("QUALIFIED"/"REJECTED") and `qualification_confidence` (float).
  Consumed by the orchestrator in Task 7.

- [ ] **Step 1: Write the failing tests**

Create `tests/intelligence/test_qualification_agent.py`:

```python
import httpx
import pytest
import respx

from backend.intelligence.qualification_agent import QualificationAgent

pytestmark = pytest.mark.asyncio


async def test_qualifies_a_complete_lead():
    lead = {
        "business_name": "Acme Dental",
        "email": "info@acmedental.co",
        "phone": "+15551234567",
        "website": "https://acmedental.co",
        "niche": "dentist",
        "city": "Metropolis",
    }
    campaign = {"niche": "dentist", "city": "Metropolis"}

    with respx.mock:
        respx.head("https://acmedental.co").mock(return_value=httpx.Response(200))
        agent = QualificationAgent()
        result = await agent.run(lead, campaign)

    assert result.status == "ok"
    assert result.data["qualification_status"] == "QUALIFIED"
    assert result.confidence >= 0.5


async def test_rejects_lead_with_no_name_or_contact():
    lead = {"business_name": "", "email": None, "phone": None, "website": None}
    agent = QualificationAgent()
    result = await agent.run(lead, None)

    assert result.status == "rejected"
    assert result.data["qualification_status"] == "REJECTED"
    assert "missing business name" in result.reason
    assert "no email, phone, or website" in result.reason


async def test_rejects_unreachable_website():
    lead = {
        "business_name": "Beta LLC",
        "email": "hi@betallc.io",
        "website": "https://betallc.io",
    }
    with respx.mock:
        respx.head("https://betallc.io").mock(side_effect=httpx.ConnectError("refused"))
        respx.get("https://betallc.io").mock(side_effect=httpx.ConnectError("refused"))
        agent = QualificationAgent()
        result = await agent.run(lead, None)

    assert "website unreachable" in result.reason


async def test_niche_mismatch_recorded_in_reason():
    lead = {
        "business_name": "Gamma Cafe",
        "email": "hi@gammacafe.io",
        "phone": "+15559876543",
        "niche": "cafe",
    }
    campaign = {"niche": "dentist", "city": "Metropolis"}
    agent = QualificationAgent()
    result = await agent.run(lead, campaign)

    assert "niche mismatch" in (result.reason or "")


async def test_missing_campaign_skips_niche_and_location_checks():
    lead = {
        "business_name": "Standalone Co",
        "email": "hi@standalone.io",
        "phone": "+15551112222",
    }
    agent = QualificationAgent()
    result = await agent.run(lead, None)

    assert result.status == "ok"
    assert result.data["qualification_status"] == "QUALIFIED"
    assert result.reason is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/intelligence/test_qualification_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.intelligence.qualification_agent'`

- [ ] **Step 3: Implement**

Create `backend/intelligence/qualification_agent.py`:

```python
"""
qualification_agent.py — Lead Qualification Agent.

Fast, free, no LLM — runs for every lead to decide whether the more
expensive Company Research Agent is worth running at all.
"""
import logging
from typing import Any, Dict, List, Optional

import httpx

from ..validators import clean_business_name, is_valid_email, is_valid_phone
from .base import AgentResult, EvidenceItem

logger = logging.getLogger(__name__)

_REACHABILITY_TIMEOUT = 6.0
_CONFIDENCE_THRESHOLD = 0.5

_CHECK_WEIGHTS = {
    "website_reachable": 0.3,
    "has_business_name": 0.25,
    "has_contact_info":  0.25,
    "niche_match":       0.1,
    "location_match":    0.1,
}


async def _check_website_reachable(url: Optional[str]) -> Optional[bool]:
    """None = skipped (no website, neutral — doesn't count against confidence)."""
    if not url:
        return None
    target = url if url.startswith(("http://", "https://")) else f"https://{url}"
    try:
        async with httpx.AsyncClient(timeout=_REACHABILITY_TIMEOUT, follow_redirects=True, verify=False) as client:
            resp = await client.head(target)
            if resp.status_code >= 400:
                resp = await client.get(target)
            return resp.status_code < 400
    except Exception as exc:
        logger.debug("qualification: reachability check failed for %s: %s", url, exc)
        return False


def _niche_matches(lead_niche: Optional[str], campaign_niche: Optional[str]) -> Optional[bool]:
    if not campaign_niche:
        return None
    if not lead_niche:
        return False
    a, b = lead_niche.strip().lower(), campaign_niche.strip().lower()
    return a in b or b in a


def _location_matches(lead: Dict[str, Any], campaign: Dict[str, Any]) -> Optional[bool]:
    campaign_city = campaign.get("city")
    if not campaign_city:
        return None
    lead_city = lead.get("city")
    if not lead_city:
        return False
    return campaign_city.strip().lower() == lead_city.strip().lower()


class QualificationAgent:
    name = "qualification"

    async def run(self, lead: Dict[str, Any], campaign: Optional[Dict[str, Any]] = None) -> AgentResult:
        campaign = campaign or {}
        reasons_failed: List[str] = []
        score = 0.0
        max_score = 0.0

        has_name = bool(clean_business_name(lead.get("business_name")))
        max_score += _CHECK_WEIGHTS["has_business_name"]
        if has_name:
            score += _CHECK_WEIGHTS["has_business_name"]
        else:
            reasons_failed.append("missing business name")

        has_contact = bool(
            (lead.get("email") and is_valid_email(lead["email"]))
            or (lead.get("phone") and is_valid_phone(lead["phone"]))
            or lead.get("website")
        )
        max_score += _CHECK_WEIGHTS["has_contact_info"]
        if has_contact:
            score += _CHECK_WEIGHTS["has_contact_info"]
        else:
            reasons_failed.append("no email, phone, or website")

        reachable = await _check_website_reachable(lead.get("website"))
        if reachable is not None:
            max_score += _CHECK_WEIGHTS["website_reachable"]
            if reachable:
                score += _CHECK_WEIGHTS["website_reachable"]
            else:
                reasons_failed.append("website unreachable")

        niche_ok = _niche_matches(lead.get("niche"), campaign.get("niche"))
        if niche_ok is not None:
            max_score += _CHECK_WEIGHTS["niche_match"]
            if niche_ok:
                score += _CHECK_WEIGHTS["niche_match"]
            else:
                reasons_failed.append("niche mismatch")

        location_ok = _location_matches(lead, campaign)
        if location_ok is not None:
            max_score += _CHECK_WEIGHTS["location_match"]
            if location_ok:
                score += _CHECK_WEIGHTS["location_match"]
            else:
                reasons_failed.append("location mismatch")

        confidence = round((score / max_score) if max_score > 0 else 0.0, 2)
        qualified = confidence >= _CONFIDENCE_THRESHOLD

        evidence = [EvidenceItem(
            field_name="qualification_status",
            source_type="heuristic",
            source_url=lead.get("website"),
            snippet="; ".join(reasons_failed) if reasons_failed else "all checks passed",
        )]

        return AgentResult(
            status="ok" if qualified else "rejected",
            data={
                "qualification_status": "QUALIFIED" if qualified else "REJECTED",
                "qualification_confidence": confidence,
            },
            evidence=evidence,
            confidence=confidence,
            reason="; ".join(reasons_failed) if reasons_failed else None,
        )
```

- [ ] **Step 4: Run to verify pass**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/intelligence/test_qualification_agent.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/intelligence/qualification_agent.py tests/intelligence/test_qualification_agent.py
git commit -m "feat: add Lead Qualification Agent"
```

---

### Task 6: Company Research Agent (+ analyze_website raw_html field)

**Files:**
- Modify: `backend/enrichment/website_analyzer.py`
- Create: `backend/intelligence/company_research_agent.py`
- Create: `tests/intelligence/test_company_research_agent.py`
- Create: `tests/enrichment/__init__.py` (empty, if `tests/enrichment/` doesn't exist yet)
- Create: `tests/enrichment/test_website_analyzer_raw_html.py`

**Interfaces:**
- Consumes: `analyze_website` (edited to add `raw_html`), `enrich_lead_with_ai` (existing,
  unchanged signature), `ai_brain._call_llm_raw`/`_ollama_cfg`/`_load_company_dna` (existing,
  unchanged), `techstack.detect_tech_stack` (Task 4), `base.AgentResult/EvidenceItem` (Task 3).
- Produces: `CompanyResearchAgent` class, `name = "company_research"`,
  `async def run(lead, campaign=None) -> AgentResult`. `AgentResult.data` may contain
  `industry`, `services`, `products`, `company_description`, `company_size_estimate`,
  `maturity_estimate`, `tech_stack`, `social_profiles`. Consumed by orchestrator in Task 7.

- [ ] **Step 1: Write the failing test for the analyze_website change**

Create `tests/enrichment/__init__.py` (empty, only if the directory doesn't already exist —
check with `ls tests/enrichment` first).

Create `tests/enrichment/test_website_analyzer_raw_html.py`:

```python
import pytest
import respx
import httpx

from backend.enrichment.website_analyzer import analyze_website

pytestmark = pytest.mark.asyncio


async def test_analyze_website_includes_raw_html():
    html = "<html><body><h1>Hello</h1><div class='wp-content'>x</div></body></html>"
    with respx.mock:
        respx.get("https://example-test-site.co").mock(
            return_value=httpx.Response(200, text=html, headers={"content-type": "text/html"})
        )
        result = await analyze_website("https://example-test-site.co")

    assert "raw_html" in result
    assert "wp-content" in result["raw_html"]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/enrichment/test_website_analyzer_raw_html.py -v`
Expected: FAIL — `assert "raw_html" in result` fails (key doesn't exist yet).

Note: `analyze_website` has an in-process TTL cache keyed by normalized URL — using a unique
test-only hostname (`example-test-site.co`) avoids collisions with any other test or real run.

- [ ] **Step 3: Add raw_html to analyze_website's result**

Read `backend/enrichment/website_analyzer.py`. Find:

```python
    result: Dict[str, Any] = {
        # Network
        "url":               norm,
        "has_ssl":           has_ssl,
        "error":             None,
```

Add one line after it:

```python
    result: Dict[str, Any] = {
        # Network
        "url":               norm,
        "has_ssl":           has_ssl,
        "error":             None,
        "raw_html":          "",
```

Then find:

```python
    soup = BeautifulSoup(html, "lxml")
```

Immediately after it, add:

```python
    result["raw_html"] = html[:200_000]  # capped — used only for in-request tech-stack detection, never persisted
```

- [ ] **Step 4: Run to verify the analyze_website test passes**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/enrichment/test_website_analyzer_raw_html.py -v`
Expected: 1 passed.

- [ ] **Step 5: Write the failing tests for CompanyResearchAgent**

Create `tests/intelligence/test_company_research_agent.py`:

```python
import pytest

from backend.intelligence import company_research_agent as cra_module
from backend.intelligence.company_research_agent import CompanyResearchAgent

pytestmark = pytest.mark.asyncio


def _fake_website_data(**overrides):
    base = {
        "url": "https://acmedental.co", "has_ssl": True, "error": None,
        "page_title": "Acme Dental — Home", "meta_description": "Best dental care in town",
        "all_headings": ["Welcome to Acme Dental", "Our Services"],
        "body_text": "Acme Dental offers general and cosmetic dentistry services.",
        "word_count": 50, "image_count": 3,
        "has_contact_form": True, "has_phone_on_page": True, "has_email_on_page": True,
        "cta_buttons": ["Book Now"], "social_media_links": ["facebook", "instagram"],
        "has_social_links": True, "page_text": "Acme Dental offers general and cosmetic dentistry.",
        "raw_html": '<html><div class="wp-content">x</div></html>',
    }
    base.update(overrides)
    return base


async def test_research_with_no_website_returns_low_confidence_ok():
    agent = CompanyResearchAgent()
    result = await agent.run({"id": 1, "business_name": "No Site Co", "website": None}, None)
    assert result.status == "ok"
    assert result.confidence < 0.5
    assert result.evidence == []


async def test_research_success_path(monkeypatch):
    async def fake_analyze_website(url, timeout=10):
        return _fake_website_data()

    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        return '''{"industry": "Dental Care", "services": ["general dentistry", "cosmetic dentistry"],
                   "products": [], "company_description": "A dental clinic offering general and cosmetic care.",
                   "company_size_estimate": "small", "maturity_estimate": "established"}'''

    async def fake_ollama_cfg():
        return {"provider": "ollama", "base_url": "http://x", "model": "y", "timeout": 30}

    async def fake_enrich_lead_with_ai(lead, website_data, company_dna):
        return {}

    monkeypatch.setattr(cra_module, "analyze_website", fake_analyze_website)
    monkeypatch.setattr(cra_module, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(cra_module, "_ollama_cfg", fake_ollama_cfg)
    monkeypatch.setattr(cra_module, "enrich_lead_with_ai", fake_enrich_lead_with_ai)
    monkeypatch.setattr(cra_module, "_load_company_dna", lambda: "We build CRMs.")

    agent = CompanyResearchAgent()
    lead = {"id": 1, "business_name": "Acme Dental", "website": "https://acmedental.co", "niche": "dentist"}
    result = await agent.run(lead, None)

    assert result.status == "ok"
    assert result.confidence == 1.0
    assert result.data["industry"] == "Dental Care"
    assert "WordPress" in result.data["tech_stack"]

    field_names = {e.field_name for e in result.evidence}
    assert "industry" in field_names
    assert "tech_stack" in field_names
    industry_evidence = [e for e in result.evidence if e.field_name == "industry"][0]
    assert industry_evidence.source_type == "ai_inference"
    tech_evidence = [e for e in result.evidence if e.field_name == "tech_stack"][0]
    assert tech_evidence.source_type == "heuristic"


async def test_research_degrades_gracefully_on_llm_failure(monkeypatch):
    async def fake_analyze_website(url, timeout=10):
        return _fake_website_data()

    async def fake_call_llm_raw(prompt, cfg, temperature=None, num_predict=800):
        raise RuntimeError("model unavailable")

    async def fake_ollama_cfg():
        return {"provider": "ollama", "base_url": "http://x", "model": "y", "timeout": 30}

    async def fake_enrich_lead_with_ai(lead, website_data, company_dna):
        return {}

    monkeypatch.setattr(cra_module, "analyze_website", fake_analyze_website)
    monkeypatch.setattr(cra_module, "_call_llm_raw", fake_call_llm_raw)
    monkeypatch.setattr(cra_module, "_ollama_cfg", fake_ollama_cfg)
    monkeypatch.setattr(cra_module, "enrich_lead_with_ai", fake_enrich_lead_with_ai)
    monkeypatch.setattr(cra_module, "_load_company_dna", lambda: "We build CRMs.")

    agent = CompanyResearchAgent()
    lead = {"id": 2, "business_name": "Acme Dental", "website": "https://acmedental.co"}
    result = await agent.run(lead, None)

    assert result.status == "ok"
    assert result.confidence == 0.6  # heuristic-only, no AI fields
    assert "industry" not in result.data


async def test_research_handles_website_fetch_error(monkeypatch):
    async def fake_analyze_website(url, timeout=10):
        return _fake_website_data(error="timeout", body_text="", raw_html="")

    monkeypatch.setattr(cra_module, "analyze_website", fake_analyze_website)

    agent = CompanyResearchAgent()
    lead = {"id": 3, "business_name": "Acme Dental", "website": "https://acmedental.co"}
    result = await agent.run(lead, None)

    assert result.status == "ok"
    assert result.confidence == 0.2
```

- [ ] **Step 6: Run to verify failure**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/intelligence/test_company_research_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.intelligence.company_research_agent'`

- [ ] **Step 7: Implement**

Create `backend/intelligence/company_research_agent.py`:

```python
"""
company_research_agent.py — Company Research Agent.

Reuses analyze_website()/enrich_lead_with_ai() rather than duplicating them —
enriched_data/scores keep populating exactly as they did before this agent
existed. This agent's job is the additional evidence-tracked structured
profile (company_profiles/research_evidence), not a replacement enrichment
pipeline.

Imports the reused functions by name (not via module attribute access) so
tests can monkeypatch them directly on this module.
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional

from ..ai_brain import _call_llm_raw, _load_company_dna, _ollama_cfg
from ..enrichment.ai_enricher import enrich_lead_with_ai
from ..enrichment.website_analyzer import analyze_website
from .base import AgentResult, EvidenceItem
from .techstack import detect_tech_stack

logger = logging.getLogger(__name__)


def _build_research_prompt(lead: Dict[str, Any], website_data: Dict[str, Any]) -> str:
    biz      = lead.get("business_name") or "this business"
    niche    = lead.get("niche") or "unknown industry"
    body     = (website_data.get("body_text") or "")[:1500]
    headings = " | ".join((website_data.get("all_headings") or [])[:8]) or "none"

    return f"""You are a B2B sales research analyst. Analyse this business's website and
return ONLY a valid JSON object — no markdown, no explanation, no text before or after the JSON.

BUSINESS: {biz}
CLAIMED NICHE: {niche}
WEBSITE TITLE: {website_data.get("page_title") or "unknown"}
HEADINGS: {headings}
CONTENT SAMPLE: {body}

Return ONLY this JSON — every field required, use "unknown" if you cannot determine it:
{{
  "industry": "specific industry category",
  "services": ["service 1", "service 2"],
  "products": ["product 1"],
  "company_description": "2 sentence factual summary of what this business does",
  "company_size_estimate": "solo OR small OR medium OR large OR unknown",
  "maturity_estimate": "startup OR growing OR established OR enterprise OR unknown"
}}"""


def _parse_json_object(raw: str) -> Optional[Dict[str, Any]]:
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()
    match = re.search(r"\{[\s\S]+\}", cleaned)
    if not match:
        return None
    json_str = match.group(0)
    json_str = re.sub(r",\s*([}\]])", r"\1", json_str)
    json_str = re.sub(r"'([^']+)'\s*:", r'"\1":', json_str)
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


_AI_FIELDS = ("industry", "services", "products", "company_description",
              "company_size_estimate", "maturity_estimate")


class CompanyResearchAgent:
    name = "company_research"

    async def run(self, lead: Dict[str, Any], campaign: Optional[Dict[str, Any]] = None) -> AgentResult:
        website = lead.get("website")
        if not website:
            return AgentResult(status="ok", data={}, evidence=[], confidence=0.1)

        try:
            website_data = await analyze_website(website)
        except Exception as exc:
            logger.warning("company_research: analyze_website failed for %s: %s", website, exc)
            return AgentResult(status="failed", reason=str(exc), confidence=0.0)

        if website_data.get("error"):
            return AgentResult(status="ok", data={}, evidence=[], confidence=0.2)

        evidence: List[EvidenceItem] = []
        data: Dict[str, Any] = {}

        tech_stack = detect_tech_stack(website_data.get("raw_html") or "")
        if tech_stack:
            data["tech_stack"] = tech_stack
            evidence.append(EvidenceItem("tech_stack", "heuristic", website, ", ".join(tech_stack)))

        social = website_data.get("social_media_links") or []
        if social:
            data["social_profiles"] = social
            evidence.append(EvidenceItem("social_profiles", "website", website, ", ".join(social)))

        confidence = 0.6
        parsed: Optional[Dict[str, Any]] = None
        try:
            cfg = await _ollama_cfg()
            prompt = _build_research_prompt(lead, website_data)
            raw = await _call_llm_raw(prompt, cfg, temperature=0.2, num_predict=500)
            parsed = _parse_json_object(raw)
        except Exception as exc:
            logger.warning("company_research: LLM synthesis failed for lead %s: %s", lead.get("id"), exc)

        if parsed:
            for field_name in _AI_FIELDS:
                if field_name in parsed:
                    data[field_name] = parsed[field_name]
                    evidence.append(EvidenceItem(field_name, "ai_inference", website, None))
            confidence = 1.0

        # Keep the existing enrichment pipeline populated (enriched_data/scores) —
        # this agent adds structured, evidence-tracked fields alongside it, not instead of it.
        try:
            company_dna = _load_company_dna()
            await enrich_lead_with_ai(lead, website_data, company_dna)
        except Exception as exc:
            logger.warning("company_research: enrich_lead_with_ai failed for lead %s: %s", lead.get("id"), exc)

        return AgentResult(status="ok", data=data, evidence=evidence, confidence=confidence)
```

- [ ] **Step 8: Run to verify pass**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/intelligence/test_company_research_agent.py -v`
Expected: 4 passed.

- [ ] **Step 9: Regression check on website_analyzer's existing behavior**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/ -v`
Expected: all tests (harness + schema + base + techstack + qualification + this task) pass.
Also run: `"./backend/.venv/Scripts/python.exe" -m compileall backend` — no errors.

- [ ] **Step 10: Commit**

```bash
git add backend/enrichment/website_analyzer.py backend/intelligence/company_research_agent.py tests/enrichment/ tests/intelligence/test_company_research_agent.py
git commit -m "feat: add Company Research Agent (reuses existing enrichment, adds raw_html + tech-stack + evidence)"
```

---

### Task 7: Orchestrator — pipeline runner + resume

**Files:**
- Create: `backend/intelligence/orchestrator.py`
- Create: `tests/intelligence/test_orchestrator.py`

**Interfaces:**
- Consumes: `db.upsert_company_profile/get_company_profile/add_research_evidence/
  get_leads_without_company_profile/get_pending_company_profiles/get_leads_by_ids` (Task 2 +
  existing), `QualificationAgent` (Task 5), `CompanyResearchAgent` (Task 6).
- Produces:
  - `run_research_pipeline(lead: dict, campaign: Optional[dict] = None) -> dict` — returns
    `{"lead_id": int, "status": "DONE"|"REJECTED"|"FAILED", ...}`. Never raises.
  - `run_pending_research(lead_ids: Optional[list[int]] = None, limit: int = 50,
    max_concurrent: int = 3, campaign: Optional[dict] = None) -> dict` — returns
    `{"processed": int, "results": list[dict]}`. When `lead_ids` is given, only those leads are
    considered (prevents cross-campaign niche/city contamination); when `None`, scans the
    global backlog. Consumed by `routers/campaigns.py` and `scheduler.py` in Task 9.
  - Module-level `_qualification_agent`/`_company_research_agent` instances (monkeypatchable
    by tests).

- [ ] **Step 1: Write the failing tests**

Create `tests/intelligence/test_orchestrator.py`:

```python
import pytest

from backend.intelligence import orchestrator as orch_module
from backend.intelligence.base import AgentResult, EvidenceItem

pytestmark = pytest.mark.asyncio


class _StubAgent:
    def __init__(self, result: AgentResult):
        self._result = result

    async def run(self, lead, campaign=None):
        return self._result


class _CrashingAgent:
    async def run(self, lead, campaign=None):
        raise RuntimeError("simulated crash")


async def test_qualified_lead_runs_full_pipeline(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Acme Dental", "email": "hi@acmedental.co"})
    lead = await db.get_lead_by_id(lead_id)

    monkeypatch.setattr(orch_module, "_qualification_agent", _StubAgent(AgentResult(
        status="ok",
        data={"qualification_status": "QUALIFIED", "qualification_confidence": 0.9},
        evidence=[EvidenceItem("qualification_status", "heuristic", None, "all checks passed")],
        confidence=0.9,
    )))
    monkeypatch.setattr(orch_module, "_company_research_agent", _StubAgent(AgentResult(
        status="ok",
        data={"industry": "Dental Care"},
        evidence=[EvidenceItem("industry", "ai_inference", "https://acmedental.co", None)],
        confidence=1.0,
    )))

    result = await orch_module.run_research_pipeline(lead)
    assert result["status"] == "DONE"

    profile = await db.get_company_profile(lead_id)
    assert profile["status"] == "DONE"
    assert profile["qualification_status"] == "QUALIFIED"
    assert profile["industry"] == "Dental Care"

    evidence = await db.get_research_evidence(profile["id"])
    assert len(evidence) == 2


async def test_rejected_lead_short_circuits_before_research(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Spammy Co"})
    lead = await db.get_lead_by_id(lead_id)

    research_agent = _StubAgent(AgentResult(status="ok", data={"industry": "should not run"}))
    monkeypatch.setattr(orch_module, "_qualification_agent", _StubAgent(AgentResult(
        status="rejected",
        data={"qualification_status": "REJECTED", "qualification_confidence": 0.1},
        evidence=[], confidence=0.1, reason="missing business name",
    )))
    monkeypatch.setattr(orch_module, "_company_research_agent", research_agent)

    result = await orch_module.run_research_pipeline(lead)
    assert result["status"] == "REJECTED"

    profile = await db.get_company_profile(lead_id)
    assert profile["status"] == "REJECTED"
    assert profile["industry"] is None  # research agent never ran


async def test_qualification_result_survives_a_research_crash(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Crashy Co", "email": "hi@crashyco.io"})
    lead = await db.get_lead_by_id(lead_id)

    monkeypatch.setattr(orch_module, "_qualification_agent", _StubAgent(AgentResult(
        status="ok",
        data={"qualification_status": "QUALIFIED", "qualification_confidence": 0.9},
        evidence=[], confidence=0.9,
    )))
    monkeypatch.setattr(orch_module, "_company_research_agent", _CrashingAgent())

    result = await orch_module.run_research_pipeline(lead)
    assert result["status"] == "FAILED"

    profile = await db.get_company_profile(lead_id)
    assert profile["qualification_status"] == "QUALIFIED"  # progressive persistence — survived the crash
    assert profile["status"] == "FAILED"


async def test_run_pending_research_scoped_to_explicit_lead_ids(clean_db, monkeypatch):
    db = clean_db
    lead_id_1 = await db.create_lead({"business_name": "New Lead Co"})
    lead_id_2 = await db.create_lead({"business_name": "Already Pending Co"})
    await db.upsert_company_profile(lead_id_2, {"status": "PENDING"})
    # a third, unrelated lead must NOT be touched by this scoped call
    lead_id_3 = await db.create_lead({"business_name": "Unrelated Co"})

    monkeypatch.setattr(orch_module, "_qualification_agent", _StubAgent(AgentResult(
        status="ok", data={"qualification_status": "QUALIFIED", "qualification_confidence": 0.9},
        evidence=[], confidence=0.9,
    )))
    monkeypatch.setattr(orch_module, "_company_research_agent", _StubAgent(AgentResult(
        status="ok", data={"industry": "Test"}, evidence=[], confidence=1.0,
    )))

    outcome = await orch_module.run_pending_research(lead_ids=[lead_id_1, lead_id_2])
    assert outcome["processed"] == 2

    for lid in (lead_id_1, lead_id_2):
        profile = await db.get_company_profile(lid)
        assert profile["status"] == "DONE"

    assert await db.get_company_profile(lead_id_3) is None  # untouched


async def test_resume_after_interruption(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Resumed Co"})
    await db.upsert_company_profile(lead_id, {"status": "RESEARCHING"})

    await db.init_db()  # simulates a process restart -> sweep resets RESEARCHING to PENDING

    monkeypatch.setattr(orch_module, "_qualification_agent", _StubAgent(AgentResult(
        status="ok", data={"qualification_status": "QUALIFIED", "qualification_confidence": 0.9},
        evidence=[], confidence=0.9,
    )))
    monkeypatch.setattr(orch_module, "_company_research_agent", _StubAgent(AgentResult(
        status="ok", data={"industry": "Resumed"}, evidence=[], confidence=1.0,
    )))

    outcome = await orch_module.run_pending_research()
    assert outcome["processed"] == 1
    profile = await db.get_company_profile(lead_id)
    assert profile["status"] == "DONE"


async def test_run_pending_research_returns_zero_when_nothing_pending(clean_db):
    outcome = await orch_module.run_pending_research()
    assert outcome == {"processed": 0, "results": []}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/intelligence/test_orchestrator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.intelligence.orchestrator'`

- [ ] **Step 3: Implement**

Create `backend/intelligence/orchestrator.py`:

```python
"""
orchestrator.py — runs Qualification then (if qualified) Company Research for a
lead, persisting progressively so a crash mid-pipeline never loses the
qualification verdict. run_pending_research is the resumable, bounded-
concurrency entry point used by the campaign pipeline (Task 9) and available
for standalone backlog catch-up.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .. import database as db
from .base import AgentResult
from .company_research_agent import CompanyResearchAgent
from .qualification_agent import QualificationAgent

logger = logging.getLogger(__name__)

_qualification_agent = QualificationAgent()
_company_research_agent = CompanyResearchAgent()


async def _persist(lead_id: int, result: AgentResult, agent_name: str, extra: Dict[str, Any]) -> int:
    profile_id = await db.upsert_company_profile(lead_id, {**result.data, **extra})
    if result.evidence:
        await db.add_research_evidence(profile_id, [
            {"agent_name": agent_name, "field_name": e.field_name, "source_type": e.source_type,
             "source_url": e.source_url, "snippet": e.snippet}
            for e in result.evidence
        ])
    return profile_id


async def run_research_pipeline(lead: Dict[str, Any], campaign: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run Qualification then (if qualified) Company Research for one lead.
    Persists after each agent so a crash never loses earlier progress. Never raises."""
    lead_id = lead["id"]

    try:
        await db.upsert_company_profile(lead_id, {"status": "QUALIFYING"})

        qual_result = await _qualification_agent.run(lead, campaign)
        rejected = qual_result.status == "rejected"
        await _persist(lead_id, qual_result, "qualification", {
            "status": "REJECTED" if rejected else "RESEARCHING",
            "qualification_reason": qual_result.reason,
        })

        if rejected:
            logger.info("Lead %s rejected by qualification: %s", lead_id, qual_result.reason)
            return {"lead_id": lead_id, "status": "REJECTED", "reason": qual_result.reason}

        research_result = await _company_research_agent.run(lead, campaign)
        final_status = "DONE" if research_result.status == "ok" else "FAILED"
        await _persist(lead_id, research_result, "company_research", {
            "status": final_status,
            "research_confidence": research_result.confidence,
            "researched_at": datetime.now(timezone.utc).isoformat(),
        })
        return {"lead_id": lead_id, "status": final_status}

    except Exception as exc:
        logger.error("Research pipeline failed for lead %s: %s", lead_id, exc, exc_info=True)
        try:
            await db.upsert_company_profile(lead_id, {"status": "FAILED"})
        except Exception:
            pass
        return {"lead_id": lead_id, "status": "FAILED", "error": str(exc)}


async def run_pending_research(
    lead_ids: Optional[List[int]] = None,
    limit: int = 50,
    max_concurrent: int = 3,
    campaign: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Process research for a set of leads with bounded concurrency.

    lead_ids given: scoped strictly to those leads (used by the campaign pipeline — prevents
    a different campaign's backlog from being evaluated against this campaign's niche/city).
    lead_ids=None: scans the global PENDING backlog (used for standalone resume/catch-up).
    """
    if lead_ids is not None:
        leads_map = await db.get_leads_by_ids(lead_ids)
        for lid in lead_ids:
            if lid in leads_map and await db.get_company_profile(lid) is None:
                await db.upsert_company_profile(lid, {"status": "PENDING"})
        pending_lead_ids = []
        for lid in lead_ids:
            profile = await db.get_company_profile(lid)
            if profile and profile["status"] == "PENDING":
                pending_lead_ids.append(lid)
    else:
        new_leads = await db.get_leads_without_company_profile(limit=limit)
        for lead in new_leads:
            await db.upsert_company_profile(lead["id"], {"status": "PENDING"})
        pending_profiles = await db.get_pending_company_profiles(limit=limit)
        pending_lead_ids = [p["lead_id"] for p in pending_profiles]
        leads_map = await db.get_leads_by_ids(pending_lead_ids)

    if not pending_lead_ids:
        return {"processed": 0, "results": []}

    sem = asyncio.Semaphore(max_concurrent)
    results: List[Dict[str, Any]] = []

    async def _bounded(lead: Dict[str, Any]) -> None:
        async with sem:
            results.append(await run_research_pipeline(lead, campaign))

    await asyncio.gather(*[_bounded(leads_map[lid]) for lid in pending_lead_ids if lid in leads_map])
    return {"processed": len(results), "results": results}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/intelligence/test_orchestrator.py -v`
Expected: 6 passed.

- [ ] **Step 5: Full regression check**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/ -v`
Expected: every test across all prior tasks still passes.

- [ ] **Step 6: Commit**

```bash
git add backend/intelligence/orchestrator.py tests/intelligence/test_orchestrator.py
git commit -m "feat: add resumable research orchestrator with progressive persistence"
```

---

### Task 8: Read API — GET /api/leads/{id}/research

**Files:**
- Create: `backend/routers/intelligence.py`
- Modify: `backend/main.py`
- Create: `tests/intelligence/test_intelligence_router.py`

**Interfaces:**
- Consumes: `db.get_lead_by_id`, `db.get_company_profile`, `db.get_research_evidence`
  (existing + Task 2).
- Produces: `GET /api/leads/{lead_id}/research` → `{"profile": {...}, "evidence": [...]}` or 404.

- [ ] **Step 1: Write the failing tests**

Create `tests/intelligence/test_intelligence_router.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


async def test_get_research_404_when_lead_missing(clean_db):
    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/leads/999999/research")
    assert resp.status_code == 404


async def test_get_research_404_when_no_profile_yet(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "No Profile Co"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/leads/{lead_id}/research")
    assert resp.status_code == 404


async def test_get_research_returns_profile_and_evidence(clean_db):
    from backend.main import app
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Has Profile Co"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE", "industry": "SaaS"})
    await db.add_research_evidence(profile_id, [
        {"agent_name": "company_research", "field_name": "industry",
         "source_type": "ai_inference", "source_url": None, "snippet": None},
    ])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/leads/{lead_id}/research")

    assert resp.status_code == 200
    body = resp.json()
    assert body["profile"]["industry"] == "SaaS"
    assert len(body["evidence"]) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/intelligence/test_intelligence_router.py -v`
Expected: FAIL — 404 becomes a connection/import error, or all return 404 including the
"should succeed" case (route doesn't exist yet → 404 from FastAPI's default handler).

- [ ] **Step 3: Implement**

Create `backend/routers/intelligence.py`:

```python
import logging

from fastapi import APIRouter, HTTPException

from .. import database as db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/leads", tags=["intelligence"])


@router.get("/{lead_id}/research")
async def get_lead_research(lead_id: int):
    lead = await db.get_lead_by_id(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")

    profile = await db.get_company_profile(lead_id)
    if not profile:
        raise HTTPException(404, "No research available for this lead yet")

    evidence = await db.get_research_evidence(profile["id"])
    return {"profile": profile, "evidence": evidence}
```

Read `backend/main.py`. Find the router imports (near the top, something like
`from .routers import leads, campaigns, ai, scraper_router, settings_router, status`) and add
`intelligence` to that import list. Find the `app.include_router(...)` calls (e.g.
`app.include_router(scraper_router.router, dependencies=_authed)`) and add one more line:

```python
app.include_router(intelligence.router,   dependencies=_authed)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/intelligence/test_intelligence_router.py -v`
Expected: 3 passed.

- [ ] **Step 5: Full regression check**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/ -v && "./backend/.venv/Scripts/python.exe" -m compileall backend`
Expected: all tests pass, no compile errors.

- [ ] **Step 6: Commit**

```bash
git add backend/routers/intelligence.py backend/main.py tests/intelligence/test_intelligence_router.py
git commit -m "feat: add GET /api/leads/{id}/research read endpoint"
```

---

### Task 9: Pipeline integration behind the settings toggle

**Files:**
- Modify: `backend/intelligence/__init__.py`
- Modify: `backend/routers/campaigns.py`
- Modify: `backend/scheduler.py`
- Create: `tests/intelligence/test_settings_toggle.py`

**Interfaces:**
- Produces: `backend.intelligence.intelligence_enabled(stored_settings: dict) -> bool`.
- **Global Constraint reminder**: when the toggle is unset/false, both `_run_campaign_task` and
  `run_campaign` must behave identically to their current (pre-this-task) code paths. Verify
  this explicitly in Step 6 before committing.

- [ ] **Step 1: Write the failing test for the toggle helper**

Create `tests/intelligence/test_settings_toggle.py`:

```python
from backend.intelligence import intelligence_enabled


def test_disabled_by_default():
    assert intelligence_enabled({}) is False


def test_enabled_when_true_string():
    assert intelligence_enabled({"sales_intelligence_enabled": "true"}) is True


def test_enabled_case_insensitive():
    assert intelligence_enabled({"sales_intelligence_enabled": "TRUE"}) is True


def test_disabled_when_false_string():
    assert intelligence_enabled({"sales_intelligence_enabled": "false"}) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/intelligence/test_settings_toggle.py -v`
Expected: FAIL — `ImportError: cannot import name 'intelligence_enabled'`

- [ ] **Step 3: Implement the toggle helper and public exports**

Replace the contents of `backend/intelligence/__init__.py` (currently empty from Task 3) with:

```python
from typing import Any, Dict

from .orchestrator import run_pending_research, run_research_pipeline

__all__ = ["run_pending_research", "run_research_pipeline", "intelligence_enabled"]


def intelligence_enabled(stored_settings: Dict[str, Any]) -> bool:
    """True if the sales_intelligence_enabled app_setting is turned on. Default: off —
    existing campaign behavior is unchanged unless explicitly opted in."""
    return str(stored_settings.get("sales_intelligence_enabled", "false")).lower() == "true"
```

- [ ] **Step 4: Run to verify pass**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/intelligence/test_settings_toggle.py -v`
Expected: 4 passed.

- [ ] **Step 5: Wire into routers/campaigns.py**

Read `backend/routers/campaigns.py`. Add to the imports near the top (alongside the existing
`from ..enrichment.ai_enricher import enrich_lead_with_ai` /
`from ..enrichment.website_analyzer import analyze_website` lines):

```python
from ..intelligence import intelligence_enabled, run_pending_research
```

Find the ENRICHING block inside `_run_campaign_task`:

```python
        await _set_stage("ENRICHING")
        if not await _wait_while_paused():
            leads_with_site = [l for l in all_leads if l.get("website")]
            if leads_with_site:
                _log_sync(f"🔎 Enriching {len(leads_with_site)} lead(s) with business intelligence...")
                company_dna = ai_brain._load_company_dna()
                sem = asyncio.Semaphore(3)

                async def _enrich_one(lead: dict) -> None:
                    async with sem:
                        try:
                            website_data = await analyze_website(lead["website"])
                            await enrich_lead_with_ai(dict(lead), website_data, company_dna)
                        except Exception as exc:
                            logger.warning("Enrichment failed for lead %s: %s", lead.get("id"), exc)

                await asyncio.gather(*[_enrich_one(l) for l in leads_with_site])

                # Re-fetch (batched — one query, not one per lead) so scoring
                # below sees the enriched_at/status fields
                fresh_map = await db.get_leads_by_ids([l["id"] for l in all_leads])
                all_leads = [fresh_map.get(l["id"], l) for l in all_leads]
```

Replace it with (existing block preserved unchanged as the `else` branch):

```python
        await _set_stage("ENRICHING")
        if not await _wait_while_paused():
            stored_settings = await db.get_all_settings()
            if intelligence_enabled(stored_settings):
                _log_sync(f"🔎 Running sales-intelligence research on {len(all_leads)} lead(s)...")
                campaign_ctx = {"niche": niche, "city": city, "country": country}
                await run_pending_research(
                    lead_ids=[l["id"] for l in all_leads],
                    max_concurrent=3,
                    campaign=campaign_ctx,
                )
                fresh_map = await db.get_leads_by_ids([l["id"] for l in all_leads])
                all_leads = [fresh_map.get(l["id"], l) for l in all_leads]
            else:
                leads_with_site = [l for l in all_leads if l.get("website")]
                if leads_with_site:
                    _log_sync(f"🔎 Enriching {len(leads_with_site)} lead(s) with business intelligence...")
                    company_dna = ai_brain._load_company_dna()
                    sem = asyncio.Semaphore(3)

                    async def _enrich_one(lead: dict) -> None:
                        async with sem:
                            try:
                                website_data = await analyze_website(lead["website"])
                                await enrich_lead_with_ai(dict(lead), website_data, company_dna)
                            except Exception as exc:
                                logger.warning("Enrichment failed for lead %s: %s", lead.get("id"), exc)

                    await asyncio.gather(*[_enrich_one(l) for l in leads_with_site])

                    # Re-fetch (batched — one query, not one per lead) so scoring
                    # below sees the enriched_at/status fields
                    fresh_map = await db.get_leads_by_ids([l["id"] for l in all_leads])
                    all_leads = [fresh_map.get(l["id"], l) for l in all_leads]
```

- [ ] **Step 6: Wire into scheduler.py**

Read `backend/scheduler.py`. Add to the imports near the top:

```python
from .intelligence import intelligence_enabled, run_research_pipeline
```

Find, inside `run_campaign`:

```python
        # ── 3d.5 Business-intelligence enrichment (website analysis + AI) ──────
        # Previously only wired into the manual single-lead endpoint — the
        # daily automated campaign scored every lead on structural-only data.
        if lead.get("website") and not lead.get("enriched_at"):
            await _qlog(log_queue, f"   🔎 Enriching {biz}...")
            try:
                site_data = await _analyze_website(lead["website"], timeout=settings.enrichment_timeout)
                await _enrich_lead(lead, site_data, company_dna)
                refreshed_lead = await db.get_lead_by_id(lead_id)
                if refreshed_lead:
                    lead = dict(refreshed_lead)
            except Exception as exc:
                await _qlog(log_queue, f"   ⚠️  Enrichment failed for {biz}: {exc}", "WARNING")
```

Replace with (`stored` is the settings dict already fetched near the top of `run_campaign` for
`cfg = _resolve_config(config, stored)` — reused here, not re-fetched):

```python
        # ── 3d.5 Business-intelligence enrichment (website analysis + AI) ──────
        # Previously only wired into the manual single-lead endpoint — the
        # daily automated campaign scored every lead on structural-only data.
        if lead.get("website") and not lead.get("enriched_at"):
            await _qlog(log_queue, f"   🔎 Enriching {biz}...")
            try:
                if intelligence_enabled(stored):
                    await run_research_pipeline(lead, campaign={"niche": niche, "city": city})
                else:
                    site_data = await _analyze_website(lead["website"], timeout=settings.enrichment_timeout)
                    await _enrich_lead(lead, site_data, company_dna)
                refreshed_lead = await db.get_lead_by_id(lead_id)
                if refreshed_lead:
                    lead = dict(refreshed_lead)
            except Exception as exc:
                await _qlog(log_queue, f"   ⚠️  Enrichment failed for {biz}: {exc}", "WARNING")
```

- [ ] **Step 7: Verify the "off" path is byte-for-byte unchanged (Global Constraint check)**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m py_compile backend/routers/campaigns.py backend/scheduler.py backend/intelligence/__init__.py`
Expected: no errors.

Then manually re-read both edited blocks and confirm: with `sales_intelligence_enabled` absent
from `stored`/`stored_settings`, `intelligence_enabled(...)` returns `False`, so execution
falls into the `else`/pre-existing branch, which is textually identical to the code that was
there before this task. This is the most important check in this whole plan — do not skip it.

- [ ] **Step 8: Full regression suite**

Run: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m pytest tests/ -v && "./backend/.venv/Scripts/python.exe" -m compileall backend`
Expected: every test across every task passes; no compile errors anywhere in `backend/`.

- [ ] **Step 9: Manual end-to-end verification against the real app**

This mirrors the manual verification approach used in the prior hardening session — full
automated coverage of `_run_campaign_task`/`run_campaign` themselves isn't practical (they do
real scraping/sending), so this step is deliberately manual:

1. Start the backend: `cd "C:\Users\Fahad\Desktop\Marketing software\AutoLead-manager-" && "./backend/.venv/Scripts/python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`
2. Confirm the schema migrated cleanly: check the startup log for
   `Schema migrations applied` with no errors, and no `AttributeError`/`OperationalError`.
3. Confirm the toggle defaults off: `curl -s http://127.0.0.1:8000/api/settings` should not
   show `sales_intelligence_enabled` as `"true"` unless explicitly set.
4. Pick an existing lead id from `GET /api/leads?page=1&page_size=1`, confirm
   `GET /api/leads/{id}/research` returns 404 (no profile yet).
5. Enable it: `curl -X PUT http://127.0.0.1:8000/api/settings -H "Content-Type: application/json" -d "{\"key\":\"sales_intelligence_enabled\",\"value\":\"true\"}"`
6. Manually invoke research for one real lead id via a short Python one-liner (no dedicated
   endpoint for on-demand single-lead trigger exists yet — that's fine, this is a manual
   verification step, not a permanent interface):
   `"./backend/.venv/Scripts/python.exe" -c "import asyncio,sys; sys.path.insert(0,'.'); from backend import database as db; from backend.intelligence import run_research_pipeline; asyncio.run((lambda: __import__('asyncio').gather())())"`
   — simpler: write a throwaway script in the scratchpad directory that calls
   `await run_research_pipeline(await db.get_lead_by_id(<id>))` against the real DB for one
   real lead with a real website, and inspect the printed result plus
   `GET /api/leads/{id}/research` afterward. Confirm `qualification_status`,
   `research_confidence`, and at least one evidence row with a real `source_url` are present.
7. Turn the toggle back off (`PUT /api/settings` with `"value":"false"`) once verified, so the
   currently-running/scheduled campaigns are unaffected until the user deliberately opts in.

- [ ] **Step 10: Commit**

```bash
git add backend/intelligence/__init__.py backend/routers/campaigns.py backend/scheduler.py tests/intelligence/test_settings_toggle.py
git commit -m "feat: wire sales-intelligence pipeline into campaigns behind opt-in settings toggle"
```

---

## Post-plan state

After Task 9: `sales_intelligence_enabled` exists as an app_setting, default off. Every existing
campaign behavior is unchanged until a user explicitly turns it on. Once on, every lead scraped
by a campaign gets a `company_profiles` row with a qualification verdict and (for qualified
leads) a structured, evidence-tracked company research profile, viewable via
`GET /api/leads/{id}/research`. `enriched_data`/`scores`/HOT-WARM-COLD scoring continue to work
exactly as before, since the Company Research Agent calls the same underlying functions.

Not built here (tracked for sub-projects 2–6 per the approved spec): ICP Match scoring, Sales
Intelligence briefing, Personalization, Decision Maker Discovery, Contact Verification, the 6
Lead Details UI tabs, hiring/news/partnership signal collection, and any Settings-page UI
toggle (this sub-project is API/backend-only for the toggle).
