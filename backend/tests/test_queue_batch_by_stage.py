from __future__ import annotations

import threading

from app.db import get_connection, get_job
from app.events import EventBus
from app.pipeline import StageResult, StageStatus
from app.queue import JobQueueManager
from app.recipes.registry import RecipeDefinition

from .queue_test_helpers import wait_for_event


class _RecordingStage:
    def __init__(self, name: str, calls: list[tuple[str, str]]) -> None:
        self.name = name
        self._calls = calls

    def declared_outputs(self, _ctx):
        return []

    def run(self, ctx):
        self._calls.append((self.name, ctx.source_path.name))
        return StageResult(status=StageStatus.COMPLETED, detail="done")


def test_batch_recipe_runs_stage_major_across_tracks(tmp_path):
    calls: list[tuple[str, str]] = []
    recipe = RecipeDefinition(
        name="batch",
        lane="gpu",
        stage_factories=[
            lambda _options: _RecordingStage("separate", calls),
            lambda _options: _RecordingStage("align", calls),
        ],
        batch_by_stage=True,
    )
    conn = get_connection(tmp_path / "library.db")
    bus = EventBus()
    subscriber = bus.subscribe()
    manager = JobQueueManager(conn, bus, registry={"batch": recipe})

    job_id = manager.submit(
        "batch",
        {},
        [
            {"track_id": None, "source_path": str(tmp_path / "a.flac")},
            {"track_id": None, "source_path": str(tmp_path / "b.flac")},
        ],
    )
    wait_for_event(subscriber, lambda event: event == {"type": "job", "job_id": job_id, "status": "completed"})

    assert calls == [
        ("separate", "a.flac"),
        ("separate", "b.flac"),
        ("align", "a.flac"),
        ("align", "b.flac"),
    ]
    assert all(item["status"] == "completed" for item in get_job(conn, job_id)["items"])


class _BlockingFirstStage:
    name = "separate"

    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        self._started = started
        self._release = release

    def declared_outputs(self, _ctx):
        return []

    def run(self, _ctx):
        self._started.set()
        assert self._release.wait(timeout=5)
        return StageResult(status=StageStatus.COMPLETED, detail="done")


def test_cancelling_stage_major_batch_does_not_start_later_tracks_or_phases(tmp_path):
    started = threading.Event()
    release = threading.Event()
    later_calls: list[tuple[str, str]] = []
    recipe = RecipeDefinition(
        name="batch",
        lane="gpu",
        stage_factories=[
            lambda _options: _BlockingFirstStage(started, release),
            lambda _options: _RecordingStage("align", later_calls),
        ],
        batch_by_stage=True,
    )
    conn = get_connection(tmp_path / "library.db")
    bus = EventBus()
    subscriber = bus.subscribe()
    manager = JobQueueManager(conn, bus, registry={"batch": recipe})
    job_id = manager.submit(
        "batch",
        {},
        [
            {"track_id": None, "source_path": str(tmp_path / "a.flac")},
            {"track_id": None, "source_path": str(tmp_path / "b.flac")},
        ],
    )

    assert started.wait(timeout=5)
    manager.cancel(job_id)
    release.set()
    wait_for_event(subscriber, lambda event: event == {"type": "job", "job_id": job_id, "status": "cancelled"})

    assert later_calls == []
    assert all(item["status"] == "cancelled" for item in get_job(conn, job_id)["items"])
