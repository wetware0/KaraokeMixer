from pathlib import Path

import pytest
from pydub import AudioSegment

from app.tags import has_embedded_artwork, read_embedded_artwork, write_embedded_artwork, write_text_tags

_FFMPEG_FORMAT = {"flac": "flac", "mp3": "mp3", "m4a": "ipod"}


def _make_audio_file(tmp_path: Path, suffix: str) -> Path:
    """Real, ffmpeg-encoded fixture (requires ffmpeg on PATH - already a
    documented project prerequisite, see test_stages_audio_io.py's
    test_export_mp3_writes_a_non_empty_file). mutagen's tag/artwork WRITE
    path operates on genuine container structure, not raw bytes, so a
    placeholder byte string is not sufficient here the way it is for the
    scanner's read-only, monkeypatched-mutagen tests."""
    fmt = _FFMPEG_FORMAT[suffix.lstrip(".")]
    path = tmp_path / f"song{suffix}"
    AudioSegment.silent(duration=100, frame_rate=8000).export(str(path), format=fmt)
    return path


@pytest.mark.parametrize("suffix", [".flac", ".mp3", ".m4a"])
def test_write_text_tags_then_read_back_round_trips(tmp_path, suffix):
    path = _make_audio_file(tmp_path, suffix)

    write_text_tags(path, artist="ABBA", title="Dancing Queen", album="Arrival", year=1976)

    from app.scanner import read_extended_tags

    extended = read_extended_tags(path)
    assert extended.artist == "ABBA"
    assert extended.title == "Dancing Queen"
    assert extended.album == "Arrival"
    assert extended.year == 1976


@pytest.mark.parametrize("suffix", [".flac", ".mp3", ".m4a"])
def test_write_text_tags_clears_album_when_given_none(tmp_path, suffix):
    path = _make_audio_file(tmp_path, suffix)
    write_text_tags(path, artist="ABBA", title="Song", album="Old Album", year=1976)

    write_text_tags(path, artist="ABBA", title="Song", album=None, year=1976)

    from app.scanner import read_extended_tags

    assert read_extended_tags(path).album is None


@pytest.mark.parametrize("suffix", [".flac", ".mp3", ".m4a"])
def test_read_embedded_artwork_returns_none_when_there_is_none(tmp_path, suffix):
    path = _make_audio_file(tmp_path, suffix)
    assert read_embedded_artwork(path) is None


@pytest.mark.parametrize("suffix", [".flac", ".mp3", ".m4a"])
def test_write_then_read_embedded_artwork_round_trips_bytes_and_mime(tmp_path, suffix):
    path = _make_audio_file(tmp_path, suffix)
    data = b"\xff\xd8\xff\xe0FAKEJPEGBYTES"

    write_embedded_artwork(path, data, "image/jpeg")

    result = read_embedded_artwork(path)
    assert result is not None
    read_data, mime = result
    assert read_data == data
    assert mime == "image/jpeg"
    assert has_embedded_artwork(path) is True


@pytest.mark.parametrize("suffix", [".flac", ".mp3", ".m4a"])
def test_has_embedded_artwork_is_false_when_there_is_none(tmp_path, suffix):
    assert has_embedded_artwork(_make_audio_file(tmp_path, suffix)) is False


@pytest.mark.parametrize("suffix", [".flac", ".mp3", ".m4a"])
def test_write_embedded_artwork_replaces_a_previous_picture(tmp_path, suffix):
    path = _make_audio_file(tmp_path, suffix)
    write_embedded_artwork(path, b"old-bytes", "image/jpeg")

    write_embedded_artwork(path, b"new-bytes", "image/png")

    read_data, mime = read_embedded_artwork(path)
    assert read_data == b"new-bytes"
    assert mime == "image/png"


def test_write_text_tags_rejects_an_unsupported_format(tmp_path):
    path = tmp_path / "song.wav"
    path.write_bytes(b"RIFF....WAVEfmt ")

    with pytest.raises(ValueError):
        write_text_tags(path, artist="A", title="T", album=None, year=None)


def test_write_embedded_artwork_rejects_an_unsupported_format(tmp_path):
    path = tmp_path / "song.wav"
    path.write_bytes(b"RIFF....WAVEfmt ")

    with pytest.raises(ValueError):
        write_embedded_artwork(path, b"data", "image/jpeg")


def test_read_embedded_artwork_returns_none_for_an_unsupported_format(tmp_path):
    path = tmp_path / "song.wav"
    path.write_bytes(b"RIFF....WAVEfmt ")

    assert read_embedded_artwork(path) is None


def test_read_embedded_artwork_returns_none_for_corrupt_flac(tmp_path):
    """Corrupt FLAC (valid suffix, garbage content) returns None, does not raise."""
    path = tmp_path / "song.flac"
    path.write_bytes(b"fLaC\x00\x00\x00garbage content that is not valid FLAC")

    result = read_embedded_artwork(path)
    assert result is None


def test_write_text_tags_raises_for_corrupt_flac(tmp_path):
    """Corrupt FLAC raises ValueError (normalized), not mutagen's FLACNoHeaderError."""
    path = tmp_path / "song.flac"
    path.write_bytes(b"fLaC\x00\x00\x00garbage content that is not valid FLAC")

    with pytest.raises(ValueError, match="cannot write tags"):
        write_text_tags(path, artist="A", title="T", album=None, year=None)


@pytest.mark.parametrize("suffix", [".flac", ".mp3", ".m4a"])
def test_write_embedded_artwork_rejects_invalid_mime(tmp_path, suffix):
    """Invalid MIME type raises ValueError on all supported formats."""
    path = _make_audio_file(tmp_path, suffix)

    with pytest.raises(ValueError, match="Unsupported MIME type"):
        write_embedded_artwork(path, b"data", "image/gif")


@pytest.mark.parametrize("suffix", [".flac", ".mp3", ".m4a"])
def test_write_metadata_does_not_modify_audio_payload(tmp_path, suffix):
    """Writes to metadata only - PCM payload must be byte-identical before/after."""
    import subprocess

    path = _make_audio_file(tmp_path, suffix)

    # Decode to PCM before write
    pcm_before = subprocess.run(
        ["ffmpeg", "-i", str(path), "-f", "s16le", "-"],
        capture_output=True,
        check=True,
    ).stdout

    # Write metadata
    write_text_tags(path, artist="Artist", title="Title", album="Album", year=2024)
    write_embedded_artwork(path, b"\xff\xd8\xff\xe0FAKEJPEGBYTES", "image/jpeg")

    # Decode to PCM after write
    pcm_after = subprocess.run(
        ["ffmpeg", "-i", str(path), "-f", "s16le", "-"],
        capture_output=True,
        check=True,
    ).stdout

    # PCM must be byte-identical
    assert pcm_before == pcm_after, "Audio payload was modified during metadata write"
