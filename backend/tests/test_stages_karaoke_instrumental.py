from pathlib import Path

import numpy as np

from app.pipeline import StageContext, StageStatus
from app.stages.karaoke_instrumental import KaraokeInstrumentalStage
from app.workers.runner import WorkerResult


class _FakeDemucsRunner:
    def __init__(self, stems: dict[str, np.ndarray], sample_rate: int = 8000):
        self._stems = stems
        self._sample_rate = sample_rate
        self.calls = []
        self.kwargs_calls = []

    def __call__(self, python_executable, script_path, args, **kwargs):
        self.calls.append(args)
        self.kwargs_calls.append(kwargs)
        from app.stages.audio_io import write_wav

        for stem, path in args["output_paths"].items():
            write_wav(Path(path), self._stems[stem], self._sample_rate)
        return WorkerResult(status="completed", payload={"stems": list(args["stem_indices"])}, error_text=None)


def _tone(samples=400, value=0.1):
    return np.full((2, samples), value, dtype=np.float32)


def _stems():
    return {
        "drums": _tone(value=0.1),
        "bass": _tone(value=0.05),
        "other": _tone(value=0.05),
        "vocals": _tone(value=0.4),
    }


def test_declared_outputs_is_just_the_instrumental_file(tmp_path):
    stage = KaraokeInstrumentalStage(
        model="htdemucs", device="cpu", backing_vocal_mode="stripped",
        demucs_venv_python=Path("fake"), demucs_runner=_FakeDemucsRunner(_stems()),
    )
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    assert stage.declared_outputs(ctx) == [tmp_path / "song.instrumental.mp3"]


def test_stripped_mode_publishes_instrumental_and_requests_all_four_stems(tmp_path, monkeypatch):
    monkeypatch.setattr("app.stages.karaoke_instrumental.export_mp3", lambda wav, mp3: mp3.write_bytes(b"fake-mp3"))
    runner = _FakeDemucsRunner(_stems())
    stage = KaraokeInstrumentalStage(
        model="htdemucs", device="cpu", backing_vocal_mode="stripped",
        demucs_venv_python=Path("fake"), demucs_runner=runner,
    )
    ctx = StageContext(
        source_path=tmp_path / "song.flac", overwrite=False,
        options={"processing_profile": "balanced", "device": "cpu"},
    )

    result = stage.run(ctx)

    assert result.status == StageStatus.COMPLETED
    assert (tmp_path / "song.instrumental.mp3").read_bytes() == b"fake-mp3"
    assert runner.calls[0]["stem_indices"] == {"drums": 0, "bass": 1, "other": 2, "vocals": 3}
    assert result.output_provenance[0]["quality"] == "balanced"
    assert result.output_provenance[0]["engine"] == "demucs"
    assert result.output_provenance[0]["model"] == "htdemucs"


def test_six_stem_model_requests_guitar_and_piano_too(tmp_path, monkeypatch):
    monkeypatch.setattr("app.stages.karaoke_instrumental.export_mp3", lambda wav, mp3: mp3.write_bytes(b"fake-mp3"))
    stems = {**_stems(), "guitar": _tone(value=0.02), "piano": _tone(value=0.02)}
    runner = _FakeDemucsRunner(stems)
    stage = KaraokeInstrumentalStage(
        model="htdemucs_6s", device="cpu", backing_vocal_mode="stripped",
        demucs_venv_python=Path("fake"), demucs_runner=runner,
    )
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    stage.run(ctx)

    assert runner.calls[0]["stem_indices"] == {
        "drums": 0, "bass": 1, "other": 2, "vocals": 3, "guitar": 4, "piano": 5,
    }


def test_demucs_worker_failure_fails_the_stage_and_publishes_nothing(tmp_path):
    class _FailingRunner:
        def __call__(self, python_executable, script_path, args, **kwargs):
            return WorkerResult(status="failed", payload=None, error_text="GPU OOM")

    stage = KaraokeInstrumentalStage(
        model="htdemucs", device="cpu", backing_vocal_mode="stripped",
        demucs_venv_python=Path("fake"), demucs_runner=_FailingRunner(),
    )
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.FAILED
    assert "GPU OOM" in result.detail
    assert not (tmp_path / "song.instrumental.mp3").exists()


def test_constructor_rejects_unsupported_backing_vocal_mode():
    try:
        KaraokeInstrumentalStage(model="htdemucs", device="cpu", backing_vocal_mode="loud")
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_karaoke_instrumental_stage_forwards_cancel_event_and_on_progress_in_fast_mode(tmp_path, monkeypatch):
    monkeypatch.setattr("app.stages.karaoke_instrumental.export_mp3", lambda wav, mp3: mp3.write_bytes(b"fake-mp3"))
    runner = _FakeDemucsRunner(_stems())
    stage = KaraokeInstrumentalStage(
        model="htdemucs", device="cpu", backing_vocal_mode="stripped",
        demucs_venv_python=Path("fake"), demucs_runner=runner,
    )
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    stage.run(ctx)

    assert runner.kwargs_calls[0]["cancel_event"] is ctx.cancel_event
    assert runner.kwargs_calls[0]["on_progress"] is ctx.on_progress
