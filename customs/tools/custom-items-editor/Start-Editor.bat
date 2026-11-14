@echo off
cd /d "%~dp0"
echo Starting RO3 Eclipse Custom Items Editor... (close this window to stop it)
py -3 app.py
if errorlevel 1 (
  echo.
  echo Could not start. Install Python 3.10 or newer from python.org, then run this again.
  pause
)
