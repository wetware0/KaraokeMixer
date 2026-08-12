from pathlib import Path

import pytest

from app.pipeline import StageContext, StageStatus
from app.stages.demucs import SEPARATION_TIMEOUT_SECONDS, DemucsSeparateStage, separate_to_temp, stem_index_map
from app.workers.runner import WorkerResult


def test_stem_index_map_four_stem_model():
    assert stem_index_map("htdemucs") == {"drums": 0, "bass": 1, "other": 2, "vocals": 3}


def test_stem_index_map_six_stem_model_includes_guitar_and_piano():
    index_map = stem_index_map("htdemucs_6s")
    assert index_map["guitar"] == 4
    assert index_map["piano"] == 5
    assert index_map["vocals"] == 3


def test_stem_index_map_rejects_unknown_model():
    with pytest.raises(ValueError):
        stem_index_map("not_a_real_model")


class _FakeRunner:
    def __init__(self, status="completed", error_text=None):
        self.status = status
        self.error_text = error_text
        self.calls = []
        self.kwargs_calls = []

    def __call__(self, python_executable, script_path, args, **kwargs):
        self.calls.append(args)
        self.kwargs_calls.append(kwargs)
        if self.status == "completed":
            for path in args["output_paths"].values():
                Path(path).write_bytes(b"RIFF....fake-wav-bytes")
        return WorkerResult(
            status=self.status, payload={"stems": list(args["stem_indices"])}, error_text=self.error_text
        )


def test_separate_to_temp_builds_correct_args_and_returns_stem_paths(tmp_path):
    runner = _FakeRunner()

    result = separate_to_temp(
        tmp_path / "song.flac", "htdemucs", "cpu", ["vocals", "drums"], tmp_path, Path("fake-python"), runner
    )

    assert result == {"vocals": tmp_path / "vocals.wav", "drums": tmp_path / "drums.wav"}
    args = runner.calls[0]
    assert args["model"] == "htdemucs"
    assert args["device"] == "cpu"
    assert args["stem_indices"] == {"vocals": 3, "drums": 0}


def test_separate_to_temp_passes_separation_timeout_to_runner(tmp_path):
    runner = _FakeRunner()

    separate_to_temp(tmp_path / "song.flac", "htdemucs", "cpu", ["vocals"], tmp_path, Path("fake-python"), runner)

    assert runner.kwargs_calls[0]["timeout_seconds"] == SEPARATION_TIMEOUT_SECONDS


def test_separate_to_temp_raises_runtime_error_on_worker_failure(tmp_path):
    runner = _FakeRunner(status="failed", error_text="GPU OOM")

    with pytest.raises(RuntimeError, match="GPU OOM"):
        separate_to_temp(tmp_path / "song.flac", "htdemucs", "cpu", ["vocals"], tmp_path, Path("fake-python"), runner)


def test_demucs_separate_stage_declared_outputs_beside(tmp_path):
    stage = DemucsSeparateStage(
        model="htdemucs", device="cpu", stems=["vocals", "drums"], venv_python=Path("fake"), runner=_FakeRunner()
    )
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    outputs = stage.declared_outputs(ctx)

    assert outputs == [tmp_path / "song.vocals.mp3", tmp_path / "song.drums.mp3"]


def test_combined_demucs_stage_declares_instrumental_without_a_second_separation(tmp_path, monkeypatch):
    monkeypatch.setattr("app.stages.demucs.read_wav", lambda _path: (object(), 44100))
    monkeypatch.setattr("app.stages.demucs.combine_stems", lambda *_stems: object())
    monkeypatch.setattr("app.stages.demucs.apply_backing_vocal_mix", lambda _bed, _vocals, _mode: object())
    monkeypatch.setattr("app.stages.demucs.write_wav", lambda path, _audio, _rate: path.write_bytes(b"wav"))
    monkeypatch.setattr("app.stages.demucs.export_mp3", lambda _wav, mp3: mp3.write_bytes(b"mp3"))
    runner = _FakeRunner()
    stage = DemucsSeparateStage(
        model="htdemucs",
        device="cpu",
        stems=["drums", "bass", "other", "vocals"],
        venv_python=Path("fake"),
        runner=runner,
        instrumental_mode="stripped",
    )
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.COMPLETED
    assert len(runner.calls) == 1
    assert (tmp_path / "song.instrumental.mp3").read_bytes() == b"mp3"
    assert stage.declared_outputs(ctx)[-1] == tmp_path / "song.instrumental.mp3"
    assert result.output_provenance[0]["engine"] == "demucs"
    assert result.output_provenance[0]["model"] == "htdemucs"


def test_demucs_separate_stage_publishes_mp3_for_each_stem(tmp_path, monkeypatch):
    monkeypatch.setattr("app.stages.demucs.export_mp3", lambda wav, mp3: mp3.write_bytes(b"ID3-fake-mp3"))
    runner = _FakeRunner()
    stage = DemucsSeparateStage(
        model="htdemucs", device="cpu", stems=["vocals"], venv_python=Path("fake"), runner=runner
    )
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.COMPLETED
    assert (tmp_path / "song.vocals.mp3").read_bytes() == b"ID3-fake-mp3"
    assert not (tmp_path / "song.vocals.mp3.part").exists()


def test_demucs_separate_stage_returns_failed_on_worker_error_and_publishes_nothing(tmp_path):
    stage = DemucsSeparateStage(
        model="htdemucs", device="cpu", stems=["vocals"], venv_python=Path("fake"),
        runner=_FakeRunner(status="failed", error_text="GPU OOM - try CPU or a lighter model"),
    )
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.FAILED
    assert "GPU OOM" in result.detail
    assert not (tmp_path / "song.vocals.mp3").exists()


def test_demucs_separate_stage_honors_mirror_output_mode(tmp_path, monkeypatch):
    monkeypatch.setattr("app.stages.demucs.export_mp3", lambda wav, mp3: mp3.write_bytes(b"fake-mp3"))
    media_root = tmp_path / "Media" / "ABBA"
    media_root.mkdir(parents=True)
    mirror_root = tmp_path / "Stems"
    stage = DemucsSeparateStage(
        model="htdemucs", device="cpu", stems=["vocals"], venv_python=Path("fake"), runner=_FakeRunner()
    )
    ctx = StageContext(
        source_path=media_root / "song.flac",
        overwrite=False,
        options={
            "output_mode": "mirror",
            "media_roots": [str(tmp_path / "Media")],
            "mirror_roots": [str(mirror_root)],
        },
    )

    result = stage.run(ctx)

    assert result.status == StageStatus.COMPLETED
    assert (mirror_root / "ABBA" / "song.vocals.mp3").read_bytes() == b"fake-mp3"


def test_demucs_separate_stage_forwards_cancel_event_and_on_progress_to_the_runner(tmp_path, monkeypatch):
    monkeypatch.setattr("app.stages.demucs.export_mp3", lambda wav, mp3: mp3.write_bytes(b"fake-mp3"))
    runner = _FakeRunner()
    stage = DemucsSeparateStage(
        model="htdemucs", device="cpu", stems=["vocals"], venv_python=Path("fake"), runner=runner
    )
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    stage.run(ctx)

    assert runner.kwargs_calls[0]["cancel_event"] is ctx.cancel_event
    assert runner.kwargs_calls[0]["on_progress"] is ctx.on_progress
