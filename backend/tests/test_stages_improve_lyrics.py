from pathlib import Path

import pytest

from app.lyrics.provenance import read_lyric_timing_report
from app.pipeline import StageContext, StageStatus
from app.stages import improve_lyrics as improve_module
from app.stages.improve_lyrics import ImproveLyricsStage
from app.workers.runner import WorkerResult


class _SequenceRunner:
    def __init__(self, payloads: list[list[dict]]):
        self.payloads = list(payloads)
        self.calls: list[dict] = []

    def __call__(self, _python, _script, args, **_kwargs):
        self.calls.append(args)
        return WorkerResult("completed", {"words": self.payloads.pop(0)}, None)


@pytest.fixture
def fake_python(tmp_path: Path) -> Path:
    path = tmp_path / "venv" / "python.exe"
    path.parent.mkdir()
    path.write_bytes(b"")
    return path


def _word(word: str, start: float, score: float = 0.9) -> dict:
    return {"word": word, "start": start, "end": start + 0.2, "score": score}


def test_corrects_supported_words_keeps_disputed_words_and_writes_backup(
    tmp_path: Path, monkeypatch, fake_python: Path,
):
    source = tmp_path / "song.flac"
    source.write_bytes(b"audio")
    (tmp_path / "song.instrumental.mp3").write_bytes(b"instrumental")
    lrc = tmp_path / "song.lrc"
    original_lrc = "[00:01.00]<00:01.00>Hello <00:03.50>world\n"
    lrc.write_text(original_lrc, encoding="utf-8", newline="")
    runner = _SequenceRunner([
        [_word("Hello", 2.00), _word("world", 3.00)],
        [_word("Hello", 2.04), _word("world", 4.00)],
    ])
    monkeypatch.setattr(improve_module, "_probe_duration", lambda _path: 10.0)

    def residual_builder(_source: Path, _instrumental: Path, destination: Path) -> None:
        destination.write_bytes(b"residual")

    stage = ImproveLyricsStage(
        device="cuda", venv_python=fake_python, runner=runner,
        residual_builder=residual_builder,
    )
    result = stage.run(StageContext(source, False, {"media_roots": [str(tmp_path)]}))

    assert result.status == StageStatus.COMPLETED
    assert "confidence" in result.detail
    content = lrc.read_text(encoding="utf-8")
    assert "<00:02.02>Hello" in content
    assert "<00:03.50> world" in content
    assert (tmp_path / "song.before-confidence.lrc").read_text(encoding="utf-8") == original_lrc
    report = read_lyric_timing_report(lrc)
    assert report is not None
    assert report["summary"]["corrected_words"] == 1
    assert report["summary"]["review_words"] == 1
    assert report["words"][0]["status"] == "verified"
    assert report["words"][1]["status"] == "review"
    assert len(runner.calls) == 2
    assert all(call["normalize_audio"] is True for call in runner.calls)


def test_low_evidence_coverage_leaves_lrc_unchanged(
    tmp_path: Path, monkeypatch, fake_python: Path,
):
    source = tmp_path / "song.flac"
    source.write_bytes(b"audio")
    (tmp_path / "song.instrumental.mp3").write_bytes(b"instrumental")
    lrc = tmp_path / "song.lrc"
    original_lrc = "[00:01.00]<00:01.00>one <00:02.00>two <00:03.00>three\n"
    lrc.write_text(original_lrc, encoding="utf-8", newline="")
    runner = _SequenceRunner([[_word("one", 1.0)], [_word("one", 1.0)]])
    monkeypatch.setattr(improve_module, "_probe_duration", lambda _path: 10.0)
    stage = ImproveLyricsStage(
        venv_python=fake_python, runner=runner,
        residual_builder=lambda _a, _b, dest: dest.write_bytes(b"residual"),
    )

    result = stage.run(StageContext(source, False, {"media_roots": [str(tmp_path)]}))

    assert result.status == StageStatus.FAILED
    assert "did not match enough words" in result.detail
    assert lrc.read_text(encoding="utf-8") == original_lrc
    assert not (tmp_path / "song.before-confidence.lrc").exists()


def test_skips_without_instrumental(tmp_path: Path, fake_python: Path):
    source = tmp_path / "song.flac"
    source.write_bytes(b"audio")
    (tmp_path / "song.lrc").write_text(
        "[00:01.00]<00:01.00>Hello\n", encoding="utf-8", newline="",
    )

    result = ImproveLyricsStage(venv_python=fake_python).run(
        StageContext(source, False, {"media_roots": [str(tmp_path)]}),
    )

    assert result.status == StageStatus.SKIPPED
    assert "instrumental" in result.detail


def test_concurrent_lyric_edit_wins_and_is_not_overwritten(
    tmp_path: Path, monkeypatch, fake_python: Path,
):
    source = tmp_path / "song.flac"
    source.write_bytes(b"audio")
    (tmp_path / "song.instrumental.mp3").write_bytes(b"instrumental")
    lrc = tmp_path / "song.lrc"
    lrc.write_text("[00:01.00]<00:01.00>Hello\n", encoding="utf-8", newline="")
    runner = _SequenceRunner([[_word("Hello", 2.0)], [_word("Hello", 2.0)]])
    monkeypatch.setattr(improve_module, "_probe_duration", lambda _path: 10.0)

    def edit_during_analysis(_source: Path, _instrumental: Path, destination: Path) -> None:
        lrc.write_text("[00:03.00]<00:03.00>Newer\n", encoding="utf-8", newline="")
        destination.write_bytes(b"residual")

    stage = ImproveLyricsStage(
        venv_python=fake_python, runner=runner, residual_builder=edit_during_analysis,
    )
    result = stage.run(StageContext(source, False, {"media_roots": [str(tmp_path)]}))

    assert result.status == StageStatus.FAILED
    assert "changed while" in result.detail
    assert lrc.read_text(encoding="utf-8") == "[00:03.00]<00:03.00>Newer\n"
    assert not (tmp_path / "song.before-confidence.lrc").exists()
