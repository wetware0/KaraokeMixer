from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from app.pipeline import StageResult, StageStatus
from app.queue import execute_item
from app.workers.runner import run_worker

FIXTURE_SCRIPT = Path(__file__).resolve().parent / "fixtures" / "fake_worker_script.py"
PYTHON = Path(sys.executable)


class _SleepyWorkerStage:
    """A stand-in for any real GPU stage (demucs/uvr/whisperx): its run()
    forwards ctx.cancel_event into run_worker(), exactly like Task 3's real
    stages will. Proves the queue-level plumbing works before any real stage
    is touched. When actually cancelled mid-run, run_worker returns
    status="cancelled", which this stage maps to StageStatus.FAILED (there is
    no StageStatus.CANCELLED) - execute_item's own cancel_event check is what
    turns that into the item-level "cancelled" outcome, not this mapping."""

    name = "sleepy_worker"

    def declared_outputs(self, ctx):
        return []

    def run(self, ctx):
        result = run_worker(
            PYTHON, FIXTURE_SCRIPT, {"mode": "sleep", "duration_seconds": 5.0},
            cancel_event=ctx.cancel_event,
        )
        status = StageStatus.COMPLETED if result.status == "completed" else StageStatus.FAILED
        return StageResult(status=status, detail=result.status)


def test_cancelling_mid_stage_terminates_the_worker_promptly_and_the_item_is_cancelled(tmp_path):
    cancel_event = threading.Event()
    result_holder: dict = {}

    def run():
        result_holder["terminal"] = execute_item(
            [_SleepyWorkerStage()],
            tmp_path / "song.flac",
            {},
            False,
            cancel_event,
            on_stage_change=lambda stage, result: None,
        )

    thread = threading.Thread(target=run)
    started = time.monotonic()
    thread.start()

    time.sleep(0.3)  # let the fixture worker get into its 5s sleep
    cancel_event.set()
    thread.join(timeout=5)
    elapsed = time.monotonic() - started

    assert result_holder["terminal"] == "cancelled"
    assert elapsed < 3.0  # proves the worker was actually terminated, not awaited for the full 5s
