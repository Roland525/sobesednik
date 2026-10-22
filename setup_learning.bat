@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
powershell -NoProfile -ExecutionPolicy Bypass -File tools\setup_learning.ps1
if errorlevel 1 echo Learning setup failed. Existing voice settings were preserved.
pause
