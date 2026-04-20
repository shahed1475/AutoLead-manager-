@echo off
title AutoLead - Scraper Engine (Puppeteer)
cd /d "%~dp0scraper-engine"

where node >nul 2>&1
if errorlevel 1 (
    echo ERROR: Node.js not found. Install from https://nodejs.org
    pause & exit /b 1
)

if not exist node_modules (
    echo Installing Node.js dependencies...
    npm install
)

echo.
echo  Starting Puppeteer scraper engine on http://localhost:3001
echo  Keep this window open while using the app.
echo.
npx ts-node src/server.ts
pause
