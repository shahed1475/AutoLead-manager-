#!/usr/bin/env python3
"""
hom_supervisor.py — runs client workspaces on this computer.

Started by ./start.sh (runs on the host, as you — standard library only).
Every few seconds it reads what the owner app wants (backend/data/workspaces.json),
makes Docker match it, and reports back (.run/workspaces/status.json):

  * one Docker Compose project per client: hom-ws-<id> (deploy/workspace-compose.yml)
    — its own containers, network and data folder (workspaces/ws-<id>/);
    the web port is published on 127.0.0.1 only, and the client link routes
    each signed-in client to their own port;
  * the client edition (HOM_EDITION=client) of the LAST PUBLISHED image;
  * paused workspaces are stopped; deleted ones are shut down and their data
    folder is MOVED to workspaces/_deleted/ (never erased);
  * "Publish to clients": builds your latest *commit* (git archive — never
    uncommitted changes, never .env files or your company profile), tags it
    hom-client-*:current and moves every running workspace to it.

Usage:  python3 scripts/hom_supervisor.py            (loop; start.sh does this)
        python3 scripts/hom_supervisor.py --once     (one pass, for debugging)
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.edition import workspace_secret  # noqa: E402  (stdlib-only module)

# Paths can be overridden (HOM_WS_*) only to run an isolated test copy.
RUN = Path(os.getenv("HOM_WS_RUN", ROOT / ".run"))
STATUS_DIR = RUN / "workspaces"
STATUS_FILE = STATUS_DIR / "status.json"
KEY_FILE = RUN / "workspace.key"
PID_FILE = RUN / "supervisor.pid"
RELEASE_DIR = RUN / "release"
CONTROL_FILE = Path(os.getenv("HOM_WS_CONTROL", ROOT / "backend" / "data" / "workspaces.json"))
WS_ROOT = Path(os.getenv("HOM_WS_ROOT", ROOT / "workspaces"))
MASTER_KEY = WS_ROOT / ".master.key"
COMPOSE_FILE = ROOT / "deploy" / "workspace-compose.yml"
PROJECT_PREFIX = os.getenv("HOM_WS_PROJECT_PREFIX", "hom-ws-")
IMAGE_TAG = os.getenv("HOM_WS_IMAGE_TAG", "current")

DOCKER = ["docker", "--context", "default"]
IMAGE_BACKEND = "hom-client-backend"
IMAGE_FRONTEND = "hom-client-frontend"
AI_RELAY = "hom-ai-relay"
AI_BIND = os.getenv("HOM_AI_BIND", "172.17.0.1")     # docker0: reachable from containers, not from the LAN
AI_URL = f"http://{AI_BIND}:11434"
TICK_S = 3

DNA_TEMPLATE = """# Company DNA

