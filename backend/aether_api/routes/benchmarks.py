"""POST /api/v1/benchmarks/run: local-development-only benchmark execution.

There is no authentication yet, so this route refuses every caller that is
not on this machine, and also refuses browser requests from origins outside
the configured CORS list (a page on another site could otherwise make a
user's browser call localhost).
"""

from __future__ import annotations

import ipaddress
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from ..benchmarks import BenchmarkService
from ..errors import AccessDeniedError
from ..models import BenchmarkRunRequest, BenchmarkRunResponse, ErrorResponse

router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:  # e.g. a non-IP test client name
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped  # ::ffff:127.0.0.1
    return address.is_loopback


def require_local_development_access(request: Request) -> None:
    settings = request.app.state.settings
    if not settings.benchmarks_enabled:
        raise AccessDeniedError("benchmarks_disabled", "Benchmark execution is disabled on this server.")
    if not _is_loopback(request.client.host if request.client else None):
        raise AccessDeniedError("local_only", "Benchmark execution is only available from localhost.")
    origin = request.headers.get("origin")
    if origin is not None and origin not in settings.cors_origins:
        raise AccessDeniedError("origin_not_allowed", "Requests from this origin are not allowed.")


def get_benchmark_service(request: Request) -> BenchmarkService:
    return request.app.state.benchmark_service  # type: ignore[no-any-return]


# Sync handler: the engine run executes in a worker thread, not on the event loop.
@router.post(
    "/run",
    response_model=BenchmarkRunResponse,
    summary="Run one predefined C++ benchmark workload (localhost only)",
    dependencies=[Depends(require_local_development_access)],
    responses={403: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
               429: {"model": ErrorResponse}, 502: {"model": ErrorResponse},
               503: {"model": ErrorResponse}, 504: {"model": ErrorResponse}},
)
def run_benchmark(body: BenchmarkRunRequest,
                  service: Annotated[BenchmarkService, Depends(get_benchmark_service)]) -> BenchmarkRunResponse:
    return service.run(body)
