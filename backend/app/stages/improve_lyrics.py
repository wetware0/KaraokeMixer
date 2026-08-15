from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from ..lrc import TimingState, classify_lrc_file, read_lrc_text
from ..lyrics.alignment import (
    AlignmentDocument,
    ObservedWord,
    WORD_TIMESTAMP_RE,
    _parse_timestamp,
    assign_word_timings,
    line_timed_segments,
)
from ..lyrics.confidence import build_dual_audio_consensus
from ..lyrics.paths import resolve_lrc_path
from ..lyrics.provenance import (
    lyric_timing_details_path,
    lyric_timing_sidecar_path,
    write_lyric_timing_report,
)
from ..pipeline import StageContext, StageResult, StageStatus, atomic_publish
from ..scanner import locate_output
from ..workers.runner import WorkerResult, run_worker
from .align_lyrics import ALIGN_TIMEOUT_SECONDS, WHISPERX_SCRIPT, default_whisperx_venv_python


MIN_EVIDENCE_COVERAGE = 0.80
BACKUP_SUFFIX = ".before-confidence.lrc"


class ImproveLyricsStage:
    """Improve enhanced timing using agreement across two acoustic views.

    The canonical LRC is changed only for words independently supported by
    line-constrained alignment of the original mix and original-minus-
    instrumental vocal residual. Disputed words remain untouched and are
    recorded for focused editor review.
    """

    name = "improve_lyrics"

    def __init__(
        self,
        *,
        device: str = "cpu",
        language: str = "en",
        venv_python: Path | None = None,
        runner: Callable[..., WorkerResult] = run_worker,
        residual_builder: Callable[[Path, Path, Path], None] | None = None,
    ) -> None:
        self._device = device
        self._language = language
        self._venv_python = venv_python or default_whisperx_venv_python()
        self._runner = runner
        self._residual_builder = residual_builder or _build_vocal_residual

    def declared_outputs(self, ctx: StageContext) -> list[Path]:
        return []

    def run(self, ctx: StageContext) -> StageResult:
        if not self._venv_python.exists():
            return StageResult(
                status=StageStatus.FAILED,
                detail="Improving lyric confidence requires the WhisperX worker",
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
                detail="confidence improvement currently requires enhanced per-word timing",
            )
        instrumental = _locate_instrumental(source_path, ctx.options)
        if instrumental is None:
            return StageResult(
                status=StageStatus.SKIPPED,
                detail="a karaoke instrumental is required to build the vocal residual",
            )
        try:
            instrumental = _validated_managed_path(instrumental, ctx.options, must_exist=True)
        except ValueError as exc:
            return StageResult(status=StageStatus.FAILED, detail=str(exc))

        try:
            initial_lrc_bytes = lrc_path.read_bytes()
            content = read_lrc_text(lrc_path)
        except OSError as exc:
            return StageResult(status=StageStatus.FAILED, detail=str(exc))
        document = AlignmentDocument.parse(content)
        current_starts = [_parse_timestamp(match) for match in WORD_TIMESTAMP_RE.finditer(content)]
        if len(current_starts) != len(document.tokens):
            return StageResult(
                status=StageStatus.FAILED,
                detail="enhanced LRC word markers do not match its lyric word count",
            )
        try:
            duration = _probe_duration(source_path)
            segments = line_timed_segments(document, duration=duration)
            runner = ctx.worker_runner or self._runner
            original_result = _run_alignment(
                runner, self._venv_python, source_path, segments, self._language,
                self._device, ctx,
            )
            if original_result.status != "completed":
                return StageResult(
                    status=StageStatus.FAILED,
                    detail=original_result.error_text or "original-mix lyric alignment failed",
                )
            with tempfile.TemporaryDirectory(prefix="karaoke-lyric-confidence-") as raw_temp:
                residual_path = Path(raw_temp) / "vocal-residual.wav"
                self._residual_builder(source_path, instrumental, residual_path)
                residual_result = _run_alignment(
                    runner, self._venv_python, residual_path, segments, self._language,
                    self._device, ctx,
                )
            if residual_result.status != "completed":
                return StageResult(
                    status=StageStatus.FAILED,
                    detail=residual_result.error_text or "vocal-residual lyric alignment failed",
                )

            target_words = [token.text for token in document.tokens]
            original = assign_word_timings(target_words, _observed_words(original_result))
            residual = assign_word_timings(target_words, _observed_words(residual_result))
            if min(original.coverage, residual.coverage) < MIN_EVIDENCE_COVERAGE:
                return StageResult(
                    status=StageStatus.FAILED,
                    detail=(
                        "Lyric confidence audit did not match enough words "
                        f"(original {original.coverage:.0%}, residual {residual.coverage:.0%}). "
                        "The existing LRC was left unchanged."
                    ),
                )
            confidence = build_dual_audio_consensus(document, current_starts, original, residual)
            improved = document.render_enhanced(confidence.selected_starts)
        except (ValueError, KeyError, OSError, subprocess.CalledProcessError, FileNotFoundError) as exc:
            return StageResult(status=StageStatus.FAILED, detail=str(exc))

        try:
            if lrc_path.read_bytes() != initial_lrc_bytes:
                return StageResult(
                    status=StageStatus.FAILED,
                    detail="Lyrics changed while confidence analysis was running; the newer file was left untouched",
                )
        except OSError as exc:
            return StageResult(status=StageStatus.FAILED, detail=str(exc))

        backup_path = lrc_path.with_name(f"{lrc_path.stem}{BACKUP_SUFFIX}")
        previous_files = _capture_files(
            lrc_path, backup_path, lyric_timing_sidecar_path(lrc_path),
            lyric_timing_details_path(lrc_path),
        )
        try:
            if confidence.corrected_words:
                if not backup_path.exists():
                    atomic_publish(
                        backup_path,
                        # backup_path is a fixed sibling of the validated LRC.
                        lambda part: part.write_bytes(initial_lrc_bytes),  # lgtm[py/path-injection]
                    )
                atomic_publish(
                    lrc_path,
                    # lrc_path was normalized and checked against configured roots.
                    lambda part: part.write_text(improved, encoding="utf-8", newline=""),  # lgtm[py/path-injection]
                )
            summary = write_lyric_timing_report(lrc_path, {
                "quality": confidence.quality,
                "engine": "whisperx",
                "model": "wav2vec2-alignment",
                "method": "dual_audio_consensus_v1",
                "device": self._device,
                "words": len(target_words),
                "matched": min(original.matched, residual.matched),
                "interpolated": max(original.interpolated, residual.interpolated),
                "coverage": min(original.coverage, residual.coverage),
                "median_confidence": confidence.confidence_score / 100.0,
                "low_confidence_words": confidence.review_words,
                "confidence_score": confidence.confidence_score,
                "verified_words": confidence.verified_words,
                "review_words": confidence.review_words,
                "corrected_words": confidence.corrected_words,
                "review_lines": confidence.review_lines,
                "agreement_within_0_25": confidence.agreement_within_0_25,
                "median_agreement_seconds": confidence.median_agreement_seconds,
                "attribution": "automatic",
                "confirmed_by": None,
            }, confidence.word_details)
        except (OSError, ValueError, TypeError) as exc:
            _restore_files(previous_files)
            return StageResult(
                status=StageStatus.FAILED,
                detail=f"could not publish the lyric confidence report; original LRC restored: {exc}",
            )

        return StageResult(
            status=StageStatus.COMPLETED,
            detail=(
                f"confidence {summary['confidence_score']}/100 · corrected "
                f"{confidence.corrected_words} word timings · {confidence.review_words} words in "
                f"{confidence.review_lines} lines still need review"
            ),
        )


