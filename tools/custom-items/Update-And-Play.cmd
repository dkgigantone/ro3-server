@echo off
cd /d "%~dp0"
echo === Step 1: build the custom items from custom-items.csv ===
python build_custom_items.py
if errorlevel 1 (
  echo.
  echo The build failed - the game was NOT started. Read the message above.
  pause
  exit /b 1
)
echo.
echo === Step 2: open the live hat tuner (HatStudio) ===
start "" powershell -STA -ExecutionPolicy Bypass -File "C:\Games\Ragnarok3\ro3emu\HatStudio.ps1"
echo.
echo === Step 3: build the game hooks, start the server and the game ===
cd /d "%~dp0..\.."
call scripts\start.cmd -ClientDir C:\Games\Ragnarok3
