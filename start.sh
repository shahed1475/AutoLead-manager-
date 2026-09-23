#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
#  HCM · Sales Growth Engine — one-click server (Linux)
#
#    ./start.sh              start the app + a public link you can share
#    ./start.sh local        start the app on this computer only (no link)
#    ./start.sh stop         stop the app and the public link
#    ./start.sh status       show what is running and the current link
#    ./start.sh link         print (and copy) the current share link
#    ./start.sh install-button   add the HCM button to the desktop + app menu
#
#  The app runs in Docker on this machine (it needs the local AI model, the
#  research browser and WhatsApp desktop). The public link is a Cloudflare
#  Tunnel: HTTPS, no router changes, your IP stays private.
#  Before any link is opened the app must be protected by a password.
#  Developer mode (venv + Vite dev server) lives in scripts/dev.sh.
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
cd "$ROOT"
RUN_DIR="$ROOT/.run"
mkdir -p "$RUN_DIR"

FRONTEND_PORT="${FRONTEND_PORT:-5173}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
LOCAL_URL="http://localhost:${FRONTEND_PORT}"
API="http://127.0.0.1:${BACKEND_PORT}/api"
TUNNEL_PID="$RUN_DIR/tunnel.pid"
TUNNEL_LOG="$RUN_DIR/tunnel.log"
LINK_FILE="$RUN_DIR/share-link.txt"
MIN_PASSWORD=10
# Optional permanent link: deploy/tunnel.env with TUNNEL_TOKEN=... and
# PUBLIC_URL=https://app.yourdomain.com (see docs/SHARING.md). Git-ignored.
TUNNEL_ENV="$ROOT/deploy/tunnel.env"

# Always the system Docker engine (Docker Desktop, if installed, is a
# separate engine in a VM and can't reach the local AI model).
DOCKER=(docker --context default)
COMPOSE=("${DOCKER[@]}" compose -f docker-compose.yml -f docker-compose.host.yml --profile full)

# ── Output ────────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  B=$'\e[1m'; DIM=$'\e[2m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; C=$'\e[36m'; X=$'\e[0m'
else
  B=""; DIM=""; G=""; Y=""; R=""; C=""; X=""
fi
say()  { printf '  %s\n' "$*"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$X" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$X" "$*"; }
fail() { printf '  %s✗ %s%s\n' "$R" "$*" "$X"; }
step() { printf '\n  %s%s%s\n' "$B" "$*" "$X"; }

banner() {
  printf '\n  %sHCM%s  %sSales Growth Engine%s\n' "$B" "$X" "$DIM" "$X"
}

# Keep the launcher window open until the user has read the result.
finish() {
  local code="${1:-0}"
  if [[ "${HCM_FROM_LAUNCHER:-}" == "1" && -t 0 ]]; then
    printf '\n  %sPress Enter to close this window.%s' "$DIM" "$X"
    read -r _ || true
  fi
  exit "$code"
}

# ── Helpers ───────────────────────────────────────────────────────────────────
wait_http() {  # url seconds
  local url="$1" deadline=$((SECONDS + $2))
  while (( SECONDS < deadline )); do
    curl -fsS -o /dev/null --max-time 3 "$url" 2>/dev/null && return 0
    sleep 2
  done
  return 1
}

# Check a brand-new public link through Cloudflare's DNS: asking the local
# router too early caches "not found" for that name for a while.
wait_public() {  # url seconds
  local url="$1" deadline=$((SECONDS + $2))
  while (( SECONDS < deadline )); do
    curl -fsS -o /dev/null --max-time 5 --doh-url https://1.1.1.1/dns-query "$url" 2>/dev/null && return 0
    sleep 2
  done
  return 1
}

app_up() { curl -fsS -o /dev/null --max-time 3 "$API/health" 2>/dev/null; }

tunnel_alive() {
  [[ -f "$TUNNEL_PID" ]] && kill -0 "$(cat "$TUNNEL_PID")" 2>/dev/null
}

copy_to_clipboard() {
  local text="$1"
  if command -v qdbus >/dev/null 2>&1 && qdbus org.kde.klipper >/dev/null 2>&1; then
    qdbus org.kde.klipper /klipper setClipboardContents "$text" >/dev/null 2>&1 && return 0
  fi
  for tool in "wl-copy" "xclip -selection clipboard" "xsel --clipboard --input"; do
    command -v "${tool%% *}" >/dev/null 2>&1 && { printf '%s' "$text" | $tool 2>/dev/null && return 0; }
  done
  return 1
}

