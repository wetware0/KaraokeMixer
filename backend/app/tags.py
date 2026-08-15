from __future__ import annotations

from pathlib import Path

from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, ID3NoHeaderError, APIC
from mutagen.mp4 import MP4, MP4Cover

_MP4_COVER_FORMATS = {"image/jpeg": MP4Cover.FORMAT_JPEG, "image/png": MP4Cover.FORMAT_PNG}
_SUPPORTED_SUFFIXES = {".flac", ".mp3", ".m4a"}


def _require_supported(path: Path, purpose: str) -> str:
    suffix = path.suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported audio format for {purpose}: {suffix}")
    return suffix


def read_embedded_artwork(path: Path) -> tuple[bytes, str] | None:
    """Returns (bytes, mime) for the first embedded picture, or None if the
    format is unsupported, has no embedded picture, or is corrupt. Never raises."""
    suffix = path.suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        return None

    try:
        if suffix == ".flac":
            audio = FLAC(path)
            if not audio.pictures:
                return None
            picture = audio.pictures[0]
            return picture.data, picture.mime or "image/jpeg"

        if suffix == ".mp3":
            try:
                id3 = ID3(path)
            except ID3NoHeaderError:
                return None
            frames = id3.getall("APIC")
            if not frames:
                return None
            return frames[0].data, frames[0].mime or "image/jpeg"

        # suffix == ".m4a"
        audio = MP4(path)
        covers = audio.get("covr")
        if not covers:
            return None
        cover = covers[0]
        mime = "image/png" if cover.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
        return bytes(cover), mime
    except Exception:
        # Corrupt file, missing header, or other read error - return None
        return None


def has_embedded_artwork(path: Path) -> bool:
    """Return whether a supported audio container has embedded artwork.

    This deliberately avoids copying the image payload. The library scanner
    needs only a compact catalogue flag so an 80,000-row UI can filter
    artwork without issuing one HTTP artwork request per track.
    """
    suffix = path.suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        return False
    try:
        if suffix == ".flac":
            return bool(FLAC(path).pictures)
        if suffix == ".mp3":
            try:
                return bool(ID3(path).getall("APIC"))
            except ID3NoHeaderError:
                return False
        return bool(MP4(path).get("covr"))
    except Exception:
        return False


def write_embedded_artwork(path: Path, data: bytes, mime: str) -> None:
    """Embeds/replaces the file's cover picture. Only ever touches the
    metadata container - never the compressed audio payload.
    Raises ValueError for unsupported formats or invalid MIME types."""
    suffix = _require_supported(path, "artwork")

    if mime not in _MP4_COVER_FORMATS:
        raise ValueError(f"Unsupported MIME type for artwork: {mime}. Must be 'image/jpeg' or 'image/png'")

    try:
        if suffix == ".flac":
            audio = FLAC(path)
            audio.clear_pictures()
            picture = Picture()
            picture.type = 3  # "Cover (front)"
            picture.mime = mime
            picture.data = data
            audio.add_picture(picture)
            audio.save()
            return

        if suffix == ".mp3":
            try:
                id3 = ID3(path)
            except ID3NoHeaderError:
                id3 = ID3()
            id3.delall("APIC")
            id3.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
            id3.save(path)
            return

        # suffix == ".m4a"
        audio = MP4(path)
        image_format = _MP4_COVER_FORMATS.get(mime, MP4Cover.FORMAT_JPEG)
        audio["covr"] = [MP4Cover(data, imageformat=image_format)]
        audio.save()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"cannot write artwork to {path.name}: {exc}") from exc


def _set_or_clear(audio, key: str, value: str | None) -> None:
    if value:
        audio[key] = [value]
    elif key in audio:
        del audio[key]


def write_text_tags(path: Path, *, artist: str | None, title: str, album: str | None, year: int | None) -> None:
    """Writes artist/title/album/year into the file's metadata container.
    `title` is always set (never cleared); artist/album/year are cleared
    when passed None. Only ever touches the metadata container - never the
    compressed audio payload. Raises ValueError for unsupported formats or
    corrupt files."""
    suffix = _require_supported(path, "tags")
    date_value = str(year) if year is not None else None

    try:
        if suffix == ".flac":
            audio = FLAC(path)
            _set_or_clear(audio, "artist", artist)
            audio["title"] = [title]
            _set_or_clear(audio, "album", album)
            _set_or_clear(audio, "date", date_value)
            audio.save()
            return

        if suffix == ".mp3":
            try:
                audio = EasyID3(path)
            except ID3NoHeaderError:
                fresh = EasyID3()
                fresh.save(path)
                audio = EasyID3(path)
            _set_or_clear(audio, "artist", artist)
            audio["title"] = [title]
            _set_or_clear(audio, "album", album)
            _set_or_clear(audio, "date", date_value)
            audio.save()
            return

        # suffix == ".m4a"
        audio = MP4(path)
        _set_or_clear(audio, "\xa9ART", artist)
        audio["\xa9nam"] = [title]
        _set_or_clear(audio, "\xa9alb", album)
        _set_or_clear(audio, "\xa9day", date_value)
        audio.save()
    except Exception as exc:
        raise ValueError(f"cannot write tags to {path.name}: {exc}") from exc
