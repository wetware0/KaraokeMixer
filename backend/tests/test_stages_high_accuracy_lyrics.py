from pathlib import Path

import pytest

from app.lyrics.provenance import read_lyric_timing_report
from app.pipeline import StageContext, StageStatus
from app.stages import high_accuracy_lyrics as high_module
from app.stages.high_accuracy_lyrics import (
    HighAccuracyLyricsStage,
    PrepareVocalReferenceStage,
    vocal_reference_cache_path,
)
from app.workers.runner import WorkerResult


class _SequenceRunner:
    def __init__(self, payloads: list[list[dict]]):
        self.payloads = list(payloads)
        self.calls: list[dict] = []

    def __call__(self, _python, _script, args, **_kwargs):
        self.calls.append(args)
        return WorkerResult("completed", {"words": self.payloads.pop(0)}, None)


def _word(word: str, start: float, score: float = 0.9) -> dict:
    return {"word": word, "start": start, "end": start + 0.2, "score": score}


@pytest.fixture
def fake_python(tmp_path: Path) -> Path:
    path = tmp_path / "venv" / "python.exe"
    path.parent.mkdir()
    path.write_bytes(b"")
    return path


def test_vocal_reference_cache_path_supports_windows_paths(tmp_path: Path):
    source = tmp_path / "song.flac"
    source.write_bytes(b"audio")

    cache = vocal_reference_cache_path(source)

    assert cache.suffix == ".wav"
    assert cache.parent.name == "lyric-vocals"


def test_prepare_stage_writes_the_temporary_vocal_cache(
    tmp_path: Path, monkeypatch, fake_python: Path,
):
    source = tmp_path / "song.flac"
    source.write_bytes(b"audio")
    cache = tmp_path / "cache" / "vocals.wav"
    monkeypatch.setattr(high_module, "vocal_reference_cache_path", lambda _source: cache)

    def fake_separate(_source, _model, _device, _stems, temporary, *_args, **kwargs):
        assert kwargs["shifts"] == 0
        vocals = temporary / "vocals.wav"
        vocals.write_bytes(b"isolated vocals")
        return {"vocals": vocals}

    monkeypatch.setattr(high_module, "separate_to_temp", fake_separate)
    stage = PrepareVocalReferenceStage(
        enabled=True, device="cuda", venv_python=fake_python,
    )

    result = stage.run(StageContext(source, False, {"media_roots": [str(tmp_path)]}))

    assert result.status == StageStatus.COMPLETED
    assert cache.read_bytes() == b"isolated vocals"

    stage.cleanup(StageContext(source, False, {"media_roots": [str(tmp_path)]}))

    assert not cache.exists()


def test_high_accuracy_stage_uses_vocal_transcript_and_exact_alignment(
    tmp_path: Path, monkeypatch, fake_python: Path,
):
    source = tmp_path / "song.flac"
    source.write_bytes(b"audio")
    lrc = tmp_path / "song.lrc"
    original_lrc = "[00:10.00]<00:10.00>Hello <00:11.00>world\n"
    lrc.write_text(original_lrc, encoding="utf-8", newline="")
    cache = tmp_path / "cache" / "vocals.wav"
    cache.parent.mkdir()
    cache.write_bytes(b"isolated vocals")
    monkeypatch.setattr(high_module, "vocal_reference_cache_path", lambda _source: cache)
    monkeypatch.setattr(high_module, "_probe_duration", lambda _source: 20.0)
    runner = _SequenceRunner([
        [_word("Hello", 1.0), _word("world", 2.0)],
        [_word("Hello", 1.02), _word("world", 2.02)],
    ])
    stage = HighAccuracyLyricsStage(
        device="cuda", asr_model="medium", venv_python=fake_python, runner=runner,
    )

    result = stage.run(StageContext(source, False, {"media_roots": [str(tmp_path)]}))

    assert result.status == StageStatus.COMPLETED
    assert lrc.read_text(encoding="utf-8") == "[00:01.02]<00:01.02>Hello<00:02.02> world\n"
    assert (tmp_path / "song.before-confidence.lrc").read_text(encoding="utf-8") == original_lrc
    assert not cache.exists()
    assert [call["mode"] for call in runner.calls] == ["transcribe", "align"]
    report = read_lyric_timing_report(lrc)
    assert report is not None
    assert report["summary"]["method"] == "isolated_vocal_transcript_alignment_v1"
    assert report["summary"]["corrected_words"] == 2


def test_high_accuracy_stage_preserves_concurrent_manual_edit(
    tmp_path: Path, monkeypatch, fake_python: Path,
):
    source = tmp_path / "song.flac"
    source.write_bytes(b"audio")
    lrc = tmp_path / "song.lrc"
    lrc.write_text("[00:01.00]<00:01.00>Hello\n", encoding="utf-8", newline="")
    cache = tmp_path / "cache.wav"
    cache.write_bytes(b"vocals")
    monkeypatch.setattr(high_module, "vocal_reference_cache_path", lambda _source: cache)
    monkeypatch.setattr(high_module, "_probe_duration", lambda _source: 20.0)

    class EditingRunner(_SequenceRunner):
        def __call__(self, *args, **kwargs):
            result = super().__call__(*args, **kwargs)
            if len(self.calls) == 2:
                lrc.write_text("[00:03.00]<00:03.00>Manual\n", encoding="utf-8", newline="")
            return result

    runner = EditingRunner([[_word("Hello", 1.0)], [_word("Hello", 1.0)]])
    stage = HighAccuracyLyricsStage(device="cuda", venv_python=fake_python, runner=runner)

    result = stage.run(StageContext(source, False, {"media_roots": [str(tmp_path)]}))

    assert result.status == StageStatus.FAILED
    assert "changed while" in result.detail
    assert lrc.read_text(encoding="utf-8") == "[00:03.00]<00:03.00>Manual\n"
