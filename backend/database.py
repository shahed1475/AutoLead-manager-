import aiosqlite
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Optional, List, Dict, Any
from datetime import date
from .config import get_settings

settings = get_settings()


async def init_db() -> None:
    db_path = Path(settings.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(db_path) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS leads (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                business_name     TEXT NOT NULL,
                phone             TEXT,
                email             TEXT,
                website           TEXT,
                niche             TEXT,
                city              TEXT,
                ai_whatsapp_msg   TEXT,
                ai_email_subject  TEXT,
                ai_email_body     TEXT,
                ai_followup_msg   TEXT,
                status            TEXT DEFAULT 'PENDING'
                                    CHECK(status IN ('PENDING','SENT','REPLIED','SKIPPED')),
                channel           TEXT CHECK(channel IN ('EMAIL','WHATSAPP','BOTH')),
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sent_at           TIMESTAMP,
                followup_sent_at  TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_leads_status    ON leads(status);
            CREATE INDEX IF NOT EXISTS idx_leads_channel   ON leads(channel);
            CREATE INDEX IF NOT EXISTS idx_leads_niche     ON leads(niche);
            CREATE INDEX IF NOT EXISTS idx_leads_sent_at   ON leads(sent_at);
            CREATE INDEX IF NOT EXISTS idx_leads_email     ON leads(email) WHERE email IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_leads_phone     ON leads(phone) WHERE phone IS NOT NULL;

            CREATE TABLE IF NOT EXISTS app_settings (
                key         TEXT PRIMARY KEY,
                value       TEXT,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS campaign_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id     INTEGER REFERENCES leads(id) ON DELETE SET NULL,
                channel     TEXT,
                action      TEXT,
                success     INTEGER DEFAULT 0,
                error_msg   TEXT,
                timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_log_lead_id   ON campaign_log(lead_id);
            CREATE INDEX IF NOT EXISTS idx_log_timestamp ON campaign_log(timestamp);

            CREATE TABLE IF NOT EXISTS campaign_runs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                niche        TEXT,
                city         TEXT,
                channel      TEXT,
                daily_cap    INTEGER DEFAULT 20,
                leads_found  INTEGER DEFAULT 0,
                leads_sent   INTEGER DEFAULT 0,
                started_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at  TIMESTAMP,
                status       TEXT DEFAULT 'RUNNING'
                               CHECK(status IN ('RUNNING','COMPLETED','STOPPED','FAILED'))
            );
            CREATE INDEX IF NOT EXISTS idx_runs_started ON campaign_runs(started_at);
        """)
        await db.commit()

        # ── Idempotent schema migrations ──────────────────────────────────────
        for _migration_sql in [
            "ALTER TABLE leads ADD COLUMN source TEXT DEFAULT 'GOOGLE_MAPS'",
            "ALTER TABLE leads ADD COLUMN address TEXT",
            "ALTER TABLE campaign_runs ADD COLUMN sources TEXT DEFAULT 'GOOGLE_MAPS'",
        ]:
            try:
                await db.execute(_migration_sql)
                await db.commit()
            except Exception:
                pass   # column already exists — safe to ignore


@asynccontextmanager
async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    db_path = Path(settings.database_path)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        yield db


# ── Stats ─────────────────────────────────────────────────────────────────────

async def get_dashboard_stats() -> Dict[str, Any]:
    async with get_db() as db:
        counts_row = await db.execute_fetchall("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status='PENDING'  THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN status='SENT'     THEN 1 ELSE 0 END) AS sent,
                SUM(CASE WHEN status='REPLIED'  THEN 1 ELSE 0 END) AS replied,
                SUM(CASE WHEN status='SKIPPED'  THEN 1 ELSE 0 END) AS skipped
            FROM leads
        """)
        row     = counts_row[0]
        total   = row["total"]   or 0
        replied = row["replied"] or 0

        today     = date.today().isoformat()
        today_rows = await db.execute_fetchall("""
            SELECT channel, COUNT(*) AS cnt
            FROM campaign_log
            WHERE success=1 AND action='SEND' AND DATE(timestamp)=?
            GROUP BY channel
        """, (today,))
        sent_by_channel = {r["channel"]: r["cnt"] for r in today_rows}

        # avg_deal_value drives estimated_revenue (configurable in settings)
        deal_row = await db.execute_fetchall(
            "SELECT value FROM app_settings WHERE key='avg_deal_value'"
        )
        avg_deal = float(deal_row[0]["value"]) if deal_row else 500.0

    # BOTH channel logs as "BOTH" — count it in both email and WA totals
    email_today = sent_by_channel.get("EMAIL", 0) + sent_by_channel.get("BOTH", 0)
    wa_today    = sent_by_channel.get("WHATSAPP", 0) + sent_by_channel.get("BOTH", 0)
    sent_today  = sent_by_channel.get("EMAIL", 0) + sent_by_channel.get("WHATSAPP", 0) + sent_by_channel.get("BOTH", 0)

    return {
        "total_leads":         total,
        "pending":             row["pending"] or 0,
        "sent":                row["sent"]    or 0,
        "replied":             replied,
        "skipped":             row["skipped"] or 0,
        "email_sent_today":    email_today,
        "whatsapp_sent_today": wa_today,
        "sent_today":          sent_today,
        "reply_rate":          round(replied / total * 100, 1) if total > 0 else 0.0,
        "estimated_revenue":   round(replied * avg_deal, 2),
    }


# ── Leads ─────────────────────────────────────────────────────────────────────

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
) -> Dict[str, Any]:
    _SORTABLE    = {"business_name", "created_at", "sent_at", "status", "niche", "city"}
    _DATE_FIELDS = {"created_at", "sent_at"}

    if sort_by not in _SORTABLE:
        sort_by = "created_at"
    order = "DESC" if sort_dir.lower() == "desc" else "ASC"
    if date_field not in _DATE_FIELDS:
        date_field = "created_at"

    conditions: List[str] = []
    params: List[Any] = []

    if status:
        conditions.append("status = ?")
        params.append(status)
    if channel:
        conditions.append("channel = ?")
        params.append(channel)
    if niche:
        conditions.append("niche LIKE ?")
        params.append(f"%{niche}%")
    if city:
        conditions.append("city LIKE ?")
        params.append(f"%{city}%")
    if search:
        conditions.append("(business_name LIKE ? OR email LIKE ? OR phone LIKE ?)")
        term = f"%{search}%"
        params.extend([term, term, term])
    if date_from:
        conditions.append(f"DATE({date_field}) >= ?")
        params.append(date_from)
    if date_to:
        conditions.append(f"DATE({date_field}) <= ?")
        params.append(date_to)

    where  = "WHERE " + " AND ".join(conditions) if conditions else ""
    offset = (page - 1) * page_size

    async with get_db() as db:
        total_row = await db.execute_fetchall(
            f"SELECT COUNT(*) AS cnt FROM leads {where}", params
        )
        total = total_row[0]["cnt"]

        rows = await db.execute_fetchall(
            f"SELECT * FROM leads {where} ORDER BY {sort_by} {order} LIMIT ? OFFSET ?",
            params + [page_size, offset],
        )

    return {
        "items":       [dict(r) for r in rows],
        "total":       total,
        "page":        page,
        "page_size":   page_size,
        "total_pages": max(1, -(-total // page_size)),
    }


async def get_lead_by_id(lead_id: int) -> Optional[Dict[str, Any]]:
    async with get_db() as db:
        rows = await db.execute_fetchall(
            "SELECT * FROM leads WHERE id = ?", (lead_id,)
        )
    return dict(rows[0]) if rows else None


async def find_duplicate_lead(
    email: Optional[str], phone: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Return an existing lead matching email or phone, to prevent duplicates."""
    conditions: List[str] = []
    params: List[str] = []
    if email:
        conditions.append("email = ?")
        params.append(email)
    if phone:
        conditions.append("phone = ?")
        params.append(phone)
    if not conditions:
        return None
    async with get_db() as db:
        rows = await db.execute_fetchall(
            f"SELECT * FROM leads WHERE ({' OR '.join(conditions)}) LIMIT 1", params
        )
    return dict(rows[0]) if rows else None


async def create_lead(data: Dict[str, Any]) -> int:
    fields       = [k for k in data if data[k] is not None]
    placeholders = ", ".join("?" * len(fields))
    cols         = ", ".join(fields)
    values       = [data[f] for f in fields]

    async with get_db() as db:
        cursor = await db.execute(
            f"INSERT INTO leads ({cols}) VALUES ({placeholders})", values
        )
        await db.commit()
        return cursor.lastrowid


async def create_lead_deduped(data: Dict[str, Any]) -> tuple[int, bool]:
    """Create a lead only if email/phone not already in DB. Returns (id, is_new)."""
    existing = await find_duplicate_lead(data.get("email"), data.get("phone"))
    if existing:
        return existing["id"], False
    lead_id = await create_lead(data)
    return lead_id, True


async def update_lead(lead_id: int, data: Dict[str, Any]) -> bool:
    data = {k: v for k, v in data.items() if v is not None}
    if not data:
        return False
    assignments = ", ".join(f"{k} = ?" for k in data)
    values      = list(data.values()) + [lead_id]
    async with get_db() as db:
        cursor = await db.execute(
            f"UPDATE leads SET {assignments} WHERE id = ?", values
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_lead(lead_id: int) -> bool:
    async with get_db() as db:
        cursor = await db.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
        await db.commit()
        return cursor.rowcount > 0


async def get_leads_due_for_followup(days: int = 3) -> List[Dict[str, Any]]:
    """Leads that are SENT, have no follow-up yet, and were sent ≥ `days` days ago."""
    async with get_db() as db:
        rows = await db.execute_fetchall(
            """SELECT * FROM leads
               WHERE status = 'SENT'
                 AND followup_sent_at IS NULL
                 AND sent_at IS NOT NULL
                 AND DATE(sent_at) <= DATE('now', ? || ' days')
               LIMIT 50""",
            (f"-{days}",),
        )
    return [dict(r) for r in rows]


# ── Settings ──────────────────────────────────────────────────────────────────

async def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    async with get_db() as db:
        rows = await db.execute_fetchall(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        )
    return rows[0]["value"] if rows else default


async def upsert_setting(key: str, value: str) -> None:
    async with get_db() as db:
        await db.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP""",
            (key, value),
        )
        await db.commit()


async def get_all_settings() -> Dict[str, str]:
    async with get_db() as db:
        rows = await db.execute_fetchall("SELECT key, value FROM app_settings")
    return {r["key"]: r["value"] for r in rows}


# ── Logging ───────────────────────────────────────────────────────────────────

async def log_campaign_action(
    lead_id: Optional[int],   # None for system/scraper progress messages
    channel: str,
    action: str,
    success: bool,
    error_msg: Optional[str] = None,
) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT INTO campaign_log (lead_id, channel, action, success, error_msg) VALUES (?,?,?,?,?)",
            (lead_id, channel, action, int(success), error_msg),
        )
        await db.commit()


async def get_recent_logs(limit: int = 20) -> List[Dict[str, Any]]:
    async with get_db() as db:
        rows = await db.execute_fetchall("""
            SELECT
                cl.id,
                STRFTIME('%Y-%m-%dT%H:%M:%S', cl.timestamp) AS timestamp,
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
            LIMIT ?
        """, (limit,))
    return [dict(r) for r in rows]


async def get_weekly_activity() -> List[Dict[str, Any]]:
    """7-day outreach activity: leads found + messages sent per day."""
    async with get_db() as db:
        rows = await db.execute_fetchall("""
            WITH RECURSIVE dates(day) AS (
                SELECT DATE('now', '-6 days')
                UNION ALL
                SELECT DATE(day, '+1 day') FROM dates WHERE day < DATE('now')
            )
            SELECT
                dates.day,
                STRFTIME('%a', dates.day) AS day_label,
                (SELECT COUNT(*) FROM leads
                 WHERE DATE(created_at) = dates.day)                                    AS leads_created,
                COALESCE(SUM(
                    CASE WHEN cl.action='SEND' AND cl.success=1
                         AND cl.channel IN ('EMAIL','BOTH')
                         THEN 1 ELSE 0 END), 0)                                         AS email_sent,
                COALESCE(SUM(
                    CASE WHEN cl.action='SEND' AND cl.success=1
                         AND cl.channel IN ('WHATSAPP','BOTH')
                         THEN 1 ELSE 0 END), 0)                                         AS whatsapp_sent,
                COALESCE(SUM(
                    CASE WHEN cl.action='SEND' AND cl.success=1
                         THEN 1 ELSE 0 END), 0)                                         AS total_sent
            FROM dates
            LEFT JOIN campaign_log cl ON DATE(cl.timestamp) = dates.day
            GROUP BY dates.day
            ORDER BY dates.day ASC
        """)
    return [dict(r) for r in rows]


# ── Campaign runs ─────────────────────────────────────────────────────────────

async def mark_lead_replied(lead_id: int) -> bool:
    async with get_db() as db:
        cursor = await db.execute(
            "UPDATE leads SET status='REPLIED' WHERE id=?", (lead_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


async def create_campaign_run(
    niche: str, city: str, channel: str, daily_cap: int,
    sources: str = "GOOGLE_MAPS",
) -> int:
    async with get_db() as db:
        cursor = await db.execute(
            "INSERT INTO campaign_runs (niche, city, channel, daily_cap, sources) VALUES (?,?,?,?,?)",
            (niche, city, channel, daily_cap, sources),
        )
        await db.commit()
        return cursor.lastrowid


async def update_campaign_run(run_id: int, data: Dict[str, Any]) -> bool:
    if not data:
        return False
    assignments = ", ".join(f"{k} = ?" for k in data)
    values      = list(data.values()) + [run_id]
    async with get_db() as db:
        cursor = await db.execute(
            f"UPDATE campaign_runs SET {assignments} WHERE id = ?", values
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_campaign_history(limit: int = 10) -> List[Dict[str, Any]]:
    async with get_db() as db:
        rows = await db.execute_fetchall(
            """SELECT id, niche, city, channel, daily_cap, leads_found, leads_sent,
                      STRFTIME('%Y-%m-%dT%H:%M:%S', started_at)  AS started_at,
                      STRFTIME('%Y-%m-%dT%H:%M:%S', finished_at) AS finished_at,
                      status
               FROM campaign_runs
               ORDER BY started_at DESC
               LIMIT ?""",
            (limit,),
        )
    return [dict(r) for r in rows]
