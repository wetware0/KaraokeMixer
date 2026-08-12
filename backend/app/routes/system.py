from __future__ import annotations

from fastapi import APIRouter, Request

from ..workers.venvs import detect_worker_venvs

router = APIRouter()


@router.get("/api/system")
def read_system_info(request: Request) -> dict:
    return {
        "device": request.app.state.device,
        "workers": detect_worker_venvs(request.app.state.worker_venv_base),
    }
