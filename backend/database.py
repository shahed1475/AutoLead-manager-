"""
database.py — SQLite (aiosqlite) database layer for AutoLead local development.

Drop-in replacement for the asyncpg/PostgreSQL version.
All public function signatures and return types are identical so routers,
scrapers, and main.py require zero changes.

Compatibility shim
------------------
_SQLiteConn wraps aiosqlite.Connection and exposes the asyncpg-style API:
  conn.fetch(sql, *args)      -> List[dict]
  conn.fetchrow(sql, *args)   -> Optional[dict]
  conn.fetchval(sql, *args)   -> scalar or None
  conn.execute(sql, *args)    -> "EXEC N" string (rowcount)

Parameter translation
---------------------
asyncpg $1/$2/... markers are auto-converted to SQLite ? at query time.
"""

import aiosqlite
import json
import logging
import math
import os
import re
import sqlite3
from difflib import SequenceMatcher
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from .config import get_settings
from .validators import clean_business_name, clean_email, clean_phone, normalize_website

logger   = logging.getLogger(__name__)
settings = get_settings()

DB_PATH = settings.database_path

_JSON_ARRAY_COLS = frozenset({
    "marketing_gaps", "issues", "conversion_gaps", "seo_gaps",
    "pitch_angles", "key_problems", "sources",
    "services", "products", "social_profiles", "tech_stack", "partnerships",
    "evidence_ids", "sources_planned",
})

_PG_PARAM_RE = re.compile(r'\$\d+')


def _pg_to_sqlite(sql: str) -> str:
    return _PG_PARAM_RE.sub('?', sql)


