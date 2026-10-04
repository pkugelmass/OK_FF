@echo off
REM Launches OK_FF (Fantasy Football HQ) in your browser.
REM First run: creates the Python environment and a Desktop shortcut.
REM Optional: "start.bat --no-browser" starts without opening a new browser tab (used by the in-app Update button).
title OK_FF - Fantasy Football HQ
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
  echo First-time setup: creating Python environment...
  python -m venv .venv
  if errorlevel 1 (
    echo.
    echo Python was not found. Install it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" on the first installer screen, then run this again.
    pause
    exit /b 1
  )
  echo Installing packages. This takes 2-5 minutes on a new computer; you'll see progress below.
  .venv\Scripts\python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Package install failed. Check your internet connection, then run this again.
    pause
    exit /b 1
  )
)

REM Desktop shortcut (points to update.bat so it always pulls the latest version first)
if not exist "%USERPROFILE%\Desktop\Fantasy Football HQ.lnk" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0assets\make_shortcut.ps1"
)

set "PORT=%FFPORT%"
if "%PORT%"=="" set "PORT=8501"
set "FLAGS="
if /i "%~1"=="--no-browser" set "FLAGS=--server.headless true"

echo Starting the app... (close this window to stop it)
.venv\Scripts\python -m streamlit run app.py --server.port %PORT% --browser.gatherUsageStats false %FLAGS%

REM The in-app Update button leaves this marker so the old window closes itself instead of waiting.
if exist data\.restart (
  del data\.restart
  exit
)
pause
