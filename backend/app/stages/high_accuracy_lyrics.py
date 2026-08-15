from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path
from typing import Callable

from ..lrc import TimingState, classify_lrc_file, read_lrc_text
from ..lyrics.alignment import AlignmentDocument, WORD_TIMESTAMP_RE, _parse_timestamp
from ..lyrics.paths import resolve_lrc_path
from ..lyrics.provenance import (
    lyric_timing_details_path,
    lyric_timing_sidecar_path,
    write_lyric_timing_report,
)
from ..lyrics.vocal_reference import (
    build_vocal_reference_consensus,
    coarse_vocal_segments,
    set_line_markers_to_first_word,
)
from ..pipeline import StageContext, StageResult, StageStatus, atomic_publish
from ..workers.runner import WorkerResult, run_worker
from .align_lyrics import default_whisperx_venv_python
from .demucs import default_demucs_venv_python, separate_to_temp
from .improve_lyrics import (
    BACKUP_SUFFIX,
    _capture_files,
    _observed_words,
    _probe_duration,
    _restore_files,
    _run_alignment,
    _run_transcription,
    _validated_managed_path,
)


VOCAL_REFERENCE_MODEL = "htdemucs_ft"


def vocal_reference_cache_path(source_path: Path) -> Path:
    resolved = source_path.resolve()
    stat = resolved.stat()
    identity = f"{str(resolved).casefold()}\0{stat.st_size}\0{stat.st_mtime_ns}".encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()
    root = Path(tempfile.gettempdir()) / "karaoke-mixer" / "lyric-vocals"
    return root / f"{digest}.wav"


class PrepareVocalReferenceStage:
    """Create a temporary high-quality vocal stem for the next timing phase."""

    name = "isolate_timing_vocals"

    def __init__(
        self,
        *,
        enabled: bool,
        device: str,
        model: str = VOCAL_REFERENCE_MODEL,
        venv_python: Path | None = None,
        runner: Callable[..., WorkerResult] = run_worker,
    ) -> None:
        self._enabled = enabled
        self._device = device
        self._model = model
        self._venv_python = venv_python or default_demucs_venv_python()
        self._runner = runner
        self._cache_path: Path | None = None

    def declared_outputs(self, ctx: StageContext) -> list[Path]:
        return []

    def run(self, ctx: StageContext) -> StageResult:
        if not self._enabled:
            return StageResult(status=StageStatus.SKIPPED, detail="high-accuracy vocal isolation not selected")
        if not self._venv_python.exists():
            return StageResult(
                status=StageStatus.FAILED,
                detail="High Accuracy lyric timing requires the Demucs worker",
            )
        try:
            source_path = _validated_managed_path(ctx.source_path, ctx.options, must_exist=True)
            destination = vocal_reference_cache_path(source_path)
            self._cache_path = destination
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.is_file():
                return StageResult(status=StageStatus.COMPLETED, detail="reused prepared timing vocals")
            with tempfile.TemporaryDirectory(
                prefix="prepare-", dir=destination.parent,
            ) as raw_temp:
                outputs = separate_to_temp(
                    source_path,
                    self._model,
                    self._device,
                    ["vocals"],
                    Path(raw_temp),
                    self._venv_python,
                    ctx.worker_runner or self._runner,
                    shifts=0,
                    cancel_event=ctx.cancel_event,
                    on_progress=ctx.on_progress,
                )
                # The destination is a SHA-256 filename below the fixed
                # application temp root; the worker output is below raw_temp.
                # codeql[py/path-injection]
                atomic_publish(
                    destination,
                    lambda part: shutil.copyfile(outputs["vocals"], part),
                )
        except (OSError, ValueError, RuntimeError) as exc:
            return StageResult(status=StageStatus.FAILED, detail=str(exc))
        return StageResult(
            status=StageStatus.COMPLETED,
            detail=f"prepared isolated vocals with {self._model}",
        )

    def cleanup(self, _ctx: StageContext) -> None:
        if self._cache_path is not None:
            self._cache_path.unlink(missing_ok=True)


