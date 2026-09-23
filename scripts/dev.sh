#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
#  AutoLead Marketing Engine v2.0 — Mac / Linux launcher
#  Development mode (venv + Vite dev server). For the shareable server use ./start.sh
#  Usage:  scripts/dev.sh   (first run will do full setup automatically)
# ══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Always run from the project root (this script lives in scripts/) ────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# ── Colour helpers ───────────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()  { echo -e "${GREEN}        $*${RESET}"; }
warn()  { echo -e "${YELLOW}  [WARN]  $*${RESET}"; }
error() { echo -e "${RED}  [ERROR] $*${RESET}"; }
step()  { echo -e "${CYAN}${BOLD}$*${RESET}"; }

echo
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║         AutoLead Marketing Engine  v2.0             ║"
echo "  ║     AI-Powered Lead Generation & Outreach           ║"
echo "  ╚══════════════════════════════════════════════════════╝"
echo

# ── PID tracking for clean shutdown ─────────────────────────────────────────
BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
    echo
    echo -e "${YELLOW}  Shutting down AutoLead...${RESET}"
    [[ -n "$BACKEND_PID"  ]] && kill "$BACKEND_PID"  2>/dev/null && info "Backend stopped."
    [[ -n "$FRONTEND_PID" ]] && kill "$FRONTEND_PID" 2>/dev/null && info "Frontend stopped."
    # Kill any child processes that may have been spawned
    jobs -p | xargs -r kill 2>/dev/null || true
    echo -e "${GREEN}  Goodbye.${RESET}"
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# ═════════════════════════════════════════════════════════════════════════════
# STEP 1 — Runtime checks
# ═════════════════════════════════════════════════════════════════════════════

step "  [1/6] Checking runtime dependencies..."

# Python 3.11+
PYTHON_CMD=""
for cmd in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PY_MAJOR=$("$cmd" -c "import sys; print(sys.version_info.major)" 2>/dev/null || echo 0)
        PY_MINOR=$("$cmd" -c "import sys; print(sys.version_info.minor)" 2>/dev/null || echo 0)
        if [[ "$PY_MAJOR" -eq 3 && "$PY_MINOR" -ge 11 ]]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [[ -z "$PYTHON_CMD" ]]; then
    error "Python 3.11+ not found."
    echo "       Install from https://python.org/downloads/"
    echo "       On Mac:   brew install python@3.11"
    echo "       On Linux: sudo apt install python3.11 python3.11-venv"
    exit 1
fi
PY_VER=$("$PYTHON_CMD" --version 2>&1 | awk '{print $2}')
info "Python $PY_VER found  ($PYTHON_CMD)"

# Node.js 18+
if ! command -v node &>/dev/null; then
    error "Node.js not found."
    echo "       Install from https://nodejs.org/"
    echo "       On Mac:   brew install node"
    echo "       On Linux: curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -"
    exit 1
fi
NODE_VER=$(node --version 2>&1)
NODE_MAJOR=$(echo "$NODE_VER" | sed 's/v\([0-9]*\).*/\1/')
if [[ "$NODE_MAJOR" -lt 18 ]]; then
    error "Node.js $NODE_VER is too old — need v18 or newer."
    echo "       Update from https://nodejs.org/"
    exit 1
fi
info "Node.js $NODE_VER found"

# npm
if ! command -v npm &>/dev/null; then
    error "npm not found (should come bundled with Node.js)."
    exit 1
fi

# Ollama (soft check — warn, don't abort)
if ! command -v ollama &>/dev/null; then
    warn "Ollama not found in PATH."
    echo "         AI message generation requires Ollama running locally."
    echo "         Download from: https://ollama.com/download"
    echo "         Then run:  ollama pull llama3"
    echo
    echo "         Continuing startup — install Ollama when ready."
    echo
    sleep 3
else
    OLLAMA_VER=$(ollama --version 2>&1 | head -1)
    info "Ollama found  ($OLLAMA_VER)"
fi

echo

# ═════════════════════════════════════════════════════════════════════════════
# STEP 2 — Virtual environment
# ═════════════════════════════════════════════════════════════════════════════

step "  [2/6] Setting up Python virtual environment..."

if [[ ! -f "venv/bin/activate" ]]; then
    info "Creating virtual environment in ./venv/ ..."
    "$PYTHON_CMD" -m venv venv
    info "Virtual environment created."
else
    info "Virtual environment already exists."
fi

# shellcheck disable=SC1091
source venv/bin/activate
info "Virtual environment activated  ($(python --version 2>&1))"
echo

# ═════════════════════════════════════════════════════════════════════════════
# STEP 3 — Backend dependencies
# ═════════════════════════════════════════════════════════════════════════════

step "  [3/6] Checking backend dependencies..."

if [[ ! -f "venv/bin/uvicorn" ]]; then
    info "First run — installing all packages (2-3 minutes)..."
    pip install -r backend/requirements.txt
    echo
    info "Installing browser automation drivers..."
    python -m playwright install chromium --quiet 2>/dev/null || true
    python -m playwright install-deps chromium --quiet 2>/dev/null || true
    info "All packages installed."
else
    # Quick silent update-check — pip cache makes this near-instant
    pip install -r backend/requirements.txt -q --no-warn-script-location 2>/dev/null || true
    info "Backend packages up to date."
fi
echo