Describe your company here (Settings → Company DNA): what you sell, who it is
for, the problems you solve, and why customers choose you. The AI uses this to
research leads and write outreach in your voice.
"""

Runner = Callable[..., subprocess.CompletedProcess]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg: str) -> None:
    line = f"{now_iso()} {msg}"
    print(line, flush=True)


def run(cmd: List[str], timeout: int = 120, env: Optional[Dict[str, str]] = None,
        input_bytes: Optional[bytes] = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout, env=env, input=input_bytes)


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


# ── Keys ─────────────────────────────────────────────────────────────────────

def ensure_master_key() -> bytes:
    """The key every workspace's sign-in secret is derived from. Kept in
    workspaces/.master.key; copied to .run/ for the owner app (read-only mount)."""
    WS_ROOT.mkdir(parents=True, exist_ok=True)
    if not MASTER_KEY.exists() or len(MASTER_KEY.read_bytes().strip()) < 32:
        MASTER_KEY.write_text(secrets.token_hex(32))
        os.chmod(MASTER_KEY, 0o600)
    key = MASTER_KEY.read_bytes().strip()
    RUN.mkdir(parents=True, exist_ok=True)
    if not KEY_FILE.exists() or KEY_FILE.read_bytes().strip() != key:
        KEY_FILE.write_bytes(key)
        os.chmod(KEY_FILE, 0o600)
    return key


# ── Docker state ─────────────────────────────────────────────────────────────

def image_id(tag: str, runner: Runner = run) -> Optional[str]:
    r = runner(DOCKER + ["image", "inspect", "--format", "{{.Id}}", tag], timeout=30)
    return r.stdout.decode().strip() if r.returncode == 0 else None


def workspace_containers(runner: Runner = run) -> Dict[int, Dict[str, Dict[str, str]]]:
    """{ws_id: {service: {state, health, image}}} for every hom workspace container."""
    r = runner(DOCKER + ["ps", "-aq", "--filter", "label=hom.workspace",
                         "--filter", f"label=hom.project_prefix={PROJECT_PREFIX}"], timeout=30)
    ids = r.stdout.decode().split()
    if not ids:
        return {}
    fmt = ('{{index .Config.Labels "hom.workspace"}}|{{index .Config.Labels "com.docker.compose.service"}}|'
           '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.Image}}')
    r = runner(DOCKER + ["inspect", "--format", fmt] + ids, timeout=30)
    out: Dict[int, Dict[str, Dict[str, str]]] = {}
    for line in r.stdout.decode().splitlines():
        parts = line.split("|")
        if len(parts) != 5 or not parts[0].isdigit():
            continue
        out.setdefault(int(parts[0]), {})[parts[1]] = {"state": parts[2], "health": parts[3], "image": parts[4]}
    return out


def ensure_ai_relay(runner: Runner = run) -> None:
    """Workspaces reach the local AI model through a tiny relay bound to the
    Docker bridge address (the model itself only listens on 127.0.0.1)."""
    r = runner(DOCKER + ["inspect", "--format", "{{.State.Running}}", AI_RELAY], timeout=30)
    if r.returncode == 0 and r.stdout.decode().strip() == "true":
        return
    if r.returncode == 0:
        runner(DOCKER + ["start", AI_RELAY], timeout=30)
        return
    runner(DOCKER + ["run", "-d", "--name", AI_RELAY, "--restart", "unless-stopped", "--network", "host",
                     "alpine/socat", f"TCP-LISTEN:11434,bind={AI_BIND},fork,reuseaddr", "TCP:127.0.0.1:11434"],
           timeout=120)


# ── Workspaces ───────────────────────────────────────────────────────────────

def ws_dir(ws_id: int) -> Path:
    return WS_ROOT / f"ws-{int(ws_id)}"


def prepare_dir(ws_id: int) -> Path:
    d = ws_dir(ws_id)
    (d / "data").mkdir(parents=True, exist_ok=True)
    (d / "whatsapp").mkdir(parents=True, exist_ok=True)      # its WhatsApp login
    dna = d / "company_dna.txt"
    if not dna.exists():
        dna.write_text("")        # empty: Settings shows the guided outline to fill in
    return d


def seed_company_dna(ws: Dict[str, Any]) -> bool:
    """Put the client's set-up answers into their Company DNA — only while the
    file is still empty, so their own edits are never overwritten. Written in
    place (same file), because the running workspace has this file mounted."""
    text = (ws.get("company_dna") or "").strip()
    dna = ws_dir(ws["id"]) / "company_dna.txt"
    if not text or not dna.parent.exists():
        return False
    try:
        if dna.exists() and dna.read_text().strip():
            return False
        with open(dna, "w") as f:
            f.write(text + "\n")
        return True
    except OSError as exc:
        log(f"workspace {ws['id']}: could not write Company DNA: {exc}")
        return False


def derived(key: bytes, purpose: str, ws_id: int) -> str:
    """A per-workspace secret for one purpose (never the same across workspaces)."""
    import hashlib
    import hmac
    return hmac.new(key, f"hom-{purpose}:{ws_id}".encode(), hashlib.sha256).hexdigest()


def compose(ws: Dict[str, Any], args: List[str], key: bytes, control: Dict[str, Any],
            runner: Runner = run, timeout: int = 300) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update({
        "WS_ID": str(int(ws["id"])),
        "WS_PORT": str(int(ws.get("port") or 0)),
        "WS_DIR": str(ws_dir(ws["id"])),
        "WS_SECRET": workspace_secret(key, int(ws["id"])),
        "WS_AI_URL": AI_URL,
        "WS_AI_MODEL": str(control.get("ai_model") or ""),
        "WS_IMAGE_TAG": IMAGE_TAG,
        "WS_PROJECT_PREFIX": PROJECT_PREFIX,
        # The workspace's own WhatsApp engine: its API key and the engine → app secret.
        "WS_WAHA_KEY": derived(key, "waha", int(ws["id"])),
        "WS_WA_SECRET": derived(key, "wa-events", int(ws["id"])),
    })
    return runner(DOCKER + ["compose", "-p", f"{PROJECT_PREFIX}{int(ws['id'])}", "-f", str(COMPOSE_FILE)] + args,
                  timeout=timeout, env=env)


def _err(r: subprocess.CompletedProcess) -> str:
    text = (r.stderr or r.stdout or b"").decode(errors="replace").strip()
    return text.splitlines()[-1][:300] if text else "failed"


def reconcile(control: Dict[str, Any], status: Dict[str, Any], key: bytes, runner: Runner = run,
              force_recreate: bool = False) -> Dict[str, Any]:
    """Make Docker match the owner's wishes. Returns the new per-workspace status."""
    current = image_id(f"{IMAGE_BACKEND}:{IMAGE_TAG}", runner)
    release = status.get("release") or {}
    containers = workspace_containers(runner)
    result: Dict[str, Any] = {}
    wanted = control.get("workspaces") or []
    if any(w.get("desired") == "RUNNING" for w in wanted):
        ensure_ai_relay(runner)

    for ws in wanted:
        wid = int(ws["id"])
        if seed_company_dna(ws):
            log(f"workspace {wid}: Company DNA filled in from the set-up answers")
        have = containers.get(wid, {})
        be, fe = have.get("backend", {}), have.get("frontend", {})
        running = be.get("state") == "running" and fe.get("state") == "running"
        on_current = bool(current) and be.get("image") == current
        version = release.get("short") if on_current else (("older" if be else None))
        entry: Dict[str, Any] = {"state": None, "version": version, "error": None, "updated": now_iso()}

        if ws.get("desired") == "STOPPED":
            if any(c.get("state") == "running" for c in have.values()):
                r = compose(ws, ["stop"], key, control, runner)
                entry["state"] = "STOPPED" if r.returncode == 0 else "ERROR"
                entry["error"] = None if r.returncode == 0 else _err(r)
            else:
                entry["state"] = "STOPPED"
        elif not current:
            entry["state"] = "WAITING_FOR_RELEASE"
        elif running and on_current and not force_recreate:
            entry["state"] = "RUNNING" if be.get("health") in ("healthy", "") else "STARTING"
        else:
            prepare_dir(wid)
            seed_company_dna(ws)
            args = ["up", "-d", "--remove-orphans"] + (["--force-recreate"] if force_recreate or (be and not on_current) else [])
            r = compose(ws, args, key, control, runner)
            entry["state"] = "STARTING" if r.returncode == 0 else "ERROR"
            entry["error"] = None if r.returncode == 0 else _err(r)
            if r.returncode == 0:
                log(f"workspace {wid}: started")
        result[str(wid)] = entry

    for wid in control.get("deleted") or []:
        wid = int(wid)
        if wid in containers:
            r = compose({"id": wid, "port": 0}, ["down", "--remove-orphans"], key, control, runner)
            if r.returncode != 0:
                result[str(wid)] = {"state": "ERROR", "error": _err(r), "updated": now_iso()}
                continue
            log(f"workspace {wid}: removed")
        d = ws_dir(wid)
        if d.exists():
            archive = WS_ROOT / "_deleted" / f"ws-{wid}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            archive.parent.mkdir(parents=True, exist_ok=True)
            d.rename(archive)         # never erased: kept in workspaces/_deleted/
            log(f"workspace {wid}: data moved to {archive}")
        result[str(wid)] = {"state": "DELETED", "updated": now_iso()}
    return result


