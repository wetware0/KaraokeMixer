from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..db import get_settings, get_track
from ..scanner import PART_NAMES, locate_output

router = APIRouter()


@router.get("/api/audio/{track_id}")
def stream_audio(track_id: int, request: Request) -> Response:
    track = get_track(request.app.state.db_conn, track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")

    path = Path(track["absolute_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Audio file is missing on disk")

    return _stream_path(path, request)


@router.get("/api/audio/{track_id}/part/{part}")
def stream_audio_part(track_id: int, part: str, request: Request) -> Response:
    conn = request.app.state.db_conn
    track = get_track(conn, track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")

    source_path = Path(track["absolute_path"])
    if part == "original":
        target_path = source_path
    elif part in PART_NAMES:
        settings = get_settings(conn)
        mirror_roots = [Path(root) for root in settings["mirror_roots"]]
        located = locate_output(source_path, Path(track["media_root"]), mirror_roots, f".{part}.mp3")
        if located is None:
            raise HTTPException(status_code=404, detail=f"Part '{part}' does not exist for this track")
        target_path = located
    else:
        raise HTTPException(status_code=404, detail=f"Unknown part '{part}'")

    if not target_path.is_file():
        raise HTTPException(status_code=404, detail="Audio file is missing on disk")

    return _stream_path(target_path, request)


def _stream_path(path: Path, request: Request) -> Response:
    file_size = path.stat().st_size
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    range_header = request.headers.get("range")
    if range_header is None:
        return Response(
            content=path.read_bytes(),
            media_type=content_type,
            headers={"Accept-Ranges": "bytes", "Content-Length": str(file_size)},
        )

    start, end = _parse_range(range_header, file_size)
    chunk = _read_range(path, start, end)
    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
    }
    return Response(content=chunk, status_code=206, media_type=content_type, headers=headers)


def _parse_range(range_header: str, file_size: int) -> tuple[int, int]:
    units, _, range_spec = range_header.partition("=")
    if units.strip() != "bytes":
        raise HTTPException(status_code=416, detail="Only byte ranges are supported")

    start_text, _, end_text = range_spec.partition("-")
    try:
        if not start_text:
            # suffix form: bytes=-N means the last N bytes
            suffix_length = int(end_text)
            if suffix_length <= 0:
                raise HTTPException(status_code=416, detail="Invalid range")
            start = max(file_size - suffix_length, 0)
            end = file_size - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text else file_size - 1
    except ValueError:
        raise HTTPException(status_code=416, detail="Invalid range")
    end = min(end, file_size - 1)
    if start > end or start >= file_size:
        raise HTTPException(status_code=416, detail="Invalid range")
    return start, end


def _read_range(path: Path, start: int, end: int) -> bytes:
    with path.open("rb") as handle:
        handle.seek(start)
        return handle.read(end - start + 1)
