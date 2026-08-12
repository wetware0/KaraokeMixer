param(
    [ValidateSet('all', 'demucs', 'uvr', 'whisperx')]
    [string]$Worker = 'all'
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

# MANUAL STEP - do not run in CI. Creates the isolated worker environments
# described in README.md. The command is resumable and can target one worker:
#   .\backend\workers\setup-worker-venvs.ps1 -Worker whisperx

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$BootstrapPython = (Resolve-Path (Join-Path $RepoRoot 'backend/.venv/Scripts/python.exe')).Path

function Test-Selected([string]$Name) {
    return $Worker -eq 'all' -or $Worker -eq $Name
}

function Ensure-Venv([string]$Name) {
    $VenvRoot = Join-Path $RepoRoot "backend/.venv-$Name"
    $PythonPath = Join-Path $VenvRoot 'Scripts/python.exe'
    if (-not (Test-Path $PythonPath)) {
        & $BootstrapPython -m venv $VenvRoot
    }
    return $PythonPath
}

if (Test-Selected 'demucs') {
    # htdemucs / htdemucs_ft / mdx / mdx_extra_q / htdemucs_6s
    $DemucsPython = Ensure-Venv 'demucs'
    & $DemucsPython -m pip install --upgrade pip
    & $DemucsPython -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
    & $DemucsPython -m pip install demucs diffq
    & $DemucsPython -m pip check
    & $DemucsPython -c "import demucs, torch, torchaudio; assert torch.cuda.is_available(), 'CUDA is unavailable to Demucs'"
    Write-Host 'Demucs worker venv is ready.'
}

if (Test-Selected 'uvr') {
    # Pins match TrackSeparator/UVR_KARAOKE_ENSEMBLE_APP_INTEGRATION_GUIDE.md.
    $UvrPython = Ensure-Venv 'uvr'
    & $UvrPython -m pip install --upgrade pip
    & $UvrPython -m pip install torch==2.9.0 torchvision==0.24.0 torchaudio==2.9.0 --index-url https://download.pytorch.org/whl/cu128
    & $UvrPython -m pip install 'audio-separator[gpu]==0.44.5'
    & $UvrPython -m pip check
    & $UvrPython -c "from audio_separator.separator import Separator; import torch; assert torch.cuda.is_available(), 'CUDA is unavailable to UVR'"
    Write-Host 'UVR worker venv is ready.'
}

if (Test-Selected 'whisperx') {
    # WhisperX 3.8.6 requires the Torch 2.8 family. Install that CUDA stack
    # first, then let WhisperX resolve its complete dependency set. A prior
    # --no-deps install omitted pandas/pyannote/torchcodec/torchvision and
    # silently left the worker unusable; the final pip check guards this.
    $WhisperxPython = Ensure-Venv 'whisperx'
    & $WhisperxPython -m pip install --upgrade pip
    & $WhisperxPython -m pip install torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
    & $WhisperxPython -m pip install whisperx==3.8.6
    & $WhisperxPython -m pip check
    & $WhisperxPython -c "import whisperx, torch; assert torch.cuda.is_available(), 'CUDA is unavailable to WhisperX'"
    Write-Host 'WhisperX worker venv is ready.'
}

Write-Host "Requested worker setup completed: $Worker"
