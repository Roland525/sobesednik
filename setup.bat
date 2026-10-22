@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File tools\setup.ps1
if errorlevel 1 echo Setup failed. Read the error above.
pause
