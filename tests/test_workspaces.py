"""Client workspaces: automatic creation, capacity, entering (signed hand-off),
isolation between clients, the owner's controls and publishing, the client
edition's locks and outbound guard, and the host supervisor's reconcile loop."""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend import auth, database as db, edition, email_sender
from backend.portal import workspaces

pytestmark = pytest.mark.asyncio
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def outbox(monkeypatch):
    sent = []

    async def ready():
        return True

    async def fake_send(to, subject, body, reply_to=None):
        sent.append({"to": to, "subject": subject})
        return True
    monkeypatch.setattr(email_sender, "system_email_ready", ready)
    monkeypatch.setattr(email_sender, "send_system_email", fake_send)
    return sent


async def _client():
    from backend.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _sign_in(c, outbox, email, onboard=True):
    await db.portal_execute("UPDATE portal_login_codes SET created_at = datetime(created_at, '-2 minutes') WHERE email = ?", email)
    r = await c.post("/api/portal/auth/signup", json={"email": email, "password": "correct horse battery", "name": "Test"})
    assert r.status_code == 200, r.text
    code = outbox[-1]["subject"].rsplit(" ", 1)[-1]
    r = await c.post("/api/portal/auth/verify", json={"email": email, "code": code})
    assert r.status_code == 200, r.text
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    if onboard:
        r2 = await c.put("/api/portal/me/onboarding", headers=h, json={
            "name": "Test", "sector": "Testing", "company_dna": " ".join(["word"] * 25)})
        assert r2.status_code == 200, r2.text
    return h, r.json()["client"]


def _supervisor_says(states: dict, running=True):
    """Pretend the host supervisor reported these workspace states."""
    workspaces.STATUS_FILE.write_text(json.dumps({
        "supervisor": {"heartbeat": time.time() if running else time.time() - 999},
        "workspaces": {str(k): {"state": v, "version": "abc1234"} for k, v in states.items()},
    }))


def _control():
    return json.loads(workspaces.CONTROL_FILE.read_text())


# ── Automatic workspaces ────────────────────────────────────────────────

async def test_first_sign_in_creates_a_private_workspace(clean_db, outbox):
    async with await _client() as c:
        h, client = await _sign_in(c, outbox, "a@gmail.com")
        assert (await c.get("/api/portal/workspace", headers=h)).json()["state"] == "OFFLINE"   # service not running
        ws = await workspaces.workspace_for_client(client["id"])
        _supervisor_says({ws["id"]: "RUNNING"})
        assert (await c.get("/api/portal/workspace", headers=h)).json()["state"] == "RUNNING"
    assert ws["port"] == 7001 and ws["desired"] == "RUNNING"
    ctl = _control()
    entry = {k: v for k, v in ctl["workspaces"][0].items() if k != "company_dna"}
    assert entry == {"id": ws["id"], "port": 7001, "desired": "RUNNING", "email": "a@gmail.com"}
    assert ctl["deleted"] == []


async def test_capacity_waitlist_and_room_opening_up(clean_db, outbox):
    async with await _client() as c:
        await c.put("/api/clients/portal-settings", json={"max_workspaces": 1})
        _, a = await _sign_in(c, outbox, "a@gmail.com")
        hb, b = await _sign_in(c, outbox, "b@gmail.com")
        assert await workspaces.workspace_for_client(b["id"]) is None
        _supervisor_says({})
        assert (await c.get("/api/portal/workspace", headers=hb)).json()["state"] == "WAITLIST"
        listing = (await c.get("/api/clients")).json()["clients"]
        assert {x["email"]: x["workspace"]["state"] for x in listing}["b@gmail.com"] == "WAITLIST"
        assert (await c.get("/api/clients/setup")).json()["waiting"] == 1
        # the owner deletes A's workspace -> B gets the place (and the port) on their next check
        r = await c.post(f"/api/clients/{a['id']}/workspace", json={"action": "delete", "confirm_email": "a@gmail.com"})
        assert r.status_code == 200
        await c.get("/api/portal/workspace", headers=hb)
        ws_b = await workspaces.workspace_for_client(b["id"])
    assert ws_b and ws_b["port"] == 7001
    ctl = _control()
    assert [w["email"] for w in ctl["workspaces"]] == ["b@gmail.com"] and len(ctl["deleted"]) == 1


