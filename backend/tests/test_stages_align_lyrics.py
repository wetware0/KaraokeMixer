import subprocess

import pytest

from app.pipeline import StageContext, StageStatus
from app.stages.align_lyrics import AlignLyricsStage
from app.workers.runner import WorkerResult


class _FakeRunner:
    def __init__(self, words):
        self._words = words
        self.calls = []
        self.kwargs_calls = []

    def __call__(self, python_executable, script_path, args, **kwargs):
        self.calls.append(args)
        self.kwargs_calls.append(kwargs)
        return WorkerResult(status="completed", payload={"words": self._words}, error_text=None)


@pytest.fixture
def fake_venv_python(tmp_path):
    """A stand-in whisperx venv python that exists on disk, so the "is the
    whisperx worker venv installed" guard at the top of run() (regression
    coverage: test_skips_when_the_whisperx_venv_is_not_installed below) does
    not shadow every other test in this file - none of them are testing venv
    presence, they inject a _FakeRunner and never touch the real
    .venv-whisperx (which does not exist on a plain dev/CI checkout - see
    README's "Worker venv setup", intentionally never created by any
    automated test)."""
    venv_python = tmp_path / "fake-whisperx-venv" / "python.exe"
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_bytes(b"")
    return venv_python


def test_declared_outputs_is_always_empty_content_based_skip_only(tmp_path, fake_venv_python):
    stage = AlignLyricsStage(runner=_FakeRunner([]), venv_python=fake_venv_python)
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    assert stage.declared_outputs(ctx) == []


def test_skips_when_no_lrc_exists(tmp_path, fake_venv_python):
    stage = AlignLyricsStage(runner=_FakeRunner([]), venv_python=fake_venv_python)
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.SKIPPED
    assert "no LRC" in result.detail


def test_skips_when_lrc_already_enhanced(tmp_path, fake_venv_python):
    (tmp_path / "song.lrc").write_text("[00:01.00]<00:01.00>hello", encoding="utf-8")
    stage = AlignLyricsStage(runner=_FakeRunner([]), venv_python=fake_venv_python)
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.SKIPPED
    assert "enhanced" in result.detail


def test_realign_enhanced_runs_alignment_again_when_explicitly_requested(tmp_path, monkeypatch, fake_venv_python):
    lrc = tmp_path / "song.lrc"
    lrc.write_text("[00:01.00]<00:01.00>hello<00:01.50> world", encoding="utf-8")
    runner = _FakeRunner([
        {"word": "hello", "start": 1.2, "end": 1.5, "score": 0.9},
        {"word": "world", "start": 1.7, "end": 2.0, "score": 0.9},
    ])
    stage = AlignLyricsStage(runner=runner, venv_python=fake_venv_python, realign_enhanced=True)
    monkeypatch.setattr(AlignLyricsStage, "_probe_duration", lambda self, path: 10.0)
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.COMPLETED
    assert runner.calls[0]["mode"] == "align"
    assert lrc.read_text(encoding="utf-8") == "[00:01.20]<00:01.20>hello<00:01.70> world"


def test_reset_existing_timing_retranscribes_the_entire_file_and_removes_old_breaks(tmp_path, fake_venv_python):
    lrc = tmp_path / "song.lrc"
    lrc.write_text(
        "[ar:Artist]\n[00:01.00]<00:01.00>hello<00:01.50> world\n[00:05.00]\n[00:10.00]<00:10.00>again\n",
        encoding="utf-8",
    )
    runner = _FakeRunner([
        {"word": "hello", "start": 2.0, "end": 2.4, "score": 0.9},
        {"word": "world", "start": 2.5, "end": 2.9, "score": 0.9},
        {"word": "again", "start": 8.0, "end": 8.5, "score": 0.9},
    ])
    stage = AlignLyricsStage(
        runner=runner,
        asr_model="small.en",
        venv_python=fake_venv_python,
        reset_existing_timing=True,
    )
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.COMPLETED
    assert runner.calls == [{
        "audio_path": str(tmp_path / "song.flac"),
        "language": "en",
        "device": "cpu",
        "mode": "transcribe",
        "asr_model": "small.en",
    }]
    rendered = lrc.read_text(encoding="utf-8")
    assert rendered == (
        "[ar:Artist]\n"
        "[00:02.00]<00:02.00>hello<00:02.50> world\n"
        "\n"
        "[00:08.00]<00:08.00>again\n"
    )
    assert "00:01.00" not in rendered
    assert "00:05.00" not in rendered
    assert "00:10.00" not in rendered


def test_aligns_a_line_timed_lrc_and_writes_enhanced_tags(tmp_path, monkeypatch, fake_venv_python):
    lrc = tmp_path / "song.lrc"
    lrc.write_text("[00:01.00]hello world", encoding="utf-8")
    runner = _FakeRunner([
        {"word": "hello", "start": 1.0, "end": 1.4, "score": 0.9},
        {"word": "world", "start": 1.5, "end": 1.9, "score": 0.9},
    ])
    stage = AlignLyricsStage(runner=runner, venv_python=fake_venv_python)
    monkeypatch.setattr(AlignLyricsStage, "_probe_duration", lambda self, path: 10.0)
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.COMPLETED
    assert lrc.read_text(encoding="utf-8") == "[00:01.00]<00:01.00>hello<00:01.50> world"
    assert runner.calls[0]["mode"] == "align"


