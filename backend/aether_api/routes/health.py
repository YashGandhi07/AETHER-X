from __future__ import annotations

from fastapi import APIRouter

from aether_bench import ExecutableNotFoundError, resolve_executable

from .. import __version__
from ..models import HealthResponse

router = APIRouter(tags=["health"])


def _engine_available() -> bool:
    # A file-system check only: health must stay cheap and never run the engine.
    try:
        resolve_executable("system_info")
        return True
    except ExecutableNotFoundError:
        return False


@router.get("/health", response_model=HealthResponse, summary="Service liveness and version")
def health() -> HealthResponse:
    available = _engine_available()
    return HealthResponse(
        status="ok" if available else "degraded",
        service="aether-x-api",
        version=__version__,
        engine_available=available,
    )