def _run_alignment(
    runner: Callable[..., WorkerResult],
    venv_python: Path,
    audio_path: Path,
    segments: list[dict],
    language: str,
    device: str,
    ctx: StageContext,
) -> WorkerResult:
    return runner(
        venv_python,
        WHISPERX_SCRIPT,
        {
            "mode": "align", "audio_path": str(audio_path), "segments": segments,
            "language": language, "device": device, "normalize_audio": True,
        },
        cancel_event=ctx.cancel_event,
        on_progress=ctx.on_progress,
        timeout_seconds=ALIGN_TIMEOUT_SECONDS,
    )


def _observed_words(result: WorkerResult) -> list[ObservedWord]:
    assert result.payload is not None
    return [
        ObservedWord(word["word"], word["start"], word.get("end"), word.get("score"))
        for word in result.payload["words"]
    ]


def _locate_instrumental(source_path: Path, options: dict) -> Path | None:
    resolved_source = source_path.resolve()
    media_root = next(
        (
            Path(root) for root in options.get("media_roots", [])
            if resolved_source.is_relative_to(Path(root).resolve())
        ),
        source_path.parent,
    )
    return locate_output(
        source_path, media_root, [Path(root) for root in options.get("mirror_roots", [])],
        ".instrumental.mp3",
    )


def _validated_managed_path(path: Path, options: dict, *, must_exist: bool) -> Path:
    """Normalize a worker path and keep it inside a configured library root."""
    configured = [
        Path(root).resolve()
        for root in (*options.get("media_roots", []), *options.get("mirror_roots", []))
    ]
    if not configured:
        raise ValueError("lyric confidence processing requires a configured library root")
    try:
        resolved = path.resolve(strict=must_exist)
    except OSError as exc:
        raise ValueError(f"could not resolve managed media path: {exc}") from exc
    if not any(resolved.is_relative_to(root) for root in configured):
        raise ValueError("lyric confidence processing refused a path outside configured library roots")
    return resolved


def _probe_duration(source_path: Path) -> float:
    completed = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(source_path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(completed.stdout.strip())


def _build_vocal_residual(source_path: Path, instrumental_path: Path, destination: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source_path), "-i", str(instrumental_path),
            "-filter_complex",
            "[0:a:0][1:a:0]amix=inputs=2:weights='1 -1':normalize=0,"
            "pan=mono|c0=0.5*c0+0.5*c1,aresample=16000",
            "-c:a", "pcm_s16le", str(destination),
        ],
        check=True,
    )


def _capture_files(*paths: Path) -> dict[Path, bytes | None]:
    return {path: path.read_bytes() if path.is_file() else None for path in paths}


def _restore_files(captured: dict[Path, bytes | None]) -> None:
    for path, content in captured.items():
        if content is None:
            # Every captured path is derived from the validated canonical LRC.
            path.unlink(missing_ok=True)  # lgtm[py/path-injection]
        else:
            atomic_publish(path, lambda part, data=content: part.write_bytes(data))
