from pathlib import Path

from app.pipeline import StageContext, StageStatus
from app.stages.fetch_lyrics import FetchLyricsStage


class _StubProvider:
    name = "stub"

    def __init__(self, result):
        self._result = result

    def search(self, artist, title):
        return self._result


def _stage(result, monkeypatch, enabled_option_key="fetch_lyrics", artist="ABBA", title="Chiquitita"):
    monkeypatch.setattr("app.stages.fetch_lyrics.read_tags", lambda path: (artist, title))
    return FetchLyricsStage(providers=[_StubProvider(result)], enabled_option_key=enabled_option_key)


def test_declared_outputs_is_the_lrc_path(tmp_path, monkeypatch):
    stage = _stage(None, monkeypatch)
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    assert stage.declared_outputs(ctx) == [tmp_path / "song.lrc"]


def test_writes_the_lrc_from_the_first_provider_hit(tmp_path, monkeypatch):
    stage = _stage(("[00:01.00]la la", True), monkeypatch)
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.COMPLETED
    assert (tmp_path / "song.lrc").read_text(encoding="utf-8") == "[00:01.00]la la"
    assert "stub" in result.detail


def test_skips_without_failing_when_no_lyrics_are_found(tmp_path, monkeypatch):
    stage = _stage(None, monkeypatch)
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.SKIPPED
    assert "no lyrics found" in result.detail
    assert not (tmp_path / "song.lrc").exists()


def test_self_skips_when_the_enabled_option_is_false(tmp_path, monkeypatch):
    stage = _stage(("[00:01.00]la la", True), monkeypatch, enabled_option_key="fetch")
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={"fetch": False})

    result = stage.run(ctx)

    assert result.status == StageStatus.SKIPPED
    assert "not requested" in result.detail
    assert not (tmp_path / "song.lrc").exists()


def test_honors_mirror_output_mode(tmp_path, monkeypatch):
    media_root = tmp_path / "Media" / "ABBA"
    media_root.mkdir(parents=True)
    mirror_root = tmp_path / "Stems"
    stage = _stage(("[00:01.00]la la", True), monkeypatch)
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
    assert (mirror_root / "ABBA" / "song.lrc").read_text(encoding="utf-8") == "[00:01.00]la la"


class _RecordingProvider:
    name = "stub"

    def __init__(self, result):
        self._result = result
        self.calls = 0

    def search(self, artist, title):
        self.calls += 1
        return self._result


def test_overwrite_true_preserves_existing_enhanced_lyrics(tmp_path, monkeypatch):
    monkeypatch.setattr("app.stages.fetch_lyrics.read_tags", lambda path: ("ABBA", "Chiquitita"))
    provider = _RecordingProvider(("[00:02.00]new lyrics", True))
    stage = FetchLyricsStage(providers=[provider])

    lrc_path = tmp_path / "song.lrc"
    enhanced_content = "[00:01.00]<00:01.00>hello<00:01.50> world"
    lrc_path.write_text(enhanced_content, encoding="utf-8")
    original_bytes = lrc_path.read_bytes()

    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=True, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.SKIPPED
    assert "word-timed lyrics preserved" in result.detail
    assert lrc_path.read_bytes() == original_bytes
    assert provider.calls == 0


def test_overwrite_true_replaces_existing_line_timed_lyrics(tmp_path, monkeypatch):
    monkeypatch.setattr("app.stages.fetch_lyrics.read_tags", lambda path: ("ABBA", "Chiquitita"))
    provider = _RecordingProvider(("[00:02.00]new lyrics", True))
    stage = FetchLyricsStage(providers=[provider])

    lrc_path = tmp_path / "song.lrc"
    lrc_path.write_text("[00:01.00]old lyrics", encoding="utf-8")

    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=True, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.COMPLETED
    assert lrc_path.read_text(encoding="utf-8") == "[00:02.00]new lyrics"
    assert provider.calls == 1


def test_falls_back_to_filename_stem_when_title_tag_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("app.stages.fetch_lyrics.read_tags", lambda path: ("ABBA", None))
    captured = {}

    class _CapturingProvider:
        name = "stub"

        def search(self, artist, title):
            captured["artist"] = artist
            captured["title"] = title
            return None

    stage = FetchLyricsStage(providers=[_CapturingProvider()])
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    stage.run(ctx)

    assert captured["title"] == "song"
    assert captured["artist"] == "ABBA"
