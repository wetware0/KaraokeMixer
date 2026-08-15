"""Runs inside .venv-demucs (torch + demucs). Not imported by the main
backend app - backend/app/workers/runner.py starts it as a subprocess. See
backend/app/workers/runner.py for the JSON-over-stdio protocol this script
implements: one JSON args line on stdin, `{"type": "progress", ...}` lines
and one final `{"type": "result", ...}` line on stdout. Ported from
TrackSeparator/src/core/separator.py::AudioSeparator.separate_stems and
TrackSeparator/src/core/audio_tensor_utils.py::save_tensor_as_pcm16_wav.

args: {"input_path": str, "model": str, "device": str,
       "stem_indices": {stem_name: source_index}, "output_paths": {stem_name: wav_path},
       "shifts": optional_int}
result payload: {"stems": [stem_name, ...]}
"""
from __future__ import annotations

import json
import sys


def _emit(event: dict) -> None:
    print(json.dumps(event), flush=True)


def _run_request(args: dict, dependencies: tuple, cache: dict) -> None:
    torch, pretrained, apply_model, AudioFile = dependencies
    model_name = args["model"]
    device = args["device"]
    input_path = args["input_path"]
    stem_indices: dict[str, int] = args["stem_indices"]
    output_paths: dict[str, str] = args["output_paths"]

    model_key = (model_name, device)
    model = cache.get("model") if cache.get("model_key") == model_key else None
    if model is None:
        _emit({"type": "progress", "message": f"loading {model_name}"})
        model = pretrained.get_model(model_name)
        model.to(device)
        model.eval()
        cache.clear()
        cache.update(model_key=model_key, model=model)
    else:
        _emit({"type": "progress", "message": f"reusing loaded {model_name}"})

    _emit({"type": "progress", "message": "reading audio"})
    wav = AudioFile(input_path).read(samplerate=model.samplerate, channels=model.audio_channels)
    if wav.dim() == 2 and wav.shape[0] == 1:
        wav = wav.repeat(2, 1)
    wav = wav.to(device)

    _emit({"type": "progress", "message": "separating"})
    segment = min(
        [10.0] + [float(m.segment) for m in getattr(model, "models", [model]) if getattr(m, "segment", None)]
    )
    with torch.amp.autocast("cuda", enabled=device == "cuda" and torch.cuda.is_available()):
        sources = apply_model(
            model,
            wav,
            device=device,
            progress=False,
            segment=segment,
            overlap=0.25,
            shifts=int(args.get("shifts", 1)),
        )
    if sources.dim() == 4 and sources.shape[0] == 1:
        sources = sources[0]

    sample_rate = getattr(model, "samplerate", 44100)
    for stem, index in stem_indices.items():
        _emit({"type": "progress", "message": f"writing {stem}"})
        audio = sources[index].cpu().float()
        _write_wav(output_paths[stem], audio, sample_rate)

    _emit({"type": "result", "status": "completed", "payload": {"stems": list(stem_indices)}})


def main() -> None:
    try:
        import torch
        from demucs import pretrained
        from demucs.apply import apply_model
        from demucs.audio import AudioFile
    except Exception as exc:  # pragma: no cover - exercised only in the real venv
        _emit({"type": "result", "status": "failed", "error": f"demucs import failed: {exc}"})
        return
    dependencies = (torch, pretrained, apply_model, AudioFile)
    cache: dict = {}
    persistent = "--persistent" in sys.argv[1:]
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            _run_request(json.loads(line), dependencies, cache)
        except Exception as exc:  # pragma: no cover - real model/runtime failures
            _emit({"type": "result", "status": "failed", "error": str(exc)})
        if not persistent:
            break


def _write_wav(path: str, audio, sample_rate: int) -> None:
    import wave

    import torch

    pcm_data = (
        audio.detach()
        .to(device="cpu", dtype=torch.float32)
        .clamp(-1.0, 1.0)
        .mul(32767.0)
        .round()
        .to(torch.int16)
        .transpose(0, 1)
        .contiguous()
        .numpy()
        .tobytes()
    )
    with wave.open(path, "wb") as handle:
        handle.setnchannels(audio.shape[0])
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm_data)


if __name__ == "__main__":
    main()