notify() {
  command -v notify-send >/dev/null 2>&1 && \
    notify-send -a "HCM" -i "$ROOT/frontend/public/brand/hcm-mark.svg" "$1" "$2" 2>/dev/null || true
}

# ── Steps ─────────────────────────────────────────────────────────────────────
check_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    fail "Docker is not installed."
    say "Install it with:  sudo dnf install docker-ce  (or your distro's docker package)"
    finish 1
  fi
  if ! "${DOCKER[@]}" info >/dev/null 2>&1; then
    fail "Docker is not running."
    say "Start it with:  sudo systemctl start docker"
    finish 1
  fi
  ok "Docker is running"
}

check_ai() {
  if curl -fsS -o /dev/null --max-time 3 http://127.0.0.1:11434/api/tags 2>/dev/null; then
    ok "Local AI model (Ollama) is running"
    return
  fi
  if command -v ollama >/dev/null 2>&1; then
    say "Starting the local AI model…"
    setsid nohup ollama serve >"$RUN_DIR/ollama.log" 2>&1 </dev/null &
    if wait_http http://127.0.0.1:11434/api/tags 20; then ok "Local AI model started"; return; fi
  fi
  warn "Local AI model (Ollama) is not reachable — search works, but AI writing and research are limited."
}

start_app() {
  if app_up && curl -fsS -o /dev/null --max-time 3 "$LOCAL_URL" 2>/dev/null && [[ "${HCM_REBUILD:-}" != "1" ]]; then
    ok "App is already running"
    return
  fi
  say "Starting the app (the first start builds it and can take a few minutes)…"
  if ! "${COMPOSE[@]}" up -d --build >"$RUN_DIR/docker.log" 2>&1; then
    fail "The app could not start. Details: $RUN_DIR/docker.log"
    tail -n 15 "$RUN_DIR/docker.log" | sed 's/^/      /'
    finish 1
  fi
  if ! wait_http "$API/health" 180 || ! wait_http "$LOCAL_URL" 60; then
    fail "The app started but isn't answering. Details: $RUN_DIR/docker.log"
    finish 1
  fi
  ok "App is running at $LOCAL_URL"
}

password_is_set() {
  curl -fsS --max-time 5 "$API/auth/status" 2>/dev/null | grep -q '"password_set":true'
}

# Anyone who can reach the app while no password exists could set one and
# lock the owner out, so a password is required before a public link opens.
ensure_password() {
  if password_is_set; then
    ok "The app is protected by a password"
    return 0
  fi
  if [[ ! -t 0 ]]; then
    fail "The app has no password yet, so no public link was opened."
    say "Run ./start.sh in a terminal once to create the password."
    return 1
  fi
  step "Create a password for your app"
  say "People you share the link with will need it. They can do everything you can,"
  say "including sending email from your account, so share it only with people you trust."
  local pw pw2
  while true; do
    read -rsp "  Password (at least $MIN_PASSWORD characters): " pw; echo
    if (( ${#pw} < MIN_PASSWORD )); then warn "Too short — use at least $MIN_PASSWORD characters."; continue; fi
    read -rsp "  Type it again: " pw2; echo
    [[ "$pw" == "$pw2" ]] && break
    warn "The two passwords don't match. Try again."
  done
  local body code
  # JSON-encode via Python so any character in the password is safe.
  body=$(HCM_PW="$pw" python3 -c 'import json,os; print(json.dumps({"password": os.environ["HCM_PW"]}))')
  code=$(curl -sS -o "$RUN_DIR/auth.out" -w '%{http_code}' --max-time 10 \
    -H 'Content-Type: application/json' --data-binary @- "$API/auth/set-password" <<<"$body")
  unset pw pw2 body
  rm -f "$RUN_DIR/auth.out"
  if [[ "$code" != "200" ]]; then
    fail "The password could not be saved (HTTP $code)."
    return 1
  fi
  ok "Password saved"
}

cloudflared_bin() {
  local bin
  bin="$(command -v cloudflared 2>/dev/null || true)"
  [[ -z "$bin" && -x "$HOME/.local/bin/cloudflared" ]] && bin="$HOME/.local/bin/cloudflared"
  if [[ -z "$bin" ]]; then
    local arch
    case "$(uname -m)" in
      x86_64) arch=amd64 ;; aarch64|arm64) arch=arm64 ;; armv7l) arch=arm ;;
      *) fail "No Cloudflare Tunnel build for $(uname -m)." >&2; return 1 ;;
    esac
    say "Installing Cloudflare Tunnel (one time, no admin rights needed)…" >&2
    mkdir -p "$HOME/.local/bin"
    if ! curl -fsSL --max-time 120 -o "$HOME/.local/bin/cloudflared" \
        "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch}"; then
      fail "Could not download Cloudflare Tunnel. Check the internet connection." >&2
      rm -f "$HOME/.local/bin/cloudflared"
      return 1
    fi
    chmod +x "$HOME/.local/bin/cloudflared"
    bin="$HOME/.local/bin/cloudflared"
  fi
  printf '%s' "$bin"
}

