from pathlib import Path
from unittest.mock import MagicMock

from app.scanner import scan_media_root


def test_scan_media_root_lists_sources_and_skips_generated_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr("app.scanner.mutagen.File", lambda path, easy=True: None)

    media_root = tmp_path / "Media"
    (media_root / "ABBA").mkdir(parents=True)
    (media_root / "ABBA" / "Dancing Queen.flac").write_bytes(b"")
    (media_root / "ABBA" / "Dancing Queen.instrumental.mp3").write_bytes(b"")
    (media_root / "ABBA" / "Dancing Queen.drums.mp3").write_bytes(b"")
    (media_root / "ABBA" / "Dancing Queen.lrc").write_text("[00:01.00]Hello\n", encoding="utf-8")
    (media_root / "notes.txt").write_text("not audio", encoding="utf-8")

    records = scan_media_root(media_root, mirror_roots=[])

    assert len(records) == 1
    record = records[0]
    assert record.relative_path == str(Path("ABBA") / "Dancing Queen.flac")
    assert record.title == "Dancing Queen"
    assert record.artist is None
    assert record.outputs.instrumental is True
    assert record.outputs.drums is True
    assert record.outputs.lrc is True
    assert record.lrc_state == "line_timed"
    # instrumental has its own badge and is not a stem — only drums counts here
    assert record.stem_count == 1


def test_scan_media_root_returns_empty_list_for_empty_folder(tmp_path):
    media_root = tmp_path / "Empty"
    media_root.mkdir()
    assert scan_media_root(media_root, mirror_roots=[]) == []


def test_scan_media_root_ignores_dot_prefixed_audio_files(tmp_path, monkeypatch):
    monkeypatch.setattr("app.scanner.mutagen.File", lambda path, easy=True: None)
    monkeypatch.setattr("app.scanner.read_duration_seconds", lambda path: None)

    media_root = tmp_path / "Media"
    media_root.mkdir()
    (media_root / ".temporary.flac").write_bytes(b"")
    (media_root / "Visible.flac").write_bytes(b"")

    records = scan_media_root(media_root, mirror_roots=[])

    assert [record.relative_path for record in records] == ["Visible.flac"]


def test_scan_media_root_populates_album_year_and_duration(tmp_path, monkeypatch):
    fake_tags = MagicMock()
    fake_tags.get.side_effect = lambda key: {
        "artist": ["ABBA"], "title": ["Dancing Queen"], "album": ["Arrival"], "date": ["1976"],
    }.get(key)
    fake_audio = MagicMock(tags=fake_tags)
    monkeypatch.setattr("app.scanner.mutagen.File", lambda path, easy=True: fake_audio)
    monkeypatch.setattr("app.scanner.read_duration_seconds", lambda path: 213.5)

    media_root = tmp_path / "Media"
    (media_root / "ABBA").mkdir(parents=True)
    (media_root / "ABBA" / "Dancing Queen.flac").write_bytes(b"")

    record = scan_media_root(media_root, mirror_roots=[])[0]

    assert record.album == "Arrival"
    assert record.year == 1976
    assert record.duration_seconds == 213.5


def test_scan_media_root_leaves_album_year_duration_none_when_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr("app.scanner.mutagen.File", lambda path, easy=True: None)
    monkeypatch.setattr("app.scanner.read_duration_seconds", lambda path: None)

    media_root = tmp_path / "Media"
    media_root.mkdir()
    (media_root / "Song.flac").write_bytes(b"")

    record = scan_media_root(media_root, mirror_roots=[])[0]

    assert record.album is None
    assert record.year is None
    assert record.duration_seconds is None


def test_scan_media_root_survives_when_one_files_extraction_raises(tmp_path, monkeypatch):
    # One file has malformed tags that raise during extraction, the other is fine.
    # Scan should survive and process both files.
    def mutagen_with_one_broken(path, easy=True):
        if "broken" in str(path):
            fake_tags = MagicMock()
            fake_tags.get.side_effect = RuntimeError("corrupted metadata")
            return MagicMock(tags=fake_tags)
        # Normal file
        fake_tags = MagicMock()
        fake_tags.get.side_effect = lambda key: {"artist": ["Good Artist"], "title": ["Good Song"]}.get(key)
        return MagicMock(tags=fake_tags)

    monkeypatch.setattr("app.scanner.mutagen.File", mutagen_with_one_broken)
    monkeypatch.setattr("app.scanner.read_duration_seconds", lambda path: 100.0)

    media_root = tmp_path / "Media"
    media_root.mkdir()
    (media_root / "broken.flac").write_bytes(b"")
    (media_root / "good.flac").write_bytes(b"")

    records = scan_media_root(media_root, mirror_roots=[])

    # Should have scanned both files despite one raising
    assert len(records) == 2

    # Broken file falls back to stem + None metadata
    broken_record = next(r for r in records if r.relative_path == "broken.flac")
    assert broken_record.title == "broken"
    assert broken_record.artist is None
    assert broken_record.album is None

    # Good file scanned normally
    good_record = next(r for r in records if r.relative_path == "good.flac")
    assert good_record.title == "Good Song"
    assert good_record.artist == "Good Artist"
