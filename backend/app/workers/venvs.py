from __future__ import annotations

import os
from pathlib import Path

WORKER_VENV_DIRS = {"demucs": ".venv-demucs", "uvr": ".venv-uvr", "whisperx": ".venv-whisperx"}


def venv_python_path(base_dir: Path, worker: str) -> Path:
    venv_dir = base_dir / WORKER_VENV_DIRS[worker]
    return venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def detect_worker_venvs(base_dir: Path) -> dict[str, bool]:
    """True per worker name when that worker's isolated venv's python
    executable exists on disk. Presence-only check - never imports torch or
    otherwise probes the venv's installed package set, so this is always
    safe to call from a request handler."""
    return {worker: venv_python_path(base_dir, worker).is_file() for worker in WORKER_VENV_DIRS}
