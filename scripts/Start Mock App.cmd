@echo off
cd /d "%~dp0\.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\run_dev.ps1" -Mock
if errorlevel 1 pause

