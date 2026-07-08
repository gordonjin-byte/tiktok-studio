@echo off
rem Manual launcher for TikTok Studio (Windows). Prefer scripts\install.ps1 for full setup.
cd /d "%~dp0"
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
