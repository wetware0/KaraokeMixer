from __future__ import annotations

import math
import subprocess
import threading
from pathlib import Path

import mutagen

# Per-absolute-path cache of (mtime_ns, size, duration). /api/tracks/{id}/parts
# previously called read_duration_seconds() once per part on every single
# request with no caching at all, re-opening and re-parsing every audio
# file's header (mutagen.File) on every page load even though outputs
# rarely change between requests. Keyed by the file's mtime_ns and size (not
# just its path) so a file overwritten in place - e.g. a recipe re-run
# replacing a stem - invalidates automatically instead of serving a stale
# duration forever. A single small dict guarded by one lock is enough for
# this single-process, single-user, localhost service - same reasoning as
# db.py's module-level `_write_lock`.
_cache_lock = threading.Lock()
_cache: dict[str, tuple[int, int, float | None]] = {}


def read_duration_seconds(path: Path) -> float | None:
    """Best-effort audio duration via mutagen's header read (no decoding).
    Returns None for anything mutagen can't parse, reports no length for
    (a tiny/corrupt fixture), or that doesn't exist - callers treat this as
    "duration unknown", never as an error.

    Cached per absolute path, additionally keyed by the file's current
    (mtime_ns, size): a cache hit is only served when both still match what
    was cached, so a file changed on disk since the last read is
    transparently re-read instead of returning a stale value."""
    key = str(path)
    try:
        stat = path.stat()
    except OSError:
        return None
    stat_key = (stat.st_mtime_ns, stat.st_size)

    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and (cached[0], cached[1]) == stat_key:
            return cached[2]

    duration = _read_duration_uncached(path)

    with _cache_lock:
        _cache[key] = (stat_key[0], stat_key[1], duration)

    return duration


def _read_duration_uncached(path: Path) -> float | None:
    try:
        audio = mutagen.File(path)
    except Exception:
        audio = None
    if audio is not None and audio.info is not None:
        length = getattr(audio.info, "length", None)
        if length and length > 0:
            return float(length)

    # ffprobe understands some valid media streams that Mutagen cannot open
    # (for example ADTS AAC stored with an .m4a extension). It reads container
    # headers only; it does not decode the track or use the GPU.
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", "--", str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            return None
        duration = float(result.stdout.strip())
        return duration if math.isfinite(duration) and duration > 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
