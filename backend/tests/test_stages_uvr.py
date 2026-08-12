from pathlib import Path

import pytest

from app.pipeline import StageContext, StageStatus
from app.stages.karaoke_instrumental import KaraokeInstrumentalStage
from app.stages.uvr import SEPARATION_TIMEOUT_SECONDS, UvrVocalSplitStage, run_uvr_karaoke_ensemble
from app.workers.runner import WorkerResult


class _FakeUvrRunner:
    def __init__(self, status="completed", error_text=None, produce_vocals=True):
        self.status = status
        self.error_text = error_text
        self.produce_vocals = produce_vocals
        self.calls = []
        self.kwargs_calls = []

    def __call__(self, python_executable, script_path, args, **kwargs):
        self.calls.append(args)
        self.kwargs_calls.append(kwargs)
        output_dir = Path(args["output_dir"])
        if self.status != "completed":
            return WorkerResult(status=self.status, payload=None, error_text=self.error_text)
        instrumental = output_dir / "best_instrumental.mp3"
        instrumental.write_bytes(b"fake-instrumental")
        vocals_path = None
        if self.produce_vocals:
            vocals = output_dir / "best_vocals.mp3"
            vocals.write_bytes(b"fake-vocals")
            vocals_path = str(vocals)
        return WorkerResult(
            status="completed", payload={"instrumental": str(instrumental), "vocals": vocals_path}, error_text=None
        )


def test_run_uvr_karaoke_ensemble_returns_instrumental_and_vocals_paths(tmp_path):
    runner = _FakeUvrRunner()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    outputs = run_uvr_karaoke_ensemble(tmp_path / "song.flac", output_dir, Path("fake-python"), tmp_path / "models", runner)

    assert outputs["instrumental"].name == "best_instrumental.mp3"
    assert outputs["vocals"].name == "best_vocals.mp3"
    assert runner.calls[0]["input_path"] == str(tmp_path / "song.flac")


def test_run_uvr_karaoke_ensemble_passes_separation_timeout_to_runner(tmp_path):
    runner = _FakeUvrRunner()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    run_uvr_karaoke_ensemble(tmp_path / "song.flac", output_dir, Path("fake-python"), tmp_path / "models", runner)

    assert runner.kwargs_calls[0]["timeout_seconds"] == SEPARATION_TIMEOUT_SECONDS


def test_run_uvr_karaoke_ensemble_raises_on_worker_failure(tmp_path):
    runner = _FakeUvrRunner(status="failed", error_text="model download failed")

    with pytest.raises(RuntimeError, match="model download failed"):
        run_uvr_karaoke_ensemble(tmp_path / "song.flac", tmp_path / "out", Path("fake"), tmp_path / "models", runner)


def test_uvr_vocal_split_stage_declared_outputs(tmp_path):
    stage = UvrVocalSplitStage(venv_python=Path("fake"), model_dir=tmp_path / "models", runner=_FakeUvrRunner())
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={"split": True})

    assert stage.declared_outputs(ctx) == [
        tmp_path / "song.lead_vocals.mp3",
        tmp_path / "song.backing_vocals.mp3",
    ]


def test_uvr_vocal_split_stage_skips_cheaply_when_split_option_is_false(tmp_path):
    runner = _FakeUvrRunner()
    stage = UvrVocalSplitStage(venv_python=Path("fake"), model_dir=tmp_path / "models", runner=runner)
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={"split": False})

    result = stage.run(ctx)

    assert result.status == StageStatus.SKIPPED
    assert runner.calls == []  # no subprocess spawned


def test_uvr_vocal_split_stage_fails_clearly_when_vocals_stem_missing(tmp_path):
    stage = UvrVocalSplitStage(venv_python=Path("fake"), model_dir=tmp_path / "models", runner=_FakeUvrRunner())
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={"split": True})

    result = stage.run(ctx)

    assert result.status == StageStatus.FAILED
    assert "song.vocals.mp3" in result.detail


def test_uvr_vocal_split_stage_publishes_lead_and_backing_vocals(tmp_path):
    (tmp_path / "song.vocals.mp3").write_bytes(b"already-separated-vocals")
    stage = UvrVocalSplitStage(venv_python=Path("fake"), model_dir=tmp_path / "models", runner=_FakeUvrRunner())
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={"split": True})

    result = stage.run(ctx)

    assert result.status == StageStatus.COMPLETED
    assert (tmp_path / "song.lead_vocals.mp3").read_bytes() == b"fake-vocals"
    assert (tmp_path / "song.backing_vocals.mp3").read_bytes() == b"fake-instrumental"


def test_karaoke_instrumental_stage_best_mode_uses_uvr_not_demucs(tmp_path):
    class _UnusedDemucsRunner:
        def __call__(self, *args, **kwargs):
            raise AssertionError("demucs must not run in 'best' mode")

    stage = KaraokeInstrumentalStage(
        model="htdemucs", device="cpu", backing_vocal_mode="best",
        demucs_venv_python=Path("fake"), demucs_runner=_UnusedDemucsRunner(),
        uvr_venv_python=Path("fake"), uvr_model_dir=tmp_path / "models", uvr_runner=_FakeUvrRunner(),
    )
    ctx = StageContext(
        source_path=tmp_path / "song.flac", overwrite=False,
        options={"processing_profile": "high_quality", "device": "cuda"},
    )

    result = stage.run(ctx)

    assert result.status == StageStatus.COMPLETED
    assert (tmp_path / "song.instrumental.mp3").read_bytes() == b"fake-instrumental"
    assert result.output_provenance[0]["quality"] == "high_quality"
    assert result.output_provenance[0]["engine"] == "uvr_karaoke_ensemble"
    assert result.output_provenance[0]["model"] == "karaoke"
    assert len(result.output_provenance[0]["models"]) == 3


def test_karaoke_instrumental_stage_best_mode_fails_cleanly_on_uvr_error(tmp_path):
    stage = KaraokeInstrumentalStage(
        model="htdemucs", device="cpu", backing_vocal_mode="best",
        uvr_venv_python=Path("fake"), uvr_model_dir=tmp_path / "models",
        uvr_runner=_FakeUvrRunner(status="failed", error_text="GPU required for Best mode"),
    )
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.FAILED
    assert "GPU required" in result.detail
    assert not (tmp_path / "song.instrumental.mp3").exists()


def test_uvr_vocal_split_stage_forwards_cancel_event_and_on_progress_to_the_runner(tmp_path):
    (tmp_path / "song.vocals.mp3").write_bytes(b"already-separated-vocals")
    runner = _FakeUvrRunner()
    stage = UvrVocalSplitStage(venv_python=Path("fake"), model_dir=tmp_path / "models", runner=runner)
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={"split": True})

    stage.run(ctx)

    assert runner.kwargs_calls[0]["cancel_event"] is ctx.cancel_event
    assert runner.kwargs_calls[0]["on_progress"] is ctx.on_progress