def _row_to_dict(row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    d = dict(row)
    for col in _JSON_ARRAY_COLS:
        val = d.get(col)
        if isinstance(val, str) and val:
            try:
                d[col] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass
    return d


# ─────────────────────────────────────────────────────────────────────────────
# asyncpg-compatible connection wrapper
# ─────────────────────────────────────────────────────────────────────────────

class _SQLiteConn:
    """Wraps aiosqlite.Connection with asyncpg-style fetch/fetchrow/fetchval/execute."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def fetch(self, sql: str, *args) -> List[Dict[str, Any]]:
        sql = _pg_to_sqlite(sql)
        async with self._conn.execute(sql, args) as cur:
            rows = await cur.fetchall()
        return [_row_to_dict(r) for r in rows]

    async def fetchrow(self, sql: str, *args) -> Optional[Dict[str, Any]]:
        sql = _pg_to_sqlite(sql)
        async with self._conn.execute(sql, args) as cur:
            row = await cur.fetchone()
        return _row_to_dict(row) if row else None

    async def fetchval(self, sql: str, *args) -> Any:
        """Return first column of first row. Handles RETURNING by using lastrowid."""
        has_ret = bool(re.search(r'\bRETURNING\b', sql, re.I))
        sql_c   = _pg_to_sqlite(sql)
        if has_ret:
            sql_exec = re.sub(r'\s+RETURNING\s+\w+', '', sql_c, flags=re.I)
            cur = await self._conn.execute(sql_exec, args)
            await self._conn.commit()
            return cur.lastrowid
        async with self._conn.execute(sql_c, args) as cur:
            row = await cur.fetchone()
        return row[0] if row else None

    async def execute(self, sql: str, *args) -> str:
        sql_c = _pg_to_sqlite(sql)
        cur   = await self._conn.execute(sql_c, args)
        await self._conn.commit()
        return f"EXEC {cur.rowcount}"

    async def _executescript(self, script: str) -> None:
        await self._conn.executescript(script)

    async def _raw_execute(self, sql: str) -> None:
        await self._conn.execute(sql)
        await self._conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Pool lifecycle
# ─────────────────────────────────────────────────────────────────────────────

async def init_db() -> None:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as raw:
        raw.row_factory = aiosqlite.Row
        conn = _SQLiteConn(raw)
        await _run_migrations(conn, raw)
    logger.info("SQLite database ready at %s", DB_PATH)


async def close_db() -> None:
    logger.info("SQLite: no pool to close")


@asynccontextmanager
async def get_db() -> AsyncGenerator[_SQLiteConn, None]:
    async with aiosqlite.connect(DB_PATH) as raw:
        raw.row_factory = aiosqlite.Row
        await raw.execute("PRAGMA journal_mode=WAL")
        await raw.execute("PRAGMA foreign_keys=ON")
        # 30-second busy timeout — prevents "database is locked" under concurrent async writes
        await raw.execute("PRAGMA busy_timeout=30000")
        yield _SQLiteConn(raw)


class _SQLiteTxConn(_SQLiteConn):
    """
    Same asyncpg-style API as _SQLiteConn, but execute()/fetchval() do NOT
    auto-commit per call — the enclosing `transaction()` context manager
    commits once at the end (or rolls back on exception), so multi-step
    writes (e.g. insert lead + log campaign action) are atomic.
    """

    async def execute(self, sql: str, *args) -> str:
        sql_c = _pg_to_sqlite(sql)
        cur   = await self._conn.execute(sql_c, args)
        return f"EXEC {cur.rowcount}"

    async def fetchval(self, sql: str, *args) -> Any:
        has_ret = bool(re.search(r'\bRETURNING\b', sql, re.I))
        sql_c   = _pg_to_sqlite(sql)
        if has_ret:
            sql_exec = re.sub(r'\s+RETURNING\s+\w+', '', sql_c, flags=re.I)
            cur = await self._conn.execute(sql_exec, args)
            return cur.lastrowid
        async with self._conn.execute(sql_c, args) as cur:
            row = await cur.fetchone()
        return row[0] if row else None


@asynccontextmanager
async def transaction() -> AsyncGenerator[_SQLiteTxConn, None]:
    """
    Multi-statement atomic transaction. Use for related writes that must all
    succeed or all fail together (e.g. saving a scraped lead + its
    campaign_log row). Commits once on clean exit, rolls back on exception.
    """
    async with aiosqlite.connect(DB_PATH) as raw:
        raw.row_factory = aiosqlite.Row
        await raw.execute("PRAGMA journal_mode=WAL")
        await raw.execute("PRAGMA foreign_keys=ON")
        await raw.execute("PRAGMA busy_timeout=30000")
        try:
            yield _SQLiteTxConn(raw)
            await raw.commit()
        except Exception:
            await raw.rollback()
            raise


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS leads (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    business_name        TEXT    NOT NULL,
    phone                TEXT,
    email                TEXT,
    website              TEXT,
    address              TEXT,
    niche                TEXT,
    city                 TEXT,
    country              TEXT,
    rating               REAL,
    reviews_count        INTEGER,
    review_count         INTEGER,
    source               TEXT,
    status               TEXT    DEFAULT 'PENDING',
    channel              TEXT,
    score                INTEGER DEFAULT 0,
    score_label          TEXT    DEFAULT 'COLD',
    score_category       TEXT    DEFAULT 'COLD',
    ai_whatsapp_msg      TEXT,
    ai_email_subject     TEXT,
    ai_email_body        TEXT,
    ai_followup_msg      TEXT,
    ai_follow_up_1       TEXT,
    ai_follow_up_2       TEXT,
    ai_follow_up_3       TEXT,
    enriched_at          TIMESTAMP,
    website_summary      TEXT,
    business_gaps        TEXT,
    pain_points          TEXT,
    personalization_hook TEXT,
    verified_email       INTEGER DEFAULT 0,
    has_social_links     INTEGER DEFAULT 0,
    sent_at              TIMESTAMP,
    followup_sent_at     TIMESTAMP,
    follow_up_1_sent_at  TIMESTAMP,
    follow_up_2_sent_at  TIMESTAMP,
    follow_up_3_sent_at  TIMESTAMP,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_email_ci
    ON leads (LOWER(email)) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_phone
    ON leads (phone) WHERE phone IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_leads_website ON leads (website);
CREATE INDEX IF NOT EXISTS idx_leads_status  ON leads (status);
CREATE INDEX IF NOT EXISTS idx_leads_niche   ON leads (niche);
CREATE INDEX IF NOT EXISTS idx_leads_city    ON leads (city);
CREATE INDEX IF NOT EXISTS idx_leads_score   ON leads (score_label);
CREATE INDEX IF NOT EXISTS idx_leads_source  ON leads (source);
CREATE INDEX IF NOT EXISTS idx_leads_created ON leads (created_at);

CREATE TABLE IF NOT EXISTS campaign_log (
    id        INTEGER   PRIMARY KEY AUTOINCREMENT,
    lead_id   INTEGER   REFERENCES leads(id) ON DELETE SET NULL,
    channel   TEXT,
    action    TEXT,
    success   INTEGER   DEFAULT 0,
    error_msg TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_log_lead      ON campaign_log (lead_id);
CREATE INDEX IF NOT EXISTS idx_log_timestamp ON campaign_log (timestamp);

CREATE TABLE IF NOT EXISTS campaign_runs (
    id          INTEGER   PRIMARY KEY AUTOINCREMENT,
    niche       TEXT,
    city        TEXT,
    country     TEXT,
    channel     TEXT,
    daily_cap   INTEGER   DEFAULT 20,
    leads_found INTEGER   DEFAULT 0,
    leads_sent  INTEGER   DEFAULT 0,
    sources     TEXT,
    started_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    status      TEXT      DEFAULT 'RUNNING'
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON campaign_runs (started_at);

CREATE TABLE IF NOT EXISTS reply_inbox (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id      INTEGER REFERENCES leads(id) ON DELETE SET NULL,
    from_email   TEXT    NOT NULL,
    subject      TEXT,
    body_snippet TEXT,
    received_at  TEXT,
    intent       TEXT    DEFAULT 'NEUTRAL',
    processed    INTEGER DEFAULT 0,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_inbox_lead    ON reply_inbox (lead_id);
CREATE INDEX IF NOT EXISTS idx_inbox_email   ON reply_inbox (from_email);
CREATE INDEX IF NOT EXISTS idx_inbox_created ON reply_inbox (created_at);

CREATE TABLE IF NOT EXISTS enriched_data (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id               INTEGER UNIQUE REFERENCES leads(id) ON DELETE CASCADE,
    business_summary      TEXT,
    target_audience       TEXT,
    service_level         TEXT,
    brand_positioning     TEXT,
    marketing_gaps        TEXT,
    growth_potential      TEXT,
    best_pitch_strategy   TEXT,
    personalization_hook  TEXT,
    website_text          TEXT,
    website_quality_score REAL    DEFAULT 0,
    issues                TEXT,
    conversion_gaps       TEXT,
    seo_gaps              TEXT,
    pitch_angles          TEXT,
    enriched_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_enriched_lead ON enriched_data (lead_id);

CREATE TABLE IF NOT EXISTS scores (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id             INTEGER UNIQUE REFERENCES leads(id) ON DELETE CASCADE,
    digital_score       REAL    DEFAULT 0,
    website_score       REAL    DEFAULT 0,
    business_score      REAL    DEFAULT 0,
    opportunity_score   REAL    DEFAULT 0,
    final_score         REAL    DEFAULT 0,
    category            TEXT    DEFAULT 'COLD',
    key_problems        TEXT,
    opportunity_summary TEXT,
    pitch_angle         TEXT,
    scored_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_scores_lead ON scores (lead_id);

CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER   PRIMARY KEY AUTOINCREMENT,
    lead_id       INTEGER   REFERENCES leads(id) ON DELETE CASCADE,
    sequence_step INTEGER   DEFAULT 1,
    message_type  TEXT,
    subject       TEXT,
    body          TEXT,
    status        TEXT      DEFAULT 'PENDING',
    sent_at       TIMESTAMP,
    scheduled_for TIMESTAMP,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_messages_lead   ON messages (lead_id);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages (status);

CREATE TABLE IF NOT EXISTS replies (
    id              INTEGER   PRIMARY KEY AUTOINCREMENT,
    lead_id         INTEGER   REFERENCES leads(id) ON DELETE CASCADE,
    message_id      INTEGER   REFERENCES messages(id) ON DELETE CASCADE,
    reply_text      TEXT,
    detected_intent TEXT,
    raw_email_data  TEXT,
    received_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_replies_lead ON replies (lead_id);

CREATE TABLE IF NOT EXISTS campaigns (
    id            INTEGER   PRIMARY KEY AUTOINCREMENT,
    niche         TEXT,
    city          TEXT,
    country       TEXT,
    sources       TEXT,
    channel       TEXT,
    daily_cap     INTEGER,
    leads_found   INTEGER   DEFAULT 0,
    leads_sent    INTEGER   DEFAULT 0,
    leads_replied INTEGER   DEFAULT 0,
    status        TEXT      DEFAULT 'RUNNING',
    started_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at  TIMESTAMP
);

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

CREATE TABLE IF NOT EXISTS pain_points (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    company_profile_id   INTEGER NOT NULL REFERENCES company_profiles(id) ON DELETE CASCADE,
    title                TEXT NOT NULL,
    description          TEXT,
    evidence_snippet     TEXT,
    source_url           TEXT,
    confidence           REAL DEFAULT 0,
    severity             TEXT DEFAULT 'medium',
    classification       TEXT DEFAULT 'inferred',
    operational_impact   TEXT,
    customer_impact      TEXT,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pain_points_profile ON pain_points (company_profile_id);

CREATE TABLE IF NOT EXISTS business_opportunities (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    company_profile_id   INTEGER NOT NULL REFERENCES company_profiles(id) ON DELETE CASCADE,
    pain_point_id        INTEGER REFERENCES pain_points(id) ON DELETE SET NULL,
    area                 TEXT NOT NULL,
    title                TEXT NOT NULL,
    description          TEXT,
    confidence           REAL DEFAULT 0,
    classification       TEXT DEFAULT 'inferred',
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_biz_opp_profile ON business_opportunities (company_profile_id);

CREATE TABLE IF NOT EXISTS solution_recommendations (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    company_profile_id       INTEGER NOT NULL REFERENCES company_profiles(id) ON DELETE CASCADE,
    business_opportunity_id  INTEGER REFERENCES business_opportunities(id) ON DELETE SET NULL,
    service_name             TEXT NOT NULL,
    reason                   TEXT,
    evidence_ids             TEXT,
    confidence                REAL DEFAULT 0,
    created_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_solution_recs_profile ON solution_recommendations (company_profile_id);

-- Phase 3 — Marketing Agent drafts. Deliberately NOT the pre-existing `messages`
-- table above: that table already has a live autonomous consumer
-- (followup_engine.py), whose schedule_followups_for_lead() idempotency check
-- is `count_messages_for_lead(lead_id) > 0` with no message_type filter — any
-- row inserted here into `messages` would silently block that lead's follow-up
-- sequence forever. Kept fully isolated instead.
CREATE TABLE IF NOT EXISTS generated_messages (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id              INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    company_profile_id   INTEGER REFERENCES company_profiles(id) ON DELETE SET NULL,
    channel              TEXT NOT NULL,                       -- EMAIL | WHATSAPP
    variant              TEXT NOT NULL DEFAULT 'PRIMARY',      -- PRIMARY | ALTERNATIVE_1 | ALTERNATIVE_2
    strategy             TEXT,
    pain_point           TEXT,
    evidence             TEXT,
    business_impact      TEXT,
    solution             TEXT,
    business_benefit     TEXT,
    service_name         TEXT,
    subject              TEXT,
    message              TEXT NOT NULL,
    cta                  TEXT,
    confidence           REAL DEFAULT 0,
    approval_status      TEXT DEFAULT 'READY_FOR_REVIEW',      -- READY_FOR_REVIEW | APPROVED | REJECTED
    rejection_reason     TEXT,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at          TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_generated_messages_lead ON generated_messages (lead_id);

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

-- Phase 1 — Universal Lead Discovery. One row per Quick Search or Campaign
-- discovery run; campaign_run_id links a CAMPAIGN-mode row to the existing
-- campaign_runs table (campaign_runs itself is untouched).
CREATE TABLE IF NOT EXISTS lead_discovery_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    mode                TEXT NOT NULL,
    raw_query           TEXT,
    niche               TEXT,
    city                TEXT,
    country             TEXT,
    target_count        INTEGER,
    planner_intent      TEXT,
    planner_confidence  REAL,
    sources_planned     TEXT,
    status              TEXT DEFAULT 'QUEUED',
    raw_candidates      INTEGER DEFAULT 0,
    deduplicated_count  INTEGER DEFAULT 0,
    results_count       INTEGER DEFAULT 0,
    campaign_run_id     INTEGER REFERENCES campaign_runs(id) ON DELETE SET NULL,
    error_message       TEXT,
    started_at          TIMESTAMP,
    finished_at         TIMESTAMP,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_discovery_runs_status ON lead_discovery_runs (status);

-- One row per (lead, source) that ever discovered it — preserves provenance
-- across merges instead of discarding the second/third source's info.
-- run_id is nullable: Campaign-mode scraping (which doesn't use this table
-- in Phase 1 — see design spec) never populates it.
CREATE TABLE IF NOT EXISTS lead_sources (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id            INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    source             TEXT NOT NULL,
    source_identifier  TEXT,
    run_id             INTEGER REFERENCES lead_discovery_runs(id) ON DELETE SET NULL,
    discovered_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    raw_snapshot       TEXT
);
CREATE INDEX IF NOT EXISTS idx_lead_sources_lead ON lead_sources (lead_id);
CREATE INDEX IF NOT EXISTS idx_lead_sources_run  ON lead_sources (run_id);

-- Browser Research Agent — independent subsystem (see
-- docs/superpowers/specs/2026-08-25-browser-research-agent-design.md).
-- Deliberately separate from lead_discovery_runs/lead_sources above: same
-- provenance *pattern*, no coupling to Phase 1's tables. lead_id links a
-- completed/partial result into the main leads table via the existing
-- create_or_merge_lead() — optional, nullable.
CREATE TABLE IF NOT EXISTS lead_research_sessions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    niche                 TEXT NOT NULL,
    location              TEXT NOT NULL,
    country               TEXT,
    target_count          INTEGER NOT NULL,
    status                TEXT DEFAULT 'QUEUED',
    current_action        TEXT,
    current_query         TEXT,
    current_business      TEXT,
    current_source        TEXT,
    current_city          TEXT,
    research_phase        TEXT,
    leads_found           INTEGER DEFAULT 0,
    leads_completed       INTEGER DEFAULT 0,
    leads_failed          INTEGER DEFAULT 0,
    businesses_researched INTEGER DEFAULT 0,
    businesses_skipped    INTEGER DEFAULT 0,
    processed_keys        TEXT,
    resume_count          INTEGER DEFAULT 0,
    resumable             INTEGER DEFAULT 0,
    target_titles         TEXT,
    error_message         TEXT,
    started_at            TIMESTAMP,
    finished_at           TIMESTAMP,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_research_sessions_status ON lead_research_sessions (status);

CREATE TABLE IF NOT EXISTS lead_research_results (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id               INTEGER NOT NULL REFERENCES lead_research_sessions(id) ON DELETE CASCADE,
    lead_id                  INTEGER REFERENCES leads(id) ON DELETE SET NULL,
    city                     TEXT,
    state                    TEXT,
    country                  TEXT,
    business_name            TEXT,
    business_phone           TEXT,
    business_email           TEXT,
    business_website         TEXT,
    business_email_status    TEXT DEFAULT 'UNCONFIRMED',
    management_contact_name  TEXT,
    management_title         TEXT,
    management_phone         TEXT,
    management_phone_type    TEXT,
    management_email         TEXT,
    management_email_status  TEXT DEFAULT 'UNCONFIRMED',
    confidence               REAL DEFAULT 0,
    research_status          TEXT DEFAULT 'PENDING',
    research_notes           TEXT,
    created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_research_results_session ON lead_research_results (session_id);

CREATE TABLE IF NOT EXISTS lead_research_evidence (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id   INTEGER NOT NULL REFERENCES lead_research_results(id) ON DELETE CASCADE,
    field_name  TEXT NOT NULL,
    source_type TEXT,
    source_url  TEXT,
    snippet     TEXT,
    confidence  REAL DEFAULT 0,
    status      TEXT DEFAULT 'UNCONFIRMED',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_research_evidence_result ON lead_research_evidence (result_id);

-- Every named decision maker found for a research result (name + title seen
-- on a real page). The primary one is also mirrored into the result's
-- management_contact_name/title columns for existing readers.
CREATE TABLE IF NOT EXISTS lead_research_decision_makers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id     INTEGER NOT NULL REFERENCES lead_research_results(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    title         TEXT,
    matched_title TEXT,
    source_url    TEXT,
    snippet       TEXT,
    confidence    REAL DEFAULT 0,
    status        TEXT DEFAULT 'FOUND',
    is_primary    INTEGER DEFAULT 0,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_research_dm_result ON lead_research_decision_makers (result_id);

-- Find leads runs: one user request that chains the steps they picked —
-- collect (always) -> deep research (optional) -> draft outreach (optional) —
-- over the same set of leads. Drafts only: a run never sends (sending stays
-- the human-approved AI Lab path). JSON lists are stored as TEXT.
CREATE TABLE IF NOT EXISTS lead_runs (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    niche                TEXT NOT NULL,
    location             TEXT NOT NULL,
    target_count         INTEGER NOT NULL,
    steps                TEXT NOT NULL,
    channel              TEXT DEFAULT 'EMAIL',
    target_titles        TEXT,
    hot_warm_only        INTEGER DEFAULT 1,
    status               TEXT DEFAULT 'QUEUED',
    stage                TEXT,
    discovery_run_id     INTEGER,
    research_session_id  INTEGER,
    lead_ids             TEXT,
    leads_found          INTEGER DEFAULT 0,
    leads_researched     INTEGER DEFAULT 0,
    drafts_written       INTEGER DEFAULT 0,
    drafts_skipped       INTEGER DEFAULT 0,
    current_item         TEXT,
    error_message        TEXT,
    resume_count         INTEGER DEFAULT 0,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at           TIMESTAMP,
    finished_at          TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_lead_runs_status ON lead_runs (status);

-- Client portal (separate link for clients): accounts signed in with an
-- emailed code, their lead requests, and what the owner delivers. Kept apart
-- from the owner's own tables; clients never read leads/settings directly.
CREATE TABLE IF NOT EXISTS portal_clients (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    email          TEXT NOT NULL UNIQUE,
    name           TEXT,
    company        TEXT,
    status         TEXT DEFAULT 'ACTIVE',      -- ACTIVE | BLOCKED
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login_at  TIMESTAMP
);
CREATE TABLE IF NOT EXISTS portal_login_codes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL,
    code_hash   TEXT NOT NULL,
    attempts    INTEGER DEFAULT 0,
    expires_at  TIMESTAMP NOT NULL,
    used_at     TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_portal_codes_email ON portal_login_codes (email, created_at);
CREATE TABLE IF NOT EXISTS portal_sessions (
    token_hash  TEXT PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES portal_clients(id) ON DELETE CASCADE,
    expires_at  TIMESTAMP NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS portal_requests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id     INTEGER NOT NULL REFERENCES portal_clients(id) ON DELETE CASCADE,
    niche         TEXT NOT NULL,
    location      TEXT NOT NULL,
    target_count  INTEGER NOT NULL,
    details       TEXT,
    status        TEXT DEFAULT 'NEW',          -- NEW | IN_PROGRESS | DELIVERED | CLOSED
    admin_note    TEXT,
    lead_run_id   INTEGER REFERENCES lead_runs(id) ON DELETE SET NULL,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    delivered_at  TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_portal_requests_client ON portal_requests (client_id);

-- One private HOM workspace per client (its own containers, network and data,
-- run by scripts/hom_supervisor.py). port = the workspace's local web port
-- (127.0.0.1 only; the client link routes to it). Deleted workspaces keep
-- their row (desired DELETED, port NULL) so the supervisor archives the data.
-- ── WhatsApp Campaigns (backend/whatsapp/) ────────────────────────────────────
-- Owner-only. Messages go out through whatsapp_sender.send_whatsapp (the one
-- WhatsApp send path) using the self-hosted WhatsApp Web engine (WAHA).
CREATE TABLE IF NOT EXISTS whatsapp_campaigns (
    id             INTEGER   PRIMARY KEY AUTOINCREMENT,
    name           TEXT      NOT NULL,
    template       TEXT,                                   -- NULL = each lead's approved WhatsApp draft
    use_ai_drafts  INTEGER   NOT NULL DEFAULT 0,
    status         TEXT      NOT NULL DEFAULT 'DRAFT',     -- DRAFT | RUNNING | PAUSED | DONE | CANCELLED
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at     TIMESTAMP,
    finished_at    TIMESTAMP
);
CREATE TABLE IF NOT EXISTS whatsapp_campaign_recipients (
    id           INTEGER   PRIMARY KEY AUTOINCREMENT,
    campaign_id  INTEGER   NOT NULL REFERENCES whatsapp_campaigns(id) ON DELETE CASCADE,
    lead_id      INTEGER   NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    phone        TEXT      NOT NULL,
    status       TEXT      NOT NULL DEFAULT 'PENDING',     -- PENDING | SENT | FAILED | SKIPPED | REPLIED
    message      TEXT,
    error        TEXT,
    sent_at      TIMESTAMP,
    UNIQUE (campaign_id, lead_id)
);
CREATE INDEX IF NOT EXISTS idx_wa_recipients_status ON whatsapp_campaign_recipients (campaign_id, status);
CREATE TABLE IF NOT EXISTS whatsapp_messages (
    id             INTEGER   PRIMARY KEY AUTOINCREMENT,
    lead_id        INTEGER   REFERENCES leads(id) ON DELETE SET NULL,
    chat_id        TEXT      NOT NULL,
    direction      TEXT      NOT NULL,                     -- IN | OUT
    body           TEXT      NOT NULL,
    source         TEXT,                                   -- campaign | auto_reply | manual | inbound
    wa_message_id  TEXT      UNIQUE,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_wa_messages_chat ON whatsapp_messages (chat_id, created_at);
CREATE TABLE IF NOT EXISTS whatsapp_activity (
    id          INTEGER   PRIMARY KEY AUTOINCREMENT,
    kind        TEXT      NOT NULL,     -- sent | received | auto_reply | opt_out | skipped | error | info
    lead_id     INTEGER,
    text        TEXT      NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- (portal_login_codes.payload: a sign-up's details, applied once the code is confirmed)
CREATE TABLE IF NOT EXISTS portal_workspaces (
    id          INTEGER   PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER   NOT NULL REFERENCES portal_clients(id) ON DELETE CASCADE,
    port        INTEGER   UNIQUE,
    desired     TEXT      NOT NULL DEFAULT 'RUNNING',   -- RUNNING | STOPPED | DELETED
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_portal_workspaces_client ON portal_workspaces (client_id);

-- ── Lead Search Automation (Phase 1 — single config + single queue) ──────────
-- See docs/superpowers/specs/2026-08-30-lead-search-automation-design.md
-- Discovery-only: collects deduplicated leads, never sends outreach.
CREATE TABLE IF NOT EXISTS automation_imports (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    filename       TEXT,
    layout         TEXT,
    n_locations    INTEGER DEFAULT 0,
    n_niches       INTEGER DEFAULT 0,
    n_combinations INTEGER DEFAULT 0,
    imported_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

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


-- ─────────────────────────────────────────────────────────────────────────────
-- Email Campaigns (PopupGenix Email Campaign module — n8n orchestrates
-- preparation, AutoLead's email_sender.send_email() is still the ONLY sender).
-- Feature-flagged by app_settings 'email_campaigns_enabled' (default false).
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS email_campaigns (
    id                  INTEGER   PRIMARY KEY AUTOINCREMENT,
    name                TEXT      NOT NULL,
    description         TEXT,
    status              TEXT      DEFAULT 'DRAFT',   -- DRAFT/READY/RUNNING/PAUSED/COMPLETED/FAILED
    test_mode           INTEGER   DEFAULT 1,          -- 1 = true (SQLite has no bool); production is a separate gated change
    test_recipient      TEXT      DEFAULT 'shahedalfahad20@gmail.com',
    ai_enabled          INTEGER   DEFAULT 1,
    from_name           TEXT,
    from_email          TEXT,
    attachment_filename TEXT,
    attachment_path     TEXT,                         -- backend-managed absolute path; NEVER returned to the frontend
    attachment_size     INTEGER,
    attachment_mime     TEXT,
    config_json         TEXT,                         -- extra per-campaign config (subject hints, ai model override, …)
    sender_profile_id   INTEGER,                       -- -> email_sender_profiles.id (NULL = use global SMTP, back-compat); SET NULL on profile delete (enforced in code, FK omitted for ALTER-safe migration)
    reply_to            TEXT,                          -- optional campaign-level Reply-To (does NOT change the authenticated From)
    total_leads         INTEGER   DEFAULT 0,
    valid_leads         INTEGER   DEFAULT 0,
    sent_count          INTEGER   DEFAULT 0,
    failed_count        INTEGER   DEFAULT 0,
    replied_count       INTEGER   DEFAULT 0,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at          TIMESTAMP,
    completed_at        TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_email_campaigns_status ON email_campaigns (status);

CREATE TABLE IF NOT EXISTS email_campaign_leads (
    id              INTEGER   PRIMARY KEY AUTOINCREMENT,
    campaign_id     INTEGER   NOT NULL REFERENCES email_campaigns(id) ON DELETE CASCADE,
    lead_key        TEXT      NOT NULL,               -- stable identity within the campaign (see build_lead_key)
    lead_id         INTEGER   REFERENCES leads(id) ON DELETE SET NULL,  -- link to the global lead when matched
    email           TEXT,
    first_name      TEXT,
    last_name       TEXT,
    company         TEXT,
    raw_json        TEXT,                             -- original imported row, kept for audit / immutability
    body_source     TEXT      DEFAULT 'ai',           -- 'ai' | 'provided'
    provided_body   TEXT,                             -- verbatim supplied body when body_source = 'provided'
    ai_subject      TEXT,
    ai_body         TEXT,
    status          TEXT      DEFAULT 'IMPORTED',
        -- IMPORTED/VALIDATED/MISSING_EMAIL/INVALID_EMAIL/DUPLICATE/READY/
        -- GENERATED/AI_GENERATION_FAILED/SENT/SEND_FAILED/SKIPPED/DO_NOT_CONTACT
    status_detail   TEXT,
    message_id      TEXT,
    sent_at         TIMESTAMP,
    failure_reason  TEXT,
    idempotency_key TEXT,                             -- set at send time = '<campaign_id>:<lead_key>'
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ecl_campaign_leadkey ON email_campaign_leads (campaign_id, lead_key);
CREATE INDEX IF NOT EXISTS idx_ecl_campaign_status  ON email_campaign_leads (campaign_id, status);

CREATE TABLE IF NOT EXISTS email_campaign_runs (
    id              INTEGER   PRIMARY KEY AUTOINCREMENT,
    campaign_id     INTEGER   NOT NULL REFERENCES email_campaigns(id) ON DELETE CASCADE,
    idempotency_key TEXT      NOT NULL,               -- caller-supplied; a repeat start with the same key returns this run
    status          TEXT      DEFAULT 'PENDING',      -- PENDING/PREPARING/SENDING/PAUSED/COMPLETED/FAILED
    n8n_trigger_ref TEXT,                             -- opaque ref from N8nCampaignExecutor; never shown to users
    batch_size      INTEGER   DEFAULT 0,
    processed_count INTEGER   DEFAULT 0,
    sent_count      INTEGER   DEFAULT 0,
    failed_count    INTEGER   DEFAULT 0,
    error           TEXT,
    started_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at    TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ecr_campaign_idem   ON email_campaign_runs (campaign_id, idempotency_key);
CREATE INDEX IF NOT EXISTS idx_ecr_campaign_status ON email_campaign_runs (campaign_id, status);

CREATE TABLE IF NOT EXISTS email_campaign_activity (
    id          INTEGER   PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER   NOT NULL REFERENCES email_campaigns(id) ON DELETE CASCADE,
    ts          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    level       TEXT      DEFAULT 'INFO',
    event       TEXT      NOT NULL,   -- campaign_created / leads_imported / campaign_started / ...
    lead_key    TEXT,                 -- optional, for per-lead events
    detail      TEXT                  -- human-readable; NEVER a secret
);
CREATE INDEX IF NOT EXISTS idx_eca_campaign_ts ON email_campaign_activity (campaign_id, id);

-- ─────────────────────────────────────────────────────────────────────────────
-- Sender Profiles (Checkpoint 4) — the authorized email account a campaign
-- sends from. AutoLead's email_sender / transport layer is still the ONLY
-- sender; a profile just selects the authenticated account + transport.
-- Secrets are encrypted at rest via secrets_crypto (the *_enc columns).
-- Single-operator app: no owner/tenant column.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS email_sender_profiles (
    id                      INTEGER   PRIMARY KEY AUTOINCREMENT,
    name                    TEXT      NOT NULL,
    provider                TEXT      NOT NULL,                 -- 'smtp' | 'gmail'
    transport               TEXT      NOT NULL DEFAULT 'smtp',  -- 'smtp' | 'gmail_api'
    email_address           TEXT      NOT NULL,                 -- authoritative From identity
    display_name            TEXT,
    reply_to                TEXT,                              -- profile default Reply-To (campaign may override)
    status                  TEXT      NOT NULL DEFAULT 'disconnected',  -- 'connected' | 'disconnected' | 'error'
    is_default              INTEGER   NOT NULL DEFAULT 0,
    -- SMTP
    smtp_host               TEXT,
    smtp_port               INTEGER,
    smtp_security           TEXT,                              -- 'ssl' | 'starttls'
    smtp_username           TEXT,
    smtp_password_enc       TEXT,                              -- secrets_crypto — NEVER returned by the API
    -- Gmail OAuth2
    oauth_client_id         TEXT,                              -- which client id authorized this (not secret)
    oauth_refresh_token_enc TEXT,                              -- secrets_crypto — NEVER returned/logged
    oauth_access_token_enc  TEXT,                              -- secrets_crypto — NEVER returned/logged
    oauth_expires_at        TIMESTAMP,
    oauth_scopes            TEXT,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_tested_at          TIMESTAMP,
    last_error              TEXT
);
CREATE INDEX IF NOT EXISTS idx_sender_profiles_provider ON email_sender_profiles (provider);
CREATE INDEX IF NOT EXISTS idx_sender_profiles_default  ON email_sender_profiles (is_default);

CREATE TABLE IF NOT EXISTS oauth_states (
    state       TEXT      PRIMARY KEY,
    purpose     TEXT      NOT NULL,          -- 'gmail_sender'
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at  TIMESTAMP NOT NULL,
    used_at     TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_oauth_states_expires ON oauth_states (expires_at);
"""


async def _add_col_if_missing(raw: aiosqlite.Connection, table: str, col: str, typedef: str) -> None:
    async with raw.execute(f"PRAGMA table_info({table})") as cur:
        existing = {row[1] async for row in cur}
    if col not in existing:
        try:
            await raw.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
            await raw.commit()
            logger.info("Schema: added column %s.%s (%s)", table, col, typedef)
        except Exception as exc:
            logger.warning("Schema: could not add %s.%s — %s", table, col, exc)


async def _migrate_replies_cascade(raw: aiosqlite.Connection) -> None:
    """
    Rebuild `replies` with ON DELETE CASCADE on lead_id/message_id if an older
    install created it without a cascade action. With PRAGMA foreign_keys=ON,
    a bare (no-action) FK blocks deleting any lead that has a reply — this
    fixes that by giving existing installs the same cascade fresh installs get.
    """
    try:
        async with raw.execute("PRAGMA foreign_key_list(replies)") as cur:
            fks = await cur.fetchall()
    except Exception:
        return
    needs_rebuild = any((fk[3] == "lead_id" and (fk[6] or "NO ACTION") != "CASCADE") for fk in fks)
    if not needs_rebuild:
        return
    try:
        await raw.executescript("""
            CREATE TABLE replies_new (
                id              INTEGER   PRIMARY KEY AUTOINCREMENT,
                lead_id         INTEGER   REFERENCES leads(id) ON DELETE CASCADE,
                message_id      INTEGER   REFERENCES messages(id) ON DELETE CASCADE,
                reply_text      TEXT,
                detected_intent TEXT,
                raw_email_data  TEXT,
                received_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO replies_new SELECT id, lead_id, message_id, reply_text,
                   detected_intent, raw_email_data, received_at FROM replies;
            DROP TABLE replies;
            ALTER TABLE replies_new RENAME TO replies;
            CREATE INDEX IF NOT EXISTS idx_replies_lead ON replies (lead_id);
        """)
        await raw.commit()
        logger.info("Schema: rebuilt replies table with ON DELETE CASCADE")
    except Exception as exc:
        logger.warning("Schema: could not migrate replies to CASCADE — %s", exc)


async def _migrate_email_index_ci(raw: aiosqlite.Connection) -> None:
    """
    Older installs have a case-sensitive UNIQUE index on leads.email. Drop it
    and rely on the case-insensitive idx_leads_email_ci created by the schema
    script — if two rows already differ only by email case, log and skip
    rather than crash startup.
    """
    try:
        await raw.execute("DROP INDEX IF EXISTS idx_leads_email")
        await raw.commit()
    except Exception as exc:
        logger.warning("Schema: could not drop legacy idx_leads_email — %s", exc)


async def _run_migrations(conn: _SQLiteConn, raw: aiosqlite.Connection) -> None:
    await raw.execute("PRAGMA journal_mode=WAL")
    await raw.execute("PRAGMA foreign_keys=ON")
    # executescript implicitly commits; run schema creation
    await raw.executescript(_SCHEMA_SQL)
    await _migrate_replies_cascade(raw)
    await _migrate_email_index_ci(raw)

    # Upgrade missing columns for leads table (idempotent — safe on re-runs)
    for col, typedef in [
        ("country",        "TEXT"),
        ("reviews_count",  "INTEGER"),
        ("score_category", "TEXT DEFAULT 'COLD'"),
        # Lead Search Upgrade (2026-09-04) — discovery provenance + research handoff
        ("source_type",             "TEXT"),
        ("research_status",         "TEXT DEFAULT 'NOT_STARTED'"),
        ("email_status",            "TEXT"),
        ("last_research_session_id", "INTEGER"),
        ("excluded_from_research",  "INTEGER DEFAULT 0"),
        ("latitude",                "REAL"),
        ("longitude",               "REAL"),
        ("google_place_id",         "TEXT"),
        # Phase A — preserve incomplete businesses. FULL = has a contact
        # channel; MINIMAL = name + location/category only (still a valid
        # research candidate, excluded from outreach until enriched).
        ("discovery_status",        "TEXT"),
        ("research_submission_source", "TEXT"),
    ]:
        await _add_col_if_missing(raw, "leads", col, typedef)

    # Upgrade columns for enriched_data (idempotent)
    for col, typedef in [
        ("personalization_hook",  "TEXT"),
        ("website_quality_score", "REAL DEFAULT 0"),
        ("issues",                "TEXT"),
        ("conversion_gaps",       "TEXT"),
        ("seo_gaps",              "TEXT"),
        ("pitch_angles",          "TEXT"),
    ]:
        await _add_col_if_missing(raw, "enriched_data", col, typedef)

    # Upgrade columns for campaign_runs (idempotent) — fine-grained pipeline
    # stage (QUEUED/STARTING/SCRAPING/ENRICHING/SCORING/WRITING/SENDING/
    # COMPLETED/STOPPED/FAILED/PAUSED), separate from the coarse `status`
    # column so existing status-based logic (below) is unaffected.
    for col, typedef in [
        ("stage", "TEXT DEFAULT 'QUEUED'"),
    ]:
        await _add_col_if_missing(raw, "campaign_runs", col, typedef)

    # Upgrade columns for replies (idempotent) — auto-reply draft-and-approve
    # flow: a positive-intent reply gets an AI-drafted response held for human
    # approval (draft_status NONE/PENDING_APPROVAL/SENT/DISCARDED) before it
    # ever reaches a client.
    for col, typedef in [
        ("draft_subject", "TEXT"),
        ("draft_body",    "TEXT"),
        ("draft_status",  "TEXT DEFAULT 'NONE'"),
        ("draft_sent_at", "TIMESTAMP"),
    ]:
        await _add_col_if_missing(raw, "replies", col, typedef)

    # Upgrade columns for business_opportunities (idempotent) — Phase 2's
    # OpportunityAgent output. Phase 1's naive per-pain-point opportunities
    # (area/title/description/confidence/classification) keep working
    # unchanged; these are additive fields the richer agent also populates.
    for col, typedef in [
        ("why_it_matters", "TEXT"),
        ("business_ease",  "TEXT"),
        ("priority",       "TEXT DEFAULT 'MEDIUM'"),
    ]:
        await _add_col_if_missing(raw, "business_opportunities", col, typedef)

    # Upgrade columns for scores (idempotent) — Phase 2's additive
    # "intelligence fit" score. Parallel to final_score/category, never
    # replacing them — every existing HOT/WARM/COLD read path is unaffected.
    for col, typedef in [
        ("intelligence_score",    "REAL DEFAULT 0"),
        ("intelligence_category", "TEXT"),
    ]:
        await _add_col_if_missing(raw, "scores", col, typedef)

    # Upgrade columns for replies (idempotent) — Phase 4's Reply Intelligence
    # Agent output. Additive alongside the existing detected_intent (5-value)
    # classification, which keeps driving REPLIED/SKIPPED transitions and
    # get_reply_summary() unchanged.
    for col, typedef in [
        ("rich_intent",        "TEXT"),
        ("intent_confidence",  "REAL DEFAULT 0"),
        ("recommended_action", "TEXT"),
    ]:
        await _add_col_if_missing(raw, "replies", col, typedef)

    # Browser Research Agent — live-progress + resumability columns, added
    # after the table's first release. Idempotent; a dev DB that already ran
    # CREATE TABLE IF NOT EXISTS for lead_research_sessions won't have these
    # without this block.
    for col, typedef in [
        ("current_source",        "TEXT"),
        ("current_city",          "TEXT"),
        ("research_phase",        "TEXT"),
        ("businesses_researched", "INTEGER DEFAULT 0"),
        ("businesses_skipped",    "INTEGER DEFAULT 0"),
        ("processed_keys",        "TEXT"),
        ("resume_count",          "INTEGER DEFAULT 0"),
        ("resumable",             "INTEGER DEFAULT 0"),
    ]:
        await _add_col_if_missing(raw, "lead_research_sessions", col, typedef)
    # Lead Search Upgrade — research handoff of existing leads
    for col, typedef in [
        ("mode",            "TEXT DEFAULT 'discovery'"),
        ("seed_businesses", "TEXT"),
        ("seed_lead_ids",   "TEXT"),
        ("submission_source", "TEXT"),
        ("target_titles",   "TEXT"),
    ]:
        await _add_col_if_missing(raw, "lead_research_sessions", col, typedef)
    for col, typedef in [
        ("emails_found",   "INTEGER DEFAULT 0"),
        ("leads_scored",   "INTEGER DEFAULT 0"),
        ("research_queued", "INTEGER DEFAULT 0"),
    ]:
        await _add_col_if_missing(raw, "lead_discovery_runs", col, typedef)
    await _add_col_if_missing(raw, "lead_research_results", "research_notes", "TEXT")

    # Lead Search Automation — new tables are created by executescript(_SCHEMA_SQL)
    # above; this block only adds columns to an already-created table on dev DBs.
    for col, typedef in [
        ("import_id",   "INTEGER"),
        ("next_run_at", "TIMESTAMP"),
    ]:
        await _add_col_if_missing(raw, "automation_state", col, typedef)

    # Sender Profiles (Checkpoint 4) — email_sender_profiles / oauth_states are
    # created by executescript(_SCHEMA_SQL) above; these columns are added to the
    # already-created email_campaigns table on existing DBs. Plain INTEGER (no
    # REFERENCES) because ALTER TABLE ADD COLUMN cannot add an FK with
    # PRAGMA foreign_keys=ON — the SET-NULL-on-delete is enforced in code.
    await _add_col_if_missing(raw, "email_campaigns", "sender_profile_id", "INTEGER")
    await _add_col_if_missing(raw, "email_campaigns", "reply_to", "TEXT")

    # The person to address at a lead (e.g. from an uploaded WhatsApp contact file).
    await _add_col_if_missing(raw, "leads", "contact_name", "TEXT")
    await _add_col_if_missing(raw, "whatsapp_campaigns", "meta_template", "TEXT")   # Meta API: approved template + variables

    # Client passwords (website sign-up / log-in). Only a bcrypt hash is kept.
    for col, typedef in (("password_hash", "TEXT"), ("email_verified_at", "TIMESTAMP"),
                         ("failed_logins", "INTEGER NOT NULL DEFAULT 0"), ("locked_until", "TIMESTAMP"),
                         # First-run setup (name, sector, company profile) before the dashboard
                         ("sector", "TEXT"), ("company_dna", "TEXT"), ("onboarded_at", "TIMESTAMP")):
        await _add_col_if_missing(raw, "portal_clients", col, typedef)
    # Sessions opened with an emailed code may set a new password for a short while.
    await _add_col_if_missing(raw, "portal_sessions", "via_code_at", "TIMESTAMP")
    await _add_col_if_missing(raw, "portal_login_codes", "payload", "TEXT")

    # Data normalisation
    # DO_NOT_CONTACT (Phase 4 opt-out) is a terminal, sticky status — must be in
    # this allow-list or a lead marked DO_NOT_CONTACT would silently revert to
    # PENDING on the next restart, undoing the opt-out.
    await raw.execute("""
        UPDATE leads SET status = 'PENDING'
        WHERE status IS NULL
           OR status NOT IN (
               'PENDING','SENT','REPLIED','SKIPPED','MESSAGES_READY','ENRICHED','SCORED','DO_NOT_CONTACT',
               'INTERESTED','MEETING','PROPOSAL','WON','LOST'
           )
    """)
    await raw.execute("""
        UPDATE campaign_runs SET status = 'FAILED', stage = 'FAILED', finished_at = CURRENT_TIMESTAMP
        WHERE status = 'RUNNING'
    """)
    await raw.execute("""
        UPDATE company_profiles SET status = 'PENDING'
        WHERE status IN ('QUALIFYING', 'RESEARCHING', 'FAILED')
    """)

    # Lead Search Upgrade — one-time back-fill of source_type / research_status
    # from existing provenance. Idempotent: only touches rows where the value
    # is still unset / can be derived. Runs cheaply on every startup.
    await raw.execute("""
        UPDATE leads SET source_type = 'automation'
        WHERE source_type IS NULL AND id IN (
            SELECT lead_id FROM lead_sources
            WHERE run_id IN (SELECT id FROM lead_discovery_runs WHERE mode = 'AUTOMATION')
        )
    """)
    await raw.execute("""
        UPDATE leads SET source_type = 'manual'
        WHERE id IN (
            SELECT lead_id FROM lead_sources
            WHERE run_id IN (SELECT id FROM lead_discovery_runs WHERE mode = 'QUICK')
        )
    """)
    await raw.execute("""
        UPDATE leads SET discovery_status =
            CASE WHEN email IS NOT NULL OR phone IS NOT NULL OR website IS NOT NULL
                 THEN 'FULL' ELSE 'MINIMAL' END
        WHERE discovery_status IS NULL
    """)
    await raw.execute("""
        UPDATE leads SET research_status = 'COMPLETED',
                         last_research_session_id = (
                             SELECT r.session_id FROM lead_research_results r
                             WHERE r.lead_id = leads.id ORDER BY r.id DESC LIMIT 1
                         )
        WHERE (research_status IS NULL OR research_status = 'NOT_STARTED')
          AND id IN (SELECT lead_id FROM lead_research_results WHERE lead_id IS NOT NULL)
    """)
    await raw.commit()
    logger.info("Schema migrations applied")


# ─────────────────────────────────────────────────────────────────────────────
# Type helpers
# ─────────────────────────────────────────────────────────────────────────────

_TS_COLS = frozenset({
    "enriched_at", "sent_at", "followup_sent_at",
    "follow_up_1_sent_at", "follow_up_2_sent_at", "follow_up_3_sent_at",
})

_LEAD_WRITABLE = frozenset({
    "business_name", "phone", "email", "website", "address", "contact_name",
    "niche", "city", "country", "rating", "reviews_count", "review_count",
    "source", "status", "channel",
    "source_type", "research_status", "email_status", "last_research_session_id",
    "excluded_from_research", "latitude", "longitude", "google_place_id",
    "discovery_status", "research_submission_source",
    "score", "score_label", "score_category",
    "ai_whatsapp_msg", "ai_email_subject", "ai_email_body",
    "ai_followup_msg", "ai_follow_up_1", "ai_follow_up_2", "ai_follow_up_3",
    "enriched_at", "website_summary", "business_gaps", "pain_points",
    "personalization_hook", "verified_email", "has_social_links",
    "sent_at", "followup_sent_at",
    "follow_up_1_sent_at", "follow_up_2_sent_at", "follow_up_3_sent_at",
})


def _coerce(col: str, val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, list):
        return json.dumps(val)
    if col in _TS_COLS:
        if isinstance(val, datetime):
            if val.tzinfo is not None:
                val = val.replace(tzinfo=None)
            return val.isoformat()
        if isinstance(val, str):
            try:
                dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                return dt.replace(tzinfo=None).isoformat()
            except ValueError:
                return None
    return val


# ─────────────────────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────────────────────

# Settings keys holding secrets (SMTP/IMAP passwords, cloud LLM API keys) are
# encrypted at rest via secrets_crypto — transparent to every caller that
# already goes through get_setting/get_all_settings/upsert_setting.
_SECRET_SETTING_SUFFIXES = ("_password", "_api_key", "_secret", "_token")


def _is_secret_setting(key: str) -> bool:
    return key.lower().endswith(_SECRET_SETTING_SUFFIXES)


async def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    async with get_db() as conn:
        val = await conn.fetchval("SELECT value FROM app_settings WHERE key = $1", key)
    if val is not None and _is_secret_setting(key):
        from .secrets_crypto import decrypt
        val = decrypt(val)
    return val if val is not None else default


async def upsert_setting(key: str, value: str) -> None:
    stored = value
    if _is_secret_setting(key):
        from .secrets_crypto import encrypt
        stored = encrypt(value)
    async with get_db() as conn:
        await conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES ($1, $2, CURRENT_TIMESTAMP)
               ON CONFLICT (key) DO UPDATE
                 SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
            key, stored,
        )


