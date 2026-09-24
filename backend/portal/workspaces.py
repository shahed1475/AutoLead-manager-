"""
workspaces.py — each client's private HOM workspace, from the owner's side.

The owner app decides WHAT should exist (table portal_workspaces) and writes it
to a control file; scripts/hom_supervisor.py, running on the host, makes it so
with Docker and reports back in .run/workspaces/status.json. The owner app
never touches Docker itself.

  owner app ── backend/data/workspaces.json ──▶ supervisor ──▶ docker (hom-ws-<id>)
  owner app ◀── .run/workspaces/status.json ── supervisor

Sign-in hand-off: the owner app signs a one-time link with the workspace's
secret (derived from the supervisor's master key); only that workspace can
verify it (see edition.py).
"""
from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import database as db, edition
from . import config as portal_config

ROOT = Path(__file__).resolve().parents[2]
CONTROL_FILE = Path(os.getenv("HOM_WORKSPACES_CONTROL", ROOT / "backend" / "data" / "workspaces.json"))
STATUS_FILE = Path(os.getenv("HOM_WORKSPACES_STATUS", ROOT / ".run" / "workspaces" / "status.json"))
KEY_FILE = Path(os.getenv("HOM_WORKSPACES_KEY", ROOT / ".run" / "workspace.key"))

PORT_MIN, PORT_MAX = 7001, 7099          # the client link only routes to this range
SUPERVISOR_STALE_S = 30
MAX_WORKSPACES_CAP = 20


class WorkspaceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Supervisor status ────────────────────────────────────────────────────────

def read_status() -> Dict[str, Any]:
    try:
        data = json.loads(STATUS_FILE.read_text())
    except (OSError, ValueError):
        data = {}
    hb = (data.get("supervisor") or {}).get("heartbeat") or 0
    data["supervisor_running"] = bool(hb) and (time.time() - float(hb) < SUPERVISOR_STALE_S)
    return data


def _ws_status(status: Dict[str, Any], ws_id: int) -> Dict[str, Any]:
    return (status.get("workspaces") or {}).get(str(ws_id)) or {}


# ── Control file ─────────────────────────────────────────────────────────────

async def write_control(publish_requested_at: Optional[str] = None) -> None:
    rows = await db.portal_fetch(
        """SELECT w.id, w.port, w.desired, c.email FROM portal_workspaces w
           JOIN portal_clients c ON c.id = w.client_id ORDER BY w.id""")
    try:
        previous = json.loads(CONTROL_FILE.read_text())
    except (OSError, ValueError):
        previous = {}
    stored = await db.get_all_settings()
    from ..config import get_settings
    control = {
        "version": 1,
        "written_at": _now_iso(),
        # workspaces use your local AI model through the relay (they can't change it)
        "ai_model": stored.get("ollama_model") or get_settings().ollama_model,
        "workspaces": [{"id": r["id"], "port": r["port"], "desired": r["desired"], "email": r["email"]}
                       for r in rows if r["desired"] in ("RUNNING", "STOPPED")],
        "deleted": [r["id"] for r in rows if r["desired"] == "DELETED"],
        "publish_requested_at": publish_requested_at or previous.get("publish_requested_at"),
    }
    CONTROL_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONTROL_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(control, indent=2))
    os.replace(tmp, CONTROL_FILE)
    try:
        os.chmod(CONTROL_FILE, 0o644)    # the supervisor (your user) reads it
    except OSError:
        pass


# ── Workspaces ───────────────────────────────────────────────────────────────

async def workspace_for_client(client_id: int) -> Optional[Dict[str, Any]]:
    return await db.portal_fetchrow(
        "SELECT * FROM portal_workspaces WHERE client_id = ? AND desired != 'DELETED' ORDER BY id DESC LIMIT 1",
        client_id)


async def running_count() -> int:
    row = await db.portal_fetchrow("SELECT count(*) AS n FROM portal_workspaces WHERE desired = 'RUNNING'")
    return int(row["n"]) if row else 0


async def _free_port() -> int:
    used = {r["port"] for r in await db.portal_fetch("SELECT port FROM portal_workspaces WHERE port IS NOT NULL")}
    for port in range(PORT_MIN, PORT_MAX + 1):
        if port not in used:
            return port
    raise WorkspaceError("No free workspace slots left.", 409)


async def create(client_id: int) -> Dict[str, Any]:
    existing = await workspace_for_client(client_id)
    if existing:
        return existing
    wid = await db.portal_insert(
        "INSERT INTO portal_workspaces (client_id, port, desired) VALUES (?, ?, 'RUNNING') RETURNING id",
        client_id, await _free_port())
    await write_control()
    return await db.portal_fetchrow("SELECT * FROM portal_workspaces WHERE id = ?", wid)


async def ensure_for_client(client: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Automatic workspaces: a signed-in client gets one if there is room
    (max_workspaces counts running ones). Otherwise they wait in line."""
    ws = await workspace_for_client(client["id"])
    if ws or client.get("status") == "BLOCKED":
        return ws
    cfg = await portal_config.get_config()
    if await running_count() >= int(cfg.get("max_workspaces", 5)):
        return None
    return await create(client["id"])


async def set_desired(client_id: int, desired: str) -> Dict[str, Any]:
    ws = await workspace_for_client(client_id)
    if not ws:
        raise WorkspaceError("This client has no workspace.", 404)
    port = None if desired == "DELETED" else ws["port"]
    await db.portal_execute(
        "UPDATE portal_workspaces SET desired = ?, port = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        desired, port, ws["id"])
    await write_control()
    return await db.portal_fetchrow("SELECT * FROM portal_workspaces WHERE id = ?", ws["id"])


def view(ws: Optional[Dict[str, Any]], status: Optional[Dict[str, Any]] = None,
         waitlisted: bool = False) -> Dict[str, Any]:
    """The workspace's state as the owner and the client see it:
    NONE | WAITLIST | CREATING | STARTING | RUNNING | STOPPING | STOPPED |
    WAITING_FOR_RELEASE | ERROR | OFFLINE (the workspace service isn't running)."""
    status = status if status is not None else read_status()
    if not ws:
        return {"state": "WAITLIST" if waitlisted else "NONE"}
    s = _ws_status(status, ws["id"])
    reported = s.get("state")
    if ws["desired"] == "STOPPED":
        state = "STOPPED" if reported in (None, "STOPPED") or not status.get("supervisor_running") else "STOPPING"
    elif not status.get("supervisor_running"):
        state = "OFFLINE"
    elif reported in ("RUNNING", "STARTING", "ERROR", "WAITING_FOR_RELEASE"):
        state = reported
    else:
        state = "CREATING"
    return {"id": ws["id"], "state": state, "desired": ws["desired"], "version": s.get("version"),
            "error": s.get("error") if state == "ERROR" else None, "created_at": ws.get("created_at")}


# ── Sign-in hand-off ─────────────────────────────────────────────────────────

def _master_key() -> bytes:
    try:
        key = KEY_FILE.read_bytes().strip()
    except OSError:
        key = b""
    if len(key) < 32:
        raise WorkspaceError("The workspace service isn't running — try again in a minute.", 503)
    return key


def handoff_token(ws: Dict[str, Any], email: str) -> str:
    secret = edition.workspace_secret(_master_key(), ws["id"])
    return edition.sign_handoff(secret, ws["id"], email, secrets.token_urlsafe(16))


def gateway_cookie_value(ws: Dict[str, Any]) -> str:
    return str(int(ws["port"]))
