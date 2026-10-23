$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
try {
    $null = Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 3
    exit 0
} catch { }
$localExe = Join-Path $PSScriptRoot 'ollama\ollama.exe'
if (-not (Test-Path -LiteralPath $localExe)) {
    $foundCommand = Get-Command ollama -ErrorAction SilentlyContinue
    if ($foundCommand) { $localExe = $foundCommand.Source }
    else { throw 'Ollama missing. Run setup.bat first.' }
}
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_MODELS = Join-Path $projectRoot 'models\ollama'
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_MAX_LOADED_MODELS = '1'
$env:OLLAMA_FLASH_ATTENTION = '1'
New-Item -ItemType Directory -Force (Join-Path $projectRoot 'logs') | Out-Null
Start-Process -FilePath $localExe -ArgumentList 'serve' -WindowStyle Hidden -WorkingDirectory $projectRoot `
    -RedirectStandardOutput (Join-Path $projectRoot 'logs\ollama-stdout.log') `
    -RedirectStandardError (Join-Path $projectRoot 'logs\ollama-stderr.log')
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $null = Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2
        Write-Host 'Local Ollama ready.'
        exit 0
    } catch { }
}
throw 'Ollama did not start. See logs\ollama-stderr.log.'