async def get_all_settings() -> Dict[str, str]:
    async with get_db() as conn:
        rows = await conn.fetch("SELECT key, value FROM app_settings")
    from .secrets_crypto import decrypt
    return {
        r["key"]: (decrypt(r["value"]) if _is_secret_setting(r["key"]) else r["value"])
        for r in rows
    }


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard stats
# ─────────────────────────────────────────────────────────────────────────────

async def get_dashboard_stats() -> Dict[str, Any]:
    async with get_db() as conn:
        lead_row = await conn.fetchrow("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status NOT IN ('SENT','REPLIED','SKIPPED')
                          OR status IS NULL THEN 1 ELSE 0 END)           AS pending,
                SUM(CASE WHEN status = 'SENT'    THEN 1 ELSE 0 END)      AS sent,
                SUM(CASE WHEN status = 'REPLIED' THEN 1 ELSE 0 END)      AS replied,
                SUM(CASE WHEN status = 'SKIPPED' THEN 1 ELSE 0 END)      AS skipped,
                SUM(CASE WHEN score > 0 AND score_label = 'HOT'
                          AND status != 'SKIPPED' THEN 1 ELSE 0 END)     AS hot_leads,
                SUM(CASE WHEN score > 0 AND score_label = 'WARM'
                          AND status != 'SKIPPED' THEN 1 ELSE 0 END)     AS warm_leads,
                SUM(CASE WHEN score > 0 AND score_label = 'COLD'
                          AND status != 'SKIPPED' THEN 1 ELSE 0 END)     AS cold_leads
            FROM leads
        """)

        today_rows = await conn.fetch("""
            SELECT channel, COUNT(*) AS cnt
            FROM campaign_log
            WHERE success = 1
              AND action = 'SEND'
              AND DATE(timestamp) = DATE('now')
            GROUP BY channel
        """)

        unread = await conn.fetchval(
            "SELECT COUNT(*) FROM reply_inbox WHERE processed = 0"
        )

        avg_deal_str = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = 'avg_deal_value'"
        )

    by_channel  = {r["channel"]: r["cnt"] for r in today_rows}
    email_today = by_channel.get("EMAIL", 0) + by_channel.get("BOTH", 0)
    wa_today    = by_channel.get("WHATSAPP", 0) + by_channel.get("BOTH", 0)
    sent_today  = sum(by_channel.values())
    avg_deal    = float(avg_deal_str) if avg_deal_str else 500.0
    total       = lead_row["total"]   or 0
    replied     = lead_row["replied"] or 0

    return {
        "total_leads":         total,
        "pending":             lead_row["pending"]   or 0,
        "sent":                lead_row["sent"]      or 0,
        "replied":             replied,
        "skipped":             lead_row["skipped"]   or 0,
        "email_sent_today":    email_today,
        "whatsapp_sent_today": wa_today,
        "sent_today":          sent_today,
        "reply_rate":          round(replied / total * 100, 1) if total > 0 else 0.0,
        "estimated_revenue":   round(replied * avg_deal, 2),
        "hot_leads":           lead_row["hot_leads"]  or 0,
        "warm_leads":          lead_row["warm_leads"] or 0,
        "cold_leads":          lead_row["cold_leads"] or 0,
        "unread_replies":      unread or 0,
    }


async def get_weekly_activity() -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch("""
            WITH RECURSIVE dates(day) AS (
                SELECT DATE('now', '-6 days')
                UNION ALL
                SELECT DATE(day, '+1 day') FROM dates WHERE day < DATE('now')
            )
            SELECT
                d.day,
                CASE CAST(strftime('%w', d.day) AS INTEGER)
                    WHEN 0 THEN 'Sun' WHEN 1 THEN 'Mon' WHEN 2 THEN 'Tue'
                    WHEN 3 THEN 'Wed' WHEN 4 THEN 'Thu' WHEN 5 THEN 'Fri'
                    ELSE 'Sat'
                END AS day_label,
                COALESCE((
                    SELECT COUNT(*) FROM leads WHERE DATE(created_at) = d.day
                ), 0) AS leads_created,
                COALESCE(SUM(CASE WHEN cl.action='SEND' AND cl.success=1
                                   AND cl.channel IN ('EMAIL','BOTH')
                              THEN 1 ELSE 0 END), 0) AS email_sent,
                COALESCE(SUM(CASE WHEN cl.action='SEND' AND cl.success=1
                                   AND cl.channel IN ('WHATSAPP','BOTH')
                              THEN 1 ELSE 0 END), 0) AS whatsapp_sent,
                COALESCE(SUM(CASE WHEN cl.action='SEND' AND cl.success=1
                              THEN 1 ELSE 0 END), 0) AS total_sent
            FROM dates d
            LEFT JOIN campaign_log cl ON DATE(cl.timestamp) = d.day
            GROUP BY d.day
            ORDER BY d.day ASC
        """)
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Leads CRUD
# ─────────────────────────────────────────────────────────────────────────────

async def get_leads(
    page: int = 1,
    page_size: int = 50,
    status: Optional[str] = None,
    channel: Optional[str] = None,
    niche: Optional[str] = None,
    city: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    date_field: str = "created_at",
    score_label: Optional[str] = None,
    enriched_only: bool = False,
    source: Optional[str] = None,
    source_type: Optional[str] = None,
    research_status: Optional[str] = None,
) -> Dict[str, Any]:
    _SORTABLE    = {"business_name", "created_at", "sent_at", "status", "niche", "city", "score"}
    _DATE_FIELDS = {"created_at", "sent_at"}

    if sort_by not in _SORTABLE:    sort_by = "created_at"
    if date_field not in _DATE_FIELDS: date_field = "created_at"
    order = "DESC" if sort_dir.lower() == "desc" else "ASC"

    params: List[Any] = []

    def p(val: Any) -> str:
        params.append(val)
        return "?"

    conditions: List[str] = []
    if status:
        # "A,B,C" filters to any of several statuses (AI Lab's pre-send group).
        wanted = [s.strip().upper() for s in status.split(",") if s.strip()]
        if len(wanted) == 1:
            conditions.append(f"status = {p(wanted[0])}")
        elif wanted:
            conditions.append(f"status IN ({', '.join(p(s) for s in wanted)})")
    if channel:      conditions.append(f"channel = {p(channel.upper())}")
    if niche:        conditions.append(f"niche LIKE {p(f'%{niche}%')}")
    if city:         conditions.append(f"city LIKE {p(f'%{city}%')}")
    if search:
        term = f"%{search}%"
        conditions.append(f"(business_name LIKE {p(term)} OR email LIKE {p(term)} OR phone LIKE {p(term)})")
    if date_from:    conditions.append(f"DATE({date_field}) >= {p(date_from)}")
    if date_to:      conditions.append(f"DATE({date_field}) <= {p(date_to)}")
    if score_label and score_label.upper() in ("HOT", "WARM", "COLD"):
        conditions.append(f"score_label = {p(score_label.upper())}")
    if enriched_only:
        conditions.append("website_summary IS NOT NULL AND website_summary != ''")
    if source:          conditions.append(f"source = {p(source.upper())}")
    if source_type:     conditions.append(f"source_type = {p(source_type.lower())}")
    if research_status: conditions.append(f"research_status = {p(research_status.upper())}")

    where  = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    offset = (page - 1) * page_size

    async with get_db() as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM leads {where}", *params
        ) or 0
        rows = await conn.fetch(
            f"SELECT * FROM leads {where} ORDER BY {sort_by} {order} LIMIT ? OFFSET ?",
            *params, page_size, offset,
        )

    return {
        "items":       [dict(r) for r in rows],
        "total":       total,
        "page":        page,
        "page_size":   page_size,
        "total_pages": max(1, math.ceil(total / page_size)),
    }


async def get_lead_by_id(lead_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        row = await conn.fetchrow("SELECT * FROM leads WHERE id = $1", lead_id)
    return dict(row) if row else None


async def find_duplicate_lead(
    email: Optional[str],
    phone: Optional[str],
    website: Optional[str] = None,
    business_name: Optional[str] = None,
    city: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Cross-run duplicate check. Exact email/phone match is authoritative.
    When neither is available (a lead with only a name+website), fall back to
    a normalized-website match, then a normalized name+city match — otherwise
    such leads were never deduplicated against prior campaign runs at all.
    """
    conditions, params = [], []
    if email:
        params.append(email.lower())
        conditions.append("LOWER(email) = ?")
    if phone:
        params.append(phone)
        conditions.append("phone = ?")
    if conditions:
        async with get_db() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM leads WHERE ({' OR '.join(conditions)}) LIMIT 1",
                *params,
            )
        if row:
            return dict(row)

    norm_website = normalize_website(website) if website else None
    if norm_website:
        async with get_db() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM leads WHERE website IS NOT NULL AND LOWER(website) = LOWER($1) LIMIT 1",
                norm_website,
            )
        if row:
            return dict(row)

    norm_name = clean_business_name(business_name)
    if norm_name and city:
        async with get_db() as conn:
            row = await conn.fetchrow(
                """SELECT * FROM leads
                   WHERE LOWER(business_name) = LOWER($1) AND LOWER(city) = LOWER($2)
                   LIMIT 1""",
                norm_name, city,
            )
        if row:
            return dict(row)

    return None


