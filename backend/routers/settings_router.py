from pathlib import Path
from fastapi import APIRouter, HTTPException
from .. import database as db
from .. import email_sender
from ..models import SettingsUpdate
from ..config import get_settings as _get_cfg

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_settings():
    return await db.get_all_settings()


@router.put("")
async def update_setting(payload: SettingsUpdate):
    await db.upsert_setting(payload.key, payload.value)
    return {"key": payload.key, "value": payload.value}


@router.put("/bulk")
async def bulk_update(payload: dict):
    for key, value in payload.items():
        await db.upsert_setting(str(key), str(value))

    # Reschedule daily job live if the user changed the hour
    if "schedule_hour" in payload:
        try:
            from ..scheduler import reschedule_job
            reschedule_job(int(payload["schedule_hour"]))
        except Exception:
            pass  # Scheduler may not be running during tests — non-fatal

    return {"updated": len(payload)}


@router.post("/test-smtp")
async def test_smtp():
    """Test SMTP connection using current settings (DB values, then .env fallback)."""
    return await email_sender.test_connection()


@router.post("/reset-stats")
async def reset_stats():
    """Clear all campaign_log and campaign_runs records."""
    async with db.get_db() as conn:
        await conn.execute("DELETE FROM campaign_log")
        await conn.execute("DELETE FROM campaign_runs")
        await conn.commit()
    return {"cleared": True}


@router.get("/dna")
async def get_dna():
    """Read company_dna.txt — injected into every AI prompt."""
    path    = Path(_get_cfg().company_dna_path)
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return {"content": content}


@router.put("/dna")
async def save_dna(payload: dict):
    """Overwrite company_dna.txt with new content."""
    content = str(payload.get("content", ""))
    path    = Path(_get_cfg().company_dna_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except Exception as exc:
        raise HTTPException(500, f"Failed to save DNA: {exc}")
    return {"saved": True, "length": len(content)}
