@echo off
cd /d "%~dp0"
set TRIM_UI_OPEN_BROWSER=1
python app\server.py
