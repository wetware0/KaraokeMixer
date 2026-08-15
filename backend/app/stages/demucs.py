from __future__ import annotations

import shutil
import tempfile
import threading
from pathlib import Path
from typing import Callable

from ..output_paths import resolve_output_path
from ..pipeline import StageContext, StageResult, StageStatus, atomic_publish
from ..instrumental_provenance import build_instrumental_provenance
from ..workers.runner import WorkerResult, run_worker
from .audio_io import export_mp3, read_wav, write_wav
from .mixing import apply_backing_vocal_mix, combine_stems

DEMUCS_MODELS = ("htdemucs", "htdemucs_ft", "mdx", "mdx_extra_q", "htdemucs_6s")

FOUR_STEM_INDEX = {"drums": 0, "bass": 1, "other": 2, "vocals": 3}
SIX_STEM_INDEX = {**FOUR_STEM_INDEX, "guitar": 4, "piano": 5}

DEMUCS_SCRIPT = Path(__file__).resolve().parent.parent.parent / "workers" / "demucs_worker.py"

# Bounds how long a single demucs invocation may run before the gpu lane
# force-terminates it - a hung/crashed worker would otherwise wedge the lane
# forever (see runner.run_worker's timeout_seconds handling).
SEPARATION_TIMEOUT_SECONDS = 7200.0


def stem_index_map(model: str) -> dict[str, int]:
    if model not in DEMUCS_MODELS:
        raise ValueError(f"Unknown demucs model: {model}")
    return dict(SIX_STEM_INDEX if model == "htdemucs_6s" else FOUR_STEM_INDEX)


def default_demucs_venv_python() -> Path:
    import os

    base = Path(__file__).resolve().parent.parent.parent  # backend/
    return base / ".venv-demucs" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def separate_to_temp(
    source_path: Path,
    model: str,
    device: str,
    stems: list[str],
    temp_dir: Path,
    venv_python: Path,
    runner: Callable[..., WorkerResult] = run_worker,
    *,
    shifts: int | None = None,
    cancel_event: threading.Event | None = None,
    on_progress: Callable[[dict], None] | None = None,
) -> dict[str, Path]:
    """Invoke the demucs worker, writing raw stem WAVs into `temp_dir`.
    Returns {stem: path}. Raises RuntimeError(detail) on worker failure -
    the caller decides how that becomes a StageResult."""
    index_map = stem_index_map(model)
    unknown = [stem for stem in stems if stem not in index_map]
    if unknown:
        raise ValueError(f"Model {model!r} does not produce stems: {unknown}")

    temp_paths = {stem: temp_dir / f"{stem}.wav" for stem in stems}
    args = {
        "input_path": str(source_path),
        "model": model,
        "device": device,
        "stem_indices": {stem: index_map[stem] for stem in stems},
        "output_paths": {stem: str(path) for stem, path in temp_paths.items()},
    }
    if shifts is not None:
        args["shifts"] = shifts
    result = runner(
        venv_python, DEMUCS_SCRIPT, args,
        timeout_seconds=SEPARATION_TIMEOUT_SECONDS, cancel_event=cancel_event, on_progress=on_progress,
    )
    if result.status != "completed":
        raise RuntimeError(result.error_text or "demucs separation failed")
    return temp_paths


class DemucsSeparateStage:
    """full_stems recipe's stage: writes each requested stem as its own
    final `{name}.{part}.mp3` file, resolved via
    `output_paths.resolve_output_path` so the job's output_mode
    (beside/mirror) is honored."""

    name = "demucs_separate"

    def __init__(
        self,
        model: str,
        device: str,
        stems: list[str] | None = None,
        venv_python: Path | None = None,
        runner: Callable[..., WorkerResult] = run_worker,
        instrumental_mode: str | None = None,
    ) -> None:
        self._model = model
        self._device = device
        self._stems = stems or list(stem_index_map(model))
        self._venv_python = venv_python or default_demucs_venv_python()
        self._runner = runner
        self._instrumental_mode = instrumental_mode

    def declared_outputs(self, ctx: StageContext) -> list[Path]:
        outputs = [resolve_output_path(ctx.source_path, stem, ctx.options) for stem in self._stems]
        if self._instrumental_mode is not None:
            outputs.append(resolve_output_path(ctx.source_path, "instrumental", ctx.options))
        return outputs

    def run(self, ctx: StageContext) -> StageResult:
        with tempfile.TemporaryDirectory(prefix="demucs-job-") as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            try:
                wav_paths = separate_to_temp(
                    ctx.source_path, self._model, self._device, self._stems, temp_dir,
                    self._venv_python, ctx.worker_runner or self._runner,
                    cancel_event=ctx.cancel_event, on_progress=ctx.on_progress,
                )
            except RuntimeError as exc:
                return StageResult(status=StageStatus.FAILED, detail=str(exc))
            if self._instrumental_mode is not None:
                vocals, sample_rate = read_wav(wav_paths["vocals"])
                bed_stems = [read_wav(wav_paths[name])[0] for name in self._stems if name != "vocals"]
                instrumental_bed = combine_stems(*bed_stems)
                mixed = apply_backing_vocal_mix(instrumental_bed, vocals, self._instrumental_mode)
                mixed_wav = temp_dir / "instrumental.wav"
                mixed_mp3 = temp_dir / "instrumental.mp3"
                write_wav(mixed_wav, mixed, sample_rate)
                export_mp3(mixed_wav, mixed_mp3)
                destination = resolve_output_path(ctx.source_path, "instrumental", ctx.options)
                atomic_publish(destination, lambda part, src=mixed_mp3: shutil.copyfile(src, part))
            for stem, wav_path in wav_paths.items():
                mp3_path = wav_path.with_suffix(".mp3")
                export_mp3(wav_path, mp3_path)
                destination = resolve_output_path(ctx.source_path, stem, ctx.options)
                atomic_publish(destination, lambda part, src=mp3_path: shutil.copyfile(src, part))
        detail = f"wrote stems: {', '.join(self._stems)}"
        if self._instrumental_mode is not None:
            detail += " and instrumental from the same separation"
        provenance = []
        if self._instrumental_mode is not None:
            provenance.append(build_instrumental_provenance(
                ctx.options,
                resolve_output_path(ctx.source_path, "instrumental", ctx.options),
                engine="demucs",
                model=self._model,
                backing_vocal_mode=self._instrumental_mode,
            ))
        return StageResult(status=StageStatus.COMPLETED, detail=detail, output_provenance=provenance)