_FUZZY_NAME_STOP = frozenset({
    "the", "a", "an", "and", "&", "of", "for", "at",
    "ltd", "llc", "inc", "co", "corp", "company",
})


def _fuzzy_norm_name(name: Optional[str], city: Optional[str]) -> str:
    raw = f"{name or ''} {city or ''}".lower()
    raw = re.sub(r"[^\w\s]", " ", raw)
    return " ".join(w for w in raw.split() if w not in _FUZZY_NAME_STOP)


async def find_duplicate_lead_fuzzy(
    email: Optional[str],
    phone: Optional[str],
    website: Optional[str] = None,
    business_name: Optional[str] = None,
    city: Optional[str] = None,
    threshold: float = 0.85,
) -> Optional[Dict[str, Any]]:
    """
    Like find_duplicate_lead, but adds a genuinely fuzzy name+city fallback
    (SequenceMatcher ratio >= threshold) when no exact email/phone/website/
    name+city match is found. find_duplicate_lead's own name+city signal is
    an EXACT match only — the real fuzzy matcher lives in
    scrapers/__init__.py's in-batch dedup (_norm_name/_is_fuzzy_dup), which
    can't be imported here (scrapers already imports database — importing
    back would be circular), so the same small algorithm (stopword
    normalization + SequenceMatcher >= 0.85) is reimplemented here.

    Used only by create_or_merge_lead (the discovery layer's Quick Search
    path). find_duplicate_lead itself — and its existing callers
    (create_lead_deduped/create_lead_deduped_with_log, the live Campaign
    scraping save path) — are completely unchanged.
    """
    existing = await find_duplicate_lead(email, phone, website, business_name, city)
    if existing:
        return existing

    if not business_name or not city:
        return None
    target = _fuzzy_norm_name(business_name, city)
    if not target:
        return None

    async with get_db() as conn:
        candidates = await conn.fetch(
            "SELECT * FROM leads WHERE LOWER(city) = LOWER($1) ORDER BY created_at DESC LIMIT 500", city,
        )
    for cand in candidates:
        cand_key = _fuzzy_norm_name(cand.get("business_name"), city)
        if cand_key and SequenceMatcher(None, target, cand_key).ratio() >= threshold:
            return dict(cand)
    return None


def _normalize_lead_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Single choke point for lead data cleanliness — applied on every write path
    (manual API create, CSV import, scraper batch save, scheduled campaigns)
    regardless of whether the caller already normalized upstream.
    """
    data = dict(data)
    if data.get("business_name"):
        data["business_name"] = clean_business_name(data["business_name"])
    if data.get("email"):
        data["email"] = clean_email(data["email"])
    if data.get("phone"):
        data["phone"] = clean_phone(data["phone"])
    if data.get("website"):
        data["website"] = normalize_website(data["website"])
    return data


async def create_lead(data: Dict[str, Any]) -> int:
    """Insert a new lead; returns its id. Raises sqlite3.IntegrityError on duplicate."""
    data = _normalize_lead_fields(data)
    clean = {
        k: _coerce(k, v)
        for k, v in data.items()
        if k in _LEAD_WRITABLE and v is not None
    }
    if not clean:
        raise ValueError("No writable fields provided")

    cols         = ", ".join(clean.keys())
    placeholders = ", ".join("?" for _ in clean)
    values       = list(clean.values())

    async with get_db() as conn:
        lead_id = await conn.fetchval(
            f"INSERT INTO leads ({cols}) VALUES ({placeholders}) RETURNING id",
            *values,
        )
    return lead_id


async def create_lead_deduped(data: Dict[str, Any]) -> Tuple[int, bool]:
    """Create a lead only if not already in DB (email/phone, else website, else name+city). Returns (id, is_new)."""
    dup_kwargs = dict(
        email=data.get("email"), phone=data.get("phone"),
        website=data.get("website"), business_name=data.get("business_name"),
        city=data.get("city"),
    )
    existing = await find_duplicate_lead(**dup_kwargs)
    if existing:
        return existing["id"], False
    try:
        lead_id = await create_lead(data)
        return lead_id, True
    except sqlite3.IntegrityError:
        existing = await find_duplicate_lead(**dup_kwargs)
        if existing:
            return existing["id"], False
        raise


async def create_lead_deduped_with_log(data: Dict[str, Any]) -> Tuple[int, bool]:
    """
    Like create_lead_deduped, but the lead insert and its campaign_log FOUND
    row commit atomically in one transaction — a crash between the two can no
    longer leave a saved lead with no corresponding log row.
    """
    data = _normalize_lead_fields(data)
    dup_kwargs = dict(
        email=data.get("email"), phone=data.get("phone"),
        website=data.get("website"), business_name=data.get("business_name"),
        city=data.get("city"),
    )
    existing = await find_duplicate_lead(**dup_kwargs)
    if existing:
        return existing["id"], False

    clean = {
        k: _coerce(k, v)
        for k, v in data.items()
        if k in _LEAD_WRITABLE and v is not None
    }
    if not clean:
        raise ValueError("No writable fields provided")
    cols         = ", ".join(clean.keys())
    placeholders = ", ".join("?" for _ in clean)
    values       = list(clean.values())

    try:
        async with transaction() as tx:
            lead_id = await tx.fetchval(
                f"INSERT INTO leads ({cols}) VALUES ({placeholders}) RETURNING id", *values
            )
            await tx.execute(
                """INSERT INTO campaign_log (lead_id, channel, action, success)
                   VALUES ($1, $2, $3, $4)""",
                lead_id, "SCRAPE", "FOUND", 1,
            )
        return lead_id, True
    except sqlite3.IntegrityError:
        existing = await find_duplicate_lead(**dup_kwargs)
        if existing:
            return existing["id"], False
        raise


async def get_leads_by_ids(lead_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """Batch-fetch leads by id in one query — avoids opening a fresh connection per lead in loops."""
    if not lead_ids:
        return {}
    placeholders = ", ".join("?" for _ in lead_ids)
    async with get_db() as conn:
        rows = await conn.fetch(f"SELECT * FROM leads WHERE id IN ({placeholders})", *lead_ids)
    return {r["id"]: r for r in rows}


async def update_lead(lead_id: int, data: Dict[str, Any]) -> bool:
    data = _normalize_lead_fields(data)
    clean = {
        k: _coerce(k, v)
        for k, v in data.items()
        if k in _LEAD_WRITABLE and v is not None
    }
    if not clean:
        return False
    params     = list(clean.values())
    set_clause = ", ".join(f"{col} = ?" for col in clean.keys())
    params.append(lead_id)
    async with get_db() as conn:
        result = await conn.execute(
            f"UPDATE leads SET {set_clause} WHERE id = ?", *params
        )
    return _rows_affected(result) > 0


async def delete_lead(lead_id: int) -> bool:
    async with get_db() as conn:
        result = await conn.execute("DELETE FROM leads WHERE id = $1", lead_id)
    return _rows_affected(result) > 0


async def delete_all_leads(status: Optional[str] = None) -> int:
    valid = {"PENDING", "SENT", "REPLIED", "SKIPPED"}
    async with get_db() as conn:
        if status and status.upper() in valid:
            result = await conn.execute("DELETE FROM leads WHERE status = $1", status.upper())
        else:
            result = await conn.execute("DELETE FROM leads")
    return _rows_affected(result)


async def mark_lead_replied(lead_id: int) -> bool:
    async with get_db() as conn:
        lead = await conn.fetchrow("SELECT status FROM leads WHERE id = $1", lead_id)
        if not lead:
            return False
        if (lead["status"] or "").upper() == "DO_NOT_CONTACT":
            return True
        result = await conn.execute(
            "UPDATE leads SET status = 'REPLIED' WHERE id = $1", lead_id
        )
    return _rows_affected(result) > 0


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


async def find_lead_by_email(email: str) -> Optional[Dict[str, Any]]:
    if not email:
        return None
    async with get_db() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM leads WHERE LOWER(email) = LOWER($1) ORDER BY created_at DESC LIMIT 1",
            email,
        )
    return dict(row) if row else None


async def find_lead_by_business_name_in_subject(subject: str) -> Optional[Dict[str, Any]]:
    if not subject or not subject.strip():
        return None
    async with get_db() as conn:
        row = await conn.fetchrow(
            """SELECT * FROM leads
               WHERE status IN ('SENT', 'REPLIED')
                 AND $1 LIKE '%' || business_name || '%'
               ORDER BY created_at DESC LIMIT 1""",
            subject,
        )
    return dict(row) if row else None


async def get_leads_without_score(limit: int = 100) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            """SELECT * FROM leads
               WHERE (score IS NULL OR score = 0) AND status != 'SKIPPED'
               ORDER BY created_at DESC LIMIT $1""",
            limit,
        )
    return [dict(r) for r in rows]


async def get_pending_leads_for_niche_city(
    niche: str, city: str, limit: int = 200
) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            """SELECT * FROM leads
               WHERE status = 'PENDING'
                 AND niche LIKE $1
                 AND city  LIKE $2
               ORDER BY created_at DESC LIMIT $3""",
            f"%{niche}%", f"%{city}%", limit,
        )
    return [dict(r) for r in rows]


async def get_score_distribution() -> Dict[str, int]:
    async with get_db() as conn:
        rows = await conn.fetch(
            """SELECT score_label, COUNT(*) AS cnt
               FROM leads
               WHERE status != 'SKIPPED' AND score IS NOT NULL AND score > 0
               GROUP BY score_label"""
        )
    dist = {"HOT": 0, "WARM": 0, "COLD": 0}
    for r in rows:
        lbl = (r["score_label"] or "COLD").upper()
        if lbl in dist:
            dist[lbl] = r["cnt"]
    return dist


async def get_avg_score() -> float:
    async with get_db() as conn:
        val = await conn.fetchval(
            "SELECT COALESCE(AVG(CAST(score AS REAL)), 0) FROM leads WHERE score > 0"
        )
    return round(float(val), 1) if val else 0.0


async def get_leads_due_for_followup(days: int = 3) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            """SELECT * FROM leads
               WHERE status = 'SENT'
                 AND followup_sent_at IS NULL
                 AND sent_at IS NOT NULL
                 AND DATE(sent_at) <= DATE('now', '-' || $1 || ' days')
               LIMIT 50""",
            days,
        )
    return [dict(r) for r in rows]


async def get_leads_due_for_stage(stage: int, limit: int = 50) -> List[Dict[str, Any]]:
    _DAYS = {1: 3, 2: 7, 3: 7}
    days  = _DAYS.get(stage, 3)
    if stage == 1:
        sql = """SELECT * FROM leads
                 WHERE status NOT IN ('REPLIED','SKIPPED','DO_NOT_CONTACT') AND status = 'SENT'
                   AND follow_up_1_sent_at IS NULL AND sent_at IS NOT NULL
                   AND DATE(sent_at) <= DATE('now', '-' || ? || ' days')
                 LIMIT ?"""
    elif stage == 2:
        sql = """SELECT * FROM leads
                 WHERE status NOT IN ('REPLIED','SKIPPED','DO_NOT_CONTACT')
                   AND follow_up_1_sent_at IS NOT NULL AND follow_up_2_sent_at IS NULL
                   AND DATE(follow_up_1_sent_at) <= DATE('now', '-' || ? || ' days')
                 LIMIT ?"""
    elif stage == 3:
        sql = """SELECT * FROM leads
                 WHERE status NOT IN ('REPLIED','SKIPPED','DO_NOT_CONTACT')
                   AND follow_up_2_sent_at IS NOT NULL AND follow_up_3_sent_at IS NULL
                   AND DATE(follow_up_2_sent_at) <= DATE('now', '-' || ? || ' days')
                 LIMIT ?"""
    else:
        return []
    async with get_db() as conn:
        rows = await conn.fetch(sql, days, limit)
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Campaign logging
# ─────────────────────────────────────────────────────────────────────────────

async def log_campaign_action(
    lead_id:   Optional[int],
    channel:   str,
    action:    str,
    success:   bool,
    error_msg: Optional[str] = None,
) -> None:
    async with get_db() as conn:
        await conn.execute(
            """INSERT INTO campaign_log (lead_id, channel, action, success, error_msg)
               VALUES ($1, $2, $3, $4, $5)""",
            lead_id, channel, action, 1 if success else 0, error_msg,
        )


async def get_recent_logs(limit: int = 20) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch("""
            SELECT
                cl.id,
                strftime('%Y-%m-%dT%H:%M:%S', cl.timestamp) AS timestamp,
                cl.channel, cl.action, cl.success, cl.error_msg,
                COALESCE(l.business_name, 'Unknown') AS business_name,
                l.niche, l.city
            FROM campaign_log cl
            LEFT JOIN leads l ON l.id = cl.lead_id
            ORDER BY cl.timestamp DESC
            LIMIT $1
        """, limit)
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Campaign runs
# ─────────────────────────────────────────────────────────────────────────────

async def create_campaign_run(
    niche: str, city: str, channel: str, daily_cap: int,
    sources: str = "GOOGLE_MAPS", country: Optional[str] = None,
) -> int:
    async with get_db() as conn:
        run_id = await conn.fetchval(
            """INSERT INTO campaign_runs (niche, city, country, channel, daily_cap, sources)
               VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
            niche, city, country, channel, daily_cap, sources,
        )
    return run_id


