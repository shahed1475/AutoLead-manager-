"""
routers/portal_admin.py — the owner's side of the client portal
(/api/clients/*, behind the owner's app password like every owner route).

Clients and their private workspaces (create, pause, resume, delete), the
portal's settings (sign-in email account, who can sign up, name, how many
workspaces), and publishing your latest commit to every client workspace.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import database as db
from ..email_campaigns import senders as sender_profiles
from ..portal import config as portal_config, service as portal_service, workspaces
from ..portal.service import PortalError

router = APIRouter(prefix="/api/clients", tags=["clients"])


class ClientUpdate(BaseModel):
    status: Optional[str] = None   # ACTIVE | BLOCKED
    name: Optional[str] = None
    company: Optional[str] = None


class ClientCreate(BaseModel):
    email: str
    name: Optional[str] = None
    company: Optional[str] = None
    invite: bool = True


class PortalSettings(BaseModel):
    sender: Optional[str] = None
    signup_mode: Optional[str] = None
    name: Optional[str] = None
    welcome: Optional[str] = None
    contact_email: Optional[str] = None
    max_workspaces: Optional[int] = None


class TestEmail(BaseModel):
    to: str


_INVITE_EMAIL = """Hi{greeting},

{name} has set up your own lead-generation dashboard. There you can find
and research leads and run your outreach — your data is private to you.

Open it here:  {link}

Sign in with this email address ({email}) — you'll get a 6-digit code by email.
"""


# ── Portal settings ──────────────────────────────────────────────────────────

async def _settings_view():
    cfg = await portal_config.get_config()
    profiles = [sender_profiles.public_profile(p) for p in await db.list_sender_profiles()]
    return {
        "settings": cfg,
        "sender": await portal_config.sender_status(cfg),
        "senders": [{k: p[k] for k in ("id", "name", "email_address", "provider", "status", "has_credentials")}
                    for p in profiles],
        "client_link": portal_config.client_link(),
    }


@router.get("/setup")
async def portal_setup():
    """For the sidebar/banner: can clients sign in, and how many are waiting."""
    waiting = await db.portal_fetchrow(
        """SELECT count(*) AS n FROM portal_clients c WHERE c.status = 'ACTIVE' AND c.last_login_at IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM portal_workspaces w WHERE w.client_id = c.id AND w.desired != 'DELETED')""")
    full = await workspaces.running_count() >= int((await portal_config.get_config())["max_workspaces"])
    return {"email_ready": (await portal_config.sender_status())["ready"],
            "waiting": int(waiting["n"]) if waiting and full else 0,
            "supervisor_running": workspaces.read_status().get("supervisor_running", False)}


@router.get("/portal-settings")
async def get_portal_settings():
    return await _settings_view()


@router.put("/portal-settings")
async def put_portal_settings(payload: PortalSettings):
    try:
        await portal_config.update_config(payload.model_dump(exclude_none=True))
    except portal_config.ConfigError as exc:
        raise HTTPException(422, str(exc))
    return await _settings_view()


@router.post("/portal-settings/test-email")
async def send_test_email(payload: TestEmail):
    """Send one test message from the sign-in account to an address you choose."""
    try:
        to = portal_service.normalize_email(payload.to)
    except PortalError as exc:
        raise HTTPException(422, str(exc))
    cfg = await portal_config.get_config()
    status = await portal_config.sender_status(cfg)
    if not status["ready"]:
        raise HTTPException(422, status["problem"] or "No email account is ready")
    ok = await portal_config.send_mail(
        to, f"{cfg['name']} client portal — test email",
        "This is a test from your client portal. Sign-in codes and invitations will come from this account.", cfg)
    if not ok:
        raise HTTPException(502, "The email could not be sent — check the account's password or reconnect it.")
    return {"ok": True, "to": to, "from": status["label"]}


# ── Clients ──────────────────────────────────────────────────────────────────

@router.post("", status_code=201)
async def add_client(payload: ClientCreate):
    """Add a client by email (they can then sign in even when the portal is
    invite-only) and, optionally, email them the link."""
    try:
        client = await portal_service.add_client(payload.email, payload.name, payload.company)
    except PortalError as exc:
        raise HTTPException(exc.status, str(exc))
    invited, invite_problem = False, None
    if payload.invite:
        cfg = await portal_config.get_config()
        link = portal_config.client_link()
        if not link:
            invite_problem = "No client link is open right now — start HOM with ./start.sh, then send the link yourself."
        elif not (await portal_config.sender_status(cfg))["ready"]:
            invite_problem = "No email account is ready to send the invitation."
        else:
            greeting = f" {client['name'].split()[0]}" if client.get("name") else ""
            invited = await portal_config.send_mail(
                client["email"], f"Your {cfg['name']} client portal",
                _INVITE_EMAIL.format(greeting=greeting, name=cfg["name"], link=link, email=client["email"]), cfg)
            if not invited:
                invite_problem = "The invitation email could not be sent."
    return {"client": client, "invited": invited, "invite_problem": invite_problem}


@router.get("")
async def list_clients():
    """Every client with their workspace's state (WAITLIST = signed up, no room yet)."""
    status = workspaces.read_status()
    full = await workspaces.running_count() >= int((await portal_config.get_config())["max_workspaces"])
    out = []
    for c in await db.portal_fetch("SELECT * FROM portal_clients ORDER BY id DESC"):
        ws = await workspaces.workspace_for_client(c["id"])
        waiting = full and ws is None and c["status"] == "ACTIVE" and c.get("last_login_at") is not None
        out.append({**c, "workspace": workspaces.view(ws, status, waitlisted=waiting)})
    return {"clients": out, "supervisor_running": status.get("supervisor_running", False)}


