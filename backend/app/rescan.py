from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from . import scanner
from .db import (
    finish_track_root_scan,
    get_settings,
    purge_tracks_not_in_roots,
    upsert_track_scan_batch,
)
from .events import EventBus

log = logging.getLogger(__name__)

ACTIVE_SCAN_STATUSES = {"queued", "running"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LibraryScanManager:
    """Runs at most one incremental media-library scan in a daemon thread.

    This is intentionally separate from the CPU/GPU processing lanes. A large
    filesystem walk should neither block the HTTP request that started it nor
    queue behind (or delay the scheduling of) stem separation and alignment
    work. Database writes are published in small batches through db.py's
    shared write lock.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        event_bus: EventBus,
        batch_size: int = 40,
    ) -> None:
        self._conn = conn
        self._event_bus = event_bus
        self._batch_size = max(1, batch_size)
        self._lock = threading.Lock()
        self._next_scan_id = 1
        self._thread: threading.Thread | None = None
        self._state = self._initial_state()

    @staticmethod
    def _initial_state() -> dict:
        return {
            "scan_id": 0,
            "status": "idle",
            "tracks_found": 0,
            "media_roots_scanned": 0,
            "media_roots_total": 0,
            "current_root": None,
            "unavailable_roots": [],
            "tracks_purged": 0,
            "error": None,
            "updated_at": _utc_now(),
        }

    def status(self) -> dict:
        with self._lock:
            return self._snapshot_locked()

    def start(self) -> dict:
        with self._lock:
            if self._state["status"] in ACTIVE_SCAN_STATUSES:
                return self._snapshot_locked()

            settings = get_settings(self._conn)
            scan_id = self._next_scan_id
            self._next_scan_id += 1
            self._state = {
                "scan_id": scan_id,
                "status": "queued",
                "tracks_found": 0,
                "media_roots_scanned": 0,
                "media_roots_total": len(settings["media_roots"]),
                "current_root": None,
                "unavailable_roots": [],
                "tracks_purged": 0,
                "error": None,
                "updated_at": _utc_now(),
            }
            snapshot = self._snapshot_locked()
            self._thread = threading.Thread(
                target=self._run,
                args=(scan_id, settings),
                name=f"library-scan-{scan_id}",
                daemon=True,
            )
            thread = self._thread

        self._publish(snapshot)
        thread.start()
        return snapshot

    def wait(self, timeout: float | None = None) -> dict:
        """Test/controlled-shutdown helper; normal HTTP callers poll status."""
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return self.status()

    def _run(self, scan_id: int, settings: dict) -> None:
        self._update(scan_id, status="running")
        mirror_roots = [Path(root) for root in settings["mirror_roots"]]
        try:
            for root_index, media_root in enumerate(settings["media_roots"]):
                root_path = Path(media_root)
                if not root_path.is_dir():
                    with self._lock:
                        if self._state["scan_id"] != scan_id:
                            return
                        unavailable = [*self._state["unavailable_roots"], media_root]
                    self._update(scan_id, unavailable_roots=unavailable, current_root=media_root)
                    continue

                self._update(scan_id, current_root=media_root)
                scan_token = f"library-scan:{scan_id}:{root_index}:{uuid4().hex}"
                batch: list[scanner.TrackRecord] = []
                for record in scanner.iter_media_root(root_path, mirror_roots):
                    batch.append(record)
                    if len(batch) >= self._batch_size:
                        self._publish_batch(scan_id, media_root, scan_token, batch)
                        batch = []
                if batch:
                    self._publish_batch(scan_id, media_root, scan_token, batch)

                # An empty root still needs finalization so tracks for files
                # removed since the previous successful scan are cleaned up.
                finish_track_root_scan(self._conn, media_root, scan_token)
                with self._lock:
                    roots_scanned = self._state["media_roots_scanned"] + 1
                self._update(scan_id, media_roots_scanned=roots_scanned)

            tracks_purged = purge_tracks_not_in_roots(self._conn, settings["media_roots"])
            self._update(
                scan_id,
                status="completed",
                current_root=None,
                tracks_purged=tracks_purged,
            )
        except Exception as exc:
            log.exception("Library scan %s failed", scan_id)
            self._update(scan_id, status="failed", error=str(exc))

    def _publish_batch(
        self,
        scan_id: int,
        media_root: str,
        scan_token: str,
        batch: list[scanner.TrackRecord],
    ) -> None:
        upsert_track_scan_batch(self._conn, media_root, batch, scan_token)
        with self._lock:
            tracks_found = self._state["tracks_found"] + len(batch)
        self._update(scan_id, tracks_found=tracks_found)

    def _update(self, scan_id: int, **changes: object) -> None:
        with self._lock:
            if self._state["scan_id"] != scan_id:
                return
            self._state.update(changes)
            self._state["updated_at"] = _utc_now()
            snapshot = self._snapshot_locked()
        self._publish(snapshot)

    def _snapshot_locked(self) -> dict:
        return {**self._state, "unavailable_roots": list(self._state["unavailable_roots"])}

    def _publish(self, snapshot: dict) -> None:
        self._event_bus.publish({"type": "library_scan", **snapshot})