# ── Publishing ───────────────────────────────────────────────────────────────

def git(*args: str, runner: Runner = run) -> str:
    r = runner(["git", "-C", str(ROOT)] + list(args), timeout=60)
    return r.stdout.decode(errors="replace").strip() if r.returncode == 0 else ""


def git_info(release: Dict[str, Any], runner: Runner = run) -> Dict[str, Any]:
    head = git("log", "-1", "--format=%H|%h|%s|%cI", runner=runner).split("|", 3)
    if len(head) < 4:
        return {}
    dirty = git("status", "--porcelain", "--untracked-files=no", runner=runner)
    behind = None
    if release.get("sha"):
        n = git("rev-list", "--count", f"{release['sha']}..HEAD", runner=runner)
        behind = int(n) if n.isdigit() else None
    return {"sha": head[0], "short": head[1], "subject": head[2], "date": head[3],
            "uncommitted_changes": len([l for l in dirty.splitlines() if l.strip()]),
            "commits_not_published": behind}


def clean_release_source(src: Path) -> None:
    """Nothing private goes into a client image: no .env files, no company
    profile, no local data."""
    for p in list(src.rglob(".env")) + list(src.rglob("*.env")):
        if p.is_file():
            p.unlink()
    for rel in ("backend/data", "workspaces", ".run", "deploy/tunnel.env"):
        target = src / rel
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    dna = src / "backend" / "company_dna.txt"
    dna.parent.mkdir(parents=True, exist_ok=True)
    dna.write_text(DNA_TEMPLATE)


def check_release_source(src: Path) -> None:
    """Refuse a commit from before client workspaces existed: without the
    client edition, a workspace would run with no sign-in protection."""
    ed = src / "backend" / "edition.py"
    if not ed.exists() or "def verify_handoff" not in ed.read_text():
        raise RuntimeError("this commit doesn't include client workspaces yet — commit your latest code, then publish")


