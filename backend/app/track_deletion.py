from __future__ import annotations

from pathlib import Path

from .scanner import PART_NAMES


def related_output_paths(source_path: Path, media_root: Path, mirror_roots: list[Path]) -> list[Path]:
    """Return existing app-named outputs beside a source and in every mirror.

    The original is deliberately excluded so callers can move generated files
    first and the source last. Lyric Save As variants use
    ``{source-stem}.{suffix}.lrc`` and are included alongside the canonical
    ``{source-stem}.lrc`` file.
    """
    directories = [source_path.parent]
    try:
        relative_dir = source_path.resolve().parent.relative_to(media_root.resolve())
    except ValueError:
        relative_dir = None
    if relative_dir is not None:
        directories.extend(root / relative_dir for root in mirror_roots)

    candidates: list[Path] = []
    for directory in directories:
        candidates.extend(directory / f"{source_path.stem}.{part}.mp3" for part in PART_NAMES)
        candidates.append(directory / f"{source_path.stem}.lrc")
        if directory.is_dir():
            # Avoid globbing with the source stem itself: valid music names
            # can contain ``[``, ``]`` or ``*``, which have wildcard meaning
            # and could otherwise select a different track's lyric file.
            variant_prefix = f"{source_path.stem}.".casefold()
            candidates.extend(
                path
                for path in directory.iterdir()
                if path.name.casefold().startswith(variant_prefix) and path.suffix.casefold() == ".lrc"
            )

    existing: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.absolute()).casefold()
        if key not in seen and candidate.is_file():
            seen.add(key)
            existing.append(candidate)
    return existing
