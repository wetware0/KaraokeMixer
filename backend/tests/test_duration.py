import os
import wave
import subprocess
from pathlib import Path

from app import duration as duration_module
from app.duration import read_duration_seconds


def _write_silent_wav(path: Path, seconds: float, framerate: int = 8000) -> None:
    frame_count = int(seconds * framerate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(framerate)
        handle.writeframes(b"\x00\x00" * frame_count)


def test_reads_duration_from_a_real_wav_file(tmp_path):
    path = tmp_path / "clip.wav"
    _write_silent_wav(path, seconds=1.0)

    duration = read_duration_seconds(path)

    assert duration is not None
    assert abs(duration - 1.0) < 0.01


def test_returns_none_for_unparseable_audio(tmp_path):
    path = tmp_path / "clip.mp3"
    path.write_bytes(b"not actually audio data")

    assert read_duration_seconds(path) is None


def test_returns_none_when_file_does_not_exist(tmp_path):
    assert read_duration_seconds(tmp_path / "missing.wav") is None


def test_falls_back_to_ffprobe_when_mutagen_cannot_parse_the_container(tmp_path, monkeypatch):
    path = tmp_path / "unusual.m4a"
    path.write_bytes(b"media bytes")
    monkeypatch.setattr(duration_module.mutagen, "File", lambda path: None)
    monkeypatch.setattr(
        duration_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="280.55\n", stderr=""),
    )

    assert read_duration_seconds(path) == 280.55


def test_returns_none_when_mutagen_and_ffprobe_cannot_read_duration(tmp_path, monkeypatch):
    path = tmp_path / "broken.m4a"
    path.write_bytes(b"not media")
    monkeypatch.setattr(duration_module.mutagen, "File", lambda path: None)
    monkeypatch.setattr(
        duration_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, stdout="", stderr="invalid"),
    )

    assert read_duration_seconds(path) is None


def test_second_read_with_unchanged_mtime_and_size_is_served_from_cache(tmp_path, monkeypatch):
    path = tmp_path / "clip.wav"
    _write_silent_wav(path, seconds=1.0)

    call_count = 0
    real_file = duration_module.mutagen.File

    def counting_file(p, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        return real_file(p, *args, **kwargs)

    monkeypatch.setattr(duration_module.mutagen, "File", counting_file)

    first = read_duration_seconds(path)
    second = read_duration_seconds(path)

    assert first == second
    assert call_count == 1  # the second call was served from the cache, not re-parsed


def test_changing_the_files_mtime_invalidates_the_cached_duration(tmp_path):
    path = tmp_path / "clip.wav"
    _write_silent_wav(path, seconds=1.0)
    first = read_duration_seconds(path)
    assert first is not None
    assert abs(first - 1.0) < 0.01

    _write_silent_wav(path, seconds=2.0)
    # Force a distinct mtime_ns even if the rewrite landed in the same
    # filesystem-clock tick as the first write (fast successive writes can
    # otherwise share an mtime on coarser filesystems) - this is what makes
    # the assertion below deterministic rather than occasionally flaky.
    stat = path.stat()
    os.utime(path, ns=(stat.st_mtime_ns + 1_000_000, stat.st_mtime_ns + 1_000_000))

    second = read_duration_seconds(path)
    assert second is not None
    assert abs(second - 2.0) < 0.01


def test_a_cached_entry_with_a_stale_size_is_not_served_even_if_mtime_matches(tmp_path):
    path = tmp_path / "clip.wav"
    _write_silent_wav(path, seconds=1.0)
    first = read_duration_seconds(path)

    stat = path.stat()
    # Simulate a cache entry recorded for this exact mtime but a different
    # (stale) size and a bogus cached duration - proves the cache key checks
    # size too, not just mtime, without depending on filesystem write timing
    # to actually produce a same-mtime-different-size file.
    duration_module._cache[str(path)] = (stat.st_mtime_ns, stat.st_size + 1, 999.0)

    second = read_duration_seconds(path)
    assert second == first  # recomputed from the real file, not the poisoned 999.0
