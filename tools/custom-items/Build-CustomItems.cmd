@echo off
cd /d "%~dp0"
python build_custom_items.py %*
pause
