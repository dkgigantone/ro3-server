@echo off
rem Packs the custom files of a client into ro3emu\eclipse.dat. Usage: Pack-Custom.cmd C:\Games\EclipseClient
if "%~1"=="" (echo Usage: Pack-Custom.cmd ^<client folder^> & exit /b 1)
python "%~dp0pack_custom.py" "%~1"
pause
