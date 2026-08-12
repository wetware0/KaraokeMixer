from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
from pydub import AudioSegment


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Read a WAV file as a [channels, samples] float32 array + sample rate."""
    data, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    return data.T, sample_rate  # soundfile gives [samples, channels]; the rest of this app is channels-first


def write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    """Write a [channels, samples] float32 array as a 16-bit PCM WAV."""
    sf.write(str(path), audio.T, sample_rate, subtype="PCM_16")


def export_mp3(source_wav: Path, destination: Path, bitrate: str = "192k") -> None:
    """Encode a WAV file to MP3 via ffmpeg (through pydub), matching the
    `{stem}.{part}.mp3` output convention used across the app."""
    AudioSegment.from_wav(str(source_wav)).export(str(destination), format="mp3", bitrate=bitrate)
