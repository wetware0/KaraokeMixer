[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$dataRoot = if ($env:KARAOKE_MM_DATA_DIR) {
    $env:KARAOKE_MM_DATA_DIR
} else {
    Join-Path $env:USERPROFILE '.karaoke-media-manager'
}
$pidFile = Join-Path $dataRoot 'backend.pid'
if (-not (Test-Path $pidFile)) {
    Write-Host 'No launcher-managed Karaoke Media Manager process is recorded.'
    return
}

$record = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
$processId = [int]$record.pid
$port = [int]$record.port
if ($record.repo_root -ne $RepoRoot) {
    throw 'Refusing to stop a process recorded by a different installation.'
}
if ($port -lt 1024 -or $port -gt 65535) {
    throw 'Refusing to stop a process because the launcher record has an invalid port.'
}
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
if (-not $process) {
    Remove-Item -LiteralPath $pidFile
    Write-Host 'The recorded process is no longer running.'
    return
}

$recordedStart = if ($record.started_at -is [datetime]) {
    # PowerShell 7 automatically converts ISO JSON dates to DateTime values.
    $record.started_at.ToUniversalTime()
} else {
    # Windows PowerShell 5.1 leaves the same JSON value as a string.
    [DateTimeOffset]::Parse(
        $record.started_at,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::RoundtripKind
    ).UtcDateTime
}
$actualStart = if ($process.CreationDate -is [datetime]) {
    $process.CreationDate.ToUniversalTime()
} else {
    [Management.ManagementDateTimeConverter]::ToDateTime($process.CreationDate).ToUniversalTime()
}
$listener = Get-NetTCPConnection `
    -LocalAddress '127.0.0.1' `
    -LocalPort $port `
    -State Listen `
    -ErrorAction SilentlyContinue |
    Where-Object { $_.OwningProcess -eq $processId } |
    Select-Object -First 1
if (
    ($process.CommandLine -notlike '*uvicorn*app.main:app*') `
    -or ($process.CommandLine -notlike "*--port*$port*") `
    -or (-not $listener) `
    -or ([math]::Abs(($actualStart - $recordedStart).TotalSeconds) -gt 5)
) {
    throw "Refusing to stop PID $processId because it is not this installation's verified backend."
}

Stop-Process -Id $processId
Wait-Process -Id $processId -Timeout 20 -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $pidFile
Write-Host 'Karaoke Media Manager stopped.'
