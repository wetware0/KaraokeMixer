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

& (Join-Path $PSScriptRoot 'scripts\Install-KaraokeMixer.ps1') `
    -Worker $Worker `
    -InstallPrerequisites:$InstallPrerequisites `
    -NoDesktopShortcut:$NoDesktopShortcut `
    -Start:$Start
