import numpy as np

from app.stages.audio_io import export_mp3, read_wav, write_wav


def _sine_wav(path, seconds=0.05, sample_rate=8000, freq=440.0):
    samples = int(seconds * sample_rate)
    t = np.arange(samples) / sample_rate
    mono = (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    stereo = np.tile(mono, (2, 1))
    write_wav(path, stereo, sample_rate)
    return stereo, sample_rate


def test_write_wav_then_read_wav_round_trips_shape_and_sample_rate(tmp_path):
    original, sample_rate = _sine_wav(tmp_path / "tone.wav")

    audio, read_rate = read_wav(tmp_path / "tone.wav")

    assert read_rate == sample_rate
    assert audio.shape == original.shape
    assert np.allclose(audio, original, atol=1e-3)


def test_export_mp3_writes_a_non_empty_file(tmp_path):
    # Requires ffmpeg on PATH - a documented project prerequisite (see
    # README.md and TrackSeparator/requirements.txt) already relied on by
    # pydub; this is lightweight, deterministic CPU encoding, not an ML
    # engine, so it is not subject to the "no real engine in tests" rule.
    _sine_wav(tmp_path / "tone.wav")

    export_mp3(tmp_path / "tone.wav", tmp_path / "tone.mp3")

    assert (tmp_path / "tone.mp3").is_file()
    assert (tmp_path / "tone.mp3").stat().st_size > 0
