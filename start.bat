@echo off
setlocal EnableDelayedExpansion
title AutoLead Marketing Engine v2.0
color 0A

:: ── Always run from the directory where this script lives ─────────────────────
cd /d "%~dp0"

echo.
echo  ╔══════════════════════════════════════════════════════╗
echo  ║         AutoLead Marketing Engine  v2.0             ║
echo  ║     AI-Powered Lead Generation ^& Outreach           ║
echo  ╚══════════════════════════════════════════════════════╝
echo.

:: ═════════════════════════════════════════════════════════════════════════════
:: STEP 1 — Check required runtimes
:: ═════════════════════════════════════════════════════════════════════════════

echo  [1/6] Checking runtime dependencies...

:: Python
python --version >nul 2>&1
if errorlevel 1 (
    color 0C
    echo.
    echo  [ERROR] Python not found.
    echo          Install Python 3.11+ from https://python.org/downloads/
    echo          Make sure to check "Add Python to PATH" during install.
    echo.
    pause & exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo         Python !PY_VER! found.

:: Node.js
node --version >nul 2>&1
if errorlevel 1 (
    color 0C
    echo.
    echo  [ERROR] Node.js not found.
    echo          Install Node.js 18+ from https://nodejs.org/
    echo.
    pause & exit /b 1
)
for /f %%v in ('node --version 2^>^&1') do set NODE_VER=%%v
echo         Node.js !NODE_VER! found.

:: Ollama (soft check — warn but don't abort)
ollama --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [WARN]  Ollama not found in PATH.
    echo          AI message generation requires Ollama running locally.
    echo          Download from: https://ollama.com/download
    echo          Then run:  ollama pull llama3
    echo.
    echo          Continuing startup — you can install Ollama later.
    echo.
    timeout /t 3 /nobreak >nul
) else (
    for /f "tokens=*" %%v in ('ollama --version 2^>^&1') do set OLLAMA_VER=%%v
    echo         Ollama found.
)

echo.

:: ═════════════════════════════════════════════════════════════════════════════
:: STEP 2 — Virtual environment
:: ═════════════════════════════════════════════════════════════════════════════

echo  [2/6] Setting up Python virtual environment...

if not exist "venv\Scripts\activate.bat" (
    echo         Creating virtual environment in .\venv\ ...
    python -m venv venv
    if errorlevel 1 (
        color 0C
        echo.
        echo  [ERROR] Failed to create virtual environment.
        echo          Try: pip install virtualenv
        echo.
        pause & exit /b 1
    )
    echo         Virtual environment created.
) else (
    echo         Virtual environment already exists.
)

:: Activate venv for this session
call venv\Scripts\activate.bat
if errorlevel 1 (
    color 0C
    echo.
    echo  [ERROR] Failed to activate virtual environment.
    echo          Try deleting the venv\ folder and re-running start.bat.
    echo.
    pause & exit /b 1
)
echo         Virtual environment activated.
echo.

:: ═════════════════════════════════════════════════════════════════════════════
:: STEP 3 — Backend dependencies
:: ═════════════════════════════════════════════════════════════════════════════

echo  [3/6] Checking backend dependencies...

:: Use uvicorn.exe presence as a fast proxy — if it's there, core deps are installed.
:: Still run pip to catch new packages added to requirements.txt since last install.
if not exist "venv\Scripts\uvicorn.exe" (
    echo         First run — installing all packages (this takes 2-3 minutes)...
    pip install -r backend\requirements.txt
    if errorlevel 1 (
        color 0C
        echo.
        echo  [ERROR] pip install failed. Check your internet connection.
        echo          If a specific package fails, try:
        echo            pip install ^<package^> --upgrade
        echo.
        pause & exit /b 1
    )
    echo.
    echo         Installing browser automation drivers...
    python -m playwright install chromium --quiet 2>nul
    python -m playwright install-deps chromium --quiet 2>nul
) else (
    :: Quick silent update-check — pip cache makes this near-instant
    pip install -r backend\requirements.txt -q --no-warn-script-location 2>nul
    echo         Backend packages up to date.
)
echo.

:: ═════════════════════════════════════════════════════════════════════════════
:: STEP 4 — First-run configuration
:: ═════════════════════════════════════════════════════════════════════════════

echo  [4/6] Checking configuration files...

if not exist "backend\data" (
    mkdir backend\data
    echo         Created backend\data\ directory.
)

if not exist "backend\.env" (
    if exist "backend\.env.example" (
        copy backend\.env.example backend\.env >nul
        echo.
        color 0E
        echo  ╔══════════════════════════════════════════════════════╗
        echo  ║  FIRST-TIME SETUP REQUIRED                          ║
        echo  ║                                                      ║
        echo  ║  1. Edit  backend\.env  with your Gmail credentials  ║
        echo  ║  2. Edit  backend\company_dna.txt  with your info    ║
        echo  ║  3. Run   ollama pull llama3   in a terminal         ║
        echo  ║                                                      ║
        echo  ║  See README.md for step-by-step instructions.        ║
        echo  ╚══════════════════════════════════════════════════════╝
        color 0A
        echo.
        echo  Opening configuration files for editing...
        timeout /t 2 /nobreak >nul
        start notepad "backend\.env"
        start notepad "backend\company_dna.txt"
        echo.
        echo  Press any key after you have saved your configuration...
        pause >nul
    ) else (
        echo  [WARN]  backend\.env.example not found. Skipping .env creation.
    )
) else (
    echo         .env configuration found.
)

:: ═════════════════════════════════════════════════════════════════════════════
:: STEP 5 — Frontend dependencies
:: ═════════════════════════════════════════════════════════════════════════════

echo  [5/6] Checking frontend dependencies...

if not exist "frontend\node_modules" (
    echo         Installing Node.js packages (first run, ~30 seconds)...
    cd frontend
    call npm install --silent
    if errorlevel 1 (
        cd ..
        color 0C
        echo.
        echo  [ERROR] npm install failed.
        echo          Try running manually:  cd frontend ^&^& npm install
        echo.
        pause & exit /b 1
    )
    cd ..
    echo         Frontend packages installed.
) else (
    echo         Node.js packages already installed.
)
echo.

:: ═════════════════════════════════════════════════════════════════════════════
:: STEP 6 — Launch services
:: ═════════════════════════════════════════════════════════════════════════════

echo  [6/6] Starting services...
echo.

:: ── Backend (minimised — logs visible in taskbar window) ──────────────────────
echo         Launching backend  → http://localhost:8000
start "AutoLead-Backend" /min cmd /k ^"^
cd /d "%~dp0" ^&^& ^
call venv\Scripts\activate.bat ^&^& ^
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload^"

:: Give uvicorn a moment to bind the port before frontend proxy tries to connect
timeout /t 3 /nobreak >nul

:: ── Frontend ───────────────────────────────────────────────────────────────────
echo         Launching frontend → http://localhost:5173
start "AutoLead-Frontend" cmd /k "cd /d "%~dp0frontend" && npm run dev"

:: ── Open browser ──────────────────────────────────────────────────────────────
timeout /t 4 /nobreak >nul
echo.
echo         Opening browser...
start http://localhost:5173

:: ── Done ──────────────────────────────────────────────────────────────────────
echo.
echo  ╔══════════════════════════════════════════════════════╗
echo  ║  AutoLead is running!                               ║
echo  ║                                                      ║
echo  ║  Frontend  →  http://localhost:5173                  ║
echo  ║  Backend   →  http://localhost:8000                  ║
echo  ║  API Docs  →  http://localhost:8000/docs             ║
echo  ║                                                      ║
echo  ║  To stop: close the Backend and Frontend windows.    ║
echo  ╚══════════════════════════════════════════════════════╝
echo.
echo  This window can be closed safely.
echo.
pause
endlocal
