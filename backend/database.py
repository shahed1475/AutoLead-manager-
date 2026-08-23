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
    "evidence_ids",
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

    # Data normalisation
    # DO_NOT_CONTACT (Phase 4 opt-out) is a terminal, sticky status — must be in
    # this allow-list or a lead marked DO_NOT_CONTACT would silently revert to
    # PENDING on the next restart, undoing the opt-out.
    await raw.execute("""
        UPDATE leads SET status = 'PENDING'
        WHERE status IS NULL
           OR status NOT IN ('PENDING','SENT','REPLIED','SKIPPED','MESSAGES_READY','ENRICHED','SCORED','DO_NOT_CONTACT')
    """)
    await raw.execute("""
        UPDATE campaign_runs SET status = 'FAILED', stage = 'FAILED', finished_at = CURRENT_TIMESTAMP
        WHERE status = 'RUNNING'
    """)
    await raw.execute("""
        UPDATE company_profiles SET status = 'PENDING'
        WHERE status IN ('QUALIFYING', 'RESEARCHING', 'FAILED')
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
    "business_name", "phone", "email", "website", "address",
    "niche", "city", "country", "rating", "reviews_count", "review_count",
    "source", "status", "channel",
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
    if status:       conditions.append(f"status = {p(status.upper())}")
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
        result = await conn.execute(
            "UPDATE leads SET status = 'REPLIED' WHERE id = $1", lead_id
        )
    return _rows_affected(result) > 0


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
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _rows_affected(status_str: str) -> int:
    try:
        return int(str(status_str).split()[-1])
    except (IndexError, ValueError):
        return 0
