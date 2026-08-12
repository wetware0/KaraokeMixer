from app.db import get_connection, get_job
from app.events import EventBus
from app.queue import JobQueueManager

from .queue_test_helpers import BlockingStage, blocking_recipe, wait_for_event


def test_job_cancelled_while_still_queued_behind_a_running_job_never_runs(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    bus = EventBus()
    stage1 = BlockingStage()
    stage2 = BlockingStage()
    registry = {
        "block1": blocking_recipe("block1", "cpu", stage1),
        "block2": blocking_recipe("block2", "cpu", stage2),
    }
    manager = JobQueueManager(conn, bus, registry=registry)
    subscriber = bus.subscribe()

    job1_id = manager.submit("block1", {}, [{"track_id": None, "source_path": str(tmp_path / "a.flac")}])
    assert stage1.started.wait(timeout=5)  # job1's stage is now blocking

    job2_id = manager.submit("block2", {}, [{"track_id": None, "source_path": str(tmp_path / "b.flac")}])
    manager.cancel(job2_id)

    stage1.release.set()  # let job1 finish

    wait_for_event(subscriber, lambda e: e == {"type": "job", "job_id": job1_id, "status": "completed"})
    wait_for_event(subscriber, lambda e: e == {"type": "job", "job_id": job2_id, "status": "cancelled"})

    assert stage2.started.is_set() is False
    job2 = get_job(conn, job2_id)
    assert job2["status"] == "cancelled"
    assert job2["items"][0]["status"] == "cancelled"


def test_cancelling_mid_job_finishes_the_current_item_then_skips_the_rest(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    bus = EventBus()
    stage_item1 = BlockingStage()
    stage_item2 = BlockingStage()
    stages_by_filename = {"a.flac": stage_item1, "b.flac": stage_item2}

    class _RoutingStage:
        name = "blocking"

        def declared_outputs(self, ctx):
            return []

        def run(self, ctx):
            return stages_by_filename[ctx.source_path.name].run(ctx)

    registry = {"routed": blocking_recipe("routed", "cpu", _RoutingStage())}
    manager = JobQueueManager(conn, bus, registry=registry)
    subscriber = bus.subscribe()

    job_id = manager.submit(
        "routed",
        {},
        [
            {"track_id": None, "source_path": str(tmp_path / "a.flac")},
            {"track_id": None, "source_path": str(tmp_path / "b.flac")},
        ],
    )
    assert stage_item1.started.wait(timeout=5)
    manager.cancel(job_id)
    stage_item1.release.set()

    wait_for_event(subscriber, lambda e: e == {"type": "job", "job_id": job_id, "status": "cancelled"})

    assert stage_item2.started.is_set() is False
    job = get_job(conn, job_id)
    assert job["items"][0]["status"] == "completed"
    assert job["items"][1]["status"] == "cancelled"