async def test_zero_means_never_automatic_but_owner_can_create(clean_db, outbox):
    async with await _client() as c:
        await c.put("/api/clients/portal-settings", json={"max_workspaces": 0})
        _, a = await _sign_in(c, outbox, "a@gmail.com")
        assert await workspaces.workspace_for_client(a["id"]) is None
        r = await c.post(f"/api/clients/{a['id']}/workspace", json={"action": "create"})
    assert r.status_code == 200 and await workspaces.workspace_for_client(a["id"])


# ── Entering a workspace ───────────────────────────────────────────────

async def test_enter_gives_a_one_time_link_only_for_your_own_workspace(clean_db, outbox):
    workspaces.KEY_FILE.write_text("k" * 64)
    async with await _client() as c:
        ha, a = await _sign_in(c, outbox, "a@gmail.com")
        hb, b = await _sign_in(c, outbox, "b@gmail.com")
        ws_a = await workspaces.workspace_for_client(a["id"])
        ws_b = await workspaces.workspace_for_client(b["id"])
        _supervisor_says({ws_a["id"]: "RUNNING", ws_b["id"]: "STARTING"})
        assert (await c.post("/api/portal/workspace/enter", headers=hb)).status_code == 409    # not ready yet
        assert (await c.post("/api/portal/workspace/enter")).status_code == 401                # not signed in
        r = await c.post("/api/portal/workspace/enter", headers=ha)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "hom_ws=7001" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    token = r.json()["url"].split("handoff=", 1)[1]
    secret_a = edition.workspace_secret(b"k" * 64, ws_a["id"])
    secret_b = edition.workspace_secret(b"k" * 64, ws_b["id"])
    with pytest.raises(edition.HandoffError):          # useless in anyone else's workspace
        edition.verify_handoff(token, secret_b, ws_b["id"])
    with pytest.raises(edition.HandoffError):
        edition.verify_handoff(token, secret_a, ws_b["id"])
    assert edition.verify_handoff(token, secret_a, ws_a["id"])["e"] == "a@gmail.com"
    with pytest.raises(edition.HandoffError, match="already used"):
        edition.verify_handoff(token, secret_a, ws_a["id"])


async def test_enter_without_the_service_key_is_refused(clean_db, outbox):
    async with await _client() as c:
        h, a = await _sign_in(c, outbox, "a@gmail.com")
        ws = await workspaces.workspace_for_client(a["id"])
        _supervisor_says({ws["id"]: "RUNNING"})
        assert (await c.post("/api/portal/workspace/enter", headers=h)).status_code == 503


async def test_sign_out_clears_the_routing_cookie(clean_db, outbox):
    async with await _client() as c:
        h, _ = await _sign_in(c, outbox, "a@gmail.com")
        r = await c.post("/api/portal/auth/sign-out", headers=h)
    assert 'hom_ws=""' in r.headers["set-cookie"] or "hom_ws=;" in r.headers["set-cookie"]


# ── Owner controls ─────────────────────────────────────────────────────

async def test_pause_resume_delete_and_block(clean_db, outbox):
    async with await _client() as c:
        _, a = await _sign_in(c, outbox, "a@gmail.com")
        url = f"/api/clients/{a['id']}/workspace"
        assert (await c.post(url, json={"action": "stop"})).json()["workspace"]["state"] == "STOPPED"
        assert _control()["workspaces"][0]["desired"] == "STOPPED"
        await c.post(url, json={"action": "start"})
        assert _control()["workspaces"][0]["desired"] == "RUNNING"
        assert (await c.post(url, json={"action": "delete", "confirm_email": "wrong@x.com"})).status_code == 422
        assert (await c.post(url, json={"action": "explode"})).status_code == 422
        # blocking pauses the workspace
        await c.patch(f"/api/clients/{a['id']}", json={"status": "BLOCKED"})
        assert _control()["workspaces"][0]["desired"] == "STOPPED"


async def test_publish_needs_the_service_and_records_the_request(clean_db):
    async with await _client() as c:
        assert (await c.post("/api/clients/release/publish")).status_code == 503
        _supervisor_says({})
        assert (await c.post("/api/clients/release/publish")).status_code == 200
        assert _control()["publish_requested_at"]
        status = json.loads(workspaces.STATUS_FILE.read_text())
        status["publish"] = {"state": "BUILDING"}
        workspaces.STATUS_FILE.write_text(json.dumps(status))
        assert (await c.post("/api/clients/release/publish")).status_code == 409


# ── Client edition ─────────────────────────────────────────────────────

