from __future__ import annotations

import time


def run_rescan(client, timeout: float = 5.0):
    """Start the asynchronous scan and wait for its terminal status."""
    started = client.post("/api/rescan")
    assert started.status_code == 200
    assert started.json()["status"] in {"queued", "running", "completed"}

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get("/api/rescan")
        if status.json()["status"] in {"completed", "failed"}:
            return status
        time.sleep(0.01)
    raise AssertionError("background library scan did not finish before the test timeout")


def scan_counts(response) -> dict:
    body = response.json()
    return {
        "tracks_found": body["tracks_found"],
        "media_roots_scanned": body["media_roots_scanned"],
        "unavailable_roots": body["unavailable_roots"],
        "tracks_purged": body["tracks_purged"],
    }
