from app.db import get_connection
from app.events import EventBus
from app.queue import JobQueueManager

from .queue_test_helpers import BlockingStage, blocking_recipe


def test_gpu_and_cpu_lanes_run_concurrently(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    bus = EventBus()
    gpu_stage = BlockingStage()
    cpu_stage = BlockingStage()
    registry = {
        "gpu_job": blocking_recipe("gpu_job", "gpu", gpu_stage),
        "cpu_job": blocking_recipe("cpu_job", "cpu", cpu_stage),
    }
    manager = JobQueueManager(conn, bus, registry=registry)

    manager.submit("gpu_job", {}, [{"track_id": None, "source_path": str(tmp_path / "g.flac")}])
    manager.submit("cpu_job", {}, [{"track_id": None, "source_path": str(tmp_path / "c.flac")}])

    assert gpu_stage.started.wait(timeout=5)
    assert cpu_stage.started.wait(timeout=5)  # both running "at once" - independent lanes

    gpu_stage.release.set()
    cpu_stage.release.set()


def test_two_jobs_on_the_same_lane_run_serially(tmp_path):
    conn = get_connection(tmp_path / "library.db")
    bus = EventBus()
    stage1 = BlockingStage()
    stage2 = BlockingStage()
    registry = {
        "first": blocking_recipe("first", "cpu", stage1),
        "second": blocking_recipe("second", "cpu", stage2),
    }
    manager = JobQueueManager(conn, bus, registry=registry)

    manager.submit("first", {}, [{"track_id": None, "source_path": str(tmp_path / "a.flac")}])
    assert stage1.started.wait(timeout=5)

    manager.submit("second", {}, [{"track_id": None, "source_path": str(tmp_path / "b.flac")}])
    # Bounded negative check, not synchronization: 300ms is generous slack to
    # prove job 2 has NOT started while job 1 still holds the "cpu" lane.
    assert stage2.started.wait(timeout=0.3) is False

    stage1.release.set()
    assert stage2.started.wait(timeout=5)
    stage2.release.set()