def publish(status: Dict[str, Any], write: Callable[[], None], runner: Runner = run) -> Dict[str, Any]:
    """Build the latest commit as the client release. Returns the new release."""
    log_path = RELEASE_DIR / "build.log"
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    sha = git("rev-parse", "HEAD", runner=runner)
    if not sha:
        raise RuntimeError("could not read the latest commit")
    short = sha[:10]
    src = RELEASE_DIR / "src"
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)
    arch = runner(["git", "-C", str(ROOT), "archive", "--format=tar", sha], timeout=300)
    if arch.returncode != 0:
        raise RuntimeError("git archive failed: " + arch.stderr.decode(errors="replace")[-300:])
    tar_path = RELEASE_DIR / "src.tar"
    tar_path.write_bytes(arch.stdout)
    with tarfile.open(tar_path) as tf:
        tf.extractall(src, filter="data")
    tar_path.unlink()
    check_release_source(src)
    clean_release_source(src)

    with open(log_path, "wb") as logf:
        for tag, args in (
            (f"{IMAGE_BACKEND}:{short}", ["-f", str(src / "backend" / "Dockerfile"), str(src)]),
            (f"{IMAGE_FRONTEND}:{short}", [str(src / "frontend")]),
        ):
            status["publish"]["step"] = f"Building {tag.split(':')[0].replace('hom-client-', '')}"
            write()
            r = runner(DOCKER + ["build", "-t", tag] + args, timeout=3600)
            logf.write(r.stdout + r.stderr)
            if r.returncode != 0:
                tail = (r.stderr or r.stdout).decode(errors="replace").strip().splitlines()[-8:]
                raise RuntimeError("build failed: " + " / ".join(tail)[-600:])
        for name in (IMAGE_BACKEND, IMAGE_FRONTEND):
            runner(DOCKER + ["tag", f"{name}:{short}", f"{name}:current"], timeout=60)
    subject = git("log", "-1", "--format=%s|%cI", sha, runner=runner).split("|", 1)
    return {"sha": sha, "short": short, "subject": subject[0] if subject else "",
            "committed_at": subject[1] if len(subject) > 1 else "", "published_at": now_iso()}


# ── Main loop ────────────────────────────────────────────────────────────────

class Supervisor:
    def __init__(self, runner: Runner = run):
        self.runner = runner
        self.status: Dict[str, Any] = read_json(STATUS_FILE) or {}
        self.status.setdefault("publish", {"state": "IDLE"})
        self.status["release"] = read_json(RELEASE_DIR / "release.json") or self.status.get("release")
        self._git_at = 0.0

    def write(self) -> None:
        self.status["supervisor"] = {"pid": os.getpid(), "heartbeat": time.time()}
        write_json(STATUS_FILE, self.status)

    def tick(self) -> None:
        key = ensure_master_key()
        control = read_json(CONTROL_FILE)
        if time.time() - self._git_at > 30:
            self.status["git"] = git_info(self.status.get("release") or {}, self.runner)
            self._git_at = time.time()
        if control is None:          # the owner app hasn't written anything yet: touch nothing
            self.write()
            return

        force = False
        req = control.get("publish_requested_at")
        pub = self.status.get("publish") or {}
        if req and req != pub.get("handled_request"):
            self.status["publish"] = {"state": "BUILDING", "handled_request": req, "started_at": now_iso(),
                                      "step": "Preparing"}
            self.write()
            try:
                release = publish(self.status, self.write, self.runner)
                write_json(RELEASE_DIR / "release.json", release)
                self.status["release"] = release
                self.status["publish"] = {"state": "DONE", "handled_request": req, "finished_at": now_iso()}
                self._git_at = 0
                force = True
                log(f"published {release['short']} to clients")
            except Exception as exc:  # noqa: BLE001
                self.status["publish"] = {"state": "FAILED", "handled_request": req, "finished_at": now_iso(),
                                          "error": str(exc)[:800]}
                log(f"publish failed: {exc}")

        try:
            self.status["workspaces"] = reconcile(control, self.status, key, self.runner, force_recreate=force)
        except Exception as exc:  # noqa: BLE001 — keep the loop alive; report it
            log(f"reconcile error: {exc}")
            self.status["error"] = str(exc)[:300]
        else:
            self.status.pop("error", None)
        self.write()


def _already_running() -> bool:
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return pid != os.getpid()
    except (OSError, ValueError):
        return False


def main() -> int:
    once = "--once" in sys.argv
    if not once:
        if _already_running():
            print("supervisor already running")
            return 0
        RUN.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()))
    sup = Supervisor()
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *a: stop.update(now=True))
    signal.signal(signal.SIGINT, lambda *a: stop.update(now=True))
    log("workspace service started")
    while not stop["now"]:
        try:
            sup.tick()
        except Exception as exc:  # noqa: BLE001
            log(f"tick failed: {exc}")
        if once:
            break
        for _ in range(TICK_S * 10):
            if stop["now"]:
                break
            time.sleep(0.1)
    if not once:
        try:
            PID_FILE.unlink()
        except OSError:
            pass
    log("workspace service stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