_CAMPAIGN_RUN_TS_COLS = frozenset({"finished_at"})


async def update_campaign_run(run_id: int, data: Dict[str, Any]) -> bool:
    if not data:
        return False
    coerced: Dict[str, Any] = {}
    for col, val in data.items():
        if col in _CAMPAIGN_RUN_TS_COLS:
            if isinstance(val, str):
                try:
                    val = datetime.fromisoformat(val.replace("Z", "+00:00"))
                except ValueError:
                    val = None
            if isinstance(val, datetime):
                if val.tzinfo is not None:
                    val = val.replace(tzinfo=None)
                val = val.isoformat()
        coerced[col] = val
    params     = list(coerced.values())
    set_clause = ", ".join(f"{col} = ?" for col in coerced.keys())
    params.append(run_id)
    async with get_db() as conn:
        result = await conn.execute(
            f"UPDATE campaign_runs SET {set_clause} WHERE id = ?", *params
        )
    return _rows_affected(result) > 0


async def update_campaign_run_progress(run_id: int, leads_found: int, leads_sent: int) -> None:
    async with get_db() as conn:
        await conn.execute(
            "UPDATE campaign_runs SET leads_found=$1, leads_sent=$2 WHERE id=$3",
            leads_found, leads_sent, run_id,
        )


async def get_campaign_history(limit: int = 10) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch("""
            SELECT id, niche, city, country, channel, daily_cap, leads_found, leads_sent,
                   strftime('%Y-%m-%dT%H:%M:%S', started_at)  AS started_at,
                   strftime('%Y-%m-%dT%H:%M:%S', finished_at) AS finished_at,
                   status, stage, sources
            FROM campaign_runs
            ORDER BY started_at DESC
            LIMIT $1
        """, limit)
    return [dict(r) for r in rows]


async def get_campaign_run_by_id(run_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        row = await conn.fetchrow("""
            SELECT id, niche, city, country, channel, daily_cap, leads_found, leads_sent,
                   strftime('%Y-%m-%dT%H:%M:%S', started_at)  AS started_at,
                   strftime('%Y-%m-%dT%H:%M:%S', finished_at) AS finished_at,
                   status, stage, sources
            FROM campaign_runs
            WHERE id = $1
        """, run_id)
    return dict(row) if row else None


async def delete_campaign_run(run_id: int) -> bool:
    async with get_db() as conn:
        result = await conn.execute("DELETE FROM campaign_runs WHERE id = $1", run_id)
    return _rows_affected(result) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Reply inbox
# ─────────────────────────────────────────────────────────────────────────────

_INBOX_WRITABLE = frozenset({
    "lead_id", "from_email", "subject", "body_snippet",
    "received_at", "intent", "processed",
})


async def save_reply(data: Dict[str, Any]) -> int:
    clean        = {k: v for k, v in data.items() if k in _INBOX_WRITABLE and v is not None}
    cols         = ", ".join(clean.keys())
    placeholders = ", ".join("?" for _ in clean)
    async with get_db() as conn:
        entry_id = await conn.fetchval(
            f"INSERT INTO reply_inbox ({cols}) VALUES ({placeholders}) RETURNING id",
            *clean.values(),
        )
    return entry_id


async def find_inbox_entry(from_email: str, subject: str) -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        row = await conn.fetchrow(
            """SELECT id FROM reply_inbox
               WHERE LOWER(from_email) = LOWER($1) AND subject = $2 LIMIT 1""",
            from_email, subject,
        )
    return dict(row) if row else None


async def get_inbox(
    page: int = 1,
    page_size: int = 50,
    intent: Optional[str] = None,
    processed: Optional[bool] = None,
) -> Dict[str, Any]:
    params: List[Any] = []

    def p(val: Any) -> str:
        params.append(val)
        return "?"

    conditions: List[str] = []
    if intent:
        conditions.append(f"ri.intent = {p(intent.upper())}")
    if processed is not None:
        conditions.append(f"ri.processed = {p(1 if processed else 0)}")

    where  = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    offset = (page - 1) * page_size

    async with get_db() as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM reply_inbox ri {where}", *params
        ) or 0
        rows = await conn.fetch(
            f"""SELECT ri.*, COALESCE(l.business_name, '') AS business_name,
                       l.niche, l.city, l.status AS lead_status
                FROM reply_inbox ri
                LEFT JOIN leads l ON l.id = ri.lead_id
                {where}
                ORDER BY ri.created_at DESC
                LIMIT ? OFFSET ?""",
            *params, page_size, offset,
        )
    return {
        "items":       [dict(r) for r in rows],
        "total":       total,
        "page":        page,
        "page_size":   page_size,
        "total_pages": max(1, math.ceil(total / page_size)),
    }


async def update_inbox_entry(entry_id: int, data: Dict[str, Any]) -> bool:
    clean = {k: v for k, v in data.items() if k in _INBOX_WRITABLE and v is not None}
    if not clean:
        return False
    params     = list(clean.values())
    set_clause = ", ".join(f"{col} = ?" for col in clean.keys())
    params.append(entry_id)
    async with get_db() as conn:
        result = await conn.execute(
            f"UPDATE reply_inbox SET {set_clause} WHERE id = ?", *params
        )
    return _rows_affected(result) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Enriched data
# ─────────────────────────────────────────────────────────────────────────────

_ENRICHED_WRITABLE = frozenset({
    "business_summary", "target_audience", "service_level", "brand_positioning",
    "marketing_gaps", "growth_potential", "best_pitch_strategy",
    "personalization_hook", "website_text",
    "website_quality_score", "issues", "conversion_gaps", "seo_gaps", "pitch_angles",
    "enriched_at",
})


async def upsert_enriched_data(lead_id: int, data: Dict[str, Any]) -> int:
    clean = {
        k: (json.dumps(v) if isinstance(v, list) else v)
        for k, v in data.items()
        if k in _ENRICHED_WRITABLE and v is not None
    }
    cols         = ", ".join(["lead_id"] + list(clean.keys()))
    placeholders = ", ".join("?" for _ in range(len(clean) + 1))
    update_set   = ", ".join(f"{c} = excluded.{c}" for c in clean.keys())
    update_set  += ", enriched_at = CURRENT_TIMESTAMP"
    async with get_db() as conn:
        row_id = await conn.fetchval(
            f"""INSERT INTO enriched_data ({cols}) VALUES ({placeholders})
                ON CONFLICT (lead_id) DO UPDATE SET {update_set}
                RETURNING id""",
            lead_id, *clean.values(),
        )
    return row_id


async def get_enriched_data(lead_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM enriched_data WHERE lead_id = $1", lead_id
        )
    return dict(row) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# Scores
# ─────────────────────────────────────────────────────────────────────────────

_SCORE_WRITABLE = frozenset({
    "digital_score", "website_score", "business_score",
    "opportunity_score", "final_score", "category",
    "key_problems", "opportunity_summary", "pitch_angle",
    "intelligence_score", "intelligence_category",
})


async def upsert_score(lead_id: int, data: Dict[str, Any]) -> int:
    clean = {
        k: (json.dumps(v) if isinstance(v, list) else v)
        for k, v in data.items()
        if k in _SCORE_WRITABLE and v is not None
    }
    cols         = ", ".join(["lead_id"] + list(clean.keys()))
    placeholders = ", ".join("?" for _ in range(len(clean) + 1))
    update_set   = ", ".join(f"{c} = excluded.{c}" for c in clean.keys())
    update_set  += ", scored_at = CURRENT_TIMESTAMP"
    async with get_db() as conn:
        row_id = await conn.fetchval(
            f"""INSERT INTO scores ({cols}) VALUES ({placeholders})
                ON CONFLICT (lead_id) DO UPDATE SET {update_set}
                RETURNING id""",
            lead_id, *clean.values(),
        )
    return row_id


async def get_score(lead_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        row = await conn.fetchrow("SELECT * FROM scores WHERE lead_id = $1", lead_id)
    return dict(row) if row else None


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
        await conn.execute(
            f"""INSERT INTO company_profiles ({cols}) VALUES ({placeholders})
                ON CONFLICT (lead_id) DO UPDATE SET {update_set}""",
            lead_id, *clean.values(),
        )
        row = await conn.fetchrow(
            "SELECT id FROM company_profiles WHERE lead_id = $1",
            lead_id,
        )
    return row["id"] if row else None


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


# ─────────────────────────────────────────────────────────────────────────────
# Sales Intelligence — pain points & business opportunities
#
# "Replace" semantics (delete-then-insert in one transaction) rather than
# append-only: re-running pain-point analysis for a profile represents the
# *current* understanding of that business, same as scores/enriched_data —
# not a history log. This is also what makes duplicate prevention trivial:
# running analysis twice on the same profile always leaves exactly one row
# per pain point/opportunity, never two.
# ─────────────────────────────────────────────────────────────────────────────

_PAIN_POINT_WRITABLE = frozenset({
    "title", "description", "evidence_snippet", "source_url",
    "confidence", "severity", "classification",
    "operational_impact", "customer_impact",
})

_BUSINESS_OPPORTUNITY_WRITABLE = frozenset({
    "pain_point_id", "area", "title", "description", "confidence", "classification",
    "why_it_matters", "business_ease", "priority",
})

_SOLUTION_RECOMMENDATION_WRITABLE = frozenset({
    "business_opportunity_id", "service_name", "reason", "evidence_ids", "confidence",
})


async def replace_pain_points(company_profile_id: int, items: List[Dict[str, Any]]) -> List[int]:
    """Delete all existing pain points for this profile and insert the given set. Returns new ids."""
    async with transaction() as tx:
        await tx.execute("DELETE FROM pain_points WHERE company_profile_id = $1", company_profile_id)
        new_ids: List[int] = []
        for item in items:
            clean = {k: v for k, v in item.items() if k in _PAIN_POINT_WRITABLE and v is not None}
            cols         = ", ".join(["company_profile_id"] + list(clean.keys()))
            placeholders = ", ".join("?" for _ in range(len(clean) + 1))
            new_id = await tx.fetchval(
                f"INSERT INTO pain_points ({cols}) VALUES ({placeholders}) RETURNING id",
                company_profile_id, *clean.values(),
            )
            new_ids.append(new_id)
    return new_ids


async def get_pain_points(company_profile_id: int) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT * FROM pain_points WHERE company_profile_id = $1 ORDER BY created_at",
            company_profile_id,
        )
    return [dict(r) for r in rows]


async def replace_business_opportunities(company_profile_id: int, items: List[Dict[str, Any]]) -> List[int]:
    """Delete all existing opportunities for this profile and insert the given set. Returns new ids."""
    async with transaction() as tx:
        await tx.execute("DELETE FROM business_opportunities WHERE company_profile_id = $1", company_profile_id)
        new_ids: List[int] = []
        for item in items:
            clean = {k: v for k, v in item.items() if k in _BUSINESS_OPPORTUNITY_WRITABLE and v is not None}
            cols         = ", ".join(["company_profile_id"] + list(clean.keys()))
            placeholders = ", ".join("?" for _ in range(len(clean) + 1))
            new_id = await tx.fetchval(
                f"INSERT INTO business_opportunities ({cols}) VALUES ({placeholders}) RETURNING id",
                company_profile_id, *clean.values(),
            )
            new_ids.append(new_id)
    return new_ids


async def get_business_opportunities(company_profile_id: int) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT * FROM business_opportunities WHERE company_profile_id = $1 ORDER BY created_at",
            company_profile_id,
        )
    return [dict(r) for r in rows]


async def replace_solution_recommendations(company_profile_id: int, items: List[Dict[str, Any]]) -> List[int]:
    """Delete all existing solution recommendations for this profile and insert the given set."""
    async with transaction() as tx:
        await tx.execute("DELETE FROM solution_recommendations WHERE company_profile_id = $1", company_profile_id)
        new_ids: List[int] = []
        for item in items:
            clean = {
                k: (json.dumps(v) if isinstance(v, list) else v)
                for k, v in item.items()
                if k in _SOLUTION_RECOMMENDATION_WRITABLE and v is not None
            }
            cols         = ", ".join(["company_profile_id"] + list(clean.keys()))
            placeholders = ", ".join("?" for _ in range(len(clean) + 1))
            new_id = await tx.fetchval(
                f"INSERT INTO solution_recommendations ({cols}) VALUES ({placeholders}) RETURNING id",
                company_profile_id, *clean.values(),
            )
            new_ids.append(new_id)
    return new_ids


async def get_solution_recommendations(company_profile_id: int) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT * FROM solution_recommendations WHERE company_profile_id = $1 ORDER BY created_at",
            company_profile_id,
        )
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — Marketing Agent generated messages (isolated from `messages` —
# see the schema comment above generated_messages for why)
# ─────────────────────────────────────────────────────────────────────────────

_GENERATED_MESSAGE_WRITABLE = frozenset({
    "company_profile_id", "channel", "variant", "strategy", "pain_point",
    "evidence", "business_impact", "solution", "business_benefit",
    "service_name", "subject", "message", "cta", "confidence",
})

_GENERATED_MESSAGE_UPDATABLE = frozenset({
    "subject", "message", "approval_status", "rejection_reason", "reviewed_at",
})


async def replace_generated_messages(lead_id: int, items: List[Dict[str, Any]]) -> List[int]:
    """Delete all existing generated messages for this lead and insert the given set."""
    async with transaction() as tx:
        await tx.execute("DELETE FROM generated_messages WHERE lead_id = $1", lead_id)
        new_ids: List[int] = []
        for item in items:
            clean = {k: v for k, v in item.items() if k in _GENERATED_MESSAGE_WRITABLE and v is not None}
            cols         = ", ".join(["lead_id"] + list(clean.keys()))
            placeholders = ", ".join("?" for _ in range(len(clean) + 1))
            new_id = await tx.fetchval(
                f"INSERT INTO generated_messages ({cols}) VALUES ({placeholders}) RETURNING id",
                lead_id, *clean.values(),
            )
            new_ids.append(new_id)
    return new_ids


async def get_generated_messages(lead_id: int) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT * FROM generated_messages WHERE lead_id = $1 ORDER BY created_at",
            lead_id,
        )
    return [dict(r) for r in rows]


async def get_generated_message(message_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        row = await conn.fetchrow("SELECT * FROM generated_messages WHERE id = $1", message_id)
    return dict(row) if row else None


async def update_generated_message(message_id: int, data: Dict[str, Any]) -> bool:
    clean = {k: v for k, v in data.items() if k in _GENERATED_MESSAGE_UPDATABLE and v is not None}
    if not clean:
        return False
    params     = list(clean.values())
    set_clause = ", ".join(f"{col} = ?" for col in clean.keys())
    params.append(message_id)
    async with get_db() as conn:
        result = await conn.execute(
            f"UPDATE generated_messages SET {set_clause} WHERE id = ?", *params
        )
    return _rows_affected(result) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Messages
# ─────────────────────────────────────────────────────────────────────────────

_MSG_WRITABLE = frozenset({
    "lead_id", "sequence_step", "message_type", "subject", "body",
    "status", "sent_at", "scheduled_for",
})


async def create_message(data: Dict[str, Any]) -> int:
    clean        = {k: v for k, v in data.items() if k in _MSG_WRITABLE and v is not None}
    cols         = ", ".join(clean.keys())
    placeholders = ", ".join("?" for _ in clean)
    async with get_db() as conn:
        msg_id = await conn.fetchval(
            f"INSERT INTO messages ({cols}) VALUES ({placeholders}) RETURNING id",
            *clean.values(),
        )
    return msg_id


async def update_message(message_id: int, data: Dict[str, Any]) -> bool:
    clean = {k: v for k, v in data.items() if k in _MSG_WRITABLE - {"lead_id"} and v is not None}
    if not clean:
        return False
    params     = list(clean.values())
    set_clause = ", ".join(f"{col} = ?" for col in clean.keys())
    params.append(message_id)
    async with get_db() as conn:
        result = await conn.execute(
            f"UPDATE messages SET {set_clause} WHERE id = ?", *params
        )
    return _rows_affected(result) > 0


async def get_messages(lead_id: int) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT * FROM messages WHERE lead_id = $1 ORDER BY sequence_step, created_at",
            lead_id,
        )
    return [dict(r) for r in rows]


