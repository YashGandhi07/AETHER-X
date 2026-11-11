"""Centralized error handling: every failure becomes the same JSON shape,

    {"error": {"code": "...", "message": "...", "request_id": "..."}}

Clients get a stable code and a safe message. Internal details (file paths,
engine stderr, tracebacks) go to the server log only, keyed by request id.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from aether_bench import (
    BenchmarkError,
    BenchmarkOutputError,
    BenchmarkProcessError,
    BenchmarkTimeoutError,
    ExecutableNotFoundError,
)

from .benchmarks import BenchmarkBusyError
from .models import ErrorBody, ErrorResponse

logger = logging.getLogger("aether.api.errors")

# Seconds a client should wait before retrying a busy benchmark endpoint.
_BUSY_RETRY_AFTER_S = 5


class AccessDeniedError(Exception):
    """The caller may not use this endpoint (e.g. not on localhost)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

# Engine failure -> (HTTP status, code, client-safe message). The engine is an
# upstream dependency: unavailable is 503, slow is 504, wrong answer is 502.
_ENGINE_ERRORS: list[tuple[type[BenchmarkError], int, str, str]] = [
    (ExecutableNotFoundError, 503, "engine_not_built", "The C++ engine is not built on this server."),
    (BenchmarkTimeoutError, 504, "engine_timeout", "The C++ engine did not respond in time."),
    (BenchmarkProcessError, 502, "engine_failed", "The C++ engine exited with an error."),
    (BenchmarkOutputError, 502, "engine_bad_output", "The C++ engine returned invalid data."),
]


def _request_id(request: Request) -> Optional[str]:
    return getattr(request.state, "request_id", None)


def error_response(request: Request, status: int, code: str, message: str,
                   details: Optional[list[dict[str, Any]]] = None,
                   headers: Optional[dict[str, str]] = None) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message,
                                         request_id=_request_id(request), details=details))
    all_headers = dict(headers or {})
    if rid := _request_id(request):
        all_headers["X-Request-ID"] = rid
    return JSONResponse(status_code=status, content=body.model_dump(exclude_none=True),
                        headers=all_headers or None)


async def _access_denied(request: Request, exc: AccessDeniedError) -> JSONResponse:
    logger.warning("access denied", extra={"fields": {
        "request_id": _request_id(request), "code": exc.code, "path": request.url.path,
        "client": request.client.host if request.client else None}})
    return error_response(request, 403, exc.code, exc.message)


async def _benchmark_busy(request: Request, exc: BenchmarkBusyError) -> JSONResponse:
    return error_response(request, 429, "benchmark_busy",
                          "Another benchmark is running; retry after it finishes.",
                          headers={"Retry-After": str(_BUSY_RETRY_AFTER_S)})


async def _engine_error(request: Request, exc: Exception) -> JSONResponse:
    for error_type, status, code, message in _ENGINE_ERRORS:
        if isinstance(exc, error_type):
            break
    else:
        status, code, message = 502, "engine_error", "The C++ engine failed."
    logger.error("engine call failed", extra={"fields": {
        "request_id": _request_id(request), "error_type": type(exc).__name__,
        "detail": str(exc), "stderr": getattr(exc, "stderr", None)}})
    return error_response(request, status, code, message)


async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    codes = {404: "not_found", 405: "method_not_allowed"}
    message = exc.detail if isinstance(exc.detail, str) else "HTTP error"
    return error_response(request, exc.status_code, codes.get(exc.status_code, "http_error"), message)


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Report where and why, but never echo the submitted input back.
    details = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
               for e in exc.errors()]
    return error_response(request, 422, "validation_error", "Request validation failed.", details)


async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error", extra={"fields": {
        "request_id": _request_id(request), "path": request.url.path}})
    return error_response(request, 500, "internal_error", "An internal error occurred.")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AccessDeniedError, _access_denied)  # type: ignore[arg-type]
    app.add_exception_handler(BenchmarkBusyError, _benchmark_busy)  # type: ignore[arg-type]
    app.add_exception_handler(BenchmarkError, _engine_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unhandled_error)