@pytest.fixture
def client_edition(monkeypatch):
    monkeypatch.setenv("HOM_EDITION", "client")
    monkeypatch.setenv("HOM_WORKSPACE_ID", "5")
    monkeypatch.setenv("HOM_HANDOFF_SECRET", "s3cret")
    auth._sessions.clear()
    yield
    auth._sessions.clear()


async def test_client_edition_signs_in_only_by_handoff(clean_db, client_edition):
    async with await _client() as c:
        assert (await c.get("/api/auth/status")).json()["edition"] == "client"
        assert (await c.get("/api/leads")).status_code == 401            # never open, even without a password
        assert (await c.post("/api/auth/set-password", json={"password": "hijack-attempt-1"})).status_code == 403
        assert (await c.post("/api/auth/unlock", json={"password": "x"})).status_code == 403
        good = edition.sign_handoff("s3cret", 5, "a@gmail.com", "n-1")
        other_ws = edition.sign_handoff("s3cret", 6, "a@gmail.com", "n-2")
        forged = edition.sign_handoff("guess", 5, "a@gmail.com", "n-3")
        expired = edition.sign_handoff("s3cret", 5, "a@gmail.com", "n-4", now=time.time() - 3600)
        for bad in (other_ws, forged, expired, "junk"):
            assert (await c.post("/api/auth/handoff", json={"token": bad})).status_code == 401
        r = await c.post("/api/auth/handoff", json={"token": good})
        assert r.status_code == 200
        token = r.json()["token"]
        assert (await c.get("/api/leads", headers={"Authorization": f"Bearer {token}"})).status_code == 200
        assert (await c.post("/api/auth/handoff", json={"token": good})).status_code == 401   # one time only


async def test_client_edition_locks_owner_settings(clean_db, client_edition):
    edition_token = auth.issue_session()
    h = {"Authorization": f"Bearer {edition_token}"}
    async with await _client() as c:
        assert (await c.put("/api/settings", headers=h, json={"key": "ollama_model", "value": "huge:70b"})).status_code == 403
        assert (await c.put("/api/settings", headers=h, json={"key": "daily_cap", "value": "20"})).status_code == 200
        r = await c.put("/api/settings/bulk", headers=h, json={"ollama_base_url": "http://10.0.0.5:11434", "dedup_days": "9"})
        assert r.status_code == 200
    stored = await db.get_all_settings()
    assert stored.get("dedup_days") == "9" and stored.get("ollama_base_url") != "http://10.0.0.5:11434"


async def test_client_edition_never_uses_the_owners_whatsapp(clean_db, client_edition, monkeypatch):
    """A workspace sends only through its OWN Meta API — never the owner's
    WhatsApp Web engine or desktop, even if those were reachable."""
    from backend import whatsapp_sender
    from backend.whatsapp import engine

    async def owner_engine_ready():
        raise AssertionError("a workspace must never touch the owner's WhatsApp engine")
    monkeypatch.setattr(engine, "ready", owner_engine_ready)
    assert await whatsapp_sender.send_whatsapp("+971500000000", "hello", {}) is False   # no Meta keys yet


def test_owner_edition_is_unchanged(monkeypatch):
    monkeypatch.delenv("HOM_EDITION", raising=False)
    assert edition.edition() == "owner" and not edition.setting_locked("ollama_model")


def test_client_app_has_no_portal_or_clients_routes():
    code = ("from backend.main import app\n"
            "paths = set(app.openapi()['paths'])\n"
            "assert not any(p.startswith('/api/portal') or p.startswith('/api/clients') for p in paths), paths\n"
            "assert '/api/auth/handoff' in paths\n")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
                       env={**__import__('os').environ, "HOM_EDITION": "client",
                            "DATABASE_PATH": str(ROOT / "tests" / ".client-edition-test.db")})
    Path(ROOT / "tests" / ".client-edition-test.db").unlink(missing_ok=True)
    assert r.returncode == 0, r.stderr[-2000:]


# ── Outbound guard ─────────────────────────────────────────────────────

