from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
import re

import mutagen

from .duration import read_duration_seconds
from .instrumental_provenance import read_instrumental_provenance_tag
from .lrc import classify_lrc_file
from .lyrics.provenance import read_lyric_timing_provenance
from .release_year import is_plausible_release_year
from .tags import has_embedded_artwork

AUDIO_EXTENSIONS = {".flac", ".mp3", ".m4a", ".wav", ".aac", ".ogg"}

PART_NAMES = [
    "instrumental",
    "vocals",
    "lead_vocals",
    "backing_vocals",
    "drums",
    "bass",
    "guitar",
    "piano",
    "other",
]

_GENERATED_OUTPUT_RE = re.compile(
    r"\.(" + "|".join(PART_NAMES) + r")\.mp3$",
    re.IGNORECASE,
)


def is_generated_output(path: Path) -> bool:
    """True if the filename matches the {stem}.{part}.mp3 output convention."""
    return bool(_GENERATED_OUTPUT_RE.search(path.name))


def _first_tag(tags, key: str) -> str | None:
    values = tags.get(key)
    if not values:
        return None
    value = str(values[0]).strip()
    return value or None


def _joined_tag(tags, *keys: str) -> str | None:
    """Return all non-empty values for the first populated tag key.

    Mutagen exposes the Windows "Contributing artists" field as ``artist``.
    Some containers store more than one contributor as separate values, so
    preserve them instead of silently dropping everyone after the first.
    """
    for key in keys:
        values = tags.get(key)
        if not values:
            continue
        normalized = [str(value).strip() for value in values if str(value).strip()]
        if normalized:
            return "; ".join(dict.fromkeys(normalized))
    return None


_YEAR_RE = re.compile(r"(\d{4})")


def _parse_year(date_value: str | None) -> int | None:
    if not date_value:
        return None
    try:
        # Coerce to string in case the tag is not a string
        date_str = str(date_value)
        match = _YEAR_RE.search(date_str)
        if not match:
            return None
        year = int(match.group(1))
        return year if is_plausible_release_year(year) else None
    except Exception:
        return None


@dataclass
class ExtendedTags:
    artist: str | None
    title: str
    album: str | None
    year: int | None
    has_artwork: bool | None = None


def read_extended_tags(path: Path, *, include_artwork: bool = True) -> ExtendedTags:
    """Like read_tags, but also surfaces album/year - the single mutagen
    open this and read_tags now share. Survives any exception during extraction
    by falling back to defaults (title from stem, others None)."""
    artist: str | None = None
    title: str | None = None
    album: str | None = None
    year: int | None = None

    try:
        audio = mutagen.File(path, easy=True)
        if audio is not None and audio.tags is not None:
            artist = _joined_tag(audio.tags, "artist") or _joined_tag(
                audio.tags, "albumartist", "album artist"
            )
            title = _first_tag(audio.tags, "title")
            album = _first_tag(audio.tags, "album")
            year = _parse_year(
                _first_tag(audio.tags, "date")
                or _first_tag(audio.tags, "year")
                or _first_tag(audio.tags, "originaldate")
                or _first_tag(audio.tags, "original date")
            )
    except Exception:
        # Malformed tags, encoding issues, or any other exception during extraction
        # — survive and use defaults. Caller will see title from stem + None for metadata.
        pass

    return ExtendedTags(
        artist=artist,
        title=title or path.stem,
        album=album,
        year=year,
        has_artwork=has_embedded_artwork(path) if include_artwork else None,
    )


def read_tags(path: Path) -> tuple[str | None, str]:
    """Return (artist, title); title falls back to the filename stem."""
    # Lyrics lookup needs only these two fields. Avoid a second container read
    # for artwork in this hot path; scans and metadata refreshes request the
    # complete ExtendedTags record and populate the catalogue flag.
    extended = read_extended_tags(path, include_artwork=False)
    return extended.artist, extended.title


@dataclass
class TrackOutputs:
    instrumental: bool = False
    vocals: bool = False
    lead_vocals: bool = False
    backing_vocals: bool = False
    drums: bool = False
    bass: bool = False
    guitar: bool = False
    piano: bool = False
    other: bool = False
    lrc: bool = False


