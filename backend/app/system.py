from __future__ import annotations

import shutil
import subprocess


def probe_device() -> str:
    """Return 'cuda' if an NVIDIA GPU driver is present, else 'cpu'.

    Checks for the nvidia-smi executable rather than importing torch, so the
    main backend venv never needs a torch dependency; GPU work happens in
    the isolated worker venvs described in the design spec.
    """
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        return "cpu"
    try:
        result = subprocess.run([nvidia_smi], capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return "cpu"
    return "cuda" if result.returncode == 0 else "cpu"
