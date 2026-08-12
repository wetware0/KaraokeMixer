from pathlib import Path

from app.output_paths import resolve_output_path


def test_beside_mode_is_the_default_when_output_mode_is_absent():
    source = Path("D:/Media/ABBA/Song.flac")
    assert resolve_output_path(source, "instrumental", {}) == Path("D:/Media/ABBA/Song.instrumental.mp3")


def test_beside_mode_explicit():
    source = Path("D:/Media/ABBA/Song.flac")
    options = {"output_mode": "beside"}
    assert resolve_output_path(source, "vocals", options) == Path("D:/Media/ABBA/Song.vocals.mp3")


def test_mirror_mode_resolves_under_the_matching_media_root():
    source = Path("D:/Media/ABBA/Song.flac")
    options = {
        "output_mode": "mirror",
        "media_roots": ["D:/Media"],
        "mirror_roots": ["D:/Stems"],
    }
    assert resolve_output_path(source, "drums", options) == Path("D:/Stems/ABBA/Song.drums.mp3")


def test_mirror_mode_falls_back_to_beside_when_no_mirror_root_configured():
    source = Path("D:/Media/ABBA/Song.flac")
    options = {"output_mode": "mirror", "media_roots": ["D:/Media"], "mirror_roots": []}
    assert resolve_output_path(source, "drums", options) == Path("D:/Media/ABBA/Song.drums.mp3")


def test_mirror_mode_falls_back_to_beside_when_source_is_outside_every_media_root():
    source = Path("D:/Other/Song.flac")
    options = {
        "output_mode": "mirror",
        "media_roots": ["D:/Media"],
        "mirror_roots": ["D:/Stems"],
    }
    assert resolve_output_path(source, "drums", options) == Path("D:/Other/Song.drums.mp3")


def test_mirror_mode_uses_the_first_configured_mirror_root():
    source = Path("D:/Media/ABBA/Song.flac")
    options = {
        "output_mode": "mirror",
        "media_roots": ["D:/Media"],
        "mirror_roots": ["D:/StemsA", "D:/StemsB"],
    }
    assert resolve_output_path(source, "bass", options) == Path("D:/StemsA/ABBA/Song.bass.mp3")