start_tunnel() {
  if tunnel_alive && [[ -s "$LINK_FILE" ]]; then
    ok "Share link is already open"
    return 0
  fi
  stop_tunnel quiet
  local bin
  bin="$(cloudflared_bin)" || return 1
  local url=""
  if [[ -f "$TUNNEL_ENV" ]]; then
    # Permanent link on your own domain (named tunnel).
    # shellcheck disable=SC1090
    source "$TUNNEL_ENV"
    if [[ -z "${TUNNEL_TOKEN:-}" || -z "${PUBLIC_URL:-}" ]]; then
      fail "$TUNNEL_ENV needs TUNNEL_TOKEN and PUBLIC_URL."
      return 1
    fi
    say "Opening your permanent link…"
    TUNNEL_TOKEN="$TUNNEL_TOKEN" setsid nohup "$bin" tunnel --no-autoupdate run \
      >"$TUNNEL_LOG" 2>&1 </dev/null &
    echo $! >"$TUNNEL_PID"
    url="$PUBLIC_URL"
  else
    say "Opening a share link…"
    setsid nohup "$bin" tunnel --no-autoupdate --url "http://127.0.0.1:${FRONTEND_PORT}" \
      >"$TUNNEL_LOG" 2>&1 </dev/null &
    echo $! >"$TUNNEL_PID"
    local deadline=$((SECONDS + 45))
    while (( SECONDS < deadline )) && [[ -z "$url" ]]; do
      url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | grep -v '://api\.' | head -n1 || true)"
      tunnel_alive || break
      [[ -z "$url" ]] && sleep 1
    done
  fi
  if [[ -z "$url" ]] || ! tunnel_alive; then
    fail "The share link could not be opened. Details: $TUNNEL_LOG"
    stop_tunnel quiet
    return 1
  fi
  # A new link takes a few seconds to become reachable worldwide: wait for
  # Cloudflare to register the connection, then for the link to answer.
  say "Waiting for the link to go live…"
  local reg_deadline=$((SECONDS + 30))
  while (( SECONDS < reg_deadline )) && ! grep -q "Registered tunnel connection" "$TUNNEL_LOG" 2>/dev/null; do
    sleep 1
  done
  if ! wait_public "$url" 60; then
    warn "The link was created but isn't answering yet — give it a minute."
  fi
  printf '%s\n' "$url" >"$LINK_FILE"
  ok "Share link is open"
}

stop_tunnel() {
  local quiet="${1:-}"
  if tunnel_alive; then
    kill "$(cat "$TUNNEL_PID")" 2>/dev/null || true
    [[ -z "$quiet" ]] && ok "Share link closed"
  fi
  rm -f "$TUNNEL_PID" "$LINK_FILE"
}

open_browser() {
  [[ "${HCM_NO_BROWSER:-}" == "1" ]] && return
  command -v xdg-open >/dev/null 2>&1 && (xdg-open "$LOCAL_URL" >/dev/null 2>&1 &)
}

summary() {
  local link=""
  [[ -s "$LINK_FILE" ]] && link="$(cat "$LINK_FILE")"
  printf '\n  %s──────────────────────────────────────────────────────────%s\n' "$DIM" "$X"
  printf '  %sHCM is running%s\n\n' "$B" "$X"
  printf '  On this computer   %s%s%s\n' "$C" "$LOCAL_URL" "$X"
  if [[ -n "$link" ]]; then
    printf '  Share this link    %s%s%s\n' "$C$B" "$link" "$X"
    if copy_to_clipboard "$link"; then printf '  %s(copied to your clipboard)%s\n' "$DIM" "$X"; fi
    [[ -f "$TUNNEL_ENV" ]] || printf '\n  %sThis link changes each time the link is reopened.%s\n' "$DIM" "$X"
  fi
  printf '\n  %sStop everything:%s  ./start.sh stop   %s(or right-click the HCM button → Stop)%s\n' "$DIM" "$X" "$DIM" "$X"
  printf '  %s──────────────────────────────────────────────────────────%s\n' "$DIM" "$X"
}

