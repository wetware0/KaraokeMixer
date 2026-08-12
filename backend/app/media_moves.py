from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

from .db import get_track, list_remembered_library_folders
from .track_deletion import related_output_paths


INVALID_FILENAME_CHARS = set('<>:"/\\|?*')
WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *{f"COM{i}" for i in range(1, 10)}, *{f"LPT{i}" for i in range(1, 10)}}


def validate_entry_name(name: str, *, kind: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError(f"{kind} name is required")
    if cleaned in {".", ".."} or any(char in INVALID_FILENAME_CHARS for char in cleaned):
        raise ValueError(f"{kind} name contains characters Windows cannot use")
    if cleaned.endswith((" ", ".")):
        raise ValueError(f"{kind} name cannot end with a space or period")
    if cleaned.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        raise ValueError(f"{kind} name is reserved by Windows")
    if kind == "File" and cleaned.startswith("."):
        raise ValueError("File name cannot begin with a period because hidden source files are not indexed")
    return cleaned


def resolve_managed_folder(
    raw_path: str,
    media_roots: list[str],
    *,
    must_exist: bool = True,
) -> tuple[Path, str, Path]:
    """Return (resolved folder, configured-root spelling, resolved root)."""
    folder = Path(raw_path).expanduser().resolve(strict=must_exist)
    if must_exist and not folder.is_dir():
        raise FileNotFoundError("Folder does not exist")
    for configured_root in media_roots:
        try:
            root = Path(configured_root).expanduser().resolve(strict=True)
            folder.relative_to(root)
        except (FileNotFoundError, OSError, ValueError):
            continue
        return folder, configured_root, root
    raise PermissionError("Choose a folder inside a configured media folder")


def folder_payload(path: Path, configured_root: str, resolved_root: Path) -> dict[str, str]:
    relative = path.relative_to(resolved_root)
    return {
        "path": path.as_posix(),
        "media_root": Path(configured_root).as_posix(),
        "relative_path": "" if relative == Path(".") else relative.as_posix(),
        "name": path.name if relative != Path(".") else Path(configured_root).as_posix(),
    }


def list_library_folders(conn: sqlite3.Connection, media_roots: list[str]) -> list[dict[str, str]]:
    """List roots, track-parent folders, and explicitly created empty folders."""
    candidates: set[str] = set()
    for root in media_roots:
        try:
            candidates.add(str(Path(root).resolve(strict=True)))
        except (FileNotFoundError, OSError):
            continue
    for row in conn.execute("SELECT media_root, relative_path FROM tracks"):
        candidates.add(str(Path(row["media_root"]) / Path(row["relative_path"]).parent))
    for row in list_remembered_library_folders(conn):
        if Path(row["path"]).is_dir():
            candidates.add(row["path"])

    folders: list[dict[str, str]] = []
    seen: set[str] = set()
    for candidate in sorted(candidates, key=str.casefold):
        try:
            folder, configured_root, resolved_root = resolve_managed_folder(candidate, media_roots)
        except (FileNotFoundError, OSError, PermissionError):
            continue
        key = os.path.normcase(str(folder))
        if key in seen:
            continue
        seen.add(key)
        folders.append(folder_payload(folder, configured_root, resolved_root))
    return folders


def catalogue_tracks_under(conn: sqlite3.Connection, folder: Path) -> list[dict]:
    tracks: list[dict] = []
    for row in conn.execute("SELECT id, media_root, relative_path, absolute_path FROM tracks"):
        try:
            Path(row["absolute_path"]).resolve().relative_to(folder.resolve())
        except (OSError, ValueError):
            continue
        tracks.append(dict(row))
    return tracks


def folder_has_active_job(conn: sqlite3.Connection, folder: Path) -> bool:
    rows = conn.execute(
        """
        SELECT item.source_path
        FROM job_items AS item
        JOIN jobs AS job ON job.id = item.job_id
        WHERE job.status IN ('queued', 'running')
        """
    ).fetchall()
    for row in rows:
        try:
            Path(row["source_path"]).resolve().relative_to(folder.resolve())
        except (OSError, ValueError):
            continue
        return True
    return False


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _renamed_companion_name(path: Path, old_stem: str, new_stem: str) -> str:
    if path.name[: len(old_stem)].casefold() == old_stem.casefold():
        return f"{new_stem}{path.name[len(old_stem):]}"
    return path.name


def companion_moves(
    source_path: Path,
    source_root: Path,
    destination_folder: Path,
    destination_root: Path,
    mirror_roots: list[Path],
    *,
    new_stem: str,
) -> list[tuple[Path, Path]]:
    moves: list[tuple[Path, Path]] = []
    destination_relative_dir = destination_folder.relative_to(destination_root)
    for companion in related_output_paths(source_path, source_root, mirror_roots):
        target_name = _renamed_companion_name(companion, source_path.stem, new_stem)
        if _same_path(companion.parent, source_path.parent):
            destination = destination_folder / target_name
        else:
            destination = None
            for mirror_root in mirror_roots:
                try:
                    companion.resolve().relative_to(mirror_root.resolve())
                except (OSError, ValueError):
                    continue
                destination = mirror_root / destination_relative_dir / target_name
                break
            if destination is None:
                continue
        if not _same_path(companion, destination):
            moves.append((companion, destination))
    return moves


def preflight_moves(moves: list[tuple[Path, Path]]) -> None:
    destinations: set[str] = set()
    for source, destination in moves:
        if not source.exists():
            raise FileNotFoundError(f"{source.name} is missing")
        key = os.path.normcase(os.path.abspath(destination))
        if key in destinations:
            raise FileExistsError(f"More than one file would become {destination.name}")
        destinations.add(key)
        if destination.exists() and not _same_path(source, destination):
            raise FileExistsError(f"{destination.name} already exists in the destination folder")


def perform_moves(moves: list[tuple[Path, Path]]) -> dict[str, str]:
    """Move a set of paths and roll back already-moved paths after a failure."""
    preflight_moves(moves)
    moved: list[tuple[Path, Path]] = []
    try:
        for source, destination in moves:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            moved.append((source, destination))
    except Exception:
        for source, destination in reversed(moved):
            try:
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(destination), str(source))
            except OSError:
                # Preserve the original failure. Any rollback failure is still
                # recoverable because both locations are reported in server logs.
                pass
        raise
    return {str(source): str(destination) for source, destination in moved}


