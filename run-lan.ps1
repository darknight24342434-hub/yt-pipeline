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

$ip = (Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.PrefixOrigin -ne "WellKnown" } |
    Select-Object -First 1 -ExpandProperty IPAddress)

Write-Host "LAN URL: http://${ip}:$Port"
Write-Host "Token is in: $Root\\.env"
& ".\run.ps1" -Port $Port -HostName "0.0.0.0"
