[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$BackendRoot = Join-Path $RepoRoot 'backend'
$Python = Join-Path $BackendRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    throw 'Karaoke Media Manager is not installed. Run .\install.ps1 first.'
}

$url = "http://127.0.0.1:$Port"
try {
    $health = Invoke-RestMethod "$url/api/health" -TimeoutSec 2
    if ($health.status -eq 'ok') {
        if (-not $NoBrowser) { Start-Process $url }
        Write-Host "Karaoke Media Manager is already running at $url"
        return
    }
} catch {
    # No healthy local instance; continue with startup.
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    throw "Port $Port is already in use by process $($listener.OwningProcess). Choose another port with -Port."
}

$dataRoot = if ($env:KARAOKE_MM_DATA_DIR) {
    $env:KARAOKE_MM_DATA_DIR
} else {
    Join-Path $env:USERPROFILE '.karaoke-media-manager'
}
$logRoot = Join-Path $dataRoot 'logs'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stdout = Join-Path $logRoot "backend-$stamp.out.log"
$stderr = Join-Path $logRoot "backend-$stamp.err.log"

$process = Start-Process `
    -FilePath $Python `
    -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', $Port `
    -WorkingDirectory $BackendRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -PassThru

$deadline = (Get-Date).AddSeconds(30)
do {
    if ($process.HasExited) {
        $detail = if (Test-Path $stderr) { Get-Content $stderr -Raw } else { 'No startup log was written.' }
        throw "Karaoke Media Manager failed to start.`n$detail"
    }
    try {
        $health = Invoke-RestMethod "$url/api/health" -TimeoutSec 2
    } catch {
        $health = $null
        Start-Sleep -Milliseconds 400
    }
} while ($health.status -ne 'ok' -and (Get-Date) -lt $deadline)

if ($health.status -ne 'ok') {
    Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
    throw "Karaoke Media Manager did not become healthy within 30 seconds. See $stderr"
}

$listener = Get-NetTCPConnection `
    -LocalAddress '127.0.0.1' `
    -LocalPort $Port `
    -State Listen `
    -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $listener) {
    Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
    throw "Karaoke Media Manager became healthy but its loopback listener could not be verified. See $stderr"
}

# A Microsoft Store Python launcher can briefly own the process returned by
# Start-Process and then hand execution to a child process. Record the process
# that actually owns the verified loopback listener so Stop-KaraokeMixer never
# targets a stale launcher PID.
$backendProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
if (-not $backendProcess -or $backendProcess.CommandLine -notlike "*uvicorn*app.main:app*--port*$Port*") {
    Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
    throw "Port $Port is not owned by the expected Karaoke Media Manager backend. See $stderr"
}
$backendStart = if ($backendProcess.CreationDate -is [datetime]) {
    $backendProcess.CreationDate.ToUniversalTime()
} else {
    [Management.ManagementDateTimeConverter]::ToDateTime($backendProcess.CreationDate).ToUniversalTime()
}

$pidFile = Join-Path $dataRoot 'backend.pid'
@{
    pid = [int]$listener.OwningProcess
    port = $Port
    started_at = $backendStart.ToString('o')
    repo_root = $RepoRoot
} | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding utf8
if (-not $NoBrowser) { Start-Process $url }
Write-Host "Karaoke Media Manager started at $url (PID $($listener.OwningProcess))."
Write-Host "Logs: $stdout and $stderr"
