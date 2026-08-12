from app.db import create_job, get_connection, get_job, set_item_status, set_job_status
from app.events import EventBus
from app.queue import JobQueueManager

from .queue_test_helpers import wait_for_event


def test_stuck_running_job_is_reset_and_re_executed_on_startup(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    job_id = create_job(
        conn,
        "fake",
        {"fake_delay_seconds": 0},
        [{"track_id": None, "source_path": str(tmp_path / "a.flac")}],
    )
    job = get_job(conn, job_id)
    set_job_status(conn, job_id, "running", started_at="2026-01-01T00:00:00+00:00")
    set_item_status(conn, job["items"][0]["id"], "running", current_stage="fake_prepare")

    bus = EventBus()
    subscriber = bus.subscribe()
    manager = JobQueueManager(conn, bus)  # crash recovery must run in __init__

    wait_for_event(subscriber, lambda e: e == {"type": "job", "job_id": job_id, "status": "completed"})

    recovered = get_job(conn, job_id)
    assert recovered["status"] == "completed"
    assert recovered["items"][0]["status"] == "completed"


def test_queued_job_never_started_is_recovered_and_executed_on_startup(tmp_path):
    # Simulates a process that died after create_job() inserted the row but
    # before the lane thread ever pulled it off the in-memory queue.Queue -
    # that queue does not survive a restart, so without recovery this job
    # would stay 'queued' in the database forever with nothing to run it.
    conn = get_connection(tmp_path / "library.db")
    job_id = create_job(
        conn,
        "fake",
        {"fake_delay_seconds": 0},
        [{"track_id": None, "source_path": str(tmp_path / "a.flac")}],
    )

    bus = EventBus()
    subscriber = bus.subscribe()
    manager = JobQueueManager(conn, bus)  # crash recovery must re-enqueue it

    wait_for_event(subscriber, lambda e: e == {"type": "job", "job_id": job_id, "status": "completed"})

    recovered = get_job(conn, job_id)
    assert recovered["status"] == "completed"
    assert recovered["items"][0]["status"] == "completed"


def test_startup_with_no_stuck_jobs_does_not_error(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    manager = JobQueueManager(conn, EventBus())
    assert manager.registry  # constructed successfully, registry is populated


def test_stuck_job_with_missing_recipe_is_marked_failed_without_aborting_startup(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    stuck_job_id = create_job(
        conn,
        "no_such_recipe",
        {},
        [{"track_id": None, "source_path": str(tmp_path / "a.flac")}],
    )
    set_job_status(conn, stuck_job_id, "running", started_at="2026-01-01T00:00:00+00:00")

    bus = EventBus()
    subscriber = bus.subscribe()
    manager = JobQueueManager(conn, bus)  # must not raise despite the unknown recipe

    recovered = get_job(conn, stuck_job_id)
    assert recovered["status"] == "failed"
    assert recovered["finished_at"] is not None

    # A subsequent valid job on the same manager still runs to completion.
    valid_job_id = manager.submit("fake", {"fake_delay_seconds": 0}, [{"track_id": None, "source_path": str(tmp_path / "b.flac")}])
    wait_for_event(subscriber, lambda e: e == {"type": "job", "job_id": valid_job_id, "status": "completed"})
    assert get_job(conn, valid_job_id)["status"] == "completed"
