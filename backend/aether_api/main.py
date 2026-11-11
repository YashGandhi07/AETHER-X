"""Application entry point.

    uvicorn aether_api.main:app --app-dir backend --host 127.0.0.1 --port 8000

``create_app`` builds a fully configured instance; tests call it with their
own settings and metadata provider.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Awaitable, Callable, Optional

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .benchmarks import BenchmarkService
from .config import Settings
from .errors import register_exception_handlers
from .logging_config import configure_logging
from .routes import api_v1
from .system_info import SystemInfoProvider

access_log = logging.getLogger("aether.api.access")

# Accept a caller's request id only if it is short and plain, so it cannot
# inject content into logs or headers; otherwise generate one.
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


def create_app(settings: Optional[Settings] = None,
               system_info_provider: Optional[SystemInfoProvider] = None,
               benchmark_service: Optional[BenchmarkService] = None) -> FastAPI:
    settings = settings or Settings.from_environment()
    configure_logging(settings.log_level)

    app = FastAPI(title="AETHER X API", version=__version__)
    app.state.settings = settings
    app.state.system_info_provider = (
        system_info_provider or SystemInfoProvider.from_engine(settings.engine_timeout_s))
    app.state.benchmark_service = (
        benchmark_service or BenchmarkService.from_engine(settings.benchmark_timeout_s))

    # Read-only API without authentication yet: no credentials, GET only,
    # explicitly listed origins (validated in Settings; '*' is rejected).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )

    @app.middleware("http")
    async def request_context(request: Request,
                              call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _SAFE_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Path only: query strings may carry data that does not belong in logs.
        access_log.info("request", extra={"fields": {
            "request_id": request_id, "method": request.method, "path": request.url.path,
            "status": response.status_code,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3)}})
        return response

    register_exception_handlers(app)
    app.include_router(api_v1)
    return app


app = create_app()
