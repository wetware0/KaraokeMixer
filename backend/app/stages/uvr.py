from __future__ import annotations

import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Callable

from ..output_paths import resolve_output_path
from ..pipeline import StageContext, StageResult, StageStatus, atomic_publish
from ..workers.runner import WorkerResult, run_worker

UVR_SCRIPT = Path(__file__).resolve().parent.parent.parent / "workers" / "uvr_worker.py"

# Bounds how long a single UVR ensemble invocation may run before the gpu
# lane force-terminates it - a hung/crashed worker would otherwise wedge the
# lane forever (see runner.run_worker's timeout_seconds handling).
SEPARATION_TIMEOUT_SECONDS = 7200.0


def default_uvr_venv_python() -> Path:
    base = Path(__file__).resolve().parent.parent.parent  # backend/
    return base / ".venv-uvr" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def default_uvr_model_dir() -> Path:
    return Path(os.environ.get("KARAOKE_MM_DATA_DIR", str(Path.home() / ".karaoke-media-manager"))) / "uvr-models"


def run_uvr_karaoke_ensemble(
    input_path: Path,
    output_dir: Path,
    venv_python: Path,
    model_dir: Path,
    runner: Callable[..., WorkerResult] = run_worker,
    *,
    cancel_event: threading.Event | None = None,
    on_progress: Callable[[dict], None] | None = None,
) -> dict[str, Path | None]:
    """Invoke the 3-model UVR karaoke ensemble (see
    TrackSeparator/UVR_KARAOKE_ENSEMBLE_APP_INTEGRATION_GUIDE.md), writing
    into `output_dir`. Returns {"instrumental": Path, "vocals": Path | None}.
    Raises RuntimeError(detail) on failure."""
    args = {"input_path": str(input_path), "model_dir": str(model_dir), "output_dir": str(output_dir)}
    result = runner(
        venv_python, UVR_SCRIPT, args,
        timeout_seconds=SEPARATION_TIMEOUT_SECONDS, cancel_event=cancel_event, on_progress=on_progress,
    )
    if result.status != "completed":
        raise RuntimeError(result.error_text or "UVR karaoke ensemble failed")
    payload = result.payload or {}
    return {
        "instrumental": Path(payload["instrumental"]),
        "vocals": Path(payload["vocals"]) if payload.get("vocals") else None,
    }


class UvrVocalSplitStage:
    """full_stems recipe's optional lead/backing split: runs the same
    3-model karaoke ensemble on the already-published `vocals` stem (not the
    original mix) - see this task's design note for why that maps
    Instrumental->backing_vocals and Vocals->lead_vocals."""

    name = "uvr_vocal_split"

    def __init__(
        self,
        venv_python: Path | None = None,
        model_dir: Path | None = None,
        runner: Callable[..., WorkerResult] = run_worker,
        enabled: bool | None = None,
    ) -> None:
        self._venv_python = venv_python or default_uvr_venv_python()
        self._model_dir = model_dir or default_uvr_model_dir()
        self._runner = runner
        self._enabled = enabled

    def _vocals_input_path(self, ctx: StageContext) -> Path:
        return resolve_output_path(ctx.source_path, "vocals", ctx.options)

    def declared_outputs(self, ctx: StageContext) -> list[Path]:
        return [
            resolve_output_path(ctx.source_path, "lead_vocals", ctx.options),
            resolve_output_path(ctx.source_path, "backing_vocals", ctx.options),
        ]

    def run(self, ctx: StageContext) -> StageResult:
        split_requested = self._enabled if self._enabled is not None else bool(ctx.options.get("split", False))
        if not split_requested:
            return StageResult(status=StageStatus.SKIPPED, detail="lead/backing split not requested")

        vocals_path = self._vocals_input_path(ctx)
        if not vocals_path.is_file():
            return StageResult(
                status=StageStatus.FAILED,
                detail=(
                    f"lead/backing split requires {vocals_path.name}, which was not found "
                    "(run demucs separation with the vocals stem first)"
                ),
            )

        with tempfile.TemporaryDirectory(prefix="uvr-split-") as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            try:
                outputs = run_uvr_karaoke_ensemble(
                    vocals_path, temp_dir, self._venv_python, self._model_dir, self._runner,
                    cancel_event=ctx.cancel_event, on_progress=ctx.on_progress,
                )
            except RuntimeError as exc:
                return StageResult(status=StageStatus.FAILED, detail=str(exc))
            if outputs["vocals"] is None:
                return StageResult(status=StageStatus.FAILED, detail="UVR ensemble did not produce a lead-vocal file")

            backing_destination = resolve_output_path(ctx.source_path, "backing_vocals", ctx.options)
            lead_destination = resolve_output_path(ctx.source_path, "lead_vocals", ctx.options)
            atomic_publish(backing_destination, lambda part, src=outputs["instrumental"]: shutil.copyfile(src, part))
            atomic_publish(lead_destination, lambda part, src=outputs["vocals"]: shutil.copyfile(src, part))
        return StageResult(status=StageStatus.COMPLETED, detail="wrote lead_vocals and backing_vocals")
