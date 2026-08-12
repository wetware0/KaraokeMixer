from __future__ import annotations

import threading

from app import scanner
from app.db import get_connection, list_tracks, replace_tracks, update_settings
from app.events import EventBus
from app.rescan import LibraryScanManager
from app.scanner import TrackOutputs, TrackRecord


def _track(root, name: str) -> TrackRecord:
    path = root / name
    return TrackRecord(
        media_root=str(root),
        relative_path=name,
        absolute_path=str(path),
        artist="Artist",
        title=path.stem,
        outputs=TrackOutputs(),
        lrc_state=None,
        stem_count=0,
        album=None,
        year=None,
        duration_seconds=None,
    )


def _configure(conn, root) -> None:
    update_settings(
        conn,
        {
            "media_roots": [str(root)],
            "mirror_roots": [],
            "device_preference": "auto",
            "downloads_root": None,
            "youtube_cookies": {"mode": "none"},
        },
    )


def test_scan_returns_immediately_and_publishes_tracks_in_batches(tmp_path, monkeypatch):
    root = tmp_path / "Media"
    root.mkdir()
    conn = get_connection(tmp_path / "library.db")
    _configure(conn, root)
    first = _track(root, "First.flac")
    second = _track(root, "Second.flac")
    scanner_blocked_after_first = threading.Event()
    release_scanner = threading.Event()

    def slow_iterator(_root, _mirrors):
        yield first
        scanner_blocked_after_first.set()
        assert release_scanner.wait(timeout=5)
        yield second

    monkeypatch.setattr(scanner, "iter_media_root", slow_iterator)
    manager = LibraryScanManager(conn, EventBus(), batch_size=1)

    started = manager.start()
    assert started["status"] == "queued"
    assert scanner_blocked_after_first.wait(timeout=5)

    # The request-returning start() has completed, and the first committed
    # batch is already visible even though the scan thread is still blocked.
    assert manager.status()["status"] == "running"
    assert manager.status()["tracks_found"] == 1
    assert [track["title"] for track in list_tracks(conn)] == ["First"]
    assert manager.start()["scan_id"] == started["scan_id"]  # coalesced, not duplicated

    release_scanner.set()
    finished = manager.wait(timeout=5)
    assert finished["status"] == "completed"
    assert finished["tracks_found"] == 2
    assert {track["title"] for track in list_tracks(conn)} == {"First", "Second"}


def test_successful_incremental_scan_removes_stale_rows_only_at_root_completion(tmp_path, monkeypatch):
    root = tmp_path / "Media"
    root.mkdir()
    conn = get_connection(tmp_path / "library.db")
    _configure(conn, root)
    keep = _track(root, "Keep.flac")
    stale = _track(root, "Removed.flac")
    replace_tracks(conn, str(root), [keep, stale])

    monkeypatch.setattr(scanner, "iter_media_root", lambda _root, _mirrors: iter([keep]))
    manager = LibraryScanManager(conn, EventBus(), batch_size=1)

    manager.start()
    finished = manager.wait(timeout=5)

    assert finished["status"] == "completed"
    assert [track["title"] for track in list_tracks(conn)] == ["Keep"]
