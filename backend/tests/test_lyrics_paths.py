from pathlib import Path

from app.lyrics.paths import resolve_lrc_path


def test_beside_mode_is_the_default():
    source = Path("D:/Media/ABBA/Song.flac")
    assert resolve_lrc_path(source, {}) == Path("D:/Media/ABBA/Song.lrc")


def test_mirror_mode_resolves_under_the_matching_media_root():
    source = Path("D:/Media/ABBA/Song.flac")
    options = {"output_mode": "mirror", "media_roots": ["D:/Media"], "mirror_roots": ["D:/Stems"]}
    assert resolve_lrc_path(source, options) == Path("D:/Stems/ABBA/Song.lrc")


def test_mirror_mode_falls_back_to_beside_when_no_mirror_root_configured():
    source = Path("D:/Media/ABBA/Song.flac")
    options = {"output_mode": "mirror", "media_roots": ["D:/Media"], "mirror_roots": []}
    assert resolve_lrc_path(source, options) == Path("D:/Media/ABBA/Song.lrc")


def test_mirror_mode_falls_back_to_beside_when_source_is_outside_every_media_root():
    source = Path("D:/Other/Song.flac")
    options = {"output_mode": "mirror", "media_roots": ["D:/Media"], "mirror_roots": ["D:/Stems"]}
    assert resolve_lrc_path(source, options) == Path("D:/Other/Song.lrc")