def locate_output(
    audio_path: Path, media_root: Path, mirror_roots: list[Path], suffix: str
) -> Path | None:
    """Find an output beside the original first, then in each mirror root."""
    candidate = audio_path.with_name(f"{audio_path.stem}{suffix}")
    if candidate.exists():
        return candidate

    relative_dir = audio_path.parent.relative_to(media_root)
    for mirror_root in mirror_roots:
        mirror_candidate = mirror_root / relative_dir / f"{audio_path.stem}{suffix}"
        if mirror_candidate.exists():
            return mirror_candidate
    return None


def find_outputs(
    audio_path: Path, media_root: Path, mirror_roots: list[Path]
) -> tuple[TrackOutputs, str | None]:
    values: dict[str, bool] = {}
    for part in PART_NAMES:
        values[part] = locate_output(audio_path, media_root, mirror_roots, f".{part}.mp3") is not None

    lrc_path = locate_output(audio_path, media_root, mirror_roots, ".lrc")
    values["lrc"] = lrc_path is not None
    lrc_state = classify_lrc_file(lrc_path).value if lrc_path else None

    return TrackOutputs(**values), lrc_state


@dataclass
class TrackRecord:
    media_root: str
    relative_path: str
    absolute_path: str
    artist: str | None
    title: str
    outputs: TrackOutputs
    lrc_state: str | None
    stem_count: int
    album: str | None
    year: int | None
    duration_seconds: float | None
    has_artwork: bool | None = None
    instrumental_output_path: str | None = None
    instrumental_mtime_ns: int | None = None
    instrumental_size: int | None = None
    instrumental_provenance: dict | None = None
    lyric_timing_provenance: dict | None = None


def iter_media_root(media_root: Path, mirror_roots: list[Path]) -> Iterator[TrackRecord]:
    """Yield source tracks one at a time as their metadata is inspected.

    The incremental form lets the background library scanner publish small
    database batches instead of holding a large root until every tag and
    duration has been read. ``scan_media_root`` remains the convenient list
    wrapper for existing one-shot callers.
    """
    for path in sorted(media_root.rglob("*")):
        if not path.is_file():
            continue
        # Dot-prefixed files are hidden bookkeeping/temporary files on the
        # platforms where they commonly occur. Even when one has an audio
        # extension, it is not a source track the creator expects to manage.
        if path.name.startswith("."):
            continue
        if path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        if is_generated_output(path):
            continue

        extended = read_extended_tags(path)
        outputs, lrc_state = find_outputs(path, media_root, mirror_roots)
        lrc_path = locate_output(path, media_root, mirror_roots, ".lrc")
        lyric_timing_provenance = (
            read_lyric_timing_provenance(lrc_path) if lrc_path is not None else None
        )
        instrumental_path = locate_output(path, media_root, mirror_roots, ".instrumental.mp3")
        try:
            instrumental_stat = instrumental_path.stat() if instrumental_path else None
            instrumental_provenance = (
                read_instrumental_provenance_tag(instrumental_path)
                if instrumental_path else None
            )
        except OSError:
            instrumental_path = None
            instrumental_stat = None
            instrumental_provenance = None
        # "instrumental" is a mixdown with its own Library badge, not a stem
        stem_count = sum(
            1 for part in PART_NAMES if part != "instrumental" and getattr(outputs, part)
        )
        duration_seconds = read_duration_seconds(path)

        yield TrackRecord(
            media_root=str(media_root),
            relative_path=str(path.relative_to(media_root)),
            absolute_path=str(path),
            artist=extended.artist,
            title=extended.title,
            outputs=outputs,
            lrc_state=lrc_state,
            stem_count=stem_count,
            album=extended.album,
            year=extended.year,
            duration_seconds=duration_seconds,
            has_artwork=extended.has_artwork,
            instrumental_output_path=str(instrumental_path) if instrumental_path else None,
            instrumental_mtime_ns=instrumental_stat.st_mtime_ns if instrumental_stat else None,
            instrumental_size=instrumental_stat.st_size if instrumental_stat else None,
            instrumental_provenance=instrumental_provenance,
            lyric_timing_provenance=lyric_timing_provenance,
        )


def scan_media_root(media_root: Path, mirror_roots: list[Path]) -> list[TrackRecord]:
    return list(iter_media_root(media_root, mirror_roots))
