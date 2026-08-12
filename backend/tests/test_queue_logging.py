import logging

from app.db import create_job, get_connection, get_job, set_job_status
from app.events import EventBus
from app.queue import JobQueueManager

from .queue_test_helpers import wait_for_event


def test_recover_crashed_jobs_logs_the_exception_with_a_traceback(tmp_path, caplog):
    # This hits _recover_crashed_jobs's own except block, NOT _run_job's -
    # the KeyError on self._registry[job["recipe"]] happens while re-enqueuing
    # a job left "running" from a previous process, entirely inside
    # JobQueueManager.__init__, before the job's lane thread ever runs it.
    conn = get_connection(tmp_path / "library.db")
    stuck_job_id = create_job(
        conn, "no_such_recipe", {}, [{"track_id": None, "source_path": str(tmp_path / "a.flac")}]
    )
    set_job_status(conn, stuck_job_id, "running", started_at="2026-01-01T00:00:00+00:00")
    bus = EventBus()
    subscriber = bus.subscribe()

    with caplog.at_level(logging.ERROR, logger="app.queue"):
        JobQueueManager(conn, bus)  # crash recovery hits the KeyError synchronously in __init__
        wait_for_event(subscriber, lambda e: e == {"type": "job", "job_id": stuck_job_id, "status": "failed"})

    assert get_job(conn, stuck_job_id)["status"] == "failed"
    error_records = [record for record in caplog.records if record.name == "app.queue" and record.levelno == logging.ERROR]
    assert error_records, "expected _recover_crashed_jobs's except block to log an ERROR"
    assert error_records[0].exc_info is not None  # log.exception attaches the traceback


def test_run_job_outer_safety_net_logs_the_exception_with_a_traceback(tmp_path, caplog):
    # This hits _run_job's own outer safety net specifically, not crash
    # recovery: the manager starts clean (no stuck jobs), then a job with an
    # unregistered recipe is created directly (bypassing submit()'s
    # validation, the same way a registry edited/swapped out between submit
    # and execution could) and _run_job is called synchronously - the
    # KeyError on self._registry[job["recipe"]] happens after the per-item
    # try/except is already set up but before any item's stage.run() is
    # reached, so it can only be caught by the outer except.
    conn = get_connection(tmp_path / "library.db")
    bus = EventBus()
    manager = JobQueueManager(conn, bus)  # no stuck jobs - registry populated normally
    job_id = create_job(conn, "no_such_recipe_at_all", {}, [{"track_id": None, "source_path": str(tmp_path / "a.flac")}])

    with caplog.at_level(logging.ERROR, logger="app.queue"):
        manager._run_job(job_id)  # synchronous call, bypassing the lane queue entirely

    assert get_job(conn, job_id)["status"] == "failed"
    error_records = [record for record in caplog.records if record.name == "app.queue" and record.levelno == logging.ERROR]
    assert error_records, "expected _run_job's outer safety net to log an ERROR"
    assert error_records[0].exc_info is not None