def track_relocation(
    conn: sqlite3.Connection,
    track_id: int,
    destination_folder: Path,
    destination_configured_root: str,
    destination_root: Path,
    mirror_roots: list[Path],
    *,
    filename_stem: str | None = None,
) -> tuple[list[tuple[Path, Path]], dict]:
    track = get_track(conn, track_id)
    if track is None:
        raise LookupError("Track not found")
    source = Path(track["absolute_path"])
    source_root = Path(track["media_root"]).resolve()
    new_stem = validate_entry_name(filename_stem, kind="File") if filename_stem is not None else source.stem
    destination = destination_folder / f"{new_stem}{source.suffix}"
    moves = companion_moves(
        source,
        source_root,
        destination_folder,
        destination_root,
        mirror_roots,
        new_stem=new_stem,
    )

    def sibling_tracks(parent: Path, configured_root: str) -> list[Path]:
        prefix = str(parent).rstrip("\\/") + os.sep + "%"
        rows = conn.execute(
            "SELECT id, absolute_path FROM tracks WHERE id != ? AND media_root = ? AND absolute_path LIKE ?",
            (track_id, configured_root, prefix),
        ).fetchall()
        siblings: list[Path] = []
        for row in rows:
            other = Path(row["absolute_path"])
            if _same_path(other.parent, parent):
                siblings.append(other)
        return siblings

    if moves and any(other.stem.casefold() == source.stem.casefold() for other in sibling_tracks(source.parent, track["media_root"])):
        raise ValueError(
            "Another track in this folder has the same filename stem and may share its lyrics or stems. "
            "Rename the duplicate first."
        )
    if any(other.stem.casefold() == new_stem.casefold() for other in sibling_tracks(destination_folder, destination_configured_root)):
        raise FileExistsError(
            "Another track in the destination has that filename stem. Choose a different filename to avoid shared outputs."
        )
    if not _same_path(source, destination):
        moves.append((source, destination))
    relocation = {
        "track_id": track_id,
        "media_root": destination_configured_root,
        "relative_path": str(destination.relative_to(destination_root)),
        "absolute_path": str(destination),
    }
    return moves, relocation
