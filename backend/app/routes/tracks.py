from __future__ import annotations

import base64
import os
import re
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from send2trash import send2trash

from ..db import (
    delete_track_records,
    delete_track_record,
    forget_remembered_library_folders,
    get_settings,
    get_track,
    relocate_remembered_library_folders,
    relocate_track_records,
    remember_library_folder,
    list_tracks,
    track_has_active_job,
    update_track_lrc_state,
    update_track_artwork_state,
    update_track_tags,
)
from ..duration import read_duration_seconds
from ..lrc import LrcDocument, classify_lrc_file, read_lrc_text
from ..media_moves import (
    catalogue_tracks_under,
    companion_moves,
    folder_has_active_job,
    folder_payload,
    list_library_folders,
    perform_moves,
    resolve_managed_folder,
    track_relocation,
    validate_entry_name,
)
from ..metadata.providers import DEFAULT_TAGS_PROVIDERS, download_artwork, search_tags_providers
from ..lyrics.paths import resolve_lrc_path
from ..pipeline import atomic_publish
from ..release_year import MIN_RELEASE_YEAR, is_plausible_release_year
from ..scanner import PART_NAMES, locate_output, read_extended_tags
from ..tags import read_embedded_artwork, write_embedded_artwork, write_text_tags
from ..track_deletion import related_output_paths

router = APIRouter()

# Guards the resolve-then-atomic_publish section of write_track_lrc. Two
# concurrent PUTs for the same track (e.g. an accidental double-click, or the
# editor autosaving while a manual Save fires) would otherwise both resolve
# the same target path and race on the same `<target>.part` sibling that
# atomic_publish creates - interleaved writes corrupt the temp file's
# contents, and the loser's os.replace can hit FileNotFoundError (500) if
# the other request's cleanup deleted the .part first. This is a single
# process, single-user, localhost service, so one coarse process-wide lock
# is enough; it is not meant to coordinate across multiple server processes.
_lrc_write_lock = threading.Lock()

# Guards the write_embedded_artwork and write_text_tags save() calls in the
# artwork and tags PUT routes. Two concurrent PUTs to the same file would
# otherwise interleave mutagen's save() operations, corrupting the metadata
# container. Same single-process, single-user assumptions as _lrc_write_lock.
_tags_write_lock = threading.Lock()
_folder_write_lock = threading.Lock()

SUFFIX_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
MAX_ARTWORK_BYTES = 20 * 1024 * 1024


@router.get("/api/tracks")
def read_tracks(request: Request, query: str | None = None) -> dict:
    return {"tracks": list_tracks(request.app.state.db_conn, query)}


class ReconcileTrackLyricsPayload(BaseModel):
    track_ids: list[int]


@router.post("/api/tracks/reconcile-lyrics")
def reconcile_track_lyrics(payload: ReconcileTrackLyricsPayload, request: Request) -> dict:
    """Reclassify only the lyric sidecars for the rows visible in the UI.

    The complete catalogue remains a fast SQLite read. The virtualized Library
    can call this bounded endpoint for its current viewport to repair stale
    timing badges after files were changed by another program, without walking
    an 80,000-track media collection on every page load.
    """
    track_ids = list(dict.fromkeys(payload.track_ids))
    if len(track_ids) > 64:
        raise HTTPException(status_code=422, detail="At most 64 tracks can be reconciled at once")

    conn = request.app.state.db_conn
    settings = get_settings(conn)
    mirror_roots = [Path(root) for root in settings["mirror_roots"]]
    changed: list[dict] = []
    for track_id in track_ids:
        track = get_track(conn, track_id)
        if track is None:
            continue
        source_path = Path(track["absolute_path"])
        located = locate_output(source_path, Path(track["media_root"]), mirror_roots, ".lrc")
        live_state = classify_lrc_file(located).value if located is not None else None
        if track["lrc_state"] == live_state and track["outputs"]["lrc"] == (located is not None):
            continue
        updated = update_track_lrc_state(conn, track_id, live_state)
        if updated is not None:
            changed.append(updated)
    return {"tracks": changed}


class DeleteTrackPayload(BaseModel):
    include_outputs: bool = True


