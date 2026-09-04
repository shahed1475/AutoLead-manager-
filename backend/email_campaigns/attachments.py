"""
attachments.py — secure per-campaign attachment storage.

One optional file per campaign. Stored under a backend-managed directory
(never a user-supplied path). The absolute path lives only in the DB / backend
and is NEVER returned to the frontend or logged. Filenames are sanitized;
path traversal is impossible because the stored file is always
`<store>/<campaign_id>/attachment<ext>` — the original name is metadata only.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Optional

from ..config import get_settings

_ALLOWED_EXT = {".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg", ".txt", ".csv", ".xlsx"}
_MAX_BYTES = 15 * 1024 * 1024  # 15 MB


def _store_root() -> Path:
    root = Path(get_settings().database_path).resolve().parent / "email_campaign_uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def sanitize_filename(name: Optional[str]) -> str:
    base = os.path.basename(name or "")            # strip any directory component
    base = base.replace("\x00", "")
    base = re.sub(r"[^A-Za-z0-9._\- ]+", "_", base).strip() or "attachment"
    return base[:120]


class AttachmentError(ValueError):
    pass


def save_campaign_attachment(campaign_id: int, filename: str, data: bytes) -> Dict[str, object]:
    """Persist the file. Returns DB metadata (incl. the backend-only `path`)."""
    if not data:
        raise AttachmentError("attachment is empty")
    if len(data) > _MAX_BYTES:
        raise AttachmentError(f"attachment exceeds {_MAX_BYTES // (1024 * 1024)} MB")

    safe_name = sanitize_filename(filename)
    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in _ALLOWED_EXT:
        raise AttachmentError(f"attachment type {ext or '(none)'} not allowed")

    camp_dir = (_store_root() / str(int(campaign_id))).resolve()
    # defense in depth — camp_dir must stay inside the store root
    if _store_root().resolve() not in camp_dir.parents and camp_dir != _store_root().resolve() / str(int(campaign_id)):
        raise AttachmentError("resolved storage path escaped the store root")
    camp_dir.mkdir(parents=True, exist_ok=True)

    dest = camp_dir / f"attachment{ext}"
    with open(dest, "wb") as fh:
        fh.write(data)

    mime = {
        ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg", ".txt": "text/plain", ".csv": "text/csv",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(ext, "application/octet-stream")

    return {
        "attachment_filename": safe_name,
        "attachment_path": str(dest),   # backend-only — never surface to the frontend
        "attachment_size": len(data),
        "attachment_mime": mime,
    }


def read_campaign_attachment(campaign: Dict[str, object]) -> Optional[tuple[str, bytes, str]]:
    """(filename, bytes, mime) for the sender, or None if the campaign has no attachment."""
    path = campaign.get("attachment_path")
    if not path:
        return None
    p = Path(str(path)).resolve()
    if _store_root().resolve() not in p.parents:
        raise AttachmentError("stored attachment path is outside the store root")
    if not p.is_file():
        raise AttachmentError("stored attachment file is missing")
    return (
        str(campaign.get("attachment_filename") or p.name),
        p.read_bytes(),
        str(campaign.get("attachment_mime") or "application/octet-stream"),
    )


def public_attachment_meta(campaign: Dict[str, object]) -> Optional[Dict[str, object]]:
    """Frontend-safe metadata — NO path."""
    if not campaign.get("attachment_filename"):
        return None
    return {
        "filename": campaign.get("attachment_filename"),
        "size": campaign.get("attachment_size"),
        "mime": campaign.get("attachment_mime"),
        "present": True,
    }
