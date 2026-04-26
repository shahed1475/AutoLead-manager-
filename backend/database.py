"""
database.py — PostgreSQL (asyncpg) database layer for AutoLead v3.

Connection pool: min=5, max=20. All public functions maintain the
same signatures as the SQLite version so routers require no changes.

Tables
──────
  Existing (migrated):  leads, app_settings, campaign_log, campaign_runs,
                        reply_inbox
  New (v3):             enriched_data, scores, messages, replies, campaigns
"""
import asyncpg
import json
import logging
import math
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from .config import get_settings

logger   = logging.getLogger(__name__)
settings = get_settings()

# Module-level pool — created once in init_db(), shared by all requests
_pool: Optional[asyncpg.Pool] = None


# ─────────────────────────────────────────────────────────────────────────────
# Pool lifecycle
# ─────────────────────────────────────────────────────────────────────────────

async def _register_codecs(conn: asyncpg.Connection) -> None:
    """Called on every new connection; registers JSONB ↔ dict codec."""
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
        format="text",
    )


async def init_db() -> None:
    """Create connection pool and apply idempotent schema."""
    global _pool
    _pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        command_timeout=60,
        init=_register_codecs,
    )
    await _run_migrations()
    logger.info(
        "PostgreSQL pool ready (min=%d max=%d)",
        settings.db_pool_min, settings.db_pool_max,
    )


async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL pool closed")


@asynccontextmanager
async def get_db() -> AsyncGenerator[asyncpg.Connection, None]:
    if _pool is None:
        raise RuntimeError("Database pool not initialised — call init_db() first")
    async with _pool.acquire() as conn:
        yield conn


