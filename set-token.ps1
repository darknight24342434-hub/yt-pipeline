param(
    [string]$Token = "",
    [switch]$Restart,
    [int]$Port = 8973
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if ([string]::IsNullOrWhiteSpace($Token)) {
    $secure = Read-Host "New APP_ACCESS_TOKEN" -AsSecureString
    $Token = [System.Net.NetworkCredential]::new("", $secure).Password
}

if ([string]::IsNullOrWhiteSpace($Token)) {
    throw "APP_ACCESS_TOKEN cannot be empty."
}

if ($Token.Length -lt 8) {
    throw "APP_ACCESS_TOKEN is too short. Use at least 8 characters."
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}

$lines = Get-Content ".env" -ErrorAction SilentlyContinue
$updated = $false
$newLines = foreach ($line in $lines) {
    if ($line -match "^APP_ACCESS_TOKEN=") {
        $updated = $true
        "APP_ACCESS_TOKEN=$Token"
    } else {
        $line
    }
}

if (-not $updated) {
    $newLines += "APP_ACCESS_TOKEN=$Token"
}

[System.IO.File]::WriteAllLines((Join-Path $Root ".env"), $newLines, [System.Text.UTF8Encoding]::new($false))
Write-Host "APP_ACCESS_TOKEN updated in $Root\\.env"

if ($Restart) {
    Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq "python.exe" -and $_.CommandLine -like "*uvicorn*app.main*"
    } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

    Start-Sleep -Seconds 1
    New-Item -ItemType Directory -Force "logs" | Out-Null
    $out = Join-Path $Root "logs\server-$Port.out.log"
    $err = Join-Path $Root "logs\server-$Port.err.log"
    Start-Process `
        -FilePath ".\.venv\Scripts\python.exe" `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$Port") `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -RedirectStandardOutput $out `
        -RedirectStandardError $err | Out-Null

    Write-Host "Server restarted at http://127.0.0.1:$Port"
}

