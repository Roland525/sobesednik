$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$configured = & $python -c 'from bot.config import Config; print(int(bool(Config.load().discord_token)))'
if ($LASTEXITCODE -ne 0) { throw 'Configuration is invalid. See config.example.json.' }
if ($configured -eq '1') { Write-Host 'Discord token already configured.'; exit 0 }
Write-Host 'Create your bot at https://discord.com/developers/applications (Bot -> Reset Token).'
Write-Host 'Paste the BOT token below. Input is hidden. Press Enter to configure it later.'
$secureToken = Read-Host 'Discord bot token' -AsSecureString
$tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
try {
    $taskToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer).Trim()
    if ($taskToken) {
        if ($taskToken -notmatch '^[A-Za-z0-9_.-]{40,}$') { throw 'Token has unexpected characters; copy the bot token from Developer Portal.' }
        # Existing .env files are not overwritten, even if their token is empty.
        if (Test-Path -LiteralPath '.env') { throw 'An .env file already exists. Set DISCORD_TOKEN there; existing data was preserved.' }
        [IO.File]::WriteAllText((Join-Path $projectRoot '.env'), "DISCORD_TOKEN=$taskToken`r`n", (New-Object Text.UTF8Encoding($false)))
        Write-Host 'Token saved locally in .env.'
    }
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
    $taskToken = $null
}