def test_egress_guard_blocks_private_addresses_only():
    code = r'''
import socket
from backend import edition
edition.install_egress_guard()
def attempt(ip, port):
    s = socket.socket(socket.AF_INET6 if ":" in ip else socket.AF_INET)
    s.settimeout(0.2)
    try:
        s.connect((ip, port))
    except edition.EgressBlocked:
        return "blocked"
    except OSError:
        return "tried"
    finally:
        s.close()
    return "tried"
results = {ip: attempt(ip, port) for ip, port in [
    ("10.0.0.1", 80), ("192.168.1.1", 80), ("172.17.0.1", 5173), ("127.0.0.1", 8000),
    ("169.254.169.254", 80), ("::1", 8000), ("127.0.0.1", 11434), ("198.51.100.7", 9)]}
assert results["10.0.0.1"] == "blocked", results
assert results["192.168.1.1"] == "blocked", results
assert results["172.17.0.1"] == "blocked", results
assert results["127.0.0.1"] == "tried", results            # the AI relay (11434) is allowed ...
s = socket.socket(); s.settimeout(0.2)
try:
    s.connect(("127.0.0.2", 3000)); raise SystemExit("unexpected")
except edition.EgressBlocked:
    raise SystemExit("own WhatsApp engine was blocked")
except OSError:
    pass                                                    # its own WhatsApp engine is allowed too
assert results["169.254.169.254"] == "blocked", results
assert results["::1"] == "blocked", results
try:
    s = socket.socket(); s.settimeout(0.2); s.connect(("127.0.0.1", 8000)); raise SystemExit("owner API reachable!")
except edition.EgressBlocked:
    pass                                                    # ... but not the owner's app
except OSError as e:
    raise SystemExit(f"not blocked: {e!r}")
print("ok")
'''
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60,
                       env={**__import__('os').environ, "HOM_EDITION": "client",
                            "OLLAMA_BASE_URL": "http://127.0.0.1:11434", "HOM_WAHA_URL": "http://127.0.0.2:3000"})
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout + r.stderr[-2000:]


def test_browser_url_guard():
    for url in ("http://localhost:8000/", "http://127.0.0.1/", "http://10.1.2.3/x", "http://[::1]/",
                "http://192.168.0.1/admin", "file:///etc/passwd", "http://host.docker.internal/", "ftp://x.com"):
        assert edition.url_is_private(url), url
    for url in ("http://93.184.216.34/", "data:text/plain,hi", "about:blank"):
        assert not edition.url_is_private(url), url


# ── Host supervisor ────────────────────────────────────────────────────

