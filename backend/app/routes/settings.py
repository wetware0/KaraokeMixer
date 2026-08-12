from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..db import get_settings, update_settings

router = APIRouter()

VALID_DEVICE_PREFERENCES = {"auto", "cuda", "cpu"}
VALID_COOKIE_MODES = {"none", "browser", "file"}


class YoutubeCookiesPayload(BaseModel):
    mode: str = "none"
    browser: str | None = None
    cookies_file: str | None = None


class SettingsPayload(BaseModel):
    media_roots: list[str]
    mirror_roots: list[str]
    device_preference: str
    downloads_root: str | None = None
    youtube_cookies: YoutubeCookiesPayload = Field(default_factory=YoutubeCookiesPayload)


class BrowseFolderPayload(BaseModel):
    initial_path: str | None = None


def _pick_folder(initial_path: str | None) -> str | None:
    """Open the local machine's native directory chooser."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    options: dict[str, object] = {"parent": root, "mustexist": True, "title": "Choose a folder"}
    if initial_path and Path(initial_path).is_dir():
        options["initialdir"] = initial_path
    try:
        selected = filedialog.askdirectory(**options)
    finally:
        root.destroy()
    return str(Path(selected)) if selected else None


@router.get("/api/settings")
def read_settings(request: Request) -> dict:
    return get_settings(request.app.state.db_conn)


@router.post("/api/settings/browse-folder")
def browse_folder(payload: BrowseFolderPayload) -> dict:
    try:
        return {"path": _pick_folder(payload.initial_path)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Folder picker is unavailable: {exc}") from exc


@router.put("/api/settings")
def write_settings(payload: SettingsPayload, request: Request) -> dict:
    if payload.device_preference not in VALID_DEVICE_PREFERENCES:
        raise HTTPException(status_code=422, detail="device_preference must be one of auto, cuda, cpu")
    if payload.youtube_cookies.mode not in VALID_COOKIE_MODES:
        raise HTTPException(status_code=422, detail="youtube_cookies.mode must be one of none, browser, file")
    if payload.youtube_cookies.mode == "browser" and not (payload.youtube_cookies.browser or "").strip():
        raise HTTPException(
            status_code=422, detail="youtube_cookies.browser is required when mode is 'browser'"
        )
    if payload.youtube_cookies.mode == "file" and not (payload.youtube_cookies.cookies_file or "").strip():
        raise HTTPException(
            status_code=422, detail="youtube_cookies.cookies_file is required when mode is 'file'"
        )
    settings = payload.model_dump()
    # exclude_none=True here, deliberately not on the outer model_dump():
    # downloads_root is a plain nullable scalar where None is a legitimate
    # value, but youtube_cookies is a nested object whose unset sub-fields
    # (e.g. cookies_file when mode is "browser") must not round-trip as
    # {"cookies_file": null} - the stored/returned shape should be exactly
    # {"mode": "browser", "browser": "chrome"}, nothing more.
    settings["youtube_cookies"] = payload.youtube_cookies.model_dump(exclude_none=True)
    return update_settings(request.app.state.db_conn, settings)