class HighAccuracyLyricsStage:
    """Retime lyrics from isolated-vocal transcription plus exact alignment."""

    name = "high_accuracy_lyrics"

    def __init__(
        self,
        *,
        device: str,
        asr_model: str = "medium",
        venv_python: Path | None = None,
        runner: Callable[..., WorkerResult] = run_worker,
    ) -> None:
        self._device = device
        self._asr_model = asr_model
        self._venv_python = venv_python or default_whisperx_venv_python()
        self._runner = runner

    def declared_outputs(self, ctx: StageContext) -> list[Path]:
        return []

    def run(self, ctx: StageContext) -> StageResult:
        if not self._venv_python.exists():
            return StageResult(
                status=StageStatus.FAILED,
                detail="High Accuracy lyric timing requires the WhisperX worker",
            )
        try:
            source_path = _validated_managed_path(ctx.source_path, ctx.options, must_exist=True)
            lrc_path = _validated_managed_path(
                resolve_lrc_path(source_path, ctx.options), ctx.options, must_exist=False,
            )
        except ValueError as exc:
            return StageResult(status=StageStatus.FAILED, detail=str(exc))
        if not lrc_path.is_file():
            return StageResult(status=StageStatus.SKIPPED, detail="no LRC to improve")
        if classify_lrc_file(lrc_path) != TimingState.ENHANCED:
            return StageResult(
                status=StageStatus.SKIPPED,
                detail="High Accuracy review currently requires enhanced per-word timing",
            )
        try:
            vocal_path = vocal_reference_cache_path(source_path)
        except OSError as exc:
            return StageResult(status=StageStatus.FAILED, detail=str(exc))
        if not vocal_path.is_file():
            return StageResult(
                status=StageStatus.FAILED,
                detail="prepared timing vocals were not found; retry the High Accuracy job",
            )

        try:
            initial_lrc_bytes = lrc_path.read_bytes()
            content = read_lrc_text(lrc_path)
        except OSError as exc:
            vocal_path.unlink(missing_ok=True)
            return StageResult(status=StageStatus.FAILED, detail=str(exc))
        document = AlignmentDocument.parse(content)
        current_starts = [_parse_timestamp(match) for match in WORD_TIMESTAMP_RE.finditer(content)]
        if len(current_starts) != len(document.tokens):
            vocal_path.unlink(missing_ok=True)
            return StageResult(
                status=StageStatus.FAILED,
                detail="enhanced LRC word markers do not match its lyric word count",
            )

        try:
            runner = ctx.worker_runner or self._runner
            transcript_result = _run_transcription(
                runner,
                self._venv_python,
                vocal_path,
                "en",
                self._device,
                self._asr_model,
                ctx,
            )
            if transcript_result.status != "completed":
                return StageResult(
                    status=StageStatus.FAILED,
                    detail=transcript_result.error_text or "isolated-vocal transcription failed",
                )
            transcript_words = _observed_words(transcript_result)
            coarse = coarse_vocal_segments(
                document, transcript_words, duration=_probe_duration(source_path),
            )
            forced_result = _run_alignment(
                runner,
                self._venv_python,
                vocal_path,
                coarse.segments,
                "en",
                self._device,
                ctx,
            )
            if forced_result.status != "completed":
                return StageResult(
                    status=StageStatus.FAILED,
                    detail=forced_result.error_text or "exact vocal lyric alignment failed",
                )
            outcome = build_vocal_reference_consensus(
                document,
                current_starts,
                transcript_words,
                _observed_words(forced_result),
            )
            set_line_markers_to_first_word(document, outcome.timing.selected_starts)
            improved = document.render_enhanced(outcome.timing.selected_starts)
        except (OSError, ValueError, KeyError) as exc:
            return StageResult(status=StageStatus.FAILED, detail=str(exc))
        finally:
            vocal_path.unlink(missing_ok=True)

        try:
            if lrc_path.read_bytes() != initial_lrc_bytes:
                return StageResult(
                    status=StageStatus.FAILED,
                    detail="Lyrics changed while High Accuracy analysis was running; the newer file was left untouched",
                )
        except OSError as exc:
            return StageResult(status=StageStatus.FAILED, detail=str(exc))

        timing = outcome.timing
        backup_path = lrc_path.with_name(f"{lrc_path.stem}{BACKUP_SUFFIX}")
        previous_files = _capture_files(
            lrc_path,
            backup_path,
            lyric_timing_sidecar_path(lrc_path),
            lyric_timing_details_path(lrc_path),
        )
        try:
            if timing.corrected_words or improved != content:
                if not backup_path.exists():
                    # lrc_path was validated below a configured media root.
                    # codeql[py/path-injection]
                    atomic_publish(backup_path, lambda part: part.write_bytes(initial_lrc_bytes))
                # lrc_path was validated below a configured media root.
                # codeql[py/path-injection]
                atomic_publish(
                    lrc_path,
                    lambda part: part.write_text(improved, encoding="utf-8", newline=""),
                )
            summary = write_lyric_timing_report(lrc_path, {
                "quality": timing.quality,
                "engine": "demucs+whisperx",
                "model": f"{VOCAL_REFERENCE_MODEL}+{self._asr_model}+wav2vec2-alignment",
                "method": "isolated_vocal_transcript_alignment_v1",
                "device": self._device,
                "words": len(document.tokens),
                "matched": outcome.asr_matched,
                "interpolated": len(document.tokens) - outcome.asr_matched,
                "coverage": outcome.asr_coverage,
                "median_confidence": timing.confidence_score / 100.0,
                "low_confidence_words": timing.review_words,
                "confidence_score": timing.confidence_score,
                "verified_words": timing.verified_words,
                "review_words": timing.review_words,
                "corrected_words": timing.corrected_words,
                "review_lines": timing.review_lines,
                "agreement_within_0_25": timing.agreement_within_0_25,
                "median_agreement_seconds": timing.median_agreement_seconds,
                "asr_matched": outcome.asr_matched,
                "asr_coverage": outcome.asr_coverage,
                "asr_corroborated_words": timing.asr_corroborated_words,
                "large_shift_words": timing.large_shift_words,
                "attribution": "automatic",
                "confirmed_by": None,
            }, timing.word_details)
        except (OSError, ValueError, TypeError) as exc:
            _restore_files(previous_files)
            return StageResult(
                status=StageStatus.FAILED,
                detail=f"could not publish High Accuracy lyrics; original LRC restored: {exc}",
            )
        mode = "preserved existing timing" if outcome.preserved_existing_track else "applied vocal timing"
        return StageResult(
            status=StageStatus.COMPLETED,
            detail=(
                f"{mode} · confidence {summary['confidence_score']}/100 · "
                f"corrected {timing.corrected_words} words · {timing.review_words} need review · "
                f"retained {outcome.isolated_outliers} isolated outliers"
            ),
        )
