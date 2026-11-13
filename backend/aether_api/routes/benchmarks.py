"""Benchmark routes (local development only):

    POST /api/v1/benchmarks/run               run one workload and save the result
    GET  /api/v1/benchmarks                   saved experiments, paginated
    GET  /api/v1/benchmarks/{experiment_id}   one saved experiment with its samples

There is no authentication yet, so every route refuses callers that are not
on this machine, and also refuses browser requests from origins outside the
configured CORS list (a page on another site could otherwise make a user's
browser call localhost).
"""

from __future__ import annotations

import ipaddress
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..benchmarks import BenchmarkService
from ..errors import AccessDeniedError
from ..models import (
    MAX_HISTORY_OFFSET,
    MAX_HISTORY_PAGE_SIZE,
    BenchmarkHistoryResponse,
    BenchmarkRunRequest,
    BenchmarkRunResponse,
    ErrorResponse,
)
from ..persistence import BenchmarkStore

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


def require_local_access(request: Request) -> None:
    if not _is_loopback(request.client.host if request.client else None):
        raise AccessDeniedError("local_only", "Benchmark endpoints are only available from localhost.")
    origin = request.headers.get("origin")
    if origin is not None and origin not in request.app.state.settings.cors_origins:
        raise AccessDeniedError("origin_not_allowed", "Requests from this origin are not allowed.")


def require_local_development_access(request: Request) -> None:
    # Checked first: a disabled endpoint says so to every caller.
    if not request.app.state.settings.benchmarks_enabled:
        raise AccessDeniedError("benchmarks_disabled", "Benchmark execution is disabled on this server.")
    require_local_access(request)


def get_benchmark_service(request: Request) -> BenchmarkService:
    return request.app.state.benchmark_service  # type: ignore[no-any-return]


def get_benchmark_store(request: Request) -> BenchmarkStore:
    return request.app.state.benchmark_store  # type: ignore[no-any-return]


# Sync handler: the engine run executes in a worker thread, not on the event loop.
@router.post(
    "/run",
    response_model=BenchmarkRunResponse,
    summary="Run one predefined C++ benchmark workload (localhost only)",
    dependencies=[Depends(require_local_development_access)],
    responses={403: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
               429: {"model": ErrorResponse}, 500: {"model": ErrorResponse},
               502: {"model": ErrorResponse}, 503: {"model": ErrorResponse},
               504: {"model": ErrorResponse}},
)
def run_benchmark(body: BenchmarkRunRequest,
                  service: Annotated[BenchmarkService, Depends(get_benchmark_service)]) -> BenchmarkRunResponse:
    return service.run(body)


# Reads are allowed even when execution is disabled: history is not a benchmark run.
_READ_ERRORS = {403: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
                500: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}


@router.get(
    "",
    response_model=BenchmarkHistoryResponse,
    summary="Saved benchmark experiments, newest first (localhost only)",
    dependencies=[Depends(require_local_access)],
    responses=_READ_ERRORS,
)
def list_benchmarks(store: Annotated[BenchmarkStore, Depends(get_benchmark_store)],
                    limit: Annotated[int, Query(ge=1, le=MAX_HISTORY_PAGE_SIZE)] = 20,
                    offset: Annotated[int, Query(ge=0, le=MAX_HISTORY_OFFSET)] = 0,
                    ) -> BenchmarkHistoryResponse:
    page = store.history(limit=limit, offset=offset)
    return BenchmarkHistoryResponse(items=page.items, total=page.total, limit=limit,
                                    offset=offset, time_unit="ms")


# The uuid converter makes non-UUID paths (including /run) fall through to
# 404/405 instead of matching this route.
@router.get(
    "/{experiment_id:uuid}",
    response_model=BenchmarkRunResponse,
    summary="One saved experiment with every timing sample (localhost only)",
    dependencies=[Depends(require_local_access)],
    responses={404: {"model": ErrorResponse}, **_READ_ERRORS},
)
def get_benchmark(experiment_id: uuid.UUID,
                  store: Annotated[BenchmarkStore, Depends(get_benchmark_store)]) -> BenchmarkRunResponse:
    saved = store.get(experiment_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Experiment not found.")
    return saved
