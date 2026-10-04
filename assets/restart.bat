@echo off
REM Used by the in-app "Update app" button. Waits for the old server to exit, then updates and relaunches.
REM Output is also written to data\restart.log so a failed restart can be diagnosed.
title OK_FF - updating
cd /d "%~dp0.."
if not exist data mkdir data
echo [%date% %time%] restart requested > data\restart.log
timeout /t 4 /nobreak >nul
echo [%date% %time%] running update.bat >> data\restart.log
call "%~dp0..\update.bat" --no-browser >> data\restart.log 2>&1
echo [%date% %time%] update.bat returned %errorlevel% >> data\restart.log