# ═════════════════════════════════════════════════════════════════════════════
# STEP 4 — First-run configuration
# ═════════════════════════════════════════════════════════════════════════════

step "  [4/6] Checking configuration files..."

[[ ! -d "backend/data" ]] && mkdir -p backend/data && info "Created backend/data/ directory."

if [[ ! -f "backend/.env" ]]; then
    if [[ -f "backend/.env.example" ]]; then
        cp backend/.env.example backend/.env
        echo
        echo -e "${YELLOW}  ╔══════════════════════════════════════════════════════╗"
        echo    "  ║  FIRST-TIME SETUP REQUIRED                          ║"
        echo    "  ║                                                      ║"
        echo    "  ║  1. Edit  backend/.env  with your Gmail credentials  ║"
        echo    "  ║  2. Edit  backend/company_dna.txt  with your info    ║"
        echo    "  ║  3. Run   ollama pull llama3   in a new terminal     ║"
        echo    "  ║                                                      ║"
        echo -e "  ╚══════════════════════════════════════════════════════╝${RESET}"
        echo

        # Detect editor and open config files
        EDITOR_CMD=""
        for ed in nano vim vi; do
            if command -v "$ed" &>/dev/null; then
                EDITOR_CMD="$ed"
                break
            fi
        done

        # Detect OS for GUI editor fallback
        case "$(uname -s)" in
            Darwin)
                info "Opening configuration files in TextEdit..."
                open -a TextEdit backend/.env backend/company_dna.txt 2>/dev/null || true
                ;;
            Linux)
                if command -v xdg-open &>/dev/null && [[ -n "${DISPLAY:-}" ]]; then
                    xdg-open backend/.env 2>/dev/null || true
                    sleep 0.5
                    xdg-open backend/company_dna.txt 2>/dev/null || true
                elif [[ -n "$EDITOR_CMD" ]]; then
                    info "Opening backend/.env in $EDITOR_CMD (save and exit to continue)..."
                    "$EDITOR_CMD" backend/.env
                    info "Opening backend/company_dna.txt in $EDITOR_CMD..."
                    "$EDITOR_CMD" backend/company_dna.txt
                fi
                ;;
        esac

        echo
        echo "  Press ENTER after you have saved your configuration..."
        read -r
    else
        warn "backend/.env.example not found. Skipping .env creation."
    fi
else
    info ".env configuration found."
fi

# ═════════════════════════════════════════════════════════════════════════════
# STEP 5 — Frontend dependencies
# ═════════════════════════════════════════════════════════════════════════════

step "  [5/6] Checking frontend dependencies..."

if [[ ! -d "frontend/node_modules" ]]; then
    info "Installing Node.js packages (first run, ~30 seconds)..."
    (cd frontend && npm install --silent)
    info "Frontend packages installed."
else
    info "Node.js packages already installed."
fi
echo

# ═════════════════════════════════════════════════════════════════════════════
# STEP 6 — Launch services
# ═════════════════════════════════════════════════════════════════════════════

step "  [6/6] Starting services..."
echo

# ── Backend ──────────────────────────────────────────────────────────────────
info "Launching backend  → http://localhost:8000"
(
    source "$SCRIPT_DIR/venv/bin/activate"
    cd "$SCRIPT_DIR"
    python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload \
        >> "$SCRIPT_DIR/backend.log" 2>&1
) &
BACKEND_PID=$!

# ── Wait for backend to be ready ─────────────────────────────────────────────
info "Waiting for backend to be ready..."
BACKEND_READY=false
for i in $(seq 1 20); do
    if curl -sf http://127.0.0.1:8000/api/health &>/dev/null || \
       curl -sf http://127.0.0.1:8000/docs &>/dev/null; then
        BACKEND_READY=true
        break
    fi
    sleep 1
done

if [[ "$BACKEND_READY" == false ]]; then
    warn "Backend health-check timed out — it may still be starting."
    warn "Check backend.log for details."
else
    info "Backend is ready."
fi

# ── Frontend ──────────────────────────────────────────────────────────────────
info "Launching frontend → http://localhost:5173"
(cd "$SCRIPT_DIR/frontend" && npm run dev >> "$SCRIPT_DIR/frontend.log" 2>&1) &
FRONTEND_PID=$!

# ── Open browser ──────────────────────────────────────────────────────────────
sleep 3

case "$(uname -s)" in
    Darwin) open http://localhost:5173 2>/dev/null || true ;;
    Linux)
        if command -v xdg-open &>/dev/null; then
            xdg-open http://localhost:5173 2>/dev/null || true
        elif command -v sensible-browser &>/dev/null; then
            sensible-browser http://localhost:5173 2>/dev/null || true
        fi
        ;;
esac

# ── Done ─────────────────────────────────────────────────────────────────────
echo
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║  AutoLead is running!                               ║"
echo "  ║                                                      ║"
echo "  ║  Frontend  →  http://localhost:5173                  ║"
echo "  ║  Backend   →  http://localhost:8000                  ║"
echo "  ║  API Docs  →  http://localhost:8000/docs             ║"
echo "  ║                                                      ║"
echo "  ║  Logs:  backend.log  |  frontend.log                 ║"
echo "  ║                                                      ║"
echo "  ║  Press Ctrl+C to stop all services.                  ║"
echo "  ╚══════════════════════════════════════════════════════╝"
echo

# Keep script alive so trap fires on Ctrl+C
wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
