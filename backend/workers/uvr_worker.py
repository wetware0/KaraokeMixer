"""Runs inside .venv-uvr (audio-separator==0.44.5). Not imported by the main
backend app. Implements the JSON-over-stdio protocol from
backend/app/workers/runner.py: reads one JSON args line from stdin, invokes
audio-separator's 3-model 'karaoke' mel-band Roformer ensemble (see
TrackSeparator/UVR_KARAOKE_ENSEMBLE_APP_INTEGRATION_GUIDE.md for the exact
command contract this ports), and prints progress/result lines.

args: {"input_path": str, "model_dir": str, "output_dir": str}
result payload: {"instrumental": "<output_dir>/best_instrumental.mp3",
                 "vocals": "<output_dir>/best_vocals.mp3" | null}
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from collections import deque
from pathlib import Path

TAIL_LINES = 20
KARAOKE_MODEL_NAMES = (
    "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt",
    "mel_band_roformer_karaoke_gabox_v2.ckpt",
    "mel_band_roformer_karaoke_becruily.ckpt",
)


def prepare_separator_input(input_path: Path, output_dir: Path, runner=subprocess.run) -> Path:
    """Downmix surround sources because the pinned UVR ensemble accepts mono/stereo.

    Normal mono and stereo files pass through untouched. Probe failures also
    pass through so audio-separator can report its own decoder error instead
    of this compatibility guard hiding it.
    """
    probe = runner(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=channels", "-of", "default=nw=1:nk=1",
            str(input_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        channel_count = int(probe.stdout.strip()) if probe.returncode == 0 else None
    except ValueError:
        channel_count = None
    if channel_count is None or channel_count <= 2:
        return input_path

    stereo_path = output_dir / "stereo-input.wav"
    print(json.dumps({
        "type": "progress",
        "message": f"Input has {channel_count} channels; creating a temporary stereo downmix for UVR.",
    }), flush=True)
    downmix = runner(
        [
            "ffmpeg", "-v", "error", "-y", "-i", str(input_path),
            "-map", "0:a:0", "-vn", "-sn", "-dn", "-ac", "2",
            "-c:a", "pcm_s24le", str(stereo_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if downmix.returncode != 0 or not stereo_path.is_file():
        detail = downmix.stderr.strip()
        raise RuntimeError(f"Could not create a stereo downmix for UVR{': ' + detail if detail else ''}")
    return stereo_path


def _audio_separator_executable() -> str:
    # Resolved relative to sys.executable (this script's own venv), not PATH -
    # this script may be launched with a bare python.exe whose Scripts/ dir
    # is not necessarily first on PATH.
    name = "audio-separator.exe" if os.name == "nt" else "audio-separator"
    candidate = Path(sys.executable).with_name(name)
    return str(candidate) if candidate.is_file() else "audio-separator"


def remove_corrupt_cached_models(model_dir: Path) -> list[str]:
    """Remove interrupted PyTorch checkpoint downloads before separation.

    audio-separator downloads directly to the final checkpoint filename. If a
    job is cancelled during that first download, a large but incomplete file
    can remain and fail much later when PyTorch looks for its ZIP directory.
    All three pinned karaoke models use the ZIP-based checkpoint format, so a
    cheap central-directory check can safely distinguish a reusable model from
    one that must be downloaded again.
    """
    removed: list[str] = []
    for name in KARAOKE_MODEL_NAMES:
        path = model_dir / name
        if not path.is_file() or zipfile.is_zipfile(path):
            continue
        try:
            path.unlink()
        except OSError as exc:
            print(json.dumps({
                "type": "progress",
                "message": f"Could not remove corrupt cached UVR model {name}: {exc}",
            }), flush=True)
            continue
        removed.append(name)
    return removed


def main() -> None:
    args = json.loads(sys.stdin.readline())
    input_path = args["input_path"]
    model_dir = Path(args["model_dir"])
    output_dir = Path(args["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in remove_corrupt_cached_models(model_dir):
        print(json.dumps({
            "type": "progress",
            "message": f"Removed incomplete cached UVR model {name}; it will be downloaded again.",
        }), flush=True)

    try:
        separator_input = prepare_separator_input(Path(input_path), output_dir)
    except RuntimeError as exc:
        print(json.dumps({"type": "result", "status": "failed", "error": str(exc)}), flush=True)
        return

    command = [
        _audio_separator_executable(),
        str(separator_input),
        "--ensemble_preset", "karaoke",
        "--model_file_dir", str(model_dir),
        "--output_format", "MP3",
        "--output_bitrate", "192k",
        "--output_dir", str(output_dir),
        "--custom_output_names",
        json.dumps({"Instrumental": "best_instrumental", "Vocals": "best_vocals"}),
        "--log_level", "info",
    ]
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        encoding="utf-8", errors="replace",
    )
    assert process.stdout is not None
    tail: deque[str] = deque(maxlen=TAIL_LINES)
    for line in process.stdout:
        message = " ".join(line.strip().split())
        if message:
            tail.append(message)
            print(json.dumps({"type": "progress", "message": message}), flush=True)
    returncode = process.wait()

    instrumental = output_dir / "best_instrumental.mp3"
    vocals = output_dir / "best_vocals.mp3"
    if returncode != 0 or not instrumental.is_file():
        detail = "\n".join(tail)
        error = f"audio-separator exited with code {returncode}"
        if detail:
            error = f"{error}\n{detail}"
        print(
            json.dumps({"type": "result", "status": "failed", "error": error}),
            flush=True,
        )
        return
    print(
        json.dumps({
            "type": "result", "status": "completed",
            "payload": {"instrumental": str(instrumental), "vocals": str(vocals) if vocals.is_file() else None},
        }),
        flush=True,
    )


if __name__ == "__main__":
    main()
