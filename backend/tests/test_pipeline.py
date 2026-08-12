import threading

from app.pipeline import (
    StageContext,
    StageResult,
    StageStatus,
    atomic_publish,
    run_stage,
    should_skip,
)


class _Stage:
    name = "test_stage"

    def __init__(self, outputs=None, result_status=StageStatus.COMPLETED):
        self._outputs = outputs or []
        self._result_status = result_status
        self.run_calls = 0

    def declared_outputs(self, ctx):
        return self._outputs

    def run(self, ctx):
        self.run_calls += 1
        return StageResult(status=self._result_status, detail="ran")


def _ctx(tmp_path, overwrite=False):
    return StageContext(source_path=tmp_path / "song.flac", overwrite=overwrite, options={})


def test_run_stage_runs_when_no_declared_outputs(tmp_path):
    stage = _Stage(outputs=[])
    result = run_stage(stage, _ctx(tmp_path))
    assert stage.run_calls == 1
    assert result.status == StageStatus.COMPLETED


def test_run_stage_runs_when_declared_outputs_missing(tmp_path):
    stage = _Stage(outputs=[tmp_path / "missing.txt"])
    result = run_stage(stage, _ctx(tmp_path))
    assert stage.run_calls == 1
    assert result.status == StageStatus.COMPLETED


def test_run_stage_skips_when_declared_outputs_exist_and_not_overwrite(tmp_path):
    output = tmp_path / "exists.txt"
    output.write_text("already here", encoding="utf-8")
    stage = _Stage(outputs=[output])

    result = run_stage(stage, _ctx(tmp_path, overwrite=False))

    assert stage.run_calls == 0
    assert result.status == StageStatus.SKIPPED


def test_run_stage_runs_when_overwrite_true_even_if_outputs_exist(tmp_path):
    output = tmp_path / "exists.txt"
    output.write_text("already here", encoding="utf-8")
    stage = _Stage(outputs=[output])

    result = run_stage(stage, _ctx(tmp_path, overwrite=True))

    assert stage.run_calls == 1
    assert result.status == StageStatus.COMPLETED


def test_should_skip_is_true_when_outputs_exist_and_not_overwrite(tmp_path):
    output = tmp_path / "exists.txt"
    output.write_text("already here", encoding="utf-8")
    stage = _Stage(outputs=[output])

    assert should_skip(stage, _ctx(tmp_path, overwrite=False)) is True


def test_should_skip_is_false_when_outputs_missing_or_overwrite_true(tmp_path):
    missing_stage = _Stage(outputs=[tmp_path / "missing.txt"])
    assert should_skip(missing_stage, _ctx(tmp_path, overwrite=False)) is False

    output = tmp_path / "exists.txt"
    output.write_text("already here", encoding="utf-8")
    overwrite_stage = _Stage(outputs=[output])
    assert should_skip(overwrite_stage, _ctx(tmp_path, overwrite=True)) is False


def test_atomic_publish_writes_destination_and_removes_part_file(tmp_path):
    destination = tmp_path / "output.txt"

    atomic_publish(destination, lambda part: part.write_text("hello", encoding="utf-8"))

    assert destination.read_text(encoding="utf-8") == "hello"
    assert not (tmp_path / "output.txt.part").exists()


def test_atomic_publish_cleans_up_part_file_and_reraises_on_write_failure(tmp_path):
    destination = tmp_path / "output.txt"

    def failing_write(part):
        part.write_text("partial", encoding="utf-8")
        raise ValueError("disk full")

    try:
        atomic_publish(destination, failing_write)
        raised = False
    except ValueError:
        raised = True

    assert raised
    assert not destination.exists()
    assert not (tmp_path / "output.txt.part").exists()


def test_atomic_publish_creates_missing_parent_directories(tmp_path):
    destination = tmp_path / "nested" / "deeper" / "output.txt"

    atomic_publish(destination, lambda part: part.write_text("hello", encoding="utf-8"))

    assert destination.read_text(encoding="utf-8") == "hello"
    assert not (destination.parent / (destination.name + ".part")).exists()


def test_stage_context_defaults_a_fresh_cancel_event_and_no_progress_callback(tmp_path):
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    assert isinstance(ctx.cancel_event, threading.Event)
    assert ctx.cancel_event.is_set() is False
    assert ctx.on_progress is None


def test_stage_context_gives_each_instance_its_own_cancel_event(tmp_path):
    first = StageContext(source_path=tmp_path / "a.flac", overwrite=False, options={})
    second = StageContext(source_path=tmp_path / "b.flac", overwrite=False, options={})

    first.cancel_event.set()

    assert second.cancel_event.is_set() is False
