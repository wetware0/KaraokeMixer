from pathlib import Path

from app.metadata.providers import TagsMatch
from app.pipeline import StageContext, StageStatus
from app.scanner import ExtendedTags
from app.stages.fetch_tags import FetchTagsStage


class _StubProvider:
    name = "stub"

    def __init__(self, match):
        self._match = match

    def search(self, artist, title):
        return self._match


def _patch(monkeypatch, *, current, has_artwork, downloaded=(b"art-bytes", "image/jpeg")):
    monkeypatch.setattr("app.stages.fetch_tags.read_extended_tags", lambda path: current)
    monkeypatch.setattr(
        "app.stages.fetch_tags.read_embedded_artwork", lambda path: (b"existing", "image/jpeg") if has_artwork else None
    )
    monkeypatch.setattr("app.stages.fetch_tags.download_artwork", lambda url, http_client=None: downloaded)

    written_tags = {}
    written_artwork = {}
    monkeypatch.setattr(
        "app.stages.fetch_tags.write_text_tags",
        lambda path, **kwargs: written_tags.update(kwargs),
    )
    monkeypatch.setattr(
        "app.stages.fetch_tags.write_embedded_artwork",
        lambda path, data, mime: written_artwork.update(data=data, mime=mime),
    )
    return written_tags, written_artwork


def test_declared_outputs_is_always_empty(tmp_path):
    stage = FetchTagsStage()
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})
    assert stage.declared_outputs(ctx) == []


