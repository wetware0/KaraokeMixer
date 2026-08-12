from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .db import DEFAULT_DB_PATH, get_connection
from .events import EventBus
from .queue import JobQueueManager
from .rescan import LibraryScanManager
from .security import LocalOnlyMiddleware
from .routes.audio import router as audio_router
from .routes.jobs import router as jobs_router
from .routes.recipes import router as recipes_router
from .routes.settings import router as settings_router
from .routes.system import router as system_router
from .routes.tracks import router as tracks_router
from .routes.youtube import router as youtube_router
from .routes.ws import router as ws_router
from .stages.youtube_import import bind_job_queue
from .system import probe_device


def create_app(
    db_path: Path = DEFAULT_DB_PATH,
    dist_dir: Path | None = None,
    worker_venv_base: Path | None = None,
    allow_remote_clients: bool | None = None,
) -> FastAPI:
    app = FastAPI(title="Karaoke Media Manager")
    app.add_middleware(LocalOnlyMiddleware, allow_remote=allow_remote_clients)
    app.state.db_conn = get_connection(db_path)
    app.state.db_path = db_path
    app.state.device = probe_device()
    app.state.worker_venv_base = worker_venv_base or Path(__file__).resolve().parent.parent
    app.state.event_bus = EventBus()
    app.state.library_scan = LibraryScanManager(app.state.db_conn, app.state.event_bus)
    app.state.job_queue = JobQueueManager(app.state.db_conn, app.state.event_bus)
    bind_job_queue(app.state.job_queue)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(audio_router)
    app.include_router(jobs_router)
    app.include_router(recipes_router)
    app.include_router(settings_router)
    app.include_router(system_router)
    app.include_router(tracks_router)
    app.include_router(youtube_router)
    app.include_router(ws_router)

    if dist_dir is not None and dist_dir.is_dir():
        app.mount("/", StaticFiles(directory=dist_dir, html=True), name="spa")

    return app


app = create_app(dist_dir=Path(__file__).resolve().parent.parent.parent / "frontend" / "dist")
