from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from .. import scanner
from ..db import get_connection, replace_tracks
from ..pipeline import StageContext, StageResult, StageStatus
from ..youtube import downloader as youtube_downloader_module

_JOB_QUEUE_CONTEXT: dict = {"queue_manager": None}


def bind_job_queue(queue_manager) -> None:
    """Called once from app.main.create_app() so a real (non-test)
    YoutubeImportStage can chain a "process after" job through the live
    JobQueueManager. Job options are persisted as JSON (app.db.create_job),
    so a live JobQueueManager object can never be threaded through them, and
    recipe stage_factories are fixed to a single `options: dict` argument -
    this narrow, explicitly-documented module-level binding is the
    deliberate trade-off. Every stage test injects submit_followup_fn
    directly at construction time and never touches this global."""
    _JOB_QUEUE_CONTEXT["queue_manager"] = queue_manager


def _default_submit_followup(recipe: str, options: dict, items: list[dict]) -> int:
    queue_manager = _JOB_QUEUE_CONTEXT["queue_manager"]
    if queue_manager is None:
        raise RuntimeError("No JobQueueManager bound - call bind_job_queue() from app startup")
    return queue_manager.submit(recipe, options, items)


def _sanitize(value: str) -> str:
    return "".join(character for character in value if character not in '<>:"/\\|?*').strip() or "Unknown"


class YoutubeImportStage:
    """Downloads one YouTube video's audio into the configured downloads
    root, rescans that root, and optionally chains a "process after" job.
    Runs on the cpu lane (see recipes/youtube_import.py). declared_outputs
    is always [] - a one-off URL import always runs; there is nothing on
    disk yet to resume from."""

    name = "youtube_import"

    def __init__(
        self,
        downloader: Optional[Callable[..., youtube_downloader_module.DownloadResult]] = None,
        prober: Optional[Callable[..., dict]] = None,
        submit_followup_fn: Optional[Callable[[str, dict, list[dict]], int]] = None,
    ) -> None:
        # Resolved against the live module attributes at construction time
        # (not copied into the __init__ signature's defaults at
        # class-definition/import time), so a test that monkeypatches
        # app.youtube.downloader.download_youtube_audio/probe_youtube_url
        # still takes effect - the youtube_import recipe's stage_factories
        # build a fresh YoutubeImportStage() per job run, well after any
        # monkeypatch has already been applied.
        self._downloader = downloader or youtube_downloader_module.download_youtube_audio
        self._prober = prober or youtube_downloader_module.probe_youtube_url
        self._submit_followup_fn = submit_followup_fn or _default_submit_followup

    def declared_outputs(self, ctx: StageContext) -> list[Path]:
        return []

    def run(self, ctx: StageContext) -> StageResult:
        url = ctx.options["youtube_url"]
        # Kept as the ORIGINAL settings-string spelling (not str(Path(...)))
        # all the way through to replace_tracks() below - /api/rescan (see
        # routes/tracks.py) keys the tracks table by that exact raw string,
        # and Path() normalization (e.g. forward slashes -> backslashes on
        # Windows) would otherwise mint a second, differently-spelled root
        # for the same directory, duplicating the whole library under it.
        # Path(...) is only ever applied for actual filesystem access below.
        media_roots = ctx.options.get("media_roots") or []
        raw_downloads_root = ctx.options.get("downloads_root") or (media_roots[0] if media_roots else None)
        if not raw_downloads_root:
            return StageResult(status=StageStatus.FAILED, detail="No downloads root configured")
        downloads_root = Path(raw_downloads_root)
        duration_cap = float(
            ctx.options.get("duration_cap_seconds", youtube_downloader_module.DEFAULT_DURATION_CAP_SECONDS)
        )
        cookies = ctx.options.get("youtube_cookies") or {"mode": "none"}
        db_path = Path(ctx.options["db_path"])

        info = self._prober(url, cookies=cookies)
        if not info.get("duration"):
            return StageResult(
                status=StageStatus.FAILED,
                detail="video duration unknown — refusing to download (duration cap)",
            )
        if info["duration"] > duration_cap:
            return StageResult(
                status=StageStatus.FAILED,
                detail=f"Video is {info['duration']:.0f}s, over the {duration_cap:.0f}s duration cap",
            )

        artist = ctx.options.get("youtube_artist") or info.get("uploader") or "Unknown Artist"
        title = ctx.options.get("youtube_title") or info.get("title") or "Unknown Title"
        destination = downloads_root / f"{_sanitize(artist)} - {_sanitize(title)}.m4a"

        try:
            self._downloader(url, destination, cookies=cookies)
        except youtube_downloader_module.YoutubeDownloadError as exc:
            if exc.age_restricted:
                return StageResult(
                    status=StageStatus.FAILED,
                    detail=(
                        "This video requires YouTube age verification. Configure YouTube "
                        "cookies in Settings (browser or cookies.txt) and retry."
                    ),
                )
            return StageResult(status=StageStatus.FAILED, detail=str(exc))

        conn = get_connection(db_path)
        mirror_roots = [Path(root) for root in ctx.options.get("mirror_roots") or []]
        records = scanner.scan_media_root(downloads_root, mirror_roots)
        replace_tracks(conn, raw_downloads_root, records)

        process_after = ctx.options.get("process_after")
        if process_after:
            track = next((record for record in records if Path(record.absolute_path) == destination), None)
            if track is not None:
                self._submit_followup_fn(
                    process_after["recipe"],
                    {
                        **process_after.get("options", {}),
                        "media_roots": ctx.options.get("media_roots", []),
                        "mirror_roots": ctx.options.get("mirror_roots", []),
                    },
                    [{"track_id": None, "source_path": str(destination)}],
                )

        return StageResult(status=StageStatus.COMPLETED, detail=f"downloaded {destination.name}")