@router.patch("/{client_id}")
async def update_client(client_id: int, payload: ClientUpdate):
    if not await db.portal_fetchrow("SELECT id FROM portal_clients WHERE id = ?", client_id):
        raise HTTPException(404, "Client not found")
    if payload.status is not None:
        if payload.status not in ("ACTIVE", "BLOCKED"):
            raise HTTPException(422, "status must be ACTIVE or BLOCKED")
        await db.portal_execute("UPDATE portal_clients SET status = ? WHERE id = ?", payload.status, client_id)
        if payload.status == "BLOCKED":   # sign them out everywhere and pause their workspace
            await db.portal_execute("DELETE FROM portal_sessions WHERE client_id = ?", client_id)
            if await workspaces.workspace_for_client(client_id):
                await workspaces.set_desired(client_id, "STOPPED")
    if payload.name is not None:
        await db.portal_execute("UPDATE portal_clients SET name = ? WHERE id = ?",
                                payload.name.strip()[:120] or None, client_id)
    if payload.company is not None:
        await db.portal_execute("UPDATE portal_clients SET company = ? WHERE id = ?",
                                payload.company.strip()[:160] or None, client_id)
    return await db.portal_fetchrow("SELECT * FROM portal_clients WHERE id = ?", client_id)


# ── Workspaces ───────────────────────────────────────────────────────────────

class WorkspaceAction(BaseModel):
    action: str        # create | stop | start | delete
    confirm_email: Optional[str] = None


@router.post("/{client_id}/workspace")
async def workspace_action(client_id: int, payload: WorkspaceAction):
    client = await db.portal_fetchrow("SELECT * FROM portal_clients WHERE id = ?", client_id)
    if not client:
        raise HTTPException(404, "Client not found")
    try:
        if payload.action == "create":
            await workspaces.create(client_id)        # you decide: this may go over the limit
        elif payload.action in ("stop", "start"):
            await workspaces.set_desired(client_id, "STOPPED" if payload.action == "stop" else "RUNNING")
        elif payload.action == "delete":
            if (payload.confirm_email or "").strip().lower() != client["email"]:
                raise HTTPException(422, "Type the client's email to confirm deleting their workspace.")
            await workspaces.set_desired(client_id, "DELETED")
        else:
            raise HTTPException(422, "Unknown action")
    except workspaces.WorkspaceError as exc:
        raise HTTPException(exc.status, str(exc))
    return {"workspace": workspaces.view(await workspaces.workspace_for_client(client_id))}


# ── Publishing to clients ────────────────────────────────────────────────────

@router.get("/release")
async def release_info():
    """What version clients run, what your latest commit is, and publish progress."""
    status = workspaces.read_status()
    return {"supervisor_running": status.get("supervisor_running", False),
            "release": status.get("release"), "git": status.get("git"), "publish": status.get("publish")}


@router.post("/release/publish")
async def publish():
    """Ask the workspace service to build your latest COMMIT (never uncommitted
    changes) and move every client workspace to it."""
    status = workspaces.read_status()
    if not status.get("supervisor_running"):
        raise HTTPException(503, "The workspace service isn't running — start HOM with ./start.sh first.")
    if (status.get("publish") or {}).get("state") == "BUILDING":
        raise HTTPException(409, "A publish is already running.")
    await workspaces.write_control(publish_requested_at=workspaces._now_iso())
    return {"ok": True}