async def get_message_by_id(message_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        row = await conn.fetchrow("SELECT * FROM messages WHERE id = $1", message_id)
    return dict(row) if row else None


async def delete_lead_messages(lead_id: int) -> int:
    async with get_db() as conn:
        result = await conn.execute("DELETE FROM messages WHERE lead_id = $1", lead_id)
    return _rows_affected(result)


# ─────────────────────────────────────────────────────────────────────────────
# Replies
# ─────────────────────────────────────────────────────────────────────────────

async def create_reply(data: Dict[str, Any]) -> int:
    _writable    = frozenset({
        "lead_id", "message_id", "reply_text", "detected_intent", "raw_email_data",
        "rich_intent", "intent_confidence", "recommended_action",
    })
    clean        = {
        k: (json.dumps(v) if isinstance(v, dict) else v)
        for k, v in data.items()
        if k in _writable and v is not None
    }
    cols         = ", ".join(clean.keys())
    placeholders = ", ".join("?" for _ in clean)
    async with get_db() as conn:
        reply_id = await conn.fetchval(
            f"INSERT INTO replies ({cols}) VALUES ({placeholders}) RETURNING id",
            *clean.values(),
        )
    return reply_id


async def get_replies(lead_id: int) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT * FROM replies WHERE lead_id = $1 ORDER BY received_at DESC",
            lead_id,
        )
    return [dict(r) for r in rows]


async def get_reply_by_id(reply_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        row = await conn.fetchrow("SELECT * FROM replies WHERE id = $1", reply_id)
    return dict(row) if row else None


_REPLY_DRAFT_WRITABLE = frozenset({"draft_subject", "draft_body", "draft_status", "draft_sent_at"})


async def set_reply_draft(reply_id: int, draft_subject: Optional[str], draft_body: str) -> None:
    """Attach a generated auto-reply draft to a reply, awaiting human approval."""
    async with get_db() as conn:
        await conn.execute(
            """UPDATE replies SET draft_subject = $1, draft_body = $2, draft_status = 'PENDING_APPROVAL'
               WHERE id = $3""",
            draft_subject, draft_body, reply_id,
        )


async def update_reply_draft(reply_id: int, data: Dict[str, Any]) -> None:
    """Edit draft_subject/draft_body before approval, or transition draft_status (approve/discard)."""
    clean = {k: v for k, v in data.items() if k in _REPLY_DRAFT_WRITABLE and v is not None}
    if not clean:
        return
    set_clause = ", ".join(f"{c} = ${i + 1}" for i, c in enumerate(clean.keys()))
    async with get_db() as conn:
        await conn.execute(
            f"UPDATE replies SET {set_clause} WHERE id = ${len(clean) + 1}",
            *clean.values(), reply_id,
        )


async def discard_pending_drafts_for_lead(lead_id: int) -> int:
    """Discard every PENDING_APPROVAL draft for a lead — called on opt-out so a
    stale draft can never be approved and sent after the lead has suppressed
    future contact."""
    async with get_db() as conn:
        result = await conn.execute(
            """UPDATE replies SET draft_status = 'DISCARDED'
               WHERE lead_id = $1 AND draft_status = 'PENDING_APPROVAL'""",
            lead_id,
        )
    return _rows_affected(result)


async def update_reply_intelligence(reply_id: int, data: Dict[str, Any]) -> None:
    """Persist ReplyIntelligenceAgent output (rich_intent/intent_confidence/
    recommended_action) onto an already-inserted reply row."""
    _writable = frozenset({"rich_intent", "intent_confidence", "recommended_action"})
    clean = {k: v for k, v in data.items() if k in _writable and v is not None}
    if not clean:
        return
    set_clause = ", ".join(f"{c} = ${i + 1}" for i, c in enumerate(clean.keys()))
    async with get_db() as conn:
        await conn.execute(
            f"UPDATE replies SET {set_clause} WHERE id = ${len(clean) + 1}",
            *clean.values(), reply_id,
        )


async def get_pending_reply_drafts() -> List[Dict[str, Any]]:
    """Drafts awaiting human approval, with lead context for display."""
    async with get_db() as conn:
        rows = await conn.fetch("""
            SELECT r.id, r.lead_id, r.reply_text, r.detected_intent, r.received_at,
                   r.draft_subject, r.draft_body, r.draft_status,
                   r.rich_intent, r.intent_confidence, r.recommended_action,
                   l.business_name, l.email, l.channel
            FROM replies r
            JOIN leads l ON l.id = r.lead_id
            WHERE r.draft_status = 'PENDING_APPROVAL'
            ORDER BY r.received_at DESC
        """)
    return [dict(row) for row in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Follow-up helpers
# ─────────────────────────────────────────────────────────────────────────────

async def count_messages_for_lead(lead_id: int) -> int:
    async with get_db() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM messages WHERE lead_id = $1", lead_id
        ) or 0


async def get_due_followup_messages(limit: int = 100) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch("""
            SELECT m.id, m.lead_id, m.sequence_step,
                   m.subject AS msg_subject, m.body AS msg_body,
                   strftime('%Y-%m-%dT%H:%M:%S', m.scheduled_for) AS scheduled_for,
                   l.business_name, l.email, l.phone, l.channel,
                   l.status AS lead_status, l.niche, l.city,
                   l.ai_email_subject, l.ai_followup_msg,
                   l.ai_follow_up_1, l.ai_follow_up_2
            FROM messages m
            JOIN leads l ON l.id = m.lead_id
            WHERE m.status = 'PENDING'
              AND m.sequence_step IN (2, 3)
              AND m.scheduled_for <= CURRENT_TIMESTAMP
            ORDER BY m.scheduled_for ASC
            LIMIT $1
        """, limit)
    return [dict(r) for r in rows]


async def cancel_pending_followups(lead_id: int) -> int:
    async with get_db() as conn:
        result = await conn.execute(
            """UPDATE messages SET status = 'CANCELLED'
               WHERE lead_id = $1 AND status = 'PENDING' AND sequence_step IN (2, 3)""",
            lead_id,
        )
    return _rows_affected(result)


async def count_pending_followups() -> int:
    async with get_db() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM messages WHERE status = 'PENDING' AND sequence_step IN (2, 3)"
        ) or 0


async def get_followup_history(page: int = 1, page_size: int = 50) -> Dict[str, Any]:
    offset = (page - 1) * page_size
    async with get_db() as conn:
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM messages WHERE status = 'SENT' AND sequence_step IN (2, 3)"
        ) or 0
        rows = await conn.fetch("""
            SELECT m.id, m.lead_id, m.sequence_step, m.status,
                   strftime('%Y-%m-%dT%H:%M:%S', m.sent_at)       AS sent_at,
                   strftime('%Y-%m-%dT%H:%M:%S', m.scheduled_for) AS scheduled_for,
                   m.subject, SUBSTR(m.body, 1, 200) AS body_snippet,
                   l.business_name, l.email, l.phone, l.channel,
                   l.status AS lead_status, l.niche, l.city
            FROM messages m
            JOIN leads l ON l.id = m.lead_id
            WHERE m.status = 'SENT' AND m.sequence_step IN (2, 3)
            ORDER BY m.sent_at DESC
            LIMIT ? OFFSET ?
        """, page_size, offset)
    return {
        "items":       [dict(r) for r in rows],
        "total":       total,
        "page":        page,
        "page_size":   page_size,
        "total_pages": max(1, math.ceil(total / page_size)),
    }


async def get_recent_send_info(lead_id: int, hours: int = 24) -> Dict[str, Any]:
    async with get_db() as conn:
        recent_count = await conn.fetchval(
            """SELECT COUNT(*) FROM messages
               WHERE lead_id = $1 AND status = 'SENT'
                 AND sent_at > datetime('now', '-' || $2 || ' hours')""",
            lead_id, hours,
        )
        last_row = await conn.fetchrow(
            "SELECT body FROM messages WHERE lead_id = $1 AND status = 'SENT' ORDER BY sent_at DESC LIMIT 1",
            lead_id,
        )
    return {
        "sent_recently": (recent_count or 0) > 0,
        "last_body":     (last_row["body"] if last_row else None) or "",
    }


async def get_reply_stats() -> Dict[str, Any]:
    async with get_db() as conn:
        intent_rows   = await conn.fetch(
            "SELECT detected_intent, COUNT(*) AS cnt FROM replies GROUP BY detected_intent"
        )
        total_replies = await conn.fetchval("SELECT COUNT(*) FROM replies") or 0
        total_sent    = await conn.fetchval(
            "SELECT COUNT(*) FROM leads WHERE status IN ('SENT', 'REPLIED')"
        ) or 0
        total_replied = await conn.fetchval(
            "SELECT COUNT(*) FROM leads WHERE status = 'REPLIED'"
        ) or 0
        followup_rows = await conn.fetch("""
            SELECT l.id, l.business_name, l.email, l.phone, l.niche, l.city,
                   r.detected_intent,
                   strftime('%Y-%m-%dT%H:%M:%S', r.received_at) AS received_at
            FROM leads l
            JOIN replies r ON r.lead_id = l.id
            WHERE l.status = 'REPLIED'
            ORDER BY r.received_at DESC LIMIT 50
        """)
    return {
        "by_intent":          [dict(r) for r in intent_rows],
        "total_replies":      total_replies,
        "total_sent":         total_sent,
        "total_replied":      total_replied,
        "leads_to_follow_up": [dict(r) for r in followup_rows],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Campaigns table
# ─────────────────────────────────────────────────────────────────────────────

_CAMPAIGN_WRITABLE = frozenset({
    "niche", "city", "country", "sources", "channel", "daily_cap",
    "leads_found", "leads_sent", "leads_replied", "status", "completed_at",
})


async def create_campaign(data: Dict[str, Any]) -> int:
    clean = {
        k: (json.dumps(v) if isinstance(v, list) else v)
        for k, v in data.items()
        if k in _CAMPAIGN_WRITABLE and v is not None
    }
    cols         = ", ".join(clean.keys())
    placeholders = ", ".join("?" for _ in clean)
    async with get_db() as conn:
        campaign_id = await conn.fetchval(
            f"INSERT INTO campaigns ({cols}) VALUES ({placeholders}) RETURNING id",
            *clean.values(),
        )
    return campaign_id


async def update_campaign(campaign_id: int, data: Dict[str, Any]) -> bool:
    clean = {k: v for k, v in data.items() if k in _CAMPAIGN_WRITABLE and v is not None}
    if not clean:
        return False
    params     = list(clean.values())
    set_clause = ", ".join(f"{col} = ?" for col in clean.keys())
    params.append(campaign_id)
    async with get_db() as conn:
        result = await conn.execute(
            f"UPDATE campaigns SET {set_clause} WHERE id = ?", *params
        )
    return _rows_affected(result) > 0


async def get_campaign(campaign_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        row = await conn.fetchrow("SELECT * FROM campaigns WHERE id = $1", campaign_id)
    return dict(row) if row else None


async def list_campaigns(limit: int = 20) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT * FROM campaigns ORDER BY started_at DESC LIMIT $1", limit
        )
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — Universal Lead Discovery (Quick Search / Campaign Planner runs)
# ─────────────────────────────────────────────────────────────────────────────

_RAW_SNAPSHOT_MAX_CHARS = 2000


async def create_discovery_run(data: Dict[str, Any]) -> int:
    """Insert a new lead_discovery_runs row (status defaults to QUEUED); returns its id."""
    cols = [
        "mode", "raw_query", "niche", "city", "country", "target_count",
        "planner_intent", "planner_confidence", "sources_planned",
        "campaign_run_id",
    ]
    clean: Dict[str, Any] = {}
    for c in cols:
        if c in data and data[c] is not None:
            v = data[c]
            clean[c] = json.dumps(v) if isinstance(v, list) else v
    if "mode" not in clean:
        raise ValueError("create_discovery_run requires 'mode'")

    col_sql  = ", ".join(clean.keys())
    ph       = ", ".join("?" for _ in clean)
    async with get_db() as conn:
        run_id = await conn.fetchval(
            f"INSERT INTO lead_discovery_runs ({col_sql}) VALUES ({ph}) RETURNING id",
            *clean.values(),
        )
    return run_id


_DISCOVERY_RUN_WRITABLE = frozenset({
    "status", "raw_candidates", "deduplicated_count", "results_count",
    "planner_intent", "planner_confidence", "sources_planned",
    "error_message", "started_at", "finished_at",
    "emails_found", "leads_scored", "research_queued",
})


async def update_discovery_run(run_id: int, data: Dict[str, Any]) -> bool:
    clean: Dict[str, Any] = {}
    for k, v in data.items():
        if k in _DISCOVERY_RUN_WRITABLE and v is not None:
            clean[k] = json.dumps(v) if isinstance(v, list) else v
    if not clean:
        return False
    set_clause = ", ".join(f"{col} = ?" for col in clean.keys())
    params     = list(clean.values()) + [run_id]
    async with get_db() as conn:
        result = await conn.execute(
            f"UPDATE lead_discovery_runs SET {set_clause} WHERE id = ?", *params
        )
    return _rows_affected(result) > 0


async def get_discovery_run(run_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM lead_discovery_runs WHERE id = $1", run_id
        )
    return dict(row) if row else None


_DISCOVERY_ACTIVE_STATUSES = ("QUEUED", "RUNNING", "CANCEL_REQUESTED")


async def get_recent_discovery_runs(mode: str, limit: int = 20) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT * FROM lead_discovery_runs WHERE mode = $1 ORDER BY id DESC LIMIT $2",
            mode, limit,
        )
    return [dict(r) for r in rows]


async def get_research_status_counts(source_type: Optional[str] = None) -> Dict[str, int]:
    """leads.research_status histogram, optionally scoped to a discovery origin."""
    where, params = "", []
    if source_type:
        where = "WHERE source_type = ?"
        params.append(source_type)
    async with get_db() as conn:
        rows = await conn.fetch(
            f"SELECT COALESCE(research_status, 'NOT_STARTED') AS s, COUNT(*) n FROM leads {where} GROUP BY s",
            *params,
        )
    return {r["s"]: int(r["n"]) for r in rows}


async def get_discovery_run_totals(mode: str) -> Dict[str, int]:
    async with get_db() as conn:
        row = await conn.fetchrow(
            """SELECT
                 COUNT(*)                         AS runs,
                 COALESCE(SUM(raw_candidates), 0)  AS raw_candidates,
                 COALESCE(SUM(results_count), 0)   AS results_count,
                 COALESCE(SUM(deduplicated_count), 0) AS deduplicated,
                 COALESCE(SUM(emails_found), 0)    AS emails_found,
                 COALESCE(SUM(leads_scored), 0)    AS leads_scored,
                 COALESCE(SUM(research_queued), 0) AS research_queued,
                 COALESCE(SUM(status = 'FAILED'), 0) AS failed
               FROM lead_discovery_runs WHERE mode = $1""",
            mode,
        )
    return {k: int(v or 0) for k, v in dict(row).items()} if row else {}


async def get_latest_discovery_run(
    mode: str = "QUICK", active_only: bool = False,
) -> Optional[Dict[str, Any]]:
    """The most recent discovery run for `mode` — used by the frontend to
    reconnect to an in-flight (or just-finished) run after a page navigation
    or refresh. `active_only` restricts to non-terminal runs."""
    where = "mode = $1"
    params: List[Any] = [mode]
    if active_only:
        placeholders = ", ".join(f"${i + 2}" for i in range(len(_DISCOVERY_ACTIVE_STATUSES)))
        where += f" AND status IN ({placeholders})"
        params.extend(_DISCOVERY_ACTIVE_STATUSES)
    async with get_db() as conn:
        row = await conn.fetchrow(
            f"SELECT * FROM lead_discovery_runs WHERE {where} ORDER BY id DESC LIMIT 1",
            *params,
        )
    return dict(row) if row else None


async def record_lead_source(
    lead_id: int,
    source: str,
    source_identifier: Optional[str] = None,
    run_id: Optional[int] = None,
    raw_snapshot: Optional[Dict[str, Any]] = None,
) -> int:
    """Insert one provenance row. Always called — even when the lead already
    existed — so every source that ever found a lead is recorded."""
    snapshot_text = None
    if raw_snapshot is not None:
        try:
            snapshot_text = json.dumps(raw_snapshot, default=str)[:_RAW_SNAPSHOT_MAX_CHARS]
        except (TypeError, ValueError):
            snapshot_text = None
    async with get_db() as conn:
        source_id = await conn.fetchval(
            """INSERT INTO lead_sources (lead_id, source, source_identifier, run_id, raw_snapshot)
               VALUES ($1, $2, $3, $4, $5) RETURNING id""",
            lead_id, source, source_identifier, run_id, snapshot_text,
        )
    return source_id


