[CmdletBinding()]
param(
    [ValidateSet('none', 'all', 'demucs', 'uvr', 'whisperx')]
    [string]$Worker = 'none',
    [switch]$InstallPrerequisites,
    [switch]$NoDesktopShortcut,
    [switch]$Start
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

function Update-ProcessPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user"
}

function Test-Command([string]$Name) {
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Install-WingetPackage([string]$Id) {
    if (-not (Test-Command 'winget')) {
        throw "winget is required for -InstallPrerequisites. Install App Installer from Microsoft Store, then retry."
    }
    & winget install --id $Id --exact --accept-package-agreements --accept-source-agreements
    Update-ProcessPath
}

function Resolve-Python311 {
    if (Test-Command 'py') {
        try {
            & py -3.11 -c "import sys; assert sys.version_info[:2] == (3, 11)"
            if ($LASTEXITCODE -eq 0) {
                return @{ Command = (Get-Command py).Source; Prefix = @('-3.11') }
            }
        } catch {}
    }
    if (Test-Command 'python') {
        try {
            & python -c "import sys; assert sys.version_info[:2] == (3, 11)"
            if ($LASTEXITCODE -eq 0) {
                return @{ Command = (Get-Command python).Source; Prefix = @() }
            }
        } catch {}
    }
    return $null
}

if ($env:OS -ne 'Windows_NT') {
    throw 'The bundled installer supports Windows 10/11 only.'
}

$requirements = @(
    @{ Command = 'node'; Package = 'OpenJS.NodeJS.LTS'; Label = 'Node.js LTS' },
    @{ Command = 'npm.cmd'; Package = 'OpenJS.NodeJS.LTS'; Label = 'npm' },
    @{ Command = 'ffmpeg'; Package = 'Gyan.FFmpeg'; Label = 'FFmpeg' },
    @{ Command = 'ffprobe'; Package = 'Gyan.FFmpeg'; Label = 'ffprobe' }
)

$bootstrapPython = Resolve-Python311
if (-not $bootstrapPython) {
    if ($InstallPrerequisites) {
        Write-Host 'Installing Python 3.11...'
        Install-WingetPackage 'Python.Python.3.11'
        $bootstrapPython = Resolve-Python311
    }
    if (-not $bootstrapPython) {
        throw 'Python 3.11 is missing. Install it first, or rerun with -InstallPrerequisites.'
    }
}

foreach ($requirement in $requirements) {
    if (-not (Test-Command $requirement.Command)) {
        if ($InstallPrerequisites) {
            Write-Host "Installing $($requirement.Label)..."
            Install-WingetPackage $requirement.Package
        } else {
            throw "$($requirement.Label) is missing. Install it first, or rerun with -InstallPrerequisites."
        }
    }
}

if (-not (Test-Command 'deno') -and $InstallPrerequisites) {
    Write-Host 'Installing Deno for YouTube import...'
    Install-WingetPackage 'DenoLand.Deno'
}

$nodeMajor = [int]((& node --version).TrimStart('v').Split('.')[0])
if ($nodeMajor -lt 20) {
    throw "Node.js 20 or newer is required; found $(& node --version)."
}

$backendRoot = Join-Path $RepoRoot 'backend'
$backendPython = Join-Path $backendRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $backendPython)) {
    Write-Host 'Creating the main Python environment...'
    $venvArgs = @($bootstrapPython.Prefix) + @('-m', 'venv', (Join-Path $backendRoot '.venv'))
    & $bootstrapPython.Command @venvArgs
}

Write-Host 'Installing backend dependencies...'
& $backendPython -m pip install --upgrade pip
& $backendPython -m pip install -r (Join-Path $backendRoot 'requirements.txt')
& $backendPython -m pip check

Write-Host 'Installing and building the frontend...'
$npm = (Get-Command npm.cmd).Source
Push-Location (Join-Path $RepoRoot 'frontend')
try {
    & $npm ci
    & $npm run build
} finally {
    Pop-Location
}

if ($Worker -ne 'none') {
    if (-not (Test-Command 'nvidia-smi')) {
        throw 'An NVIDIA driver and CUDA-capable GPU are required for the tested AI worker setup.'
    }
    Write-Host "Installing the $Worker AI worker environment. This can download tens of gigabytes..."
    & (Join-Path $backendRoot 'workers\setup-worker-venvs.ps1') -Worker $Worker
}

if (-not (Test-Command 'deno')) {
    Write-Warning 'Deno is not installed. The app will work, but current YouTube imports may fail. Install DenoLand.Deno with winget when needed.'
}

if (-not $NoDesktopShortcut) {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $shortcutPath = Join-Path $desktop 'Karaoke Media Manager.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = (Get-Command powershell.exe).Source
    $launcher = Join-Path $RepoRoot 'scripts\Start-KaraokeMixer.ps1'
    $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$launcher`""
    $shortcut.WorkingDirectory = $RepoRoot
    $shortcut.Description = 'Start Karaoke Media Manager'
    $shortcut.Save()
    Write-Host "Desktop shortcut created: $shortcutPath"
}

Write-Host ''
Write-Host 'Karaoke Media Manager is installed.'
Write-Host "Start it with: .\scripts\Start-KaraokeMixer.ps1"
Write-Host 'The application is local-only at http://127.0.0.1:8000'

if ($Start) {
    & (Join-Path $RepoRoot 'scripts\Start-KaraokeMixer.ps1')
}