@router.delete("/api/tracks/{track_id}")
def delete_track(track_id: int, payload: DeleteTrackPayload, request: Request) -> dict:
    conn = request.app.state.db_conn
    track = get_track(conn, track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")
    if request.app.state.library_scan.status()["status"] in {"queued", "running"}:
        raise HTTPException(
            status_code=409,
            detail="A library rescan is active. Wait for it to finish before deleting a track.",
        )
    if track_has_active_job(conn, track_id):
        raise HTTPException(
            status_code=409,
            detail="This track is queued or processing. Cancel or finish that job before deleting it.",
        )

    source_path = Path(track["absolute_path"])
    paths: list[Path] = []
    if payload.include_outputs:
        settings = get_settings(conn)
        paths.extend(
            related_output_paths(
                source_path,
                Path(track["media_root"]),
                [Path(root) for root in settings["mirror_roots"]],
            )
        )
    # Move the original last. If recycling a generated file fails, the source
    # and catalogue entry remain available for a safe retry.
    if source_path.is_file():
        paths.append(source_path)

    # Share the manual metadata/lyric write locks so a second browser tab
    # cannot recycle the source while mutagen or an atomic LRC save is writing
    # it. Processing jobs are guarded separately above through their DB state.
    with _lrc_write_lock, _tags_write_lock:
        try:
            for path in paths:
                send2trash(str(path))
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Could not move {path.name} to the Recycle Bin: {exc}",
            ) from exc

        delete_track_record(conn, track_id)
    return {"track_id": track_id, "moved_to_recycle_bin": [str(path) for path in paths]}


class CreateFolderPayload(BaseModel):
    parent_path: str
    name: str


class RenameFolderPayload(BaseModel):
    path: str
    name: str


class MoveTrackPayload(BaseModel):
    destination_folder: str
    filename_stem: str | None = None


def _guard_library_file_change(request: Request, *, active_track_id: int | None = None, folder: Path | None = None) -> None:
    if request.app.state.library_scan.status()["status"] in {"queued", "running"}:
        raise HTTPException(
            status_code=409,
            detail="A library rescan is active. Wait for it to finish before changing files or folders.",
        )
    conn = request.app.state.db_conn
    if active_track_id is not None and track_has_active_job(conn, active_track_id):
        raise HTTPException(
            status_code=409,
            detail="This track is queued or processing. Cancel or finish that job before moving or renaming it.",
        )
    if folder is not None and folder_has_active_job(conn, folder):
        raise HTTPException(
            status_code=409,
            detail="This folder contains a queued or processing track. Finish or cancel that job first.",
        )


def _folder_or_http(raw_path: str, media_roots: list[str]) -> tuple[Path, str, Path]:
    try:
        return resolve_managed_folder(raw_path, media_roots)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _public_track(track: dict) -> dict:
    result = dict(track)
    result.pop("absolute_path", None)
    return result


def _guard_nested_media_roots(folder: Path, media_roots: list[str]) -> None:
    for raw_root in media_roots:
        try:
            configured = Path(raw_root).resolve(strict=True)
            configured.relative_to(folder)
        except (FileNotFoundError, OSError, ValueError):
            continue
        if configured != folder:
            raise HTTPException(
                status_code=422,
                detail=f"This folder contains the configured media folder {configured}. Manage media folders in Settings first.",
            )


@router.get("/api/folders")
def read_library_folders(request: Request) -> dict:
    conn = request.app.state.db_conn
    settings = get_settings(conn)
    return {"folders": list_library_folders(conn, settings["media_roots"])}


@router.post("/api/folders")
def create_library_folder(payload: CreateFolderPayload, request: Request) -> dict:
    conn = request.app.state.db_conn
    settings = get_settings(conn)
    parent, configured_root, resolved_root = _folder_or_http(payload.parent_path, settings["media_roots"])
    try:
        name = validate_entry_name(payload.name, kind="Folder")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    destination = parent / name
    try:
        destination.resolve().relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Folder must remain inside its media folder") from exc
    if destination.exists():
        raise HTTPException(status_code=409, detail=f"A folder named {name} already exists here")
    try:
        destination.mkdir()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not create folder: {exc}") from exc
    remember_library_folder(conn, destination, Path(configured_root))
    return folder_payload(destination.resolve(), configured_root, resolved_root)


