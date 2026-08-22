param(
    [int]$Port = 8973
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}

if (-not (Select-String -Path ".env" -Pattern "^APP_ACCESS_TOKEN=\S+" -Quiet)) {
    $bytes = New-Object byte[] 24
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $rng.Dispose()
    $token = [Convert]::ToBase64String($bytes).Replace("+", "-").Replace("/", "_").TrimEnd("=")
    Add-Content ".env" "APP_ACCESS_TOKEN=$token"
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -3.10 -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt

$cloudflared = Join-Path $Root "tools\cloudflared.exe"
if (-not (Test-Path $cloudflared)) {
    New-Item -ItemType Directory -Force "tools" | Out-Null
    Invoke-WebRequest `
        -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" `
        -OutFile $cloudflared
}

New-Item -ItemType Directory -Force "logs" | Out-Null
$serverOut = Join-Path $Root "logs\public-server.out.log"
$serverErr = Join-Path $Root "logs\public-server.err.log"
$tunnelLog = Join-Path $Root "logs\cloudflared.log"

$server = $null
$localReady = $false
try {
    Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 3 | Out-Null
    $localReady = $true
} catch {}

if (-not $localReady) {
    $server = Start-Process `
        -FilePath ".\.venv\Scripts\python.exe" `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$Port") `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -RedirectStandardOutput $serverOut `
        -RedirectStandardError $serverErr `
        -PassThru

    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 3 | Out-Null
            $localReady = $true
            break
        } catch {
            if ($i -eq 29) { throw "Local server did not become ready. See $serverErr" }
        }
    }
}

if (Test-Path $tunnelLog) { Remove-Item $tunnelLog -Force }
$tunnel = Start-Process `
    -FilePath $cloudflared `
    -ArgumentList @("tunnel", "--url", "http://127.0.0.1:$Port", "--protocol", "http2", "--logfile", $tunnelLog, "--loglevel", "info") `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -PassThru

$publicUrl = $null
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $tunnelLog) {
        $text = Get-Content $tunnelLog -Raw
        $match = [regex]::Match($text, "https://[a-zA-Z0-9-]+\\.trycloudflare\\.com")
        if ($match.Success) {
            $publicUrl = $match.Value
            break
        }
    }
}

if ($publicUrl) {
    Write-Host "Public URL: $publicUrl"
    Write-Host "Login token is in: $Root\\.env"
    if ($server) { Write-Host "Server PID: $($server.Id)" } else { Write-Host "Server PID: already running on $Port" }
    Write-Host "Tunnel PID: $($tunnel.Id)"
} else {
    Write-Host "Tunnel started, but public URL was not found yet. Check: $tunnelLog"
    if ($server) { Write-Host "Server PID: $($server.Id)" } else { Write-Host "Server PID: already running on $Port" }
    Write-Host "Tunnel PID: $($tunnel.Id)"
}
