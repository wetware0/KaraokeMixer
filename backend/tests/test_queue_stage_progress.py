from __future__ import annotations

import queue
import time
from pathlib import Path

from app.db import get_connection
from app.events import EventBus
from app.pipeline import StageResult, StageStatus
from app.queue import JobQueueManager
from app.recipes.registry import RecipeDefinition

from .queue_test_helpers import wait_for_event


class _ProgressEmittingStage:
    name = "progress_emitting"

    def declared_outputs(self, ctx):
        return []

    def run(self, ctx):
        if ctx.on_progress:
            ctx.on_progress({"type": "progress", "message": "step 1 of 2"})
            # queue.py's on_progress hook rate-limits to at most one
            # stage_progress event per 250ms per stage (see
            # test_stage_progress_events_are_rate_limited below) - space
            # these two calls out past that window so both survive, since
            # this test asserts on both messages arriving.
            time.sleep(0.3)
            ctx.on_progress({"type": "progress", "message": "step 2 of 2"})
        return StageResult(status=StageStatus.COMPLETED, detail="done")


def test_job_queue_manager_publishes_stage_progress_events(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    bus = EventBus()
    registry = {
        "progress_test": RecipeDefinition(
            name="progress_test", lane="cpu",
            stage_factories=[lambda options: _ProgressEmittingStage()],
            hidden=True,
        )
    }
    manager = JobQueueManager(conn, bus, registry=registry)
    subscriber = bus.subscribe()

    job_id = manager.submit(
        "progress_test", {}, [{"track_id": None, "source_path": str(tmp_path / "song.flac")}]
    )
    item_id = conn.execute(
        "SELECT id FROM job_items WHERE job_id = ?", (job_id,)
    ).fetchone()["id"]

    first = wait_for_event(
        subscriber,
        lambda e: e.get("type") == "stage_progress" and e.get("job_id") == job_id,
    )
    assert first == {
        "type": "stage_progress", "job_id": job_id, "item_id": item_id,
        "stage": "progress_emitting", "detail": "step 1 of 2",
    }

    second = wait_for_event(
        subscriber,
        lambda e: e.get("type") == "stage_progress" and e.get("detail") == "step 2 of 2",
    )
    assert second["stage"] == "progress_emitting"

    wait_for_event(subscriber, lambda e: e == {"type": "job", "job_id": job_id, "status": "completed"})


class _RapidProgressEmittingStage:
    """A pathologically chatty stage - 50 on_progress() calls back-to-back,
    no delay - standing in for something like WhisperX emitting a progress
    line per word/segment. Without the queue's rate limit, this used to mean
    50 separate stage_progress publishes for a single stage run."""

    name = "rapid_progress_emitting"

    def declared_outputs(self, ctx):
        return []

    def run(self, ctx):
        if ctx.on_progress:
            for i in range(50):
                ctx.on_progress({"type": "progress", "message": f"tick {i}"})
        return StageResult(status=StageStatus.COMPLETED, detail="done")


def test_stage_progress_events_are_rate_limited(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    bus = EventBus()
    registry = {
        "rapid_progress_test": RecipeDefinition(
            name="rapid_progress_test", lane="cpu",
            stage_factories=[lambda options: _RapidProgressEmittingStage()],
            hidden=True,
        )
    }
    manager = JobQueueManager(conn, bus, registry=registry)
    subscriber = bus.subscribe()

    job_id = manager.submit(
        "rapid_progress_test", {}, [{"track_id": None, "source_path": str(tmp_path / "song.flac")}]
    )

    # The very first progress event must always get through immediately -
    # the rate limit is leading-edge, never delays the first tick.
    first = wait_for_event(
        subscriber,
        lambda e: e.get("type") == "stage_progress" and e.get("job_id") == job_id,
    )
    assert first["detail"] == "tick 0"

    wait_for_event(subscriber, lambda e: e == {"type": "job", "job_id": job_id, "status": "completed"})

    # 50 rapid-fire progress calls, all within a fraction of a second, must
    # collapse to a small, bounded number of published stage_progress events
    # - not one publish per call. Drain whatever's left on the subscriber's
    # queue (non-blocking; everything was already published by the time the
    # terminal "completed" job event above was observed).
    drained = []
    while True:
        try:
            drained.append(subscriber.get_nowait())
        except queue.Empty:
            break
    stage_progress_events = [event for event in drained if event.get("type") == "stage_progress"]
    assert len(stage_progress_events) < 10
