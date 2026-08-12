from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Callable

from ..output_paths import resolve_output_path
from ..pipeline import StageContext, StageResult, StageStatus, atomic_publish
from ..instrumental_provenance import UVR_KARAOKE_MODELS, build_instrumental_provenance
from ..workers.runner import WorkerResult, run_worker
from .audio_io import export_mp3, read_wav, write_wav
from .demucs import default_demucs_venv_python, separate_to_temp
from .mixing import apply_backing_vocal_mix, combine_stems
from .uvr import default_uvr_model_dir, default_uvr_venv_python, run_uvr_karaoke_ensemble

FAST_BACKING_VOCAL_MODES = ("stripped", "faint", "stereo_mix")
BACKING_VOCAL_MODES = FAST_BACKING_VOCAL_MODES + ("best",)


def instrumental_output_path(source_path: Path, options: dict) -> Path:
    return resolve_output_path(source_path, "instrumental", options)


class PreparedInstrumentalStage:
    """Full-prep marker used when Demucs already published the instrumental."""

    name = "karaoke_instrumental"

    def declared_outputs(self, ctx: StageContext) -> list[Path]:
        return [instrumental_output_path(ctx.source_path, ctx.options)]

    def run(self, ctx: StageContext) -> StageResult:
        destination = instrumental_output_path(ctx.source_path, ctx.options)
        if destination.is_file():
            return StageResult(status=StageStatus.SKIPPED, detail="instrumental created with the stem separation")
        return StageResult(status=StageStatus.FAILED, detail="combined separation did not publish the instrumental")


class KaraokeInstrumentalStage:
    """karaoke recipe's stage: publishes only `{name}.instrumental.mp3`. The
    three fast modes run demucs into a private temp dir and mix the stems in
    pure Python (Task 4's math); `best` (Task 7) replaces demucs entirely
    with the UVR karaoke ensemble."""

    name = "karaoke_instrumental"

    def __init__(
        self,
        model: str,
        device: str,
        backing_vocal_mode: str,
        demucs_venv_python: Path | None = None,
        demucs_runner: Callable[..., WorkerResult] = run_worker,
        uvr_venv_python: Path | None = None,
        uvr_model_dir: Path | None = None,
        uvr_runner: Callable[..., WorkerResult] = run_worker,
    ) -> None:
        if backing_vocal_mode not in BACKING_VOCAL_MODES:
            raise ValueError(f"Unsupported backing-vocal mode: {backing_vocal_mode}")
        self._model = model
        self._device = device
        self._backing_vocal_mode = backing_vocal_mode
        self._demucs_venv_python = demucs_venv_python or default_demucs_venv_python()
        self._demucs_runner = demucs_runner
        self._uvr_venv_python = uvr_venv_python or default_uvr_venv_python()
        self._uvr_model_dir = uvr_model_dir or default_uvr_model_dir()
        self._uvr_runner = uvr_runner

    def declared_outputs(self, ctx: StageContext) -> list[Path]:
        return [instrumental_output_path(ctx.source_path, ctx.options)]

    def run(self, ctx: StageContext) -> StageResult:
        if self._backing_vocal_mode == "best":
            return self._run_best(ctx)
        return self._run_fast(ctx)

    def _run_fast(self, ctx: StageContext) -> StageResult:
        stems = ["drums", "bass", "other", "vocals"]
        if self._model == "htdemucs_6s":
            stems += ["guitar", "piano"]
        destination = instrumental_output_path(ctx.source_path, ctx.options)
        with tempfile.TemporaryDirectory(prefix="karaoke-instrumental-") as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            try:
                wav_paths = separate_to_temp(
                    ctx.source_path, self._model, self._device, stems, temp_dir,
                    self._demucs_venv_python, ctx.worker_runner or self._demucs_runner,
                    cancel_event=ctx.cancel_event, on_progress=ctx.on_progress,
                )
            except RuntimeError as exc:
                return StageResult(status=StageStatus.FAILED, detail=str(exc))

            vocals, sample_rate = read_wav(wav_paths["vocals"])
            bed_stems = [read_wav(wav_paths[name])[0] for name in stems if name != "vocals"]
            instrumental_bed = combine_stems(*bed_stems)
            mixed = apply_backing_vocal_mix(instrumental_bed, vocals, self._backing_vocal_mode)

            mixed_wav = temp_dir / "instrumental.wav"
            write_wav(mixed_wav, mixed, sample_rate)
            mixed_mp3 = temp_dir / "instrumental.mp3"
            export_mp3(mixed_wav, mixed_mp3)
            atomic_publish(destination, lambda part, src=mixed_mp3: shutil.copyfile(src, part))
        return StageResult(
            status=StageStatus.COMPLETED,
            detail=f"wrote {destination.name}",
            output_provenance=[build_instrumental_provenance(
                ctx.options,
                destination,
                engine="demucs",
                model=self._model,
                backing_vocal_mode=self._backing_vocal_mode,
            )],
        )

    def _run_best(self, ctx: StageContext) -> StageResult:
        destination = instrumental_output_path(ctx.source_path, ctx.options)
        with tempfile.TemporaryDirectory(prefix="karaoke-best-") as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            try:
                outputs = run_uvr_karaoke_ensemble(
                    ctx.source_path, temp_dir, self._uvr_venv_python, self._uvr_model_dir, self._uvr_runner,
                    cancel_event=ctx.cancel_event, on_progress=ctx.on_progress,
                )
            except RuntimeError as exc:
                return StageResult(status=StageStatus.FAILED, detail=str(exc))
            atomic_publish(destination, lambda part, src=outputs["instrumental"]: shutil.copyfile(src, part))
        return StageResult(
            status=StageStatus.COMPLETED,
            detail=f"wrote {destination.name}",
            output_provenance=[build_instrumental_provenance(
                ctx.options,
                destination,
                engine="uvr_karaoke_ensemble",
                engine_version="audio-separator==0.44.5",
                model="karaoke",
                models=UVR_KARAOKE_MODELS,
                backing_vocal_mode=self._backing_vocal_mode,
            )],
        )
