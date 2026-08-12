import threading

from app.pipeline import StageResult, StageStatus
from app.queue import execute_item


class _FakeStage:
    def __init__(self, name, result_status=StageStatus.COMPLETED, outputs=None):
        self.name = name
        self._result_status = result_status
        self._outputs = outputs or []
        self.run_calls = 0

    def declared_outputs(self, ctx):
        return self._outputs

    def run(self, ctx):
        self.run_calls += 1
        return StageResult(status=self._result_status, detail=f"{self.name} ran")


def test_runs_all_stages_and_returns_completed_when_all_succeed(tmp_path):
    stages = [_FakeStage("a"), _FakeStage("b")]
    calls = []

    result = execute_item(
        stages,
        tmp_path / "song.flac",
        {},
        overwrite=False,
        cancel_event=threading.Event(),
        on_stage_change=lambda stage, res: calls.append(
            (stage.name, res.status.value if res else "running")
        ),
    )

    assert result == "completed"
    assert calls == [("a", "running"), ("a", "completed"), ("b", "running"), ("b", "completed")]


def test_returns_skipped_when_every_stage_is_skipped(tmp_path):
    stages = [
        _FakeStage("a", result_status=StageStatus.SKIPPED),
        _FakeStage("b", result_status=StageStatus.SKIPPED),
    ]

    result = execute_item(
        stages, tmp_path / "song.flac", {}, overwrite=False,
        cancel_event=threading.Event(), on_stage_change=lambda stage, res: None,
    )

    assert result == "skipped"


def test_stops_after_a_failed_stage_and_returns_failed(tmp_path):
    stages = [_FakeStage("a", result_status=StageStatus.FAILED), _FakeStage("b")]

    result = execute_item(
        stages, tmp_path / "song.flac", {}, overwrite=False,
        cancel_event=threading.Event(), on_stage_change=lambda stage, res: None,
    )

    assert result == "failed"
    assert stages[1].run_calls == 0


def test_cancellation_set_between_stages_stops_the_remaining_stages(tmp_path):
    stages = [_FakeStage("a"), _FakeStage("b")]
    cancel_event = threading.Event()

    def on_stage_change(stage, res):
        if stage.name == "a" and res is not None:
            cancel_event.set()  # cancel right after stage "a" completes

    result = execute_item(
        stages, tmp_path / "song.flac", {}, overwrite=False,
        cancel_event=cancel_event, on_stage_change=on_stage_change,
    )

    assert result == "cancelled"
    assert stages[0].run_calls == 1
    assert stages[1].run_calls == 0


def test_cancellation_set_before_any_stage_starts_skips_everything(tmp_path):
    stages = [_FakeStage("a"), _FakeStage("b")]
    cancel_event = threading.Event()
    cancel_event.set()

    def fail_if_called(stage, res):
        raise AssertionError("on_stage_change should not be called")

    result = execute_item(
        stages, tmp_path / "song.flac", {}, overwrite=False,
        cancel_event=cancel_event, on_stage_change=fail_if_called,
    )

    assert result == "cancelled"
    assert stages[0].run_calls == 0
    assert stages[1].run_calls == 0


def test_stage_skipped_due_to_existing_outputs_never_announces_running(tmp_path):
    existing_output = tmp_path / "already-there.txt"
    existing_output.write_text("done", encoding="utf-8")
    stage = _FakeStage("a", outputs=[existing_output])
    calls = []

    result = execute_item(
        [stage],
        tmp_path / "song.flac",
        {},
        overwrite=False,
        cancel_event=threading.Event(),
        on_stage_change=lambda stage, res: calls.append(
            (stage.name, res.status.value if res else "running")
        ),
    )

    assert result == "skipped"
    # Exactly one announcement, with the real SKIPPED status - no transient
    # ("a", "running") call before it, since the skip decision is made
    # up-front via should_skip().
    assert calls == [("a", "skipped")]
    assert stage.run_calls == 0


def test_a_stage_that_completes_despite_a_mid_run_cancellation_still_reports_completed(tmp_path):
    cancel_event = threading.Event()

    class _StageThatSetsCancelDuringItsOwnRun:
        name = "sets_cancel_during_run"

        def declared_outputs(self, ctx):
            return []

        def run(self, ctx):
            # Simulates cancellation arriving from another thread while this
            # stage is mid-flight, but the stage's own work finishes anyway
            # (e.g. a subprocess that was already past the point of no
            # return) - its genuinely COMPLETED result must be honored.
            ctx.cancel_event.set()
            return StageResult(status=StageStatus.COMPLETED, detail="finished anyway")

    result = execute_item(
        [_StageThatSetsCancelDuringItsOwnRun()],
        tmp_path / "song.flac", {}, False, cancel_event,
        on_stage_change=lambda stage, res: None,
    )

    assert result == "completed"
