from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

from ..lrc import TimingState, classify_lrc_file, read_lrc_text
from ..lyrics.alignment import AlignmentDocument, ObservedWord, assign_word_timings, line_timed_segments
from ..lyrics.paths import resolve_lrc_path
from ..pipeline import StageContext, StageResult, StageStatus, atomic_publish
from ..workers.runner import WorkerResult, run_worker

WHISPERX_SCRIPT = Path(__file__).resolve().parent.parent.parent / "workers" / "whisperx_worker.py"
ALIGN_TIMEOUT_SECONDS = 3600.0
DEFAULT_ASR_MODEL = "small.en"


def default_whisperx_venv_python() -> Path:
    base = Path(__file__).resolve().parent.parent.parent  # backend/
    return base / ".venv-whisperx" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


class AlignLyricsStage:
    """WhisperX word-level alignment against the ORIGINAL MIX audio (the
    spike-validated approach - median error 0.044s, see
    VoiceTiming/README.md), writing an enhanced <mm:ss.xx>-tagged LRC.

    Normally runs when the current LRC is line_timed or untimed. The hidden
    editor-only alignment recipe opts into discarding all existing timing and
    rebuilding even an enhanced file from a complete transcription; empty,
    unknown, or missing lyrics still skip cleanly. Resumability here is
    content-based (declared_outputs is always [], never existence-based,
    since the output path - {name}.lrc - already exists before this stage
    runs) - the same self-skip pattern UvrVocalSplitStage established in
    Milestone 2b for its `split` option.
    """

    name = "align_lyrics"

    def __init__(
        self,
        asr_model: str = DEFAULT_ASR_MODEL,
        device: str = "cpu",
        language: str = "en",
        venv_python: Path | None = None,
        runner: Callable[..., WorkerResult] = run_worker,
        enabled_option_key: str = "align_lyrics",
        realign_enhanced: bool = False,
        reset_existing_timing: bool = False,
        require_worker: bool = False,
    ) -> None:
        self._asr_model = asr_model
        self._device = device
        self._language = language
        self._venv_python = venv_python or default_whisperx_venv_python()
        self._runner = runner
        self._enabled_option_key = enabled_option_key
        self._realign_enhanced = realign_enhanced
        self._reset_existing_timing = reset_existing_timing
        self._require_worker = require_worker

    def declared_outputs(self, ctx: StageContext) -> list[Path]:
        return []

    def run(self, ctx: StageContext) -> StageResult:
        # A disabled alignment option must remain a clean skip even on a
        # machine without the optional worker. When the user explicitly asks
        # for timing, recipes set `require_worker` so absence becomes a clear
        # failure rather than a misleading successful job with line-only LRC.
        if not ctx.options.get(self._enabled_option_key, True):
            return StageResult(status=StageStatus.SKIPPED, detail="lyrics alignment not requested")

        if not self._venv_python.exists():
            return StageResult(
                status=StageStatus.FAILED if self._require_worker else StageStatus.SKIPPED,
                detail=(
                    "Enhanced word timing requires the WhisperX worker, which is not installed — "
                    "see README (worker venvs)"
                ),
            )

        lrc_path = resolve_lrc_path(ctx.source_path, ctx.options)
        if not lrc_path.is_file():
            return StageResult(status=StageStatus.SKIPPED, detail="no LRC to align")

        state = classify_lrc_file(lrc_path)
        eligible_states = {TimingState.LINE_TIMED, TimingState.UNTIMED}
        if self._realign_enhanced or self._reset_existing_timing:
            eligible_states.add(TimingState.ENHANCED)
        if state not in eligible_states:
            return StageResult(status=StageStatus.SKIPPED, detail=f"LRC is already {state.value}")

        # Tolerant read (matches classify_lrc_file's own read_lrc_text call
        # above) - a strict read_text(encoding="utf-8") here would raise
        # UnicodeDecodeError on a cp1252-encoded (or other non-UTF-8) .lrc
        # that classify_lrc_file just successfully classified moments ago.
        content = read_lrc_text(lrc_path)
        document = AlignmentDocument.parse(content)
        if self._reset_existing_timing:
            document = document.without_timing()

        try:
            args = {"audio_path": str(ctx.source_path), "language": self._language, "device": self._device}
            if self._reset_existing_timing:
                # A clean full-song transcription is intentional here: none
                # of the old line, break, or word windows may influence the
                # replacement timings requested from the Lyric Editor.
                args["mode"] = "transcribe"
                args["asr_model"] = self._asr_model
            elif state in (TimingState.LINE_TIMED, TimingState.ENHANCED):
                args["mode"] = "align"
                args["segments"] = line_timed_segments(document, duration=self._probe_duration(ctx.source_path))
            else:
                args["mode"] = "transcribe"
                args["asr_model"] = self._asr_model

            runner = ctx.worker_runner or self._runner
            result = runner(
                self._venv_python, WHISPERX_SCRIPT, args,
                cancel_event=ctx.cancel_event, on_progress=ctx.on_progress, timeout_seconds=ALIGN_TIMEOUT_SECONDS,
            )
            if result.status != "completed":
                return StageResult(status=StageStatus.FAILED, detail=result.error_text or "WhisperX alignment failed")

            observed = [
                ObservedWord(word["word"], word["start"], word.get("end"), word.get("score"))
                for word in result.payload["words"]
            ]
            target_words = [token.text for token in document.tokens]
            assignment = assign_word_timings(target_words, observed)
            enhanced = document.render_enhanced(assignment.starts)
        except (ValueError, KeyError, OSError, subprocess.CalledProcessError, FileNotFoundError) as exc:
            return StageResult(status=StageStatus.FAILED, detail=str(exc))

        # `enhanced` already carries the source file's newline convention.
        # Disable Windows text-mode newline translation or CRLF would become
        # CRCRLF and appear as spurious blank lines on the next read.
        atomic_publish(lrc_path, lambda part: part.write_text(enhanced, encoding="utf-8", newline=""))
        return StageResult(status=StageStatus.COMPLETED, detail=f"wrote enhanced LRC (coverage {assignment.coverage:.0%})")

    def _probe_duration(self, source_path: Path) -> float:
        completed = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(source_path),
            ],
            capture_output=True, text=True, check=True,
        )
        return float(completed.stdout.strip())
