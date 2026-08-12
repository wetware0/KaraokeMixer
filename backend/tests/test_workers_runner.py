from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

from app.workers.runner import PersistentWorkerPool, run_worker

FIXTURE_SCRIPT = Path(__file__).resolve().parent / "fixtures" / "fake_worker_script.py"
PYTHON = Path(sys.executable)


def test_run_worker_completes_and_forwards_progress_and_payload():
    progress_messages = []

    result = run_worker(
        PYTHON, FIXTURE_SCRIPT,
        {"mode": "success", "progress_steps": 2, "echo": {"a": 1}},
        on_progress=lambda event: progress_messages.append(event["message"]),
    )

    assert result.status == "completed"
    assert result.payload == {"echo": {"a": 1}}
    assert result.error_text is None
    assert progress_messages == ["step 1", "step 2"]


def test_run_worker_reports_a_failed_result_line():
    result = run_worker(PYTHON, FIXTURE_SCRIPT, {"mode": "fail_result"})

    assert result.status == "failed"
    assert result.error_text == "synthetic failure"


def test_run_worker_captures_stderr_when_the_child_crashes_without_a_result_line():
    result = run_worker(PYTHON, FIXTURE_SCRIPT, {"mode": "crash"})

    assert result.status == "failed"
    assert "about to crash" in result.error_text


def test_run_worker_times_out_and_terminates_the_process():
    started = time.monotonic()

    result = run_worker(PYTHON, FIXTURE_SCRIPT, {"mode": "sleep", "duration_seconds": 5.0}, timeout_seconds=0.3)

    elapsed = time.monotonic() - started
    assert result.status == "failed"
    assert "timed out" in result.error_text
    assert elapsed < 3.0  # proves the subprocess was actually terminated, not awaited to completion


def test_run_worker_is_cancelled_via_the_cancel_event():
    cancel_event = threading.Event()
    first_progress_seen = threading.Event()

    def on_progress(event):
        first_progress_seen.set()

    result_holder: dict = {}

    def run():
        result_holder["result"] = run_worker(
            PYTHON, FIXTURE_SCRIPT, {"mode": "sleep", "duration_seconds": 5.0},
            on_progress=on_progress, cancel_event=cancel_event,
        )

    thread = threading.Thread(target=run)
    started = time.monotonic()
    thread.start()

    assert first_progress_seen.wait(timeout=5), "worker never reported its first progress line"
    cancel_event.set()
    thread.join(timeout=5)
    elapsed = time.monotonic() - started

    assert result_holder["result"].status == "cancelled"
    assert elapsed < 3.0  # cancelled almost immediately, not after the full 5s sleep


def test_run_worker_preserves_a_received_result_when_cancelled_right_after():
    cancel_event = threading.Event()
    first_progress_seen = threading.Event()

    def on_progress(event):
        first_progress_seen.set()

    result_holder: dict = {}

    def run():
        result_holder["result"] = run_worker(
            PYTHON, FIXTURE_SCRIPT,
            {"mode": "result_then_sleep", "post_result_sleep_seconds": 2.0},
            on_progress=on_progress, cancel_event=cancel_event,
        )

    thread = threading.Thread(target=run)
    thread.start()

    # The child emits its progress line and its result line back-to-back,
    # before it ever sleeps - by the time our on_progress callback fires,
    # the result has already been produced. Cancelling now must not turn a
    # genuinely completed result into a cancelled one.
    assert first_progress_seen.wait(timeout=5), "worker never reported its first progress line"
    cancel_event.set()
    thread.join(timeout=10)

    result = result_holder["result"]
    assert result.status == "completed"
    assert result.payload == {"finished": True}


def test_run_worker_never_raises_when_termination_itself_fails(monkeypatch):
    from app.workers import runner as runner_module

    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="taskkill", timeout=10)

    monkeypatch.setattr(runner_module.subprocess, "run", boom)

    result = run_worker(
        PYTHON, FIXTURE_SCRIPT,
        {"mode": "sleep", "duration_seconds": 1.0},
        timeout_seconds=0.3,
    )

    assert result.status == "failed"
    assert result.error_text == "worker process could not be terminated cleanly"


def test_persistent_worker_pool_reuses_one_process_for_multiple_requests(monkeypatch):
    from app.workers import runner as runner_module

    monkeypatch.setattr(runner_module, "PERSISTENT_WORKER_SCRIPTS", {FIXTURE_SCRIPT.name})
    with PersistentWorkerPool() as pool:
        first = pool.run(PYTHON, FIXTURE_SCRIPT, {"mode": "success", "include_pid": True})
        second = pool.run(PYTHON, FIXTURE_SCRIPT, {"mode": "success", "include_pid": True})

    assert first.status == "completed"
    assert second.status == "completed"
    assert first.payload["pid"] == second.payload["pid"]


def test_persistent_worker_pool_discards_a_cancelled_process(monkeypatch):
    from app.workers import runner as runner_module

    monkeypatch.setattr(runner_module, "PERSISTENT_WORKER_SCRIPTS", {FIXTURE_SCRIPT.name})
    cancel_event = threading.Event()
    progress_seen = threading.Event()
    result_holder = {}

    with PersistentWorkerPool() as pool:
        thread = threading.Thread(
            target=lambda: result_holder.setdefault(
                "result",
                pool.run(
                    PYTHON,
                    FIXTURE_SCRIPT,
                    {"mode": "sleep", "duration_seconds": 5},
                    cancel_event=cancel_event,
                    on_progress=lambda _event: progress_seen.set(),
                ),
            )
        )
        thread.start()
        assert progress_seen.wait(timeout=5)
        cancel_event.set()
        thread.join(timeout=5)

        assert result_holder["result"].status == "cancelled"
        fresh = pool.run(PYTHON, FIXTURE_SCRIPT, {"mode": "success", "include_pid": True})
        assert fresh.status == "completed"