@router.put("/api/tracks/{track_id}/location")
def move_or_rename_track(track_id: int, payload: MoveTrackPayload, request: Request) -> dict:
    conn = request.app.state.db_conn
    track = get_track(conn, track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")
    settings = get_settings(conn)
    destination, destination_configured_root, destination_root = _folder_or_http(
        payload.destination_folder, settings["media_roots"]
    )
    _guard_library_file_change(request, active_track_id=track_id)
    mirror_roots = [Path(root) for root in settings["mirror_roots"]]
    try:
        moves, relocation = track_relocation(
            conn,
            track_id,
            destination,
            destination_configured_root,
            destination_root,
            mirror_roots,
            filename_stem=payload.filename_stem,
        )
        with _folder_write_lock, _lrc_write_lock, _tags_write_lock:
            path_map = perform_moves(moves)
            relocation["path_map"] = path_map
            updated = relocate_track_records(conn, [relocation])[0]
            remember_library_folder(conn, destination, Path(destination_configured_root))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not move track: {exc}") from exc
    return {"track": _public_track(updated), "moved": path_map}


@router.put("/api/folders/rename")
def rename_library_folder(payload: RenameFolderPayload, request: Request) -> dict:
    conn = request.app.state.db_conn
    settings = get_settings(conn)
    source, configured_root, resolved_root = _folder_or_http(payload.path, settings["media_roots"])
    if source == resolved_root:
        raise HTTPException(status_code=422, detail="Media folders are managed in Settings and cannot be renamed here")
    _guard_nested_media_roots(source, settings["media_roots"])
    _guard_library_file_change(request, folder=source)
    try:
        name = validate_entry_name(payload.name, kind="Folder")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    destination = source.parent / name
    if os.path.normcase(str(source)) == os.path.normcase(str(destination)):
        raise HTTPException(status_code=409, detail="Choose a different folder name")
    if destination.exists():
        raise HTTPException(status_code=409, detail=f"A folder named {name} already exists here")

    mirror_roots = [Path(root) for root in settings["mirror_roots"]]
    rows = catalogue_tracks_under(conn, source)
    mirror_moves: list[tuple[Path, Path]] = []
    relocations: list[dict] = []
    for row in rows:
        old_source = Path(row["absolute_path"])
        new_source = destination / old_source.resolve().relative_to(source)
        companion_pairs = companion_moves(
            old_source,
            Path(row["media_root"]).resolve(),
            new_source.parent,
            resolved_root,
            mirror_roots,
            new_stem=old_source.stem,
        )
        path_map = {str(old): str(new) for old, new in companion_pairs}
        for old, new in companion_pairs:
            try:
                old.resolve().relative_to(source)
            except (OSError, ValueError):
                mirror_moves.append((old, new))
        relocations.append(
            {
                "track_id": row["id"],
                "media_root": configured_root,
                "relative_path": str(new_source.relative_to(resolved_root)),
                "absolute_path": str(new_source),
                "path_map": path_map,
            }
        )

    try:
        with _folder_write_lock, _lrc_write_lock, _tags_write_lock:
            moved_map = perform_moves([*mirror_moves, (source, destination)])
            for relocation in relocations:
                relocation["path_map"].update(moved_map)
            relocate_track_records(conn, relocations)
            relocate_remembered_library_folders(conn, source, destination)
            remember_library_folder(conn, destination, Path(configured_root))
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not rename folder: {exc}") from exc
    return {"folder": folder_payload(destination.resolve(), configured_root, resolved_root), "track_count": len(rows)}


@router.delete("/api/folders")
def delete_library_folder(path: str, request: Request) -> dict:
    conn = request.app.state.db_conn
    settings = get_settings(conn)
    folder, _configured_root, resolved_root = _folder_or_http(path, settings["media_roots"])
    if folder == resolved_root:
        raise HTTPException(status_code=422, detail="Remove media folders through Settings instead of deleting them")
    _guard_nested_media_roots(folder, settings["media_roots"])
    _guard_library_file_change(request, folder=folder)
    rows = catalogue_tracks_under(conn, folder)
    mirror_roots = [Path(root) for root in settings["mirror_roots"]]
    external_outputs: list[Path] = []
    seen: set[str] = set()
    for row in rows:
        source = Path(row["absolute_path"])
        for output in related_output_paths(source, Path(row["media_root"]), mirror_roots):
            try:
                output.resolve().relative_to(folder)
                continue
            except (OSError, ValueError):
                pass
            key = os.path.normcase(str(output.resolve()))
            if key not in seen:
                seen.add(key)
                external_outputs.append(output)

    recycled: list[str] = []
    try:
        with _folder_write_lock, _lrc_write_lock, _tags_write_lock:
            # Mirror outputs go first. The source folder and its catalogue rows
            # remain intact if one of those recoverable moves fails.
            for output in external_outputs:
                send2trash(str(output))
                recycled.append(str(output))
            send2trash(str(folder))
            recycled.append(str(folder))
            delete_track_records(conn, [row["id"] for row in rows])
            forget_remembered_library_folders(conn, folder)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not move folder to the Recycle Bin: {exc}") from exc
    return {"deleted_track_ids": [row["id"] for row in rows], "moved_to_recycle_bin": recycled}


@router.post("/api/rescan")
def rescan(request: Request) -> dict:
    # Returns immediately. Repeated requests while a scan is active are
    # coalesced onto the same scan rather than starting competing disk walks.
    return request.app.state.library_scan.start()


@router.get("/api/rescan")
def read_rescan_status(request: Request) -> dict:
    return request.app.state.library_scan.status()


@router.get("/api/tracks/{track_id}/parts")
def read_track_parts(track_id: int, request: Request) -> dict:
    conn = request.app.state.db_conn
    track = get_track(conn, track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")

    settings = get_settings(conn)
    media_root = Path(track["media_root"])
    mirror_roots = [Path(root) for root in settings["mirror_roots"]]
    source_path = Path(track["absolute_path"])

    parts = []
    for part in PART_NAMES:
        located = locate_output(source_path, media_root, mirror_roots, f".{part}.mp3")
        parts.append({
            "part": part,
            "exists": located is not None,
            "duration": read_duration_seconds(located) if located else None,
        })

    original_exists = source_path.is_file()
    parts.append({
        "part": "original",
        "exists": original_exists,
        "duration": read_duration_seconds(source_path) if original_exists else None,
    })

    return {"parts": parts}


@router.get("/api/tracks/{track_id}/lrc")
def read_track_lrc(track_id: int, request: Request) -> dict:
    conn = request.app.state.db_conn
    track = get_track(conn, track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")

    settings = get_settings(conn)
    source_path = Path(track["absolute_path"])
    mirror_roots = [Path(root) for root in settings["mirror_roots"]]
    located = locate_output(source_path, Path(track["media_root"]), mirror_roots, ".lrc")
    if located is None:
        return {"exists": False, "content": "", "state": None}

    content = read_lrc_text(located)
    state = LrcDocument.parse(content).state
    return {"exists": True, "content": content, "state": state.value}


class LrcWritePayload(BaseModel):
    content: str


@router.put("/api/tracks/{track_id}/lrc")
def write_track_lrc(
    track_id: int,
    payload: LrcWritePayload,
    request: Request,
    create: str | None = None,
    suffix: str | None = None,
) -> dict:
    if suffix and not SUFFIX_RE.fullmatch(suffix):
        raise HTTPException(status_code=422, detail="suffix must be a plain filename component")

    conn = request.app.state.db_conn
    track = get_track(conn, track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")

    settings = get_settings(conn)
    source_path = Path(track["absolute_path"])
    media_root = Path(track["media_root"])
    mirror_roots = [Path(root) for root in settings["mirror_roots"]]

    updated_track = None
    with _lrc_write_lock:
        if suffix:
            target_path = source_path.with_name(f"{source_path.stem}.{suffix}.lrc")
        else:
            located = locate_output(source_path, media_root, mirror_roots, ".lrc")
            if located is not None:
                target_path = located
            elif create == "beside":
                target_path = resolve_lrc_path(source_path, {})
            else:
                raise HTTPException(
                    status_code=409,
                    detail="No .lrc file resolved for this track; retry with ?create=beside to create one",
                )

        atomic_publish(target_path, lambda part_path: part_path.write_bytes(payload.content.encode("utf-8")))

        # Keep publishing the file and its corresponding library state in the
        # same critical section. Otherwise two concurrent saves could leave
        # the row describing the first payload after the second won the file.
        if suffix is None:
            lrc_state = LrcDocument.parse(payload.content).state.value
            updated_track = update_track_lrc_state(conn, track_id, lrc_state)

    # A suffixed Save As file is a sidecar variant, not the canonical lyric
    # file represented by the library row. Canonical saves can update the two
    # lyric columns directly and return the fresh row without walking every
    # configured media folder again.
    response = {"path": str(target_path)}
    if updated_track is not None:
        response["track"] = updated_track
    return response


@router.get("/api/tracks/{track_id}/artwork")
def read_track_artwork(track_id: int, request: Request) -> Response:
    conn = request.app.state.db_conn
    track = get_track(conn, track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")

    path = Path(track["absolute_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Audio file is missing on disk")

    artwork = read_embedded_artwork(path)
    if artwork is None:
        raise HTTPException(status_code=404, detail="No embedded artwork")
    data, mime = artwork
    # Embedded artwork is mutable metadata. Never let a stable track URL pin
    # an earlier cover in the browser cache after a tag-editor or batch write.
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.put("/api/tracks/{track_id}/artwork")
async def write_track_artwork(track_id: int, request: Request) -> dict:
    conn = request.app.state.db_conn
    track = get_track(conn, track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")

    path = Path(track["absolute_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Audio file is missing on disk")

    data = await request.body()
    if not data:
        raise HTTPException(status_code=422, detail="Empty artwork upload")
    if len(data) > MAX_ARTWORK_BYTES:
        raise HTTPException(status_code=413, detail="Artwork must be 20 MB or smaller")

    raw_content_type = request.headers.get("content-type") or "image/jpeg"
    content_type = raw_content_type.split(";")[0].strip().lower()
    signatures = {
        "image/jpeg": data.startswith(b"\xff\xd8"),
        "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
    }
    if not signatures.get(content_type, False):
        raise HTTPException(status_code=422, detail="Artwork content must match its JPEG or PNG type")

    with _tags_write_lock:
        try:
            write_embedded_artwork(path, data, content_type)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        updated = update_track_artwork_state(conn, track_id, True)
    return {"path": str(path), "track": updated}


class TagsWritePayload(BaseModel):
    artist: str | None = None
    title: str
    album: str | None = None
    year: int | None = None


class TagsSuggestionPayload(BaseModel):
    artist: str | None = None
    title: str
    include_artwork: bool = False


@router.post("/api/tracks/{track_id}/tags/suggest")
def suggest_track_tags(track_id: int, payload: TagsSuggestionPayload, request: Request) -> dict:
    track = get_track(request.app.state.db_conn, track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")

    artist = (payload.artist or "").strip()
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Enter a title before searching")

    # Untagged downloads are commonly named "Artist - Title". Use that as a
    # search hint only; the result is still staged for user review and is
    # never written by this endpoint.
    if not artist:
        stem = Path(track["absolute_path"]).stem
        for separator in (" - ", " – ", " — "):
            if separator in stem:
                artist, title = (part.strip() for part in stem.split(separator, 1))
                break

    result = search_tags_providers(artist, title, DEFAULT_TAGS_PROVIDERS)
    if result is None:
        raise HTTPException(status_code=404, detail="No confident tag match was found. Try refining Artist and Title.")
    match, provider = result

    artwork_data_url = None
    if payload.include_artwork and match.artwork_url:
        downloaded = download_artwork(match.artwork_url)
        if downloaded is not None:
            data, mime = downloaded
            artwork_data_url = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    return {
        "artist": match.artist,
        "title": match.title,
        "album": match.album,
        "year": match.year,
        "provider": provider,
        "artwork_data_url": artwork_data_url,
    }


@router.put("/api/tracks/{track_id}/tags")
def write_track_tags_route(track_id: int, payload: TagsWritePayload, request: Request) -> dict:
    if not payload.title.strip():
        raise HTTPException(status_code=422, detail="title is required")
    if payload.year is not None and not is_plausible_release_year(payload.year):
        raise HTTPException(
            status_code=422,
            detail=f"year must be a plausible release year ({MIN_RELEASE_YEAR} to next year)",
        )

    conn = request.app.state.db_conn
    track = get_track(conn, track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")

    path = Path(track["absolute_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Audio file is missing on disk")

    with _tags_write_lock:
        try:
            write_text_tags(path, artist=payload.artist, title=payload.title, album=payload.album, year=payload.year)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        refreshed = read_extended_tags(path)
        # File write succeeded; now update the DB. This is not transactional:
        # a DB failure after a successful file write leaves file and DB diverged
        # until the next rescan, which re-reads the filesystem and self-heals.
        updated = update_track_tags(
            conn,
            track_id,
            artist=refreshed.artist,
            title=refreshed.title,
            album=refreshed.album,
            year=refreshed.year,
            has_artwork=refreshed.has_artwork,
        )
    return updated