def test_aligns_a_cp1252_encoded_line_timed_lrc_without_a_unicode_decode_error(tmp_path, monkeypatch, fake_venv_python):
    # Regression: run() used to read the LRC with a strict
    # read_text(encoding="utf-8"), which raises UnicodeDecodeError on a
    # cp1252-encoded file (e.g. containing a curly quote at byte 0x92) even
    # though classify_lrc_file() (via lrc.read_lrc_text) tolerantly
    # classified the very same file moments earlier without complaint.
    lrc = tmp_path / "song.lrc"
    # "café" written with a cp1252 e-acute (0xE9) - not valid UTF-8 on its own.
    lrc.write_bytes("[00:01.00]caf\xe9 world".encode("cp1252"))
    runner = _FakeRunner([
        {"word": "cafe", "start": 1.0, "end": 1.4, "score": 0.9},
        {"word": "world", "start": 1.5, "end": 1.9, "score": 0.9},
    ])
    stage = AlignLyricsStage(runner=runner, venv_python=fake_venv_python)
    monkeypatch.setattr(AlignLyricsStage, "_probe_duration", lambda self, path: 10.0)
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.COMPLETED


def test_transcribes_an_untimed_lrc(tmp_path, fake_venv_python):
    (tmp_path / "song.lrc").write_text("hello world", encoding="utf-8")
    runner = _FakeRunner([
        {"word": "hello", "start": 0.5, "end": 0.9, "score": 0.9},
        {"word": "world", "start": 1.0, "end": 1.4, "score": 0.9},
    ])
    stage = AlignLyricsStage(runner=runner, asr_model="small.en", venv_python=fake_venv_python)
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.COMPLETED
    assert runner.calls[0]["mode"] == "transcribe"
    assert runner.calls[0]["asr_model"] == "small.en"


def test_fails_cleanly_on_worker_error(tmp_path, fake_venv_python):
    (tmp_path / "song.lrc").write_text("hello world", encoding="utf-8")

    class _FailingRunner:
        def __call__(self, *args, **kwargs):
            return WorkerResult(status="failed", payload=None, error_text="GPU OOM")

    stage = AlignLyricsStage(runner=_FailingRunner(), venv_python=fake_venv_python)
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.FAILED
    assert "GPU OOM" in result.detail


def test_respects_the_enabled_option_key_for_the_lyrics_only_recipe(tmp_path, fake_venv_python):
    (tmp_path / "song.lrc").write_text("[00:01.00]hello", encoding="utf-8")
    stage = AlignLyricsStage(runner=_FakeRunner([]), enabled_option_key="align", venv_python=fake_venv_python)
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={"align": False})

    result = stage.run(ctx)

    assert result.status == StageStatus.SKIPPED
    assert "not requested" in result.detail


def test_forwards_cancel_event_and_on_progress_to_the_runner(tmp_path, fake_venv_python):
    (tmp_path / "song.lrc").write_text("hello", encoding="utf-8")
    runner = _FakeRunner([{"word": "hello", "start": 0.1, "end": 0.5, "score": 0.9}])
    stage = AlignLyricsStage(runner=runner, venv_python=fake_venv_python)
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    stage.run(ctx)

    assert runner.kwargs_calls[0]["cancel_event"] is ctx.cancel_event
    assert runner.kwargs_calls[0]["on_progress"] is ctx.on_progress


def test_skips_when_the_whisperx_venv_is_not_installed(tmp_path):
    nonexistent_venv_python = tmp_path / "no-such-venv" / "python.exe"

    class _RunnerThatMustNotBeCalled:
        def __call__(self, *args, **kwargs):
            raise AssertionError("runner must not be called when the whisperx venv is missing")

    stage = AlignLyricsStage(runner=_RunnerThatMustNotBeCalled(), venv_python=nonexistent_venv_python)
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.SKIPPED
    assert "WhisperX worker" in result.detail
    assert "README" in result.detail


def test_explicit_editor_retime_fails_when_enhanced_word_timing_worker_is_unavailable(tmp_path):
    nonexistent_venv_python = tmp_path / "no-such-venv" / "python.exe"

    class _RunnerThatMustNotBeCalled:
        def __call__(self, *args, **kwargs):
            raise AssertionError("runner must not be called when the whisperx venv is missing")

    stage = AlignLyricsStage(
        runner=_RunnerThatMustNotBeCalled(),
        venv_python=nonexistent_venv_python,
        reset_existing_timing=True,
        require_worker=True,
    )
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.FAILED
    assert "Enhanced word timing requires the WhisperX worker" in result.detail


def test_ffprobe_failure_is_caught_and_reported_as_a_failed_stage_result(tmp_path, monkeypatch, fake_venv_python):
    (tmp_path / "song.lrc").write_text("[00:01.00]hello world", encoding="utf-8")
    stage = AlignLyricsStage(runner=_FakeRunner([]), venv_python=fake_venv_python)

    def _raise_ffprobe_failure(self, path):
        raise subprocess.CalledProcessError(returncode=1, cmd=["ffprobe"], stderr="no such file")

    monkeypatch.setattr(AlignLyricsStage, "_probe_duration", _raise_ffprobe_failure)
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.FAILED


def test_a_malformed_worker_payload_missing_words_is_caught_and_reported_as_a_failed_stage_result(
    tmp_path, fake_venv_python
):
    (tmp_path / "song.lrc").write_text("hello world", encoding="utf-8")

    class _MalformedPayloadRunner:
        def __call__(self, *args, **kwargs):
            return WorkerResult(status="completed", payload={}, error_text=None)  # missing "words"

    stage = AlignLyricsStage(runner=_MalformedPayloadRunner(), venv_python=fake_venv_python)
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.FAILED
