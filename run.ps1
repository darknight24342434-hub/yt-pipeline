param(
    [int]$Port = 8765,
    [string]$HostName = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py -3.10 -m venv .venv
    } else {
        python -m venv .venv
    }
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt

Write-Host "Starting YouTube Analyzer at http://${HostName}:$Port"
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host $HostName --port $Port --reload

