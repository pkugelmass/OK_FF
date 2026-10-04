@echo off
REM Gets the latest version of OK_FF from GitHub, then launches the app.
title OK_FF - updating
cd /d "%~dp0"
echo Checking for updates...
git pull --ff-only
if errorlevel 1 (
  echo.
  echo Could not update. Is Git installed, and is this folder a clone of the OK_FF repo?
  echo Starting the app anyway with the version you have.
  echo.
)
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python -m pip install -q -r requirements.txt
)
call start.bat