install_button() {
  local apps="$HOME/.local/share/applications" desk
  desk="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
  mkdir -p "$apps"
  local file="$apps/hcm-sales-growth-engine.desktop"
  cat >"$file" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=HCM
GenericName=Sales Growth Engine
Comment=Start HCM and get a link to share
Exec=env HCM_FROM_LAUNCHER=1 "$ROOT/start.sh" start
Icon=$ROOT/frontend/public/brand/hcm-mark.svg
Terminal=true
Categories=Office;
Keywords=leads;sales;crm;
StartupNotify=false
Actions=local;status;link;stop;

[Desktop Action local]
Name=Start on this computer only
Exec=env HCM_FROM_LAUNCHER=1 "$ROOT/start.sh" local

[Desktop Action status]
Name=Status
Exec=env HCM_FROM_LAUNCHER=1 "$ROOT/start.sh" status

[Desktop Action link]
Name=Copy share link
Exec=env HCM_FROM_LAUNCHER=1 "$ROOT/start.sh" link

[Desktop Action stop]
Name=Stop HCM
Exec=env HCM_FROM_LAUNCHER=1 "$ROOT/start.sh" stop
EOF
  chmod +x "$file"
  if [[ -d "$desk" ]]; then
    cp "$file" "$desk/HCM.desktop"
    chmod +x "$desk/HCM.desktop"
    command -v gio >/dev/null 2>&1 && gio set "$desk/HCM.desktop" metadata::trusted true 2>/dev/null || true
  fi
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$apps" 2>/dev/null || true
  ok "HCM button added to the app menu${desk:+ and the desktop ($desk)}"
}

# ── Commands ──────────────────────────────────────────────────────────────────
cmd_start() {
  local share="$1"
  banner
  step "Starting"
  check_docker
  check_ai
  start_app
  if [[ "$share" == "yes" ]]; then
    if ensure_password; then
      if start_tunnel; then
        notify "HCM is online" "$(cat "$LINK_FILE")"
      else
        warn "The app works on this computer, but the share link isn't available right now."
      fi
    fi
  fi
  open_browser
  summary
  finish 0
}

cmd_stop() {
  banner
  step "Stopping"
  stop_tunnel
  if "${COMPOSE[@]}" stop >"$RUN_DIR/docker.log" 2>&1; then ok "App stopped"; else warn "Docker reported a problem — see $RUN_DIR/docker.log"; fi
  finish 0
}

cmd_status() {
  banner
  step "Status"
  if app_up; then ok "App is running at $LOCAL_URL"; else warn "App is not running — start it with ./start.sh"; fi
  if app_up; then
    if password_is_set; then ok "Protected by a password"; else warn "No password yet — the share link stays closed until one is set"; fi
  fi
  if tunnel_alive && [[ -s "$LINK_FILE" ]]; then ok "Share link: $(cat "$LINK_FILE")"; else say "No share link is open."; fi
  finish 0
}

cmd_link() {
  if tunnel_alive && [[ -s "$LINK_FILE" ]]; then
    local link; link="$(cat "$LINK_FILE")"
    printf '\n  %s\n' "$link"
    copy_to_clipboard "$link" && printf '  %s(copied to your clipboard)%s\n' "$DIM" "$X"
  else
    printf '\n  No share link is open. Start one with ./start.sh\n'
  fi
  finish 0
}

# Sourced (tests)? Define the functions only.
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then return 0; fi

case "${1:-start}" in
  start|"")         cmd_start yes ;;
  local)            cmd_start no ;;
  stop)             cmd_stop ;;
  status)           cmd_status ;;
  link)             cmd_link ;;
  install-button)   banner; install_button; finish 0 ;;
  -h|--help|help)   sed -n '3,17p' "$0" | sed 's/^#  \{0,1\}//' ;;
  *)                fail "Unknown command: $1"; sed -n '3,17p' "$0" | sed 's/^#  \{0,1\}//'; exit 2 ;;
esac