# ─────────────────────────────────────────────────────────────────────────────
# Schema — all CREATE TABLE / INDEX use IF NOT EXISTS → idempotent
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
-- ── Settings ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_settings (
    key        VARCHAR(100) PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ── Leads ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS leads (
    id                   SERIAL PRIMARY KEY,
    business_name        VARCHAR(255) NOT NULL,
    phone                VARCHAR(50),
    email                VARCHAR(255),
    website              VARCHAR(500),
    address              TEXT,
    niche                VARCHAR(100),
    city                 VARCHAR(100),
    country              VARCHAR(100),
    rating               FLOAT,
    reviews_count        INTEGER,
    review_count         INTEGER,
    source               VARCHAR(50),
    status               VARCHAR(30)  DEFAULT 'PENDING',
    channel              VARCHAR(20),
    score                INTEGER      DEFAULT 0,
    score_label          VARCHAR(10)  DEFAULT 'COLD',
    score_category       VARCHAR(10)  DEFAULT 'COLD',
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
    verified_email       SMALLINT     DEFAULT 0,
    has_social_links     SMALLINT     DEFAULT 0,
    sent_at              TIMESTAMP,
    followup_sent_at     TIMESTAMP,
    follow_up_1_sent_at  TIMESTAMP,
    follow_up_2_sent_at  TIMESTAMP,
    follow_up_3_sent_at  TIMESTAMP,
    created_at           TIMESTAMP    DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_email
    ON leads (email) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_phone
    ON leads (phone) WHERE phone IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_leads_status  ON leads (status);
CREATE INDEX IF NOT EXISTS idx_leads_niche   ON leads (niche);
CREATE INDEX IF NOT EXISTS idx_leads_city    ON leads (city);
CREATE INDEX IF NOT EXISTS idx_leads_score   ON leads (score_label);
CREATE INDEX IF NOT EXISTS idx_leads_source  ON leads (source);
CREATE INDEX IF NOT EXISTS idx_leads_created ON leads (created_at);

-- ── Campaign log ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS campaign_log (
    id        SERIAL    PRIMARY KEY,
    lead_id   INTEGER   REFERENCES leads(id) ON DELETE SET NULL,
    channel   VARCHAR(20),
    action    VARCHAR(50),
    success   BOOLEAN   DEFAULT FALSE,
    error_msg TEXT,
    timestamp TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_log_lead      ON campaign_log (lead_id);
CREATE INDEX IF NOT EXISTS idx_log_timestamp ON campaign_log (timestamp);

-- ── Campaign runs (backwards compat) ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS campaign_runs (
    id          SERIAL      PRIMARY KEY,
    niche       VARCHAR(100),
    city        VARCHAR(100),
    country     VARCHAR(100),
    channel     VARCHAR(20),
    daily_cap   INTEGER     DEFAULT 20,
    leads_found INTEGER     DEFAULT 0,
    leads_sent  INTEGER     DEFAULT 0,
    sources     TEXT,
    started_at  TIMESTAMP   DEFAULT NOW(),
    finished_at TIMESTAMP,
    status      VARCHAR(20) DEFAULT 'RUNNING'
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON campaign_runs (started_at);

-- ── Reply inbox (backwards compat) ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reply_inbox (
    id           SERIAL       PRIMARY KEY,
    lead_id      INTEGER      REFERENCES leads(id) ON DELETE SET NULL,
    from_email   VARCHAR(255) NOT NULL,
    subject      TEXT,
    body_snippet TEXT,
    received_at  TEXT,
    intent       VARCHAR(20)  DEFAULT 'NEUTRAL',
    processed    BOOLEAN      DEFAULT FALSE,
    created_at   TIMESTAMP    DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_inbox_lead    ON reply_inbox (lead_id);
CREATE INDEX IF NOT EXISTS idx_inbox_email   ON reply_inbox (from_email);
CREATE INDEX IF NOT EXISTS idx_inbox_created ON reply_inbox (created_at);

-- ── Enriched data (v3 new) ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS enriched_data (
    id                    SERIAL PRIMARY KEY,
    lead_id               INTEGER UNIQUE REFERENCES leads(id) ON DELETE CASCADE,
    business_summary      TEXT,
    target_audience       TEXT,
    service_level         VARCHAR(50),
    brand_positioning     TEXT,
    marketing_gaps        TEXT[],
    growth_potential      VARCHAR(50),
    best_pitch_strategy   TEXT,
    personalization_hook  TEXT,
    website_text          TEXT,
    website_quality_score FLOAT   DEFAULT 0,
    issues                TEXT[],
    conversion_gaps       TEXT[],
    seo_gaps              TEXT[],
    pitch_angles          TEXT[],
    enriched_at           TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_enriched_lead ON enriched_data (lead_id);

-- ── Enriched data — upgrade migrations (idempotent, safe on existing installs) ─
ALTER TABLE enriched_data ADD COLUMN IF NOT EXISTS personalization_hook  TEXT;
ALTER TABLE enriched_data ADD COLUMN IF NOT EXISTS website_quality_score FLOAT   DEFAULT 0;
ALTER TABLE enriched_data ADD COLUMN IF NOT EXISTS issues                TEXT[];
ALTER TABLE enriched_data ADD COLUMN IF NOT EXISTS conversion_gaps       TEXT[];
ALTER TABLE enriched_data ADD COLUMN IF NOT EXISTS seo_gaps              TEXT[];
ALTER TABLE enriched_data ADD COLUMN IF NOT EXISTS pitch_angles          TEXT[];

-- ── Scores breakdown (v3 new) ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scores (
    id                  SERIAL PRIMARY KEY,
    lead_id             INTEGER UNIQUE REFERENCES leads(id) ON DELETE CASCADE,
    digital_score       FLOAT DEFAULT 0,
    website_score       FLOAT DEFAULT 0,
    business_score      FLOAT DEFAULT 0,
    opportunity_score   FLOAT DEFAULT 0,
    final_score         FLOAT DEFAULT 0,
    category            VARCHAR(10)  DEFAULT 'COLD',
    key_problems        TEXT[],
    opportunity_summary TEXT,
    pitch_angle         TEXT,
    scored_at           TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_scores_lead ON scores (lead_id);

-- ── Message sequences (v3 new) ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS messages (
    id            SERIAL      PRIMARY KEY,
    lead_id       INTEGER     REFERENCES leads(id) ON DELETE CASCADE,
    sequence_step INTEGER     DEFAULT 1,
    message_type  VARCHAR(20),
    subject       VARCHAR(500),
    body          TEXT,
    status        VARCHAR(20) DEFAULT 'PENDING',
    sent_at       TIMESTAMP,
    scheduled_for TIMESTAMP,
    created_at    TIMESTAMP   DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_messages_lead   ON messages (lead_id);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages (status);

-- ── Replies linked to messages (v3 new) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS replies (
    id              SERIAL     PRIMARY KEY,
    lead_id         INTEGER    REFERENCES leads(id),
    message_id      INTEGER    REFERENCES messages(id),
    reply_text      TEXT,
    detected_intent VARCHAR(30),
    raw_email_data  JSONB,
    received_at     TIMESTAMP  DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_replies_lead ON replies (lead_id);

-- ── Campaigns high-level tracking (v3 new) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS campaigns (
    id            SERIAL      PRIMARY KEY,
    niche         VARCHAR(100),
    city          VARCHAR(100),
    country       VARCHAR(100),
    sources       TEXT[],
    channel       VARCHAR(20),
    daily_cap     INTEGER,
    leads_found   INTEGER     DEFAULT 0,
    leads_sent    INTEGER     DEFAULT 0,
    leads_replied INTEGER     DEFAULT 0,
    status        VARCHAR(20) DEFAULT 'RUNNING',
    started_at    TIMESTAMP   DEFAULT NOW(),
    completed_at  TIMESTAMP
);
"""


async def _run_migrations() -> None:
    async with get_db() as conn:
        await conn.execute(_SCHEMA_SQL)
    logger.info("Schema migrations applied")


# ─────────────────────────────────────────────────────────────────────────────
# Type helpers
# ─────────────────────────────────────────────────────────────────────────────

# Columns that must be datetime objects (not strings) for asyncpg TIMESTAMP binding
_TS_COLS = frozenset({
    "enriched_at", "sent_at", "followup_sent_at",
    "follow_up_1_sent_at", "follow_up_2_sent_at", "follow_up_3_sent_at",
})

# Whitelist of writable lead columns — prevents SQL injection via column names
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
    """Convert Python values to asyncpg-compatible types."""
    if val is None:
        return None
    if col in _TS_COLS and isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except ValueError:
            return None
    return val


# ─────────────────────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────────────────────

async def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    async with get_db() as conn:
        val = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key
        )
    return val if val is not None else default


async def upsert_setting(key: str, value: str) -> None:
    async with get_db() as conn:
        await conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES ($1, $2, NOW())
               ON CONFLICT (key) DO UPDATE
                 SET value = EXCLUDED.value, updated_at = NOW()""",
            key, value,
        )


async def get_all_settings() -> Dict[str, str]:
    async with get_db() as conn:
        rows = await conn.fetch("SELECT key, value FROM app_settings")
    return {r["key"]: r["value"] for r in rows}


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard stats
# ─────────────────────────────────────────────────────────────────────────────

async def get_dashboard_stats() -> Dict[str, Any]:
    async with get_db() as conn:
        # Single pass over leads for all status + score counts
        lead_row = await conn.fetchrow("""
            SELECT
                COUNT(*)                                                          AS total,
                COUNT(*) FILTER (WHERE status = 'PENDING')                        AS pending,
                COUNT(*) FILTER (WHERE status = 'SENT')                           AS sent,
                COUNT(*) FILTER (WHERE status = 'REPLIED')                        AS replied,
                COUNT(*) FILTER (WHERE status = 'SKIPPED')                        AS skipped,
                COUNT(*) FILTER (WHERE score_label = 'HOT'  AND status != 'SKIPPED') AS hot_leads,
                COUNT(*) FILTER (WHERE score_label = 'WARM' AND status != 'SKIPPED') AS warm_leads,
                COUNT(*) FILTER (WHERE score_label = 'COLD' AND status != 'SKIPPED') AS cold_leads
            FROM leads
        """)

        # Today's sends per channel
        today_rows = await conn.fetch("""
            SELECT channel, COUNT(*) AS cnt
            FROM campaign_log
            WHERE success = TRUE
              AND action = 'SEND'
              AND timestamp::date = CURRENT_DATE
            GROUP BY channel
        """)

        # Unread replies
        unread = await conn.fetchval(
            "SELECT COUNT(*) FROM reply_inbox WHERE processed = FALSE"
        )

        # Avg deal value from settings
        avg_deal_str = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = 'avg_deal_value'"
        )

    by_channel = {r["channel"]: r["cnt"] for r in today_rows}
    email_today = by_channel.get("EMAIL", 0) + by_channel.get("BOTH", 0)
    wa_today    = by_channel.get("WHATSAPP", 0) + by_channel.get("BOTH", 0)
    sent_today  = sum(by_channel.values())
    avg_deal    = float(avg_deal_str) if avg_deal_str else 500.0

    total   = lead_row["total"]   or 0
    replied = lead_row["replied"] or 0

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
    """7-day outreach activity using PostgreSQL generate_series."""
    async with get_db() as conn:
        rows = await conn.fetch("""
            WITH dates AS (
                SELECT gs::date AS day
                FROM generate_series(
                    CURRENT_DATE - 6,
                    CURRENT_DATE,
                    INTERVAL '1 day'
                ) gs
            )
            SELECT
                d.day,
                TO_CHAR(d.day, 'Dy')                                          AS day_label,
                COALESCE((
                    SELECT COUNT(*) FROM leads WHERE created_at::date = d.day
                ), 0)                                                          AS leads_created,
                COALESCE(SUM(
                    CASE WHEN cl.action='SEND' AND cl.success
                              AND cl.channel IN ('EMAIL','BOTH')
                         THEN 1 ELSE 0 END), 0)                               AS email_sent,
                COALESCE(SUM(
                    CASE WHEN cl.action='SEND' AND cl.success
                              AND cl.channel IN ('WHATSAPP','BOTH')
                         THEN 1 ELSE 0 END), 0)                               AS whatsapp_sent,
                COALESCE(SUM(
                    CASE WHEN cl.action='SEND' AND cl.success
                         THEN 1 ELSE 0 END), 0)                               AS total_sent
            FROM dates d
            LEFT JOIN campaign_log cl ON cl.timestamp::date = d.day
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

    if sort_by not in _SORTABLE:
        sort_by = "created_at"
    if date_field not in _DATE_FIELDS:
        date_field = "created_at"
    order = "DESC" if sort_dir.lower() == "desc" else "ASC"

    # Build WHERE incrementally; p() appends to params and returns $N
    params: List[Any] = []

    def p(val: Any) -> str:
        params.append(val)
        return f"${len(params)}"

    conditions: List[str] = []

    if status:
        conditions.append(f"status = {p(status.upper())}")
    if channel:
        conditions.append(f"channel = {p(channel.upper())}")
    if niche:
        conditions.append(f"niche ILIKE {p(f'%{niche}%')}")
    if city:
        conditions.append(f"city ILIKE {p(f'%{city}%')}")
    if search:
        term = f"%{search}%"
        ph   = p(term)
        conditions.append(f"(business_name ILIKE {ph} OR email ILIKE {ph} OR phone ILIKE {ph})")
    if date_from:
        conditions.append(f"{date_field}::date >= {p(date_from)}::date")
    if date_to:
        conditions.append(f"{date_field}::date <= {p(date_to)}::date")
    if score_label and score_label.upper() in ("HOT", "WARM", "COLD"):
        conditions.append(f"score_label = {p(score_label.upper())}")
    if enriched_only:
        conditions.append("website_summary IS NOT NULL AND website_summary <> ''")

    where  = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    offset = (page - 1) * page_size

    async with get_db() as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM leads {where}", *params
        ) or 0
        rows = await conn.fetch(
            f"""SELECT * FROM leads {where}
                ORDER BY {sort_by} {order}
                LIMIT {p(page_size)} OFFSET {p(offset)}""",
            *params,
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
    email: Optional[str], phone: Optional[str]
) -> Optional[Dict[str, Any]]:
    conditions, params = [], []
    if email:
        params.append(email.lower())
        conditions.append(f"LOWER(email) = ${len(params)}")
    if phone:
        params.append(phone)
        conditions.append(f"phone = ${len(params)}")
    if not conditions:
        return None
    async with get_db() as conn:
        row = await conn.fetchrow(
            f"SELECT * FROM leads WHERE ({' OR '.join(conditions)}) LIMIT 1", *params
        )
    return dict(row) if row else None


async def create_lead(data: Dict[str, Any]) -> int:
    """Insert a new lead; returns its id. Raises asyncpg.UniqueViolationError on duplicate."""
    clean = {
        k: _coerce(k, v)
        for k, v in data.items()
        if k in _LEAD_WRITABLE and v is not None
    }
    if not clean:
        raise ValueError("No writable fields provided")

    cols         = ", ".join(clean.keys())
    placeholders = ", ".join(f"${i+1}" for i in range(len(clean)))
    values       = list(clean.values())

    async with get_db() as conn:
        lead_id = await conn.fetchval(
            f"INSERT INTO leads ({cols}) VALUES ({placeholders}) RETURNING id",
            *values,
        )
    return lead_id


async def create_lead_deduped(data: Dict[str, Any]) -> Tuple[int, bool]:
    """Create a lead only if email/phone not already in DB. Returns (id, is_new)."""
    existing = await find_duplicate_lead(data.get("email"), data.get("phone"))
    if existing:
        return existing["id"], False
    try:
        lead_id = await create_lead(data)
        return lead_id, True
    except asyncpg.UniqueViolationError:
        # Race condition: another request beat us to it
        existing = await find_duplicate_lead(data.get("email"), data.get("phone"))
        if existing:
            return existing["id"], False
        raise


async def update_lead(lead_id: int, data: Dict[str, Any]) -> bool:
    clean = {
        k: _coerce(k, v)
        for k, v in data.items()
        if k in _LEAD_WRITABLE and v is not None
    }
    if not clean:
        return False

    params: List[Any] = list(clean.values())
    set_clause = ", ".join(
        f"{col} = ${i+1}" for i, col in enumerate(clean.keys())
    )
    params.append(lead_id)

    async with get_db() as conn:
        result = await conn.execute(
            f"UPDATE leads SET {set_clause} WHERE id = ${len(params)}", *params
        )
    return _rows_affected(result) > 0


async def delete_lead(lead_id: int) -> bool:
    async with get_db() as conn:
        result = await conn.execute("DELETE FROM leads WHERE id = $1", lead_id)
    return _rows_affected(result) > 0


async def delete_all_leads(status: Optional[str] = None) -> int:
    """Bulk-delete leads. Pass status to restrict to one status bucket."""
    valid = {"PENDING", "SENT", "REPLIED", "SKIPPED"}
    async with get_db() as conn:
        if status and status.upper() in valid:
            result = await conn.execute(
                "DELETE FROM leads WHERE status = $1", status.upper()
            )
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


async def get_leads_without_score(limit: int = 100) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            """SELECT * FROM leads
               WHERE (score IS NULL OR score = 0) AND status != 'SKIPPED'
               ORDER BY created_at DESC LIMIT $1""",
            limit,
        )
    return [dict(r) for r in rows]


async def get_score_distribution() -> Dict[str, int]:
    async with get_db() as conn:
        rows = await conn.fetch(
            """SELECT score_label, COUNT(*) AS cnt
               FROM leads WHERE status != 'SKIPPED'
               GROUP BY score_label"""
        )
    dist = {"HOT": 0, "WARM": 0, "COLD": 0}
    for r in rows:
        lbl = (r["score_label"] or "COLD").upper()
        if lbl in dist:
            dist[lbl] = r["cnt"]
    return dist


async def get_leads_due_for_followup(days: int = 3) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            """SELECT * FROM leads
               WHERE status = 'SENT'
                 AND followup_sent_at IS NULL
                 AND sent_at IS NOT NULL
                 AND sent_at::date <= CURRENT_DATE - ($1 * INTERVAL '1 day')
               LIMIT 50""",
            days,
        )
    return [dict(r) for r in rows]


async def get_leads_due_for_stage(stage: int, limit: int = 50) -> List[Dict[str, Any]]:
    _DAYS = {1: 3, 2: 7, 3: 7}
    days  = _DAYS.get(stage, 3)

    if stage == 1:
        sql = """
            SELECT * FROM leads
            WHERE status NOT IN ('REPLIED','SKIPPED')
              AND status = 'SENT'
              AND follow_up_1_sent_at IS NULL
              AND sent_at IS NOT NULL
              AND sent_at::date <= CURRENT_DATE - ($1 * INTERVAL '1 day')
            LIMIT $2"""
    elif stage == 2:
        sql = """
            SELECT * FROM leads
            WHERE status NOT IN ('REPLIED','SKIPPED')
              AND follow_up_1_sent_at IS NOT NULL
              AND follow_up_2_sent_at IS NULL
              AND follow_up_1_sent_at::date <= CURRENT_DATE - ($1 * INTERVAL '1 day')
            LIMIT $2"""
    elif stage == 3:
        sql = """
            SELECT * FROM leads
            WHERE status NOT IN ('REPLIED','SKIPPED')
              AND follow_up_2_sent_at IS NOT NULL
              AND follow_up_3_sent_at IS NULL
              AND follow_up_2_sent_at::date <= CURRENT_DATE - ($1 * INTERVAL '1 day')
            LIMIT $2"""
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
            lead_id, channel, action, bool(success), error_msg,
        )


async def get_recent_logs(limit: int = 20) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch("""
            SELECT
                cl.id,
                TO_CHAR(cl.timestamp, 'YYYY-MM-DD"T"HH24:MI:SS') AS timestamp,
                cl.channel,
                cl.action,
                cl.success,
                cl.error_msg,
                COALESCE(l.business_name, 'Unknown') AS business_name,
                l.niche,
                l.city
            FROM campaign_log cl
            LEFT JOIN leads l ON l.id = cl.lead_id
            ORDER BY cl.timestamp DESC
            LIMIT $1
        """, limit)
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Campaign runs (legacy — used by existing campaign router)
# ─────────────────────────────────────────────────────────────────────────────

async def create_campaign_run(
    niche: str, city: str, channel: str, daily_cap: int,
    sources: str = "GOOGLE_MAPS",
) -> int:
    async with get_db() as conn:
        run_id = await conn.fetchval(
            """INSERT INTO campaign_runs (niche, city, channel, daily_cap, sources)
               VALUES ($1, $2, $3, $4, $5) RETURNING id""",
            niche, city, channel, daily_cap, sources,
        )
    return run_id


async def update_campaign_run(run_id: int, data: Dict[str, Any]) -> bool:
    if not data:
        return False
    params: List[Any] = list(data.values())
    set_clause = ", ".join(
        f"{col} = ${i+1}" for i, col in enumerate(data.keys())
    )
    params.append(run_id)
    async with get_db() as conn:
        result = await conn.execute(
            f"UPDATE campaign_runs SET {set_clause} WHERE id = ${len(params)}", *params
        )
    return _rows_affected(result) > 0


async def get_campaign_history(limit: int = 10) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch("""
            SELECT
                id, niche, city, channel, daily_cap, leads_found, leads_sent,
                TO_CHAR(started_at,  'YYYY-MM-DD"T"HH24:MI:SS') AS started_at,
                TO_CHAR(finished_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS finished_at,
                status
            FROM campaign_runs
            ORDER BY started_at DESC
            LIMIT $1
        """, limit)
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Reply inbox
# ─────────────────────────────────────────────────────────────────────────────

_INBOX_WRITABLE = frozenset({
    "lead_id", "from_email", "subject", "body_snippet",
    "received_at", "intent", "processed",
})


async def save_reply(data: Dict[str, Any]) -> int:
    clean = {k: v for k, v in data.items() if k in _INBOX_WRITABLE and v is not None}
    cols         = ", ".join(clean.keys())
    placeholders = ", ".join(f"${i+1}" for i in range(len(clean)))
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
        return f"${len(params)}"

    conditions: List[str] = []
    if intent:
        conditions.append(f"ri.intent = {p(intent.upper())}")
    if processed is not None:
        conditions.append(f"ri.processed = {p(processed)}")

    where  = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    offset = (page - 1) * page_size

    async with get_db() as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM reply_inbox ri {where}", *params
        ) or 0
        rows = await conn.fetch(
            f"""SELECT ri.*,
                       COALESCE(l.business_name, '') AS business_name,
                       l.niche, l.city, l.status AS lead_status
                FROM reply_inbox ri
                LEFT JOIN leads l ON l.id = ri.lead_id
                {where}
                ORDER BY ri.created_at DESC
                LIMIT {p(page_size)} OFFSET {p(offset)}""",
            *params,
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
    params: List[Any] = list(clean.values())
    set_clause = ", ".join(
        f"{col} = ${i+1}" for i, col in enumerate(clean.keys())
    )
    params.append(entry_id)
    async with get_db() as conn:
        result = await conn.execute(
            f"UPDATE reply_inbox SET {set_clause} WHERE id = ${len(params)}", *params
        )
    return _rows_affected(result) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Enriched data (new table)
# ─────────────────────────────────────────────────────────────────────────────

_ENRICHED_WRITABLE = frozenset({
    "business_summary", "target_audience", "service_level", "brand_positioning",
    "marketing_gaps", "growth_potential", "best_pitch_strategy",
    "personalization_hook", "website_text",
    "website_quality_score", "issues", "conversion_gaps", "seo_gaps", "pitch_angles",
    "enriched_at",
})


async def upsert_enriched_data(lead_id: int, data: Dict[str, Any]) -> int:
    """Insert or update enriched_data for a lead. Returns row id."""
    clean = {k: v for k, v in data.items() if k in _ENRICHED_WRITABLE and v is not None}
    cols         = ", ".join(["lead_id"] + list(clean.keys()))
    placeholders = ", ".join(f"${i+1}" for i in range(len(clean) + 1))
    update_set   = ", ".join(
        f"{col} = EXCLUDED.{col}" for col in clean.keys()
    ) + ", enriched_at = NOW()"

    async with get_db() as conn:
        row_id = await conn.fetchval(
            f"""INSERT INTO enriched_data ({cols})
                VALUES ({placeholders})
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
# Scores (new table)
# ─────────────────────────────────────────────────────────────────────────────

_SCORE_WRITABLE = frozenset({
    "digital_score", "website_score", "business_score",
    "opportunity_score", "final_score", "category",
    "key_problems", "opportunity_summary", "pitch_angle",
})


async def upsert_score(lead_id: int, data: Dict[str, Any]) -> int:
    """Insert or update the detailed score for a lead. Returns row id."""
    clean      = {k: v for k, v in data.items() if k in _SCORE_WRITABLE and v is not None}
    cols       = ", ".join(["lead_id"] + list(clean.keys()))
    placeholders = ", ".join(f"${i+1}" for i in range(len(clean) + 1))
    update_set = ", ".join(
        f"{col} = EXCLUDED.{col}" for col in clean.keys()
    ) + ", scored_at = NOW()"

    async with get_db() as conn:
        row_id = await conn.fetchval(
            f"""INSERT INTO scores ({cols})
                VALUES ({placeholders})
                ON CONFLICT (lead_id) DO UPDATE SET {update_set}
                RETURNING id""",
            lead_id, *clean.values(),
        )
    return row_id


async def get_score(lead_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM scores WHERE lead_id = $1", lead_id
        )
    return dict(row) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# Messages (new table)
# ─────────────────────────────────────────────────────────────────────────────

_MSG_WRITABLE = frozenset({
    "lead_id", "sequence_step", "message_type", "subject", "body",
    "status", "sent_at", "scheduled_for",
})


async def create_message(data: Dict[str, Any]) -> int:
    clean = {k: v for k, v in data.items() if k in _MSG_WRITABLE and v is not None}
    cols         = ", ".join(clean.keys())
    placeholders = ", ".join(f"${i+1}" for i in range(len(clean)))
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
    params: List[Any] = list(clean.values())
    set_clause = ", ".join(f"{col} = ${i+1}" for i, col in enumerate(clean.keys()))
    params.append(message_id)
    async with get_db() as conn:
        result = await conn.execute(
            f"UPDATE messages SET {set_clause} WHERE id = ${len(params)}", *params
        )
    return _rows_affected(result) > 0


async def get_messages(lead_id: int) -> List[Dict[str, Any]]:
    async with get_db() as conn:
        rows = await conn.fetch(
            "SELECT * FROM messages WHERE lead_id = $1 ORDER BY sequence_step, created_at",
            lead_id,
        )
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Replies (new table)
# ─────────────────────────────────────────────────────────────────────────────

async def create_reply(data: Dict[str, Any]) -> int:
    _writable = frozenset({"lead_id", "message_id", "reply_text", "detected_intent", "raw_email_data"})
    clean = {k: v for k, v in data.items() if k in _writable and v is not None}
    cols         = ", ".join(clean.keys())
    placeholders = ", ".join(f"${i+1}" for i in range(len(clean)))
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


# ─────────────────────────────────────────────────────────────────────────────
# Campaigns (new table)
# ─────────────────────────────────────────────────────────────────────────────

_CAMPAIGN_WRITABLE = frozenset({
    "niche", "city", "country", "sources", "channel", "daily_cap",
    "leads_found", "leads_sent", "leads_replied", "status", "completed_at",
})


async def create_campaign(data: Dict[str, Any]) -> int:
    clean = {k: v for k, v in data.items() if k in _CAMPAIGN_WRITABLE and v is not None}
    cols         = ", ".join(clean.keys())
    placeholders = ", ".join(f"${i+1}" for i in range(len(clean)))
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
    params: List[Any] = list(clean.values())
    set_clause = ", ".join(f"{col} = ${i+1}" for i, col in enumerate(clean.keys()))
    params.append(campaign_id)
    async with get_db() as conn:
        result = await conn.execute(
            f"UPDATE campaigns SET {set_clause} WHERE id = ${len(params)}", *params
        )
    return _rows_affected(result) > 0


async def get_campaign(campaign_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM campaigns WHERE id = $1", campaign_id
        )
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

def _rows_affected(pg_status: str) -> int:
    """Parse the integer row count from asyncpg execute() status string, e.g. 'UPDATE 3'."""
    try:
        return int(pg_status.split()[-1])
    except (IndexError, ValueError):
        return 0
