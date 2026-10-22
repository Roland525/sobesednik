@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist .venv\Scripts\python.exe (
  echo Run setup.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -c "import sys; assert (3,11) <= sys.version_info[:2] <= (3,12)" >nul 2>&1
if errorlevel 1 (
  echo Python environment is broken. Run setup.bat to repair it.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File tools\ensure_ollama.ps1
if errorlevel 1 (
  pause
  exit /b 1
)
.venv\Scripts\python.exe main.py %*
set "SEREGA_EXIT=%errorlevel%"
if "%SEREGA_EXIT%"=="1" echo Sobesednik stopped with an error. See logs\serega.log.
pause
exit /b %SEREGA_EXIT%
