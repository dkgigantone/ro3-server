@echo off
cd /d "%~dp0"
echo === Step 1: build the custom items from custom-items.csv ===
python build_custom_items.py --client C:\Games\Ragnarok3-Official
if errorlevel 1 (
  echo.
  echo The build failed - the game was NOT started. Read the message above.
  pause
  exit /b 1
)
echo.
echo === Step 2: build the game hooks, start the server and the game ===
echo (the hat tuner HatStudio is part of the game hook now, no separate window)
cd /d "%~dp0..\..\.."
call scripts\start.cmd -ClientDir C:\Games\Ragnarok3-Official