def test_skips_unsupported_formats_without_calling_any_provider(tmp_path, monkeypatch):
    called = []
    stage = FetchTagsStage(providers=[_StubProvider(None)])
    monkeypatch.setattr(
        "app.stages.fetch_tags.search_tags_providers",
        lambda *a, **k: called.append(1) or None,
    )
    ctx = StageContext(source_path=tmp_path / "song.wav", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.SKIPPED
    assert "unsupported format" in result.detail
    assert called == []


def test_skips_when_everything_is_already_present_and_not_overwriting(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("provider should not be searched when nothing is missing")

    _patch(
        monkeypatch,
        current=ExtendedTags(artist="ABBA", title="Song", album="Arrival", year=1976),
        has_artwork=True,
    )
    monkeypatch.setattr("app.stages.fetch_tags.search_tags_providers", _boom)
    stage = FetchTagsStage()
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.SKIPPED
    assert "already present" in result.detail


def test_fills_missing_album_year_and_artwork_from_the_provider(tmp_path, monkeypatch):
    written_tags, written_artwork = _patch(
        monkeypatch,
        current=ExtendedTags(artist="ABBA", title="Dancing Queen", album=None, year=None),
        has_artwork=False,
    )
    match = TagsMatch(artist="ABBA", title="Dancing Queen", album="Arrival", year=1976, artwork_url="https://x/a.jpg")
    monkeypatch.setattr("app.stages.fetch_tags.search_tags_providers", lambda a, t, p: (match, "stub"))
    stage = FetchTagsStage()
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.COMPLETED
    assert result.refresh_track_metadata is True
    assert "stub" in result.detail
    assert written_tags == {"artist": "ABBA", "title": "Dancing Queen", "album": "Arrival", "year": 1976}
    assert written_artwork == {"data": b"art-bytes", "mime": "image/jpeg"}


def test_never_overwrites_existing_album_year_or_artwork_when_overwrite_is_false(tmp_path, monkeypatch):
    written_tags, written_artwork = _patch(
        monkeypatch,
        current=ExtendedTags(artist="ABBA", title="Dancing Queen", album="Original Album", year=1970),
        has_artwork=True,
    )
    # missing "year"? no - both album/year present here, so this exercises
    # the "has_artwork True but overwrite False" branch alone by making year
    # missing so the provider IS consulted, but overwrite=False must still
    # preserve the already-present artwork and non-empty fields.
    current = ExtendedTags(artist="ABBA", title="Dancing Queen", album="Original Album", year=None)
    monkeypatch.setattr("app.stages.fetch_tags.read_extended_tags", lambda path: current)
    match = TagsMatch(artist="Someone Else", title="X", album="New Album", year=1999, artwork_url="https://x/a.jpg")
    monkeypatch.setattr("app.stages.fetch_tags.search_tags_providers", lambda a, t, p: (match, "stub"))
    stage = FetchTagsStage()
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.COMPLETED
    assert written_tags["album"] == "Original Album"  # untouched - only year was missing
    assert written_tags["year"] == 1999  # filled in, since it was missing
    assert written_artwork == {}  # already had artwork, overwrite=False


def test_overwrite_true_replaces_existing_album_year_and_artwork(tmp_path, monkeypatch):
    written_tags, written_artwork = _patch(
        monkeypatch,
        current=ExtendedTags(artist="ABBA", title="Dancing Queen", album="Old Album", year=1970),
        has_artwork=True,
    )
    match = TagsMatch(artist="ABBA", title="Dancing Queen", album="New Album", year=1999, artwork_url="https://x/a.jpg")
    monkeypatch.setattr("app.stages.fetch_tags.search_tags_providers", lambda a, t, p: (match, "stub"))
    stage = FetchTagsStage()
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=True, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.COMPLETED
    assert written_tags["album"] == "New Album"
    assert written_tags["year"] == 1999
    assert written_artwork == {"data": b"art-bytes", "mime": "image/jpeg"}


def test_skips_when_no_provider_match_is_found(tmp_path, monkeypatch):
    _patch(
        monkeypatch,
        current=ExtendedTags(artist="ABBA", title="Dancing Queen", album=None, year=None),
        has_artwork=False,
    )
    monkeypatch.setattr("app.stages.fetch_tags.search_tags_providers", lambda a, t, p: None)
    stage = FetchTagsStage()
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.SKIPPED
    assert "no metadata match" in result.detail
    assert result.refresh_track_metadata is True


def test_skips_when_the_provider_match_adds_nothing_new(tmp_path, monkeypatch):
    written_tags, written_artwork = _patch(
        monkeypatch,
        current=ExtendedTags(artist="ABBA", title="Dancing Queen", album=None, year=None),
        has_artwork=True,
    )
    match = TagsMatch(artist="ABBA", title="Dancing Queen", album=None, year=None, artwork_url=None)
    monkeypatch.setattr("app.stages.fetch_tags.search_tags_providers", lambda a, t, p: (match, "stub"))
    stage = FetchTagsStage()
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.SKIPPED
    assert "no new data" in result.detail
    assert written_tags == {}
    assert written_artwork == {}


# --- Contract-deviation coverage: write_text_tags/write_embedded_artwork now
# raise ValueError (never crash the pipeline worker) on corrupt/unwritable
# files - the stage must catch that and report StageStatus.FAILED with the
# exception's message, matching every other stage's
# `except ... as exc: return StageResult(status=StageStatus.FAILED, detail=str(exc))`
# pattern (see app/stages/align_lyrics.py, uvr.py, demucs.py, karaoke_instrumental.py).

def test_failed_when_write_text_tags_raises_value_error(tmp_path, monkeypatch):
    _patch(
        monkeypatch,
        current=ExtendedTags(artist="ABBA", title="Dancing Queen", album=None, year=None),
        has_artwork=True,
    )
    match = TagsMatch(artist="ABBA", title="Dancing Queen", album="Arrival", year=1976, artwork_url=None)
    monkeypatch.setattr("app.stages.fetch_tags.search_tags_providers", lambda a, t, p: (match, "stub"))

    def _boom(path, **kwargs):
        raise ValueError(f"cannot write tags to {path.name}: corrupt file")

    monkeypatch.setattr("app.stages.fetch_tags.write_text_tags", _boom)
    stage = FetchTagsStage()
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.FAILED
    assert "corrupt file" in result.detail


def test_failed_when_write_embedded_artwork_raises_value_error(tmp_path, monkeypatch):
    _patch(
        monkeypatch,
        current=ExtendedTags(artist="ABBA", title="Dancing Queen", album="Arrival", year=1976),
        has_artwork=False,
    )
    match = TagsMatch(artist="ABBA", title="Dancing Queen", album=None, year=None, artwork_url="https://x/a.jpg")
    monkeypatch.setattr("app.stages.fetch_tags.search_tags_providers", lambda a, t, p: (match, "stub"))

    def _boom(path, data, mime):
        raise ValueError(f"cannot write artwork to {path.name}: unwritable")

    monkeypatch.setattr("app.stages.fetch_tags.write_embedded_artwork", _boom)
    stage = FetchTagsStage()
    ctx = StageContext(source_path=tmp_path / "song.flac", overwrite=False, options={})

    result = stage.run(ctx)

    assert result.status == StageStatus.FAILED
    assert "unwritable" in result.detail
