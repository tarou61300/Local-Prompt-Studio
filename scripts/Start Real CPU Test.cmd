@echo off
cd /d "%~dp0\.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\run_dev.ps1" -Portable
if errorlevel 1 pause
