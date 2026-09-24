import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException
from .. import database as db, edition
from .. import email_sender
from ..models import CompanyDnaUpdate, SettingsBulkUpdate, SettingsUpdate
from ..config import get_settings as _get_cfg

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Keys whose values must never be returned in a GET response — SMTP/IMAP
# passwords and cloud LLM API keys. Matched by suffix so new provider keys
# (e.g. "openai_api_key") are covered automatically.
_SECRET_KEY_SUFFIXES = ("_password", "_api_key", "_secret", "_token")
_MASKED = "••••set••••"


def _redact_settings(raw: dict) -> dict:
    return {
        k: (_MASKED if (v and k.lower().endswith(_SECRET_KEY_SUFFIXES)) else v)
        for k, v in raw.items()
    }


def _is_secret_key(key: str) -> bool:
    return key.lower().endswith(_SECRET_KEY_SUFFIXES)


def _should_skip_secret_write(key: str, value: str) -> bool:
    """GET redacts secret values to _MASKED. The Settings form loads that mask
    into its state and a later save sends it straight back — which would
    overwrite the real secret with the placeholder. Never persist the mask (or
    an empty string) for a secret key; keep whatever is already stored."""
    return _is_secret_key(key) and (value == _MASKED or value == "")


@router.get("")
async def get_settings():
    try:
        raw = await db.get_all_settings()
    except Exception as exc:
        logger.error("Failed to read settings: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to read settings — see server logs")
    return _redact_settings(raw)


@router.put("")
async def update_setting(payload: SettingsUpdate):
    if edition.setting_locked(payload.key):
        raise HTTPException(403, "This setting is managed by the workspace owner.")
    if payload.key == "research_handoff_mode" and str(payload.value).strip().lower() not in ("manual", "automatic"):
        raise HTTPException(422, "research_handoff_mode must be 'manual' or 'automatic'")
    if _should_skip_secret_write(payload.key, payload.value):
        return {"key": payload.key, "value": _MASKED, "skipped": "unchanged secret"}
    try:
        await db.upsert_setting(payload.key, payload.value)
    except Exception as exc:
        logger.error("Failed to save setting %s: %s", payload.key, exc, exc_info=True)
        raise HTTPException(500, "Failed to save setting — see server logs")
    return {"key": payload.key, "value": payload.value}


@router.put("/bulk")
async def bulk_update(payload: SettingsBulkUpdate):
    items = payload.model_dump()
    saved = 0
    skipped = 0
    try:
        for key, value in items.items():
            if edition.setting_locked(str(key)):
                skipped += 1
                continue
            if _should_skip_secret_write(str(key), str(value)):
                skipped += 1
                continue
            await db.upsert_setting(str(key), str(value))
            saved += 1
    except Exception as exc:
        logger.error("Bulk settings update failed after %d/%d keys: %s", saved, len(items), exc, exc_info=True)
        raise HTTPException(500, f"Failed to save settings ({saved}/{len(items)} saved before the error) — see server logs")

    # Reschedule daily job live if the user changed the hour
    if "schedule_hour" in items:
        try:
            from ..scheduler import reschedule_job
            reschedule_job(int(items["schedule_hour"]))
        except Exception as exc:
            logger.warning("Failed to reschedule daily job: %s", exc)  # non-fatal — scheduler may not be running

    return {"updated": saved, "skipped_unchanged_secrets": skipped}


@router.post("/test-smtp")
async def test_smtp():
    """Test SMTP connection using current settings (DB values, then .env fallback)."""
    try:
        return await email_sender.test_connection()
    except Exception as exc:
        logger.error("SMTP test failed: %s", exc, exc_info=True)
        raise HTTPException(500, "SMTP test failed — see server logs")


@router.post("/reset-stats")
async def reset_stats():
    """Clear all campaign_log and campaign_runs records."""
    try:
        async with db.transaction() as tx:
            await tx.execute("DELETE FROM campaign_log")
            await tx.execute("DELETE FROM campaign_runs")
    except Exception as exc:
        logger.error("Failed to reset stats: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to reset stats — see server logs")
    return {"cleared": True}


@router.get("/dna")
async def get_dna():
    """Read company_dna.txt — injected into every AI prompt."""
    path = Path(_get_cfg().company_dna_path)
    try:
        content = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError as exc:
        logger.error("Failed to read company DNA (%s): %s", path, exc, exc_info=True)
        raise HTTPException(500, "Failed to read company DNA — see server logs")
    return {"content": content}


@router.put("/dna")
async def save_dna(payload: CompanyDnaUpdate):
    """Overwrite company_dna.txt with new content."""
    content = payload.content
    path    = Path(_get_cfg().company_dna_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        logger.error("Failed to save company DNA (%s): %s", path, exc, exc_info=True)
        raise HTTPException(500, "Failed to save DNA — see server logs")
    return {"saved": True, "length": len(content)}
