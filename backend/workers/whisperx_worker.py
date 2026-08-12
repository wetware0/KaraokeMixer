"""Runs inside .venv-whisperx (torch + whisperx). Not imported by the main
backend app - backend/app/workers/runner.py starts it as a subprocess. See
backend/app/workers/runner.py for the JSON-over-stdio protocol this script
implements. Ported from VoiceTiming/src/vocal_timing/aligner.py::
WhisperXAligner - the proven approach (original mix audio, small.en default
ASR model, forced alignment for line-timed lyrics, full transcription then
alignment for untimed lyrics).

args (align mode):       {"mode": "align", "audio_path": str,
                          "segments": [{"start": float, "end": float, "text": str}, ...],
                          "language": str, "device": str}
args (transcribe mode):  {"mode": "transcribe", "audio_path": str,
                          "language": str, "device": str, "asr_model": str,
                          "compute_type"?: str, "batch_size"?: int}
result payload:           {"words": [{"word": str, "start": float,
                           "end": float | None, "score": float | None}, ...]}
"""
from __future__ import annotations

import json
import sys


def _emit(event: dict) -> None:
    print(json.dumps(event), flush=True)


def _words_from_result(result: dict) -> list[dict]:
    words = result.get("word_segments") or [
        word for segment in result.get("segments", []) for word in segment.get("words", [])
    ]
    return [
        {
            "word": str(word["word"]),
            "start": float(word["start"]),
            "end": float(word["end"]) if word.get("end") is not None else None,
            "score": float(word["score"]) if word.get("score") is not None else None,
        }
        for word in words
        if word.get("word") and word.get("start") is not None
    ]


def _run_request(args: dict, whisperx, cache: dict) -> None:
    mode = args["mode"]
    device = args["device"]
    language = args.get("language", "en")

    align_key = (language, device)
    if cache.get("align_key") != align_key:
        _emit({"type": "progress", "message": "loading alignment model"})
        align_model, align_metadata = whisperx.load_align_model(language_code=language, device=device)
        cache["align_key"] = align_key
        cache["align_model"] = align_model
        cache["align_metadata"] = align_metadata
    else:
        _emit({"type": "progress", "message": "reusing loaded alignment model"})
    align_model = cache["align_model"]
    align_metadata = cache["align_metadata"]

    _emit({"type": "progress", "message": "decoding audio"})
    audio = whisperx.load_audio(args["audio_path"])

    if mode == "align":
        segments = args["segments"]
        _emit({"type": "progress", "message": f"forced-aligning {len(segments)} line(s)"})
        result = whisperx.align(segments, align_model, align_metadata, audio, device, return_char_alignments=False)
    elif mode == "transcribe":
        asr_model_name = args.get("asr_model", "small.en")
        compute_type = args.get("compute_type") or ("float16" if device == "cuda" else "int8")
        batch_size = int(args.get("batch_size", 8))
        asr_key = (asr_model_name, device, compute_type, language)
        if cache.get("asr_key") != asr_key:
            _emit({"type": "progress", "message": f"loading {asr_model_name}"})
            cache["asr_model"] = whisperx.load_model(
                asr_model_name, device, compute_type=compute_type, language=language
            )
            cache["asr_key"] = asr_key
        else:
            _emit({"type": "progress", "message": f"reusing loaded {asr_model_name}"})
        _emit({"type": "progress", "message": f"transcribing with {asr_model_name}"})
        transcript = cache["asr_model"].transcribe(audio, batch_size=batch_size)
        _emit({"type": "progress", "message": "aligning transcript"})
        result = whisperx.align(
            transcript["segments"], align_model, align_metadata, audio, device, return_char_alignments=False
        )
    else:
        raise ValueError(f"unknown mode: {mode}")
    _emit({"type": "result", "status": "completed", "payload": {"words": _words_from_result(result)}})


def main() -> None:
    try:
        import whisperx
    except Exception as exc:  # pragma: no cover - exercised only in the real venv
        _emit({"type": "result", "status": "failed", "error": f"whisperx import failed: {exc}"})
        return
    cache: dict = {}
    persistent = "--persistent" in sys.argv[1:]
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            _run_request(json.loads(line), whisperx, cache)
        except Exception as exc:  # pragma: no cover - real model/runtime failures
            _emit({"type": "result", "status": "failed", "error": str(exc)})
        if not persistent:
            break


if __name__ == "__main__":
    main()
