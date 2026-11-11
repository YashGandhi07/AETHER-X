from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from ..models import ErrorResponse, SystemResponse
from ..system_info import SystemInfoProvider

router = APIRouter(tags=["system"])


def get_system_info_provider(request: Request) -> SystemInfoProvider:
    return request.app.state.system_info_provider  # type: ignore[no-any-return]


# A sync handler: FastAPI runs it in a worker thread, so the (first, uncached)
# engine subprocess call never blocks the event loop.
@router.get(
    "/system",
    response_model=SystemResponse,
    summary="Host hardware and engine build metadata",
    responses={502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}, 504: {"model": ErrorResponse}},
)
def system(provider: Annotated[SystemInfoProvider, Depends(get_system_info_provider)]) -> SystemResponse:
    return provider.get()