@pytest.fixture
def sup(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import hom_supervisor as s
    monkeypatch.setattr(s, "WS_ROOT", tmp_path / "workspaces")
    return s


class FakeDocker:
    def __init__(self, image="sha256:new", containers=None):
        self.image, self.containers, self.calls = image, containers or {}, []

    def __call__(self, cmd, timeout=120, env=None, input_bytes=None):
        self.calls.append((cmd, env))
        out, rc = b"", 0
        if "image" in cmd and "inspect" in cmd:
            out, rc = (self.image.encode(), 0) if self.image else (b"", 1)
        elif cmd[3:5] == ["ps", "-aq"]:
            out = " ".join(f"c{k}" for k in self.containers).encode()
        elif cmd[3] == "inspect" and "hom.workspace" in " ".join(cmd):
            out = "\n".join(f"{wid}|{svc}|{st['state']}|{st.get('health','')}|{st['image']}"
                            for wid, svcs in self.containers.items() for svc, st in svcs.items()).encode()
        elif cmd[3] == "inspect":
            out = b"true"
        return subprocess.CompletedProcess(cmd, rc, out, b"")

    def compose_calls(self):
        return [(c, e) for c, e in self.calls if "compose" in c]


def _ctl(*ws, deleted=()):
    return {"workspaces": [{"id": i, "port": 7000 + i, "desired": d, "email": f"{i}@x.com"} for i, d in ws],
            "deleted": list(deleted), "ai_model": "qwen3:8b"}


def test_supervisor_waits_for_a_first_publish(sup):
    d = FakeDocker(image=None)
    out = sup.reconcile(_ctl((1, "RUNNING")), {}, b"k" * 64, d)
    assert out["1"]["state"] == "WAITING_FOR_RELEASE" and not d.compose_calls()


def test_supervisor_starts_a_workspace_with_its_own_secret(sup):
    d = FakeDocker()
    out = sup.reconcile(_ctl((1, "RUNNING")), {}, b"k" * 64, d)
    (cmd, env), = d.compose_calls()
    assert out["1"]["state"] == "STARTING" and "up" in cmd and "hom-ws-1" in cmd
    assert env["WS_PORT"] == "7001" and env["WS_SECRET"] == sup.workspace_secret(b"k" * 64, 1)
    assert env["WS_AI_MODEL"] == "qwen3:8b" and (sup.WS_ROOT / "ws-1" / "data").is_dir()
    assert (sup.WS_ROOT / "ws-1" / "company_dna.txt").read_text() == ""     # starts empty: the editor guides them


def test_supervisor_leaves_healthy_workspaces_alone(sup):
    running = {"backend": {"state": "running", "health": "healthy", "image": "sha256:new"},
               "frontend": {"state": "running", "image": "x"}}
    d = FakeDocker(containers={1: running})
    out = sup.reconcile(_ctl((1, "RUNNING")), {"release": {"short": "abc"}}, b"k" * 64, d)
    assert out["1"] == {**out["1"], "state": "RUNNING", "version": "abc"} and not d.compose_calls()
    # an older image gets recreated on the current release
    d = FakeDocker(containers={1: {**running, "backend": {**running["backend"], "image": "sha256:old"}}})
    sup.reconcile(_ctl((1, "RUNNING")), {}, b"k" * 64, d)
    (cmd, _), = d.compose_calls()
    assert "--force-recreate" in cmd


def test_supervisor_pauses_and_archives_never_erases(sup):
    running = {"backend": {"state": "running", "health": "healthy", "image": "sha256:new"}}
    d = FakeDocker(containers={1: running, 2: running, 9: running})
    (sup.WS_ROOT / "ws-2" / "data").mkdir(parents=True)
    (sup.WS_ROOT / "ws-2" / "data" / "leads.db").write_text("client data")
    out = sup.reconcile(_ctl((1, "STOPPED"), deleted=[2]), {}, b"k" * 64, d)
    cmds = [c for c, _ in d.compose_calls()]
    assert any("stop" in c and "hom-ws-1" in c for c in cmds)
    assert any("down" in c and "hom-ws-2" in c for c in cmds)
    assert not any("hom-ws-9" in c for c in cmds)                      # unknown workspaces are never touched
    assert out["1"]["state"] == "STOPPED" and out["2"]["state"] == "DELETED"
    archived = list((sup.WS_ROOT / "_deleted").glob("ws-2-*/data/leads.db"))
    assert archived and archived[0].read_text() == "client data" and not (sup.WS_ROOT / "ws-2").exists()


def test_release_source_carries_no_secrets(sup, tmp_path):
    src = tmp_path / "src"
    (src / "backend" / "data").mkdir(parents=True)
    (src / ".env").write_text("SMTP_PASSWORD=owner")
    (src / "backend" / ".env").write_text("SMTP_PASSWORD=owner")
    (src / "backend" / "company_dna.txt").write_text("Owner's private company profile")
    (src / "backend" / "data" / "leads.db").write_text("owner leads")
    (src / "backend" / "main.py").write_text("code")
    sup.clean_release_source(src)
    assert not (src / ".env").exists() and not (src / "backend" / ".env").exists()
    assert not (src / "backend" / "data").exists()
    assert "Owner's" not in (src / "backend" / "company_dna.txt").read_text()
    assert (src / "backend" / "main.py").exists()


def test_publish_refuses_a_commit_without_client_mode(sup, tmp_path):
    src = tmp_path / "old"
    (src / "backend").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="doesn't include client workspaces"):
        sup.check_release_source(src)
    (src / "backend" / "edition.py").write_text((ROOT / "backend" / "edition.py").read_text())
    sup.check_release_source(src)


async def test_client_edition_hides_search_providers(clean_db, client_edition):
    h = {"Authorization": f"Bearer {auth.issue_session()}"}
    async with await _client() as c:
        assert (await c.get("/api/lead-search/providers", headers=h)).status_code == 404


# ── First-run set-up ───────────────────────────────────────────────────

DNA = ("We are Bright Dental Marketing, a small agency in Dubai. We help dental clinics win more patients "
       "with websites, online booking and local search. Our tone is friendly and direct.")


