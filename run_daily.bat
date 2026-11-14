@echo off
cd /d "%~dp0"
if not exist logs mkdir logs
call .venv\Scripts\activate.bat
python agent.py run >> logs\daily.log 2>&1
