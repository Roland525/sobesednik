$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$bundledPython = Join-Path (Split-Path -Parent $projectRoot) 'sobesednik\Python311\python.exe'
$pythonCommand = $null
if (Test-Path -LiteralPath $bundledPython) { $pythonCommand = $bundledPython }
if (-not $pythonCommand) {
    foreach ($candidate in @('python', 'python3')) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($found) {
            $version = & $found.Source -c 'import sys; print(str(sys.version_info.major)+chr(46)+str(sys.version_info.minor))'
            if ($version -in @('3.11','3.12')) { $pythonCommand = $found.Source; break }
        }
    }
}
if (-not $pythonCommand) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        $resolved = & $launcher.Source -3.11 -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $resolved) { $pythonCommand = $resolved }
    }
}
if (-not $pythonCommand) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host 'Installing Python 3.11 for this user...'
        & $winget.Source install --id Python.Python.3.11 --exact --source winget --scope user --accept-package-agreements --accept-source-agreements
        $installedPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
        if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $installedPython)) { $pythonCommand = $installedPython }
    }
}
if (-not $pythonCommand) { throw 'Install Python 3.11 from https://www.python.org/downloads/windows/ then run setup.bat again. Python 3.13/3.14 are unsupported.' }
Write-Host 'Creating/repairing project virtual environment...'
& $pythonCommand -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Failed to create .venv.' }
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $venvPython tools\download_models.py --ollama
if ($LASTEXITCODE -ne 0) { throw 'Model download failed.' }
if (-not (Test-Path -LiteralPath 'config.json')) { Copy-Item -LiteralPath config.example.json -Destination config.json }
& powershell -NoProfile -ExecutionPolicy Bypass -File tools\configure_token.ps1
if ($LASTEXITCODE -ne 0) { throw 'Token setup failed. Existing files were preserved.' }
& powershell -NoProfile -ExecutionPolicy Bypass -File tools\ensure_ollama.ps1
if ($LASTEXITCODE -ne 0) { throw 'Local Ollama startup failed.' }
$modelName = & $venvPython -c 'from bot.config import Config; print(Config.load().ollama_model)'
& .\tools\ollama\ollama.exe pull $modelName
if ($LASTEXITCODE -ne 0) { throw 'LLM download failed.' }
& $venvPython main.py --check
if ($LASTEXITCODE -ne 0) { throw 'Preflight checks failed.' }
Write-Host 'Setup complete. Configure DISCORD_TOKEN in .env if needed, then run start.bat.'