async def get_lead_sources(lead_id: int) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT * FROM lead_sources WHERE lead_id = $1 ORDER BY discovered_at ASC",
            lead_id,
        )
    return [dict(r) for r in rows]


async def get_leads_for_discovery_run(run_id: int) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            """SELECT DISTINCT l.* FROM leads l
               JOIN lead_sources ls ON ls.lead_id = l.id
               WHERE ls.run_id = $1
               ORDER BY l.created_at DESC""",
            run_id,
        )
    return [dict(r) for r in rows]


async def _insert_lead_row(conn, data: Dict[str, Any]) -> int:
    """The raw lead-row insert, factored out of create_or_merge_lead so it
    runs inside that function's shared transaction() connection — and so
    tests can inject a race at this exact point (simulate a concurrent
    insert landing between the duplicate-check and this insert) without
    reaching into the transaction machinery itself."""
    clean = {k: _coerce(k, v) for k, v in data.items() if k in _LEAD_WRITABLE and v is not None}
    if not clean:
        raise ValueError("No writable fields provided")
    cols = ", ".join(clean.keys())
    ph = ", ".join("?" for _ in clean)
    return await conn.fetchval(f"INSERT INTO leads ({cols}) VALUES ({ph}) RETURNING id", *clean.values())


async def create_or_merge_lead(
    data: Dict[str, Any],
    source: str,
    source_identifier: Optional[str] = None,
    run_id: Optional[int] = None,
) -> Tuple[int, bool, Optional[str]]:
    """
    Discovery-layer save: unlike create_lead_deduped (which discards a
    duplicate candidate's extra info), this merges missing fields into the
    existing lead and always records provenance. Existing non-null values
    always win on conflict. Returns (lead_id, is_new, merge_reason) where
    merge_reason is 'email' | 'phone' | 'website' | 'name_city' | None (None = new lead).

    Used by the Quick Search discovery path only — Campaign-mode scraping
    keeps using create_lead_deduped_with_log unchanged (see design spec).
    """
    data = _normalize_lead_fields(data)
    email   = data.get("email")
    phone   = data.get("phone")
    website = data.get("website")
    name    = data.get("business_name")
    city    = data.get("city")

    existing = await find_duplicate_lead_fuzzy(
        email=email, phone=phone, website=website, business_name=name, city=city,
    )

    snapshot_text = None
    try:
        snapshot_text = json.dumps(data, default=str)[:_RAW_SNAPSHOT_MAX_CHARS]
    except (TypeError, ValueError):
        pass

    if existing is None:
        try:
            # Atomic: the lead row and its provenance row commit together —
            # a crash between them can no longer leave a lead with zero
            # lead_sources rows, matching create_lead_deduped_with_log's
            # existing atomicity precedent elsewhere in this file.
            async with transaction() as conn:
                lead_id = await _insert_lead_row(conn, data)
                await conn.execute(
                    """INSERT INTO lead_sources (lead_id, source, source_identifier, run_id, raw_snapshot)
                       VALUES ($1, $2, $3, $4, $5)""",
                    lead_id, source, source_identifier, run_id, snapshot_text,
                )
            return lead_id, True, None
        except sqlite3.IntegrityError:
            # Race: a concurrent Quick Search run (JobQueue has multiple
            # workers) inserted the same email/phone between our lookup and
            # our insert. Re-resolve and fall through to the merge path
            # instead of raising — matches create_lead_deduped's existing
            # race-safety pattern. Only email/phone have DB-level unique
            # indexes (leads has none for website or fuzzy name+city), so
            # this only catches races on those two signals — a narrower,
            # rarer race on website-only/fuzzy matches remains a known
            # limitation (see design spec's Known Limitations).
            existing = await find_duplicate_lead_fuzzy(
                email=email, phone=phone, website=website, business_name=name, city=city,
            )
            if existing is None:
                raise

    lead_id = existing["id"]

    if email and existing.get("email") and email.lower() == str(existing["email"]).lower():
        merge_reason = "email"
    elif phone and existing.get("phone") and phone == existing.get("phone"):
        merge_reason = "phone"
    elif website and existing.get("website") and website.lower() == str(existing["website"]).lower():
        merge_reason = "website"
    else:
        merge_reason = "name_city"

    # Fill only fields the existing row is missing — existing values always
    # win, including falsy-but-real ones (0, 0.0, False, "") which are
    # genuine data, not absence, and must not be treated as "missing".
    fill: Dict[str, Any] = {}
    for col in _LEAD_WRITABLE:
        if col in ("business_name", "status"):
            continue  # never overwritten by a merge
        new_val = data.get(col)
        if new_val is not None and existing.get(col) is None:
            fill[col] = new_val

    async with transaction() as conn:
        if fill:
            clean = {k: _coerce(k, v) for k, v in fill.items()}
            set_clause = ", ".join(f"{col} = ?" for col in clean.keys())
            await conn.execute(f"UPDATE leads SET {set_clause} WHERE id = ?", *clean.values(), lead_id)
        await conn.execute(
            """INSERT INTO lead_sources (lead_id, source, source_identifier, run_id, raw_snapshot)
               VALUES ($1, $2, $3, $4, $5)""",
            lead_id, source, source_identifier, run_id, snapshot_text,
        )
    return lead_id, False, merge_reason


# ─────────────────────────────────────────────────────────────────────────────
# Browser Research Agent (independent subsystem — see research_agent design spec)
# ─────────────────────────────────────────────────────────────────────────────

_RESEARCH_SESSION_WRITABLE = frozenset({
    "status", "current_action", "current_query", "current_business",
    "current_source", "current_city", "research_phase",
    "leads_found", "leads_completed", "leads_failed",
    "businesses_researched", "businesses_skipped",
    "processed_keys", "resume_count", "resumable",
    "error_message", "started_at", "finished_at",
    "mode", "seed_businesses", "seed_lead_ids", "target_titles",
})


async def set_leads_field(lead_ids: List[int], field: str, value: Any) -> int:
    """Bulk-set one writable column on a set of leads (research handoff bookkeeping)."""
    if field not in _LEAD_WRITABLE:
        raise ValueError(f"{field} is not a writable lead column")
    ids = [i for i in dict.fromkeys(lead_ids) if i]
    if not ids:
        return 0
    ph = ", ".join("?" for _ in ids)
    async with get_db() as conn:
        result = await conn.execute(
            f"UPDATE leads SET {field} = ? WHERE id IN ({ph})", value, *ids
        )
    return _rows_affected(result)


async def set_leads_research_status(
    lead_ids: List[int],
    status: str,
    *,
    session_id: Optional[int] = None,
    only_from: Optional[Tuple[str, ...]] = None,
) -> int:
    """Bulk-set leads.research_status (research handoff). `only_from` restricts
    the transition to leads currently in one of those statuses — used so a
    session finishing never clobbers a lead a human already re-classified."""
    ids = [i for i in dict.fromkeys(lead_ids) if i]
    if not ids:
        return 0
    sets = ["research_status = ?"]
    params: List[Any] = [status]
    if session_id is not None:
        sets.append("last_research_session_id = ?")
        params.append(session_id)
    ph = ", ".join("?" for _ in ids)
    where = f"id IN ({ph})"
    params.extend(ids)
    if only_from:
        fp = ", ".join("?" for _ in only_from)
        where += f" AND research_status IN ({fp})"
        params.extend(only_from)
    async with get_db() as conn:
        result = await conn.execute(
            f"UPDATE leads SET {', '.join(sets)} WHERE {where}", *params
        )
    return _rows_affected(result)

_RESEARCH_RESULT_COLS = (
    "city", "state", "country", "business_name", "business_phone", "business_email",
    "business_website", "business_email_status", "management_contact_name", "management_title",
    "management_phone", "management_phone_type", "management_email", "management_email_status",
    "confidence", "research_status", "research_notes",
)


async def create_research_session(data: Dict[str, Any]) -> int:
    required = ("niche", "location", "target_count")
    if any(data.get(k) is None for k in required):
        raise ValueError("create_research_session requires niche, location, target_count")
    clean = {k: data[k] for k in ("niche", "location", "country", "target_count", "mode", "submission_source")
             if data.get(k) is not None}
    for jk in ("seed_businesses", "seed_lead_ids", "target_titles"):
        if data.get(jk) is not None:
            clean[jk] = json.dumps(data[jk], default=str)
    col_sql = ", ".join(clean.keys())
    ph = ", ".join("?" for _ in clean)
    async with get_db() as conn:
        session_id = await conn.fetchval(
            f"INSERT INTO lead_research_sessions ({col_sql}) VALUES ({ph}) RETURNING id", *clean.values(),
        )
    return session_id


async def update_research_session(session_id: int, data: Dict[str, Any]) -> bool:
    clean = {k: v for k, v in data.items() if k in _RESEARCH_SESSION_WRITABLE and v is not None}
    if not clean:
        return False
    set_clause = ", ".join(f"{col} = ?" for col in clean.keys())
    params = list(clean.values()) + [session_id]
    async with get_db() as conn:
        result = await conn.execute(f"UPDATE lead_research_sessions SET {set_clause} WHERE id = ?", *params)
    return _rows_affected(result) > 0


