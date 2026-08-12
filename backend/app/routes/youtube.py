from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..db import get_settings
from ..youtube.downloader import probe_youtube_url
from .jobs import _validate_options

router = APIRouter()


class ProcessAfterPayload(BaseModel):
    recipe: str
    options: dict = Field(default_factory=dict)


class YoutubeImportPayload(BaseModel):
    url: str
    artist: str | None = None
    title: str | None = None
    process_after: ProcessAfterPayload | None = None


class YoutubeProbePayload(BaseModel):
    url: str


@router.post("/api/youtube/probe")
def probe_youtube(payload: YoutubeProbePayload, request: Request) -> dict:
    settings = get_settings(request.app.state.db_conn)
    try:
        return probe_youtube_url(payload.url, cookies=settings["youtube_cookies"])
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/api/youtube/import")
def import_from_youtube(payload: YoutubeImportPayload, request: Request) -> dict:
    settings = get_settings(request.app.state.db_conn)
    downloads_root = settings["downloads_root"] or (settings["media_roots"][0] if settings["media_roots"] else None)
    if not downloads_root:
        raise HTTPException(
            status_code=422,
            detail="Configure a downloads root (or at least one media root) in Settings before importing from YouTube",
        )

    # The chained "process after" job is submitted straight from the
    # youtube_import stage (see stages/youtube_import.py), which has no
    # app.state and so cannot resolve "auto" device or validate options the
    # way POST /api/jobs does. Do both here, upfront, so the follow-up job
    # gets exactly the same treatment a direct /api/jobs submission would -
    # a bad option 422s immediately rather than surfacing as a mysterious
    # failed chained job, and "auto" resolves to the same probed device.
    process_after_dict: dict | None = None
    if payload.process_after is not None:
        queue_manager = request.app.state.job_queue
        if payload.process_after.recipe not in queue_manager.registry:
            raise HTTPException(status_code=422, detail=f"Unknown recipe: {payload.process_after.recipe}")
        recipe_def = queue_manager.registry[payload.process_after.recipe]
        _validate_options(recipe_def, payload.process_after.options)
        resolved_device = (
            request.app.state.device
            if payload.process_after.options.get("device", "auto") == "auto"
            else payload.process_after.options["device"]
        )
        process_after_dict = {
            "recipe": payload.process_after.recipe,
            "options": {**payload.process_after.options, "device": resolved_device},
        }

    options = {
        "youtube_url": payload.url,
        "youtube_artist": payload.artist,
        "youtube_title": payload.title,
        "downloads_root": downloads_root,
        "youtube_cookies": settings["youtube_cookies"],
        "db_path": str(request.app.state.db_path),
        "media_roots": settings["media_roots"],
        "mirror_roots": settings["mirror_roots"],
        "process_after": process_after_dict,
    }
    job_id = request.app.state.job_queue.submit(
        "youtube_import", options, [{"track_id": None, "source_path": payload.url}]
    )
    return {"job_id": job_id}
