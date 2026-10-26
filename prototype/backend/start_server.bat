@echo off
title Theme Lab Backend Server
cd /d "%~dp0"
echo =======================================================
echo  Theme Lab Backend Server
echo  Access at: http://127.0.0.1:8766
echo  Press Ctrl+C to stop the server
echo =======================================================
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" app.py
) else (
    python app.py
)
pause