async def get_research_session(session_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        row = await conn.fetchrow("SELECT * FROM lead_research_sessions WHERE id = $1", session_id)
    return dict(row) if row else None


_RESEARCH_ACTIVE_STATUSES = ("QUEUED", "RUNNING", "CANCEL_REQUESTED")


async def get_latest_research_session(active_only: bool = False) -> Optional[Dict[str, Any]]:
    """The most recent research session — used by the frontend to reconnect
    after a navigation/refresh. `active_only` restricts to non-terminal runs."""
    if active_only:
        placeholders = ", ".join(f"${i + 1}" for i in range(len(_RESEARCH_ACTIVE_STATUSES)))
        sql = f"SELECT * FROM lead_research_sessions WHERE status IN ({placeholders}) ORDER BY id DESC LIMIT 1"
        params: List[Any] = list(_RESEARCH_ACTIVE_STATUSES)
    else:
        sql = "SELECT * FROM lead_research_sessions ORDER BY id DESC LIMIT 1"
        params = []
    async with get_db() as conn:
        row = await conn.fetchrow(sql, *params)
    return dict(row) if row else None


_RESEARCH_SESSION_LIST_COLS = (
    "id", "mode", "submission_source", "status", "research_phase",
    "niche", "location", "country", "target_count",
    "leads_found", "leads_completed", "leads_failed", "resume_count",
    "seed_lead_ids", "target_titles", "error_message", "created_at", "started_at", "finished_at",
)


async def list_research_sessions(
    limit: int = 20, offset: int = 0, mode: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Research sessions, newest first — for the Research Agent page's session
    list. `mode` filters 'discovery' (started on that page) vs 'handoff' (from
    Lead Search / Automation)."""
    cols = ", ".join(_RESEARCH_SESSION_LIST_COLS)
    where, params = "", []
    if mode:
        where = "WHERE mode = ?"
        params.append(mode)
    params.extend([limit, offset])
    async with get_db() as conn:
        rows = await conn.fetch(
            f"SELECT {cols} FROM lead_research_sessions {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            *params,
        )
    return [dict(r) for r in rows]


async def list_interrupted_research_sessions() -> List[Dict[str, Any]]:
    """Sessions left non-terminal by a worker/server crash — reconciled at
    app startup (see main.py lifespan)."""
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT * FROM lead_research_sessions WHERE status IN ('QUEUED', 'RUNNING', 'CANCEL_REQUESTED') ORDER BY id ASC"
        )
    return [dict(r) for r in rows]


async def list_interrupted_discovery_runs() -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT * FROM lead_discovery_runs WHERE status IN ('QUEUED', 'RUNNING', 'CANCEL_REQUESTED') ORDER BY id ASC"
        )
    return [dict(r) for r in rows]


async def list_research_results(session_id: int) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT * FROM lead_research_results WHERE session_id = $1 ORDER BY created_at ASC", session_id,
        )
    return [dict(r) for r in rows]


async def get_research_evidence_for_result(result_id: int) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT * FROM lead_research_evidence WHERE result_id = $1 ORDER BY created_at ASC", result_id,
        )
    return [dict(r) for r in rows]


async def get_research_evidence_for_results(result_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    """Batched form of get_research_evidence_for_result — one query for N
    results instead of N, used by GET /api/research-agent/{id}/results
    (polled every few seconds while a session is active)."""
    if not result_ids:
        return {}
    placeholders = ", ".join("?" for _ in result_ids)
    async with get_db() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM lead_research_evidence WHERE result_id IN ({placeholders}) ORDER BY result_id, created_at ASC",
            *result_ids,
        )
    grouped: Dict[int, List[Dict[str, Any]]] = {rid: [] for rid in result_ids}
    for row in rows:
        d = dict(row)
        grouped.setdefault(d["result_id"], []).append(d)
    return grouped


async def get_research_decision_makers_for_results(result_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    """Decision makers for many results in one query, primary first."""
    if not result_ids:
        return {}
    placeholders = ", ".join("?" for _ in result_ids)
    async with get_db() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM lead_research_decision_makers WHERE result_id IN ({placeholders}) "
            f"ORDER BY result_id, is_primary DESC, id ASC",
            *result_ids,
        )
    grouped: Dict[int, List[Dict[str, Any]]] = {rid: [] for rid in result_ids}
    for row in rows:
        d = dict(row)
        grouped.setdefault(d["result_id"], []).append(d)
    return grouped


_LEAD_RUN_WRITABLE = frozenset({
    "status", "stage", "discovery_run_id", "research_session_id", "lead_ids",
    "leads_found", "leads_researched", "drafts_written", "drafts_skipped",
    "current_item", "error_message", "resume_count", "started_at", "finished_at",
})
_LEAD_RUN_JSON = ("steps", "target_titles", "lead_ids")


def _lead_run_row(row) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    d = dict(row)
    for k in _LEAD_RUN_JSON:
        try:
            d[k] = json.loads(d[k]) if d.get(k) else ([] if k != "target_titles" else None)
        except (json.JSONDecodeError, TypeError):
            d[k] = [] if k != "target_titles" else None
    return d


async def create_lead_run(data: Dict[str, Any]) -> int:
    clean = {
        "niche": data["niche"], "location": data["location"], "target_count": int(data["target_count"]),
        "steps": json.dumps(list(data["steps"])), "channel": data.get("channel") or "EMAIL",
        "target_titles": json.dumps(data["target_titles"]) if data.get("target_titles") else None,
        "hot_warm_only": 1 if data.get("hot_warm_only", True) else 0,
    }
    clean = {k: v for k, v in clean.items() if v is not None}
    col_sql = ", ".join(clean.keys())
    ph = ", ".join("?" for _ in clean)
    async with get_db() as conn:
        return await conn.fetchval(
            f"INSERT INTO lead_runs ({col_sql}) VALUES ({ph}) RETURNING id", *clean.values(),
        )


async def update_lead_run(run_id: int, data: Dict[str, Any]) -> bool:
    clean = {k: (json.dumps(v) if k == "lead_ids" else v)
             for k, v in data.items() if k in _LEAD_RUN_WRITABLE and v is not None}
    if not clean:
        return False
    set_clause = ", ".join(f"{c} = ?" for c in clean)
    async with get_db() as conn:
        result = await conn.execute(
            f"UPDATE lead_runs SET {set_clause} WHERE id = ?", *clean.values(), run_id,
        )
    return _rows_affected(result) > 0


async def get_lead_run(run_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        row = await conn.fetchrow("SELECT * FROM lead_runs WHERE id = $1", run_id)
    return _lead_run_row(row)


async def list_lead_runs(limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch("SELECT * FROM lead_runs ORDER BY id DESC LIMIT ? OFFSET ?", limit, offset)
    return [_lead_run_row(r) for r in rows]


async def list_interrupted_lead_runs() -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT * FROM lead_runs WHERE status IN ('QUEUED', 'RUNNING', 'CANCEL_REQUESTED') ORDER BY id ASC"
        )
    return [_lead_run_row(r) for r in rows]


# ── Client portal ────────────────────────────────────────────────────────────

async def portal_fetchrow(sql: str, *args) -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        row = await conn.fetchrow(sql, *args)
    return dict(row) if row else None


async def portal_fetch(sql: str, *args) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


async def portal_execute(sql: str, *args) -> int:
    async with get_db() as conn:
        result = await conn.execute(sql, *args)
    return _rows_affected(result)


async def portal_insert(sql: str, *args) -> int:
    async with get_db() as conn:
        return await conn.fetchval(sql, *args)


async def save_research_result(
    session_id: int,
    result_dict: Dict[str, Any],
    evidence_list: List[Dict[str, Any]],
    lead_id: Optional[int] = None,
    decision_makers: Optional[List[Dict[str, Any]]] = None,
) -> int:
    """Atomic: the result row and all of its evidence rows commit together
    (or neither does) — a crash mid-write can never leave a result with
    partial/missing provenance."""
    clean = {c: result_dict.get(c) for c in _RESEARCH_RESULT_COLS if result_dict.get(c) is not None}
    clean["session_id"] = session_id
    if lead_id is not None:
        clean["lead_id"] = lead_id
    col_sql = ", ".join(clean.keys())
    ph = ", ".join("?" for _ in clean)

    async with transaction() as conn:
        result_id = await conn.fetchval(
            f"INSERT INTO lead_research_results ({col_sql}) VALUES ({ph}) RETURNING id", *clean.values(),
        )
        for ev in evidence_list:
            await conn.execute(
                """INSERT INTO lead_research_evidence
                   (result_id, field_name, source_type, source_url, snippet, confidence, status)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                result_id, ev["field_name"], ev.get("source_type"), ev.get("source_url"),
                ev.get("snippet"), ev.get("confidence", 0), ev.get("status", "UNCONFIRMED"),
            )
        for dm in decision_makers or []:
            await conn.execute(
                """INSERT INTO lead_research_decision_makers
                   (result_id, name, title, matched_title, source_url, snippet, confidence, status, is_primary)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                result_id, dm["name"], dm.get("title"), dm.get("matched_title"), dm.get("source_url"),
                dm.get("snippet"), dm.get("confidence", 0), dm.get("status", "FOUND"),
                1 if dm.get("is_primary") else 0,
            )
    return result_id


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


def _now_naive_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


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
    clean["updated_at"] = _now_naive_iso()
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
    state_clean["updated_at"] = _now_naive_iso()
    async with transaction() as conn:
        if item_clean:
            set_i = ", ".join(f"{c} = ?" for c in item_clean)
            await conn.execute(f"UPDATE automation_queue SET {set_i} WHERE id = ?", *item_clean.values(), item_id)
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
            "INSERT INTO automation_log (level, message) VALUES ($1, $2)", level, (message or "")[:2000]
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


# ─────────────────────────────────────────────────────────────────────────────
# Email Campaigns  (PopupGenix Email Campaign module — Checkpoint 3A)
#
# n8n orchestrates campaign preparation; AutoLead's email_sender.send_email()
# is still the ONLY production sender. These functions are pure persistence —
# no sending, no n8n calls, no business logic beyond the denormalised counters.
# ─────────────────────────────────────────────────────────────────────────────

EMAIL_CAMPAIGN_STATUSES     = ("DRAFT", "READY", "RUNNING", "PAUSED", "COMPLETED", "FAILED")
EMAIL_CAMPAIGN_LEAD_STATUSES = (
    "IMPORTED", "VALIDATED", "MISSING_EMAIL", "INVALID_EMAIL", "DUPLICATE", "READY",
    "GENERATED", "AI_GENERATION_FAILED", "SENT", "SEND_FAILED", "SEND_BLOCKED",
    "SKIPPED", "DO_NOT_CONTACT",
)
EMAIL_CAMPAIGN_RUN_STATUSES = ("PENDING", "PREPARING", "SENDING", "PAUSED", "COMPLETED", "FAILED")

_EMAIL_CAMPAIGN_WRITABLE = frozenset({
    "name", "description", "status", "test_mode", "test_recipient", "ai_enabled",
    "from_name", "from_email",
    "sender_profile_id", "reply_to",
    "attachment_filename", "attachment_path", "attachment_size", "attachment_mime",
    "config_json",
    "total_leads", "valid_leads", "sent_count", "failed_count", "replied_count",
    "started_at", "completed_at",
})

SENDER_PROFILE_PROVIDERS = ("smtp", "gmail")
SENDER_PROFILE_STATUSES  = ("connected", "disconnected", "error")
_SENDER_PROFILE_WRITABLE = frozenset({
    "name", "provider", "transport", "email_address", "display_name", "reply_to",
    "status", "is_default",
    "smtp_host", "smtp_port", "smtp_security", "smtp_username", "smtp_password_enc",
    "oauth_client_id", "oauth_refresh_token_enc", "oauth_access_token_enc",
    "oauth_expires_at", "oauth_scopes",
    "last_tested_at", "last_error",
})
_ECL_WRITABLE = frozenset({
    "lead_id", "email", "first_name", "last_name", "company", "raw_json",
    "body_source", "provided_body", "ai_subject", "ai_body",
    "status", "status_detail", "message_id", "sent_at", "failure_reason", "idempotency_key",
})
_ECR_WRITABLE = frozenset({
    "status", "n8n_trigger_ref", "batch_size", "processed_count",
    "sent_count", "failed_count", "error", "completed_at",
})


def build_email_campaign_lead_key(campaign_id: int, email: Optional[str],
                                  first_name: str = "", last_name: str = "",
                                  company: str = "", rownum: int = 0) -> str:
    """Stable per-campaign lead identity. Mirrors the n8n V1.1 lead_key scheme:
      has email  -> '<campaign_id>::<lower(email)>'
      no email   -> '<campaign_id>::noemail::<slug>::<rownum>'
    """
    e = (email or "").strip().lower()
    if e:
        return f"{campaign_id}::{e}"
    slug = re.sub(r"[^a-z0-9]+", "-",
                  f"{first_name}-{last_name}-{company}".lower()).strip("-") or "unknown"
    return f"{campaign_id}::noemail::{slug}::{rownum}"


# ── email_campaigns ──────────────────────────────────────────────────────────

async def create_email_campaign(data: Dict[str, Any]) -> int:
    async with get_db() as conn:
        return await conn.fetchval(
            "INSERT INTO email_campaigns "
            "(name, description, test_mode, test_recipient, ai_enabled, from_name, from_email, "
            " sender_profile_id, reply_to, config_json) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING id",
            data.get("name"), data.get("description"),
            1 if data.get("test_mode", True) else 0,
            data.get("test_recipient", "shahedalfahad20@gmail.com"),
            1 if data.get("ai_enabled", True) else 0,
            data.get("from_name"), data.get("from_email"),
            data.get("sender_profile_id"), data.get("reply_to"),
            json.dumps(data["config"]) if isinstance(data.get("config"), (dict, list)) else data.get("config_json"),
        )


async def get_email_campaign(campaign_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        row = await conn.fetchrow("SELECT * FROM email_campaigns WHERE id = $1", campaign_id)
    return dict(row) if row else None


async def list_email_campaigns(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT * FROM email_campaigns ORDER BY id DESC LIMIT $1 OFFSET $2", limit, offset
        )
    return [dict(r) for r in rows]


async def update_email_campaign(campaign_id: int, data: Dict[str, Any]) -> bool:
    clean = {k: v for k, v in data.items() if k in _EMAIL_CAMPAIGN_WRITABLE and v is not None}
    # sender_profile_id / reply_to may be explicitly cleared to NULL ("no sender")
    for nullable in ("sender_profile_id", "reply_to"):
        if nullable in data and data[nullable] is None and nullable in _EMAIL_CAMPAIGN_WRITABLE:
            clean[nullable] = None
    if "test_mode" in clean:
        clean["test_mode"] = 1 if clean["test_mode"] in (True, 1, "1", "true", "True") else 0
    if "ai_enabled" in clean:
        clean["ai_enabled"] = 1 if clean["ai_enabled"] in (True, 1, "1", "true", "True") else 0
    if not clean:
        return False
    clean["updated_at"] = _now_naive_iso()
    set_clause = ", ".join(f"{c} = ?" for c in clean)
    async with get_db() as conn:
        result = await conn.execute(
            f"UPDATE email_campaigns SET {set_clause} WHERE id = ?", *clean.values(), campaign_id
        )
    return _rows_affected(result) > 0


async def delete_email_campaign(campaign_id: int) -> bool:
    """Cascades to email_campaign_leads + email_campaign_runs (FK ON DELETE CASCADE)."""
    async with get_db() as conn:
        result = await conn.execute("DELETE FROM email_campaigns WHERE id = ?", campaign_id)
    return _rows_affected(result) > 0


# ── email_campaign_leads ────────────────────────────────────────────────────

async def bulk_insert_email_campaign_leads(campaign_id: int, rows: List[Dict[str, Any]]) -> int:
    """Insert campaign leads. Rows already normalised by the caller. Uses
    INSERT OR IGNORE on (campaign_id, lead_key) so a re-import of the same file
    does not duplicate — the caller counts DUPLICATE separately."""
    if not rows:
        return 0
    inserted = 0
    async with transaction() as conn:
        for r in rows:
            res = await conn.execute(
                "INSERT OR IGNORE INTO email_campaign_leads "
                "(campaign_id, lead_key, lead_id, email, first_name, last_name, company, "
                " raw_json, body_source, provided_body, status, status_detail) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                campaign_id, r["lead_key"], r.get("lead_id"),
                (r.get("email") or "").strip().lower() or None,
                r.get("first_name"), r.get("last_name"), r.get("company"),
                json.dumps(r["raw"]) if isinstance(r.get("raw"), (dict, list)) else r.get("raw_json"),
                r.get("body_source", "ai"), r.get("provided_body"),
                r.get("status", "IMPORTED"), r.get("status_detail"),
            )
            inserted += _rows_affected(res)
    return inserted


async def get_email_campaign_leads(campaign_id: int, status: Optional[str] = None,
                                   offset: int = 0, limit: int = 200) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        if status:
            rows = await conn.fetch(
                "SELECT * FROM email_campaign_leads WHERE campaign_id = $1 AND status = $2 "
                "ORDER BY id ASC LIMIT $3 OFFSET $4", campaign_id, status, limit, offset
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM email_campaign_leads WHERE campaign_id = $1 "
                "ORDER BY id ASC LIMIT $2 OFFSET $3", campaign_id, limit, offset
            )
    return [dict(r) for r in rows]


async def get_email_campaign_lead(campaign_id: int, lead_key: str) -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM email_campaign_leads WHERE campaign_id = $1 AND lead_key = $2",
            campaign_id, lead_key,
        )
    return dict(row) if row else None


async def update_email_campaign_lead(campaign_id: int, lead_key: str, data: Dict[str, Any]) -> bool:
    clean = {k: v for k, v in data.items() if k in _ECL_WRITABLE and v is not None}
    if not clean:
        return False
    clean["updated_at"] = _now_naive_iso()
    set_clause = ", ".join(f"{c} = ?" for c in clean)
    async with get_db() as conn:
        result = await conn.execute(
            f"UPDATE email_campaign_leads SET {set_clause} WHERE campaign_id = ? AND lead_key = ?",
            *clean.values(), campaign_id, lead_key,
        )
    return _rows_affected(result) > 0


async def count_email_campaign_leads_by_status(campaign_id: int) -> Dict[str, int]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT status, COUNT(*) AS n FROM email_campaign_leads WHERE campaign_id = $1 GROUP BY status",
            campaign_id,
        )
    return {r["status"]: r["n"] for r in rows}


async def recount_email_campaign(campaign_id: int) -> None:
    """Refresh the denormalised counters on the email_campaigns row from its leads."""
    counts = await count_email_campaign_leads_by_status(campaign_id)
    total = sum(counts.values())
    valid = total - counts.get("MISSING_EMAIL", 0) - counts.get("INVALID_EMAIL", 0) - counts.get("DUPLICATE", 0)
    await update_email_campaign(campaign_id, {
        "total_leads":  total,
        "valid_leads":  max(0, valid),
        "sent_count":   counts.get("SENT", 0),
        "failed_count": counts.get("SEND_FAILED", 0) + counts.get("AI_GENERATION_FAILED", 0),
    })


# ── email_campaign_runs (idempotent) ────────────────────────────────────────

async def create_email_campaign_run(campaign_id: int, idempotency_key: str,
                                    data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Idempotent: a repeat call with the same (campaign_id, idempotency_key)
    returns the existing run instead of starting a new one."""
    data = data or {}
    async with get_db() as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO email_campaign_runs (campaign_id, idempotency_key, status, batch_size) "
            "VALUES (?, ?, ?, ?)",
            campaign_id, idempotency_key, data.get("status", "PENDING"), int(data.get("batch_size", 0)),
        )
        row = await conn.fetchrow(
            "SELECT * FROM email_campaign_runs WHERE campaign_id = $1 AND idempotency_key = $2",
            campaign_id, idempotency_key,
        )
    return dict(row)


async def get_email_campaign_run(run_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        row = await conn.fetchrow("SELECT * FROM email_campaign_runs WHERE id = $1", run_id)
    return dict(row) if row else None


async def list_email_campaign_runs(campaign_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT * FROM email_campaign_runs WHERE campaign_id = $1 ORDER BY id DESC LIMIT $2",
            campaign_id, limit,
        )
    return [dict(r) for r in rows]


async def update_email_campaign_run(run_id: int, data: Dict[str, Any]) -> bool:
    clean = {k: v for k, v in data.items() if k in _ECR_WRITABLE and v is not None}
    if not clean:
        return False
    set_clause = ", ".join(f"{c} = ?" for c in clean)
    async with get_db() as conn:
        result = await conn.execute(
            f"UPDATE email_campaign_runs SET {set_clause} WHERE id = ?", *clean.values(), run_id
        )
    return _rows_affected(result) > 0


# ── email_campaign_activity (audit — never stores a secret) ─────────────────

async def log_email_campaign_activity(campaign_id: int, event: str, detail: str = "",
                                      level: str = "INFO", lead_key: Optional[str] = None) -> None:
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO email_campaign_activity (campaign_id, level, event, lead_key, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            campaign_id, level, event, lead_key, (detail or "")[:2000],
        )


async def get_email_campaign_activity(campaign_id: int, limit: int = 200) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT * FROM email_campaign_activity WHERE campaign_id = $1 ORDER BY id DESC LIMIT $2",
            campaign_id, limit,
        )
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Sender Profiles + OAuth state (Checkpoint 4)
#
# Pure persistence. The *_enc columns hold values already encrypted by the
# caller (secrets_crypto); this layer never encrypts/decrypts and never logs a
# secret. Single-operator app — no owner column.
# ─────────────────────────────────────────────────────────────────────────────

async def create_sender_profile(data: Dict[str, Any]) -> int:
    cols = [k for k in data if k in _SENDER_PROFILE_WRITABLE]
    if "name" not in cols or "provider" not in cols or "email_address" not in cols:
        raise ValueError("sender profile requires name, provider, email_address")
    placeholders = ", ".join("?" for _ in cols)
    async with get_db() as conn:
        return await conn.fetchval(
            f"INSERT INTO email_sender_profiles ({', '.join(cols)}) VALUES ({placeholders}) RETURNING id",
            *[data[c] for c in cols],
        )


async def get_sender_profile(profile_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        row = await conn.fetchrow("SELECT * FROM email_sender_profiles WHERE id = $1", profile_id)
    return dict(row) if row else None


async def list_sender_profiles() -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT * FROM email_sender_profiles ORDER BY is_default DESC, id ASC"
        )
    return [dict(r) for r in rows]


async def get_default_sender_profile() -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM email_sender_profiles WHERE is_default = 1 LIMIT 1"
        )
    return dict(row) if row else None


async def update_sender_profile(profile_id: int, data: Dict[str, Any]) -> bool:
    clean = {k: v for k, v in data.items() if k in _SENDER_PROFILE_WRITABLE}
    # allow explicit NULL for the token / password / error columns (disconnect)
    clean = {k: v for k, v in clean.items()
             if v is not None or k in ("smtp_password_enc", "oauth_refresh_token_enc",
                                       "oauth_access_token_enc", "oauth_expires_at", "last_error")}
    if not clean:
        return False
    if "is_default" in clean:
        clean["is_default"] = 1 if clean["is_default"] in (True, 1, "1", "true", "True") else 0
    clean["updated_at"] = _now_naive_iso()
    set_clause = ", ".join(f"{c} = ?" for c in clean)
    async with get_db() as conn:
        result = await conn.execute(
            f"UPDATE email_sender_profiles SET {set_clause} WHERE id = ?",
            *clean.values(), profile_id,
        )
    return _rows_affected(result) > 0


async def set_default_sender_profile(profile_id: int) -> bool:
    """Exactly one default. Clears every other profile in the same transaction."""
    async with transaction() as conn:
        await conn.execute("UPDATE email_sender_profiles SET is_default = 0 WHERE is_default = 1")
        result = await conn.execute(
            "UPDATE email_sender_profiles SET is_default = 1, updated_at = ? WHERE id = ?",
            _now_naive_iso(), profile_id,
        )
    return _rows_affected(result) > 0


async def delete_sender_profile(profile_id: int) -> bool:
    """Deletes the profile and NULLs it out on any campaign that referenced it
    (code-enforced SET NULL — the column carries no FK, see _run_migrations)."""
    async with transaction() as conn:
        await conn.execute(
            "UPDATE email_campaigns SET sender_profile_id = NULL WHERE sender_profile_id = ?",
            profile_id,
        )
        result = await conn.execute(
            "DELETE FROM email_sender_profiles WHERE id = ?", profile_id
        )
    return _rows_affected(result) > 0


async def count_campaigns_using_sender(profile_id: int) -> int:
    async with get_db() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM email_campaigns WHERE sender_profile_id = $1", profile_id
        ) or 0


# ── oauth_states (CSRF / replay protection for the Gmail connect flow) ──────

async def create_oauth_state(state: str, purpose: str, ttl_seconds: int = 600) -> None:
    from datetime import datetime, timedelta, timezone
    exp = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).replace(tzinfo=None).isoformat()
    async with get_db() as conn:
        await conn.execute("DELETE FROM oauth_states WHERE expires_at < ?", _now_naive_iso())
        await conn.execute(
            "INSERT INTO oauth_states (state, purpose, expires_at) VALUES (?, ?, ?)",
            state, purpose, exp,
        )


async def consume_oauth_state(state: str, purpose: str) -> bool:
    """One-time use. Returns True only if the state exists, matches the purpose,
    is unexpired and unused — and atomically marks it used."""
    if not state:
        return False
    async with transaction() as conn:
        row = await conn.fetchrow(
            "SELECT state, purpose, expires_at, used_at FROM oauth_states WHERE state = $1", state
        )
        if not row or row["purpose"] != purpose or row["used_at"] is not None:
            return False
        if str(row["expires_at"]) < _now_naive_iso():
            return False
        await conn.execute(
            "UPDATE oauth_states SET used_at = ? WHERE state = ?", _now_naive_iso(), state
        )
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _rows_affected(status_str: str) -> int:
    try:
        return int(str(status_str).split()[-1])
    except (IndexError, ValueError):
        return 0
