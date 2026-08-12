from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Callable

from app.pipeline import StageContext, StageResult, StageStatus
from app.recipes.registry import RecipeDefinition


def wait_for_event(
    subscriber: "queue.Queue[dict]", predicate: Callable[[dict], bool], timeout: float = 5.0
) -> dict:
    """Block until an event matching `predicate` arrives on `subscriber`, or
    raise. Bounded by `timeout` and driven by the queue's own blocking get()
    (not a polling sleep loop), so it never races real wall-clock timing."""
    deadline = time.monotonic() + timeout
    seen: list[dict] = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"event matching predicate not seen within {timeout}s; saw: {seen}")
        try:
            event = subscriber.get(timeout=remaining)
        except queue.Empty:
            raise AssertionError(f"event matching predicate not seen within {timeout}s; saw: {seen}")
        seen.append(event)
        if predicate(event):
            return event


class RaisingStage:
    """Test stage that raises instead of returning a StageResult, so tests
    can exercise the queue manager's crash-survival path (a real stage might
    raise on a missing binary, a subprocess crash, an OSError, etc.)."""

    name = "raising"

    def __init__(self, message: str = "boom") -> None:
        self._message = message

    def declared_outputs(self, ctx: StageContext) -> list[Path]:
        return []

    def run(self, ctx: StageContext) -> StageResult:
        raise RuntimeError(self._message)


def make_raising_recipe(
    name: str = "raising", lane: str = "cpu", message: str = "boom"
) -> RecipeDefinition:
    """A single-stage recipe whose stage always raises RuntimeError(message)."""
    return RecipeDefinition(
        name=name,
        lane=lane,
        stage_factories=[lambda options: RaisingStage(message=message)],
        hidden=True,
    )


class BlockingStage:
    """A Stage whose run() blocks until the test releases it - gives tests
    precise, event-driven control over when a job is "mid-stage" instead of
    racing real wall-clock sleeps. Can accept an optional release_event for
    backward compatibility, otherwise creates its own."""

    name = "blocking"

    def __init__(self, release_event: threading.Event | None = None) -> None:
        self.started = threading.Event()
        self.release = release_event if release_event is not None else threading.Event()

    def declared_outputs(self, ctx: StageContext) -> list[Path]:
        return []

    def run(self, ctx: StageContext) -> StageResult:
        self.started.set()
        self.release.wait(timeout=5)
        return StageResult(status=StageStatus.COMPLETED, detail="released")


def blocking_recipe(name: str, lane: str, stage: BlockingStage) -> RecipeDefinition:
    """A single-stage recipe wrapping a pre-constructed BlockingStage."""
    return RecipeDefinition(name=name, lane=lane, stage_factories=[lambda options: stage], hidden=True)


def make_blocking_recipe(
    release_event: threading.Event, name: str = "blocking", lane: str = "cpu"
) -> RecipeDefinition:
    """A single-stage recipe whose stage blocks until `release_event` is set.
    Deprecated: use blocking_recipe() with a BlockingStage() directly instead."""
    return blocking_recipe(name, lane, BlockingStage(release_event))