async def test_setup_is_required_before_the_dashboard_and_reaches_the_workspace(clean_db, outbox):
    workspaces.KEY_FILE.write_text("k" * 64)
    async with await _client() as c:
        h, client = await _sign_in(c, outbox, "a@gmail.com", onboard=False)
        assert client["needs_onboarding"] is True
        ws = await workspaces.workspace_for_client(client["id"])
        _supervisor_says({ws["id"]: "RUNNING"})
        r = await c.post("/api/portal/workspace/enter", headers=h)
        assert r.status_code == 409 and "setting up" in r.json()["detail"]
        url = "/api/portal/me/onboarding"
        for bad in ({"name": "", "sector": "Dental", "company_dna": DNA},
                    {"name": "Sara", "sector": " ", "company_dna": DNA},
                    {"name": "Sara", "sector": "Dental", "company_dna": "Too short."}):
            assert (await c.put(url, headers=h, json=bad)).status_code == 400, bad
        me = (await c.put(url, headers=h, json={"name": "Sara", "company": "Bright Dental Marketing",
                                                "sector": "Marketing agency", "company_dna": DNA})).json()
        assert me["needs_onboarding"] is False and me["sector"] == "Marketing agency"
        assert (await c.post("/api/portal/workspace/enter", headers=h)).status_code == 200
        listing = (await c.get("/api/clients")).json()["clients"]
    assert listing[0]["sector"] == "Marketing agency"
    seeded = json.loads(workspaces.CONTROL_FILE.read_text())["workspaces"][0]["company_dna"]
    assert seeded.startswith("Company: Bright Dental Marketing\nSector: Marketing agency\n\nWe are Bright Dental")


def test_supervisor_fills_only_an_empty_company_dna(sup):
    d = sup.WS_ROOT / "ws-3"
    d.mkdir(parents=True)
    dna = d / "company_dna.txt"
    dna.write_text("")
    inode = dna.stat().st_ino
    ws = {"id": 3, "company_dna": "Sector: Dental\n\nWe help clinics."}
    assert sup.seed_company_dna(ws) is True
    assert dna.read_text() == "Sector: Dental\n\nWe help clinics.\n" and dna.stat().st_ino == inode   # same file (mounted)
    dna.write_text("The client's own edited profile")
    assert sup.seed_company_dna(ws) is False and dna.read_text() == "The client's own edited profile"


def test_each_workspace_gets_its_own_whatsapp_engine_and_keys(sup):
    d = FakeDocker()
    sup.reconcile(_ctl((1, "RUNNING"), (2, "RUNNING")), {}, b"k" * 64, d)
    envs = {e["WS_ID"]: e for c, e in d.compose_calls()}
    assert envs["1"]["WS_WAHA_KEY"] != envs["2"]["WS_WAHA_KEY"] and envs["1"]["WS_WA_SECRET"] != envs["2"]["WS_WA_SECRET"]
    assert len(envs["1"]["WS_WAHA_KEY"]) == 64 and (sup.WS_ROOT / "ws-1" / "whatsapp").is_dir()
    compose = (ROOT / "deploy" / "workspace-compose.yml").read_text()
    assert "waha:" in compose and "HOM_WAHA_URL: http://waha:3000" in compose
    waha_block = compose.split("  waha:")[1]
    assert "ports:" not in waha_block                      # never published: only its own backend reaches it


async def test_workspace_with_its_own_engine_can_use_whatsapp_web(clean_db, client_edition, monkeypatch):
    from backend.whatsapp import engine, service as wa_service
    from backend import whatsapp_sender
    monkeypatch.setenv("HOM_WAHA_URL", "http://waha:3000")
    assert engine.available() and await wa_service.current_engine() == "web"
    sent = []

    async def ready():
        return True

    async def send_text(phone, text):
        sent.append((phone, text))
    monkeypatch.setattr(engine, "ready", ready)
    monkeypatch.setattr(engine, "send_text", send_text)
    assert await whatsapp_sender.send_whatsapp("+971501112233", "Hello", {}) is True and sent


def test_workspace_engine_posts_events_straight_to_its_app_with_the_secret(monkeypatch):
    import importlib
    monkeypatch.setenv("HOM_WA_EVENTS_WEBHOOK", "http://backend:8000/api/whatsapp/hooks/event")
    monkeypatch.setenv("HOM_WA_SECRET", "ws-secret")
    from backend.whatsapp import engine
    importlib.reload(engine)
    try:
        captured = {}

        async def fake_request(method, path, **kw):
            captured.setdefault("calls", []).append((method, path, kw.get("json")))

            class R:
                status_code = 201
                text = ""
            return R()

        async def fake_session():
            return {"state": "SCAN_QR_CODE"}
        monkeypatch.setattr(engine, "_request", fake_request)
        monkeypatch.setattr(engine, "session", fake_session)
        import asyncio
        asyncio.run(engine.start())
        hook = captured["calls"][0][2]["config"]["webhooks"][0]
        assert hook["url"] == "http://backend:8000/api/whatsapp/hooks/event"
        assert hook["customHeaders"] == [{"name": "X-HOM-Secret", "value": "ws-secret"}]
    finally:
        monkeypatch.delenv("HOM_WA_EVENTS_WEBHOOK")
        importlib.reload(engine)
