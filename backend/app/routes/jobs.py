from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..db import (
    JOB_ITEM_STATUSES,
    get_job,
    get_settings,
    get_track,
    list_job_history,
    list_job_items_page,
    list_jobs,
    list_track_processing_failures,
    list_tracks,
)
from ..recipes.registry import RecipeDefinition

router = APIRouter()

BASE_OPTION_KEYS = {"device", "overwrite", "output_mode"}  # always-allowed universal job options
DEVICE_CHOICES = {"auto", "cuda", "cpu"}
OUTPUT_MODE_CHOICES = {"beside", "mirror"}


class JobSubmission(BaseModel):
    recipe: str
    track_ids: list[int] | None = None
    folder: str | None = None
    options: dict = {}


def _resolve_items(request: Request, payload: JobSubmission) -> list[dict]:
    conn = request.app.state.db_conn
    items: list[dict] = []
    if payload.track_ids:
        for track_id in payload.track_ids:
            track = get_track(conn, track_id)
            if track is not None:
                items.append({"track_id": track_id, "source_path": track["absolute_path"]})
    elif payload.folder:
        folder = Path(payload.folder).resolve()
        for summary in list_tracks(conn):
            track = get_track(conn, summary["id"])
            if track is not None and Path(track["absolute_path"]).resolve().is_relative_to(folder):
                items.append({"track_id": summary["id"], "source_path": track["absolute_path"]})
    return items


def _validate_options(recipe_def: RecipeDefinition, options: dict) -> None:
    schema = recipe_def.options_schema
    allowed = BASE_OPTION_KEYS | set(schema.keys() if schema else ())
    unknown = [name for name in options if name not in allowed]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown option(s): {sorted(unknown)}")
    if "device" in options and options["device"] not in DEVICE_CHOICES:
        raise HTTPException(
            status_code=422, detail=f"Option 'device' must be one of {sorted(DEVICE_CHOICES)}, got {options['device']!r}"
        )
    if "output_mode" in options and options["output_mode"] not in OUTPUT_MODE_CHOICES:
        raise HTTPException(
            status_code=422,
            detail=f"Option 'output_mode' must be one of {sorted(OUTPUT_MODE_CHOICES)}, got {options['output_mode']!r}",
        )
    if "overwrite" in options and not isinstance(options["overwrite"], bool):
        raise HTTPException(status_code=422, detail="Option 'overwrite' must be a boolean")
    if not schema:
        return
    for name, spec in schema.items():
        if name not in options:
            continue  # missing options fall back to the schema's default at stage-build time
        value = options[name]
        option_type = spec["type"]
        if option_type == "select" and value not in spec["choices"]:
            raise HTTPException(
                status_code=422, detail=f"Option {name!r} must be one of {spec['choices']}, got {value!r}"
            )
        if option_type == "checkbox" and not isinstance(value, bool):
            raise HTTPException(status_code=422, detail=f"Option {name!r} must be a boolean")
        if option_type == "number" and not isinstance(value, (int, float)):
            raise HTTPException(status_code=422, detail=f"Option {name!r} must be a number")


@router.post("/api/jobs")
def submit_job(payload: JobSubmission, request: Request) -> dict:
    queue_manager = request.app.state.job_queue
    if payload.recipe not in queue_manager.registry:
        raise HTTPException(status_code=422, detail=f"Unknown recipe: {payload.recipe}")
    recipe_def = queue_manager.registry[payload.recipe]
    _validate_options(recipe_def, payload.options)

    items = _resolve_items(request, payload)
    if not items:
        raise HTTPException(status_code=422, detail="No matching tracks to process")

    settings = get_settings(request.app.state.db_conn)
    resolved_device = (
        request.app.state.device if payload.options.get("device", "auto") == "auto" else payload.options["device"]
    )
    options = {
        **payload.options,
        "media_roots": settings["media_roots"],
        "mirror_roots": settings["mirror_roots"],
        "device": resolved_device,
    }
    job_id = queue_manager.submit(payload.recipe, options, items)
    return {"job_id": job_id}


@router.get("/api/jobs")
def read_jobs(request: Request) -> dict:
    return {"jobs": list_jobs(request.app.state.db_conn)}


@router.get("/api/jobs/history")
def read_job_history(
    request: Request,
    status: str = "all",
    query: str = "",
    limit: int = 25,
    offset: int = 0,
) -> dict:
    status_groups = {
        "all": None,
        "active": {"queued", "running"},
        "completed": {"completed"},
        "failed": {"failed"},
        "cancelled": {"cancelled"},
    }
    if status not in status_groups:
        raise HTTPException(status_code=422, detail="Invalid history status filter")
    if limit < 1 or limit > 100 or offset < 0:
        raise HTTPException(status_code=422, detail="History page is outside the supported range")
    return list_job_history(
        request.app.state.db_conn,
        statuses=status_groups[status],
        query=query,
        limit=limit,
        offset=offset,
    )


@router.get("/api/jobs/track-failures")
def read_track_failures(request: Request) -> dict:
    return {"failures": list_track_processing_failures(request.app.state.db_conn)}


@router.get("/api/jobs/{job_id}/items")
def read_job_items(
    job_id: int,
    request: Request,
    status: str = "all",
    query: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    if status != "all" and status not in JOB_ITEM_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid item status filter")
    if limit < 1 or limit > 200 or offset < 0:
        raise HTTPException(status_code=422, detail="Item page is outside the supported range")
    result = list_job_items_page(
        request.app.state.db_conn,
        job_id,
        status=None if status == "all" else status,
        query=query,
        limit=limit,
        offset=offset,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return result


@router.get("/api/jobs/{job_id}")
def read_job(job_id: int, request: Request) -> dict:
    job = get_job(request.app.state.db_conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: int, request: Request) -> dict:
    job = get_job(request.app.state.db_conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    request.app.state.job_queue.cancel(job_id)
    return {"job_id": job_id, "status": "cancelling"}
