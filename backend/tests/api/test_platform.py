"""Cross-cutting behaviour: error format, request ids, CORS, logging, config."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from aether_api.config import ConfigurationError, Settings
from aether_api.logging_config import JsonFormatter
from aether_api.main import create_app
from aether_api.system_info import SystemInfoProvider

from .conftest import CountingFetch

# ---------- consistent error responses ----------


def test_unknown_route_uses_error_format(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_wrong_method_uses_error_format(client: TestClient) -> None:
    response = client.post("/api/v1/health")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_unhandled_exception_is_generic_500(client: TestClient, fetch: CountingFetch,
                                            caplog: pytest.LogCaptureFixture) -> None:
    def crash():  # type: ignore[no-untyped-def]
        raise RuntimeError("database password is hunter2")

    fetch.result = crash
    response = client.get("/api/v1/system")
    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "internal_error", "message": "An internal error occurred.",
        "request_id": response.headers["X-Request-ID"]}
    assert "hunter2" not in response.text


# ---------- request ids ----------


def test_request_id_generated_and_echoed(client: TestClient) -> None:
    generated = client.get("/api/v1/health").headers["X-Request-ID"]
    assert len(generated) == 32
    assert client.get("/api/v1/health", headers={"X-Request-ID": "trace-123"}).headers["X-Request-ID"] == "trace-123"


def test_unsafe_request_id_is_replaced(client: TestClient) -> None:
    hostile = 'x"}\n{"level":"FAKE'
    returned = client.get("/api/v1/health", headers={"X-Request-ID": hostile}).headers["X-Request-ID"]
    assert returned != hostile and len(returned) == 32


def test_security_header_present(client: TestClient) -> None:
    assert client.get("/api/v1/health").headers["X-Content-Type-Options"] == "nosniff"


# ---------- CORS ----------


def test_cors_allows_configured_origin_without_credentials(client: TestClient) -> None:
    response = client.options("/api/v1/system", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "access-control-allow-credentials" not in response.headers


def test_cors_rejects_unknown_origin(client: TestClient) -> None:
    preflight = client.options("/api/v1/system", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in preflight.headers
    simple = client.get("/api/v1/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in simple.headers


def test_cors_only_allows_get(client: TestClient) -> None:
    response = client.options("/api/v1/system", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "DELETE"})
    assert response.status_code == 400


# ---------- configuration ----------


def test_settings_defaults_and_environment() -> None:
    assert Settings.from_environment({}) == Settings()
    custom = Settings.from_environment({
        "AETHER_CORS_ORIGINS": "https://app.example.com, http://localhost:5173",
        "AETHER_ENGINE_TIMEOUT_S": "5", "AETHER_LOG_LEVEL": "warning"})
    assert custom.cors_origins == ("https://app.example.com", "http://localhost:5173")
    assert custom.engine_timeout_s == 5.0 and custom.log_level == "WARNING"


@pytest.mark.parametrize("environ", [
    {"AETHER_CORS_ORIGINS": "*"},
    {"AETHER_CORS_ORIGINS": "http://localhost:3000,*"},
    {"AETHER_CORS_ORIGINS": "localhost:3000"},             # no scheme
    {"AETHER_CORS_ORIGINS": "http://localhost:3000/path"},  # origins have no path
    {"AETHER_ENGINE_TIMEOUT_S": "0"},
    {"AETHER_ENGINE_TIMEOUT_S": "nan"},
    {"AETHER_ENGINE_TIMEOUT_S": "ten"},
    {"AETHER_LOG_LEVEL": "VERBOSE"},
])
def test_invalid_settings_fail_fast(environ: dict[str, str]) -> None:
    with pytest.raises(ConfigurationError):
        Settings.from_environment(environ)


# ---------- structured logging ----------


def test_json_formatter_emits_one_parseable_object() -> None:
    record = logging.LogRecord("aether.test", logging.INFO, __file__, 1, 'say "hi"\nthere', None, None)
    record.fields = {"request_id": "abc", "status": 200}
    line = JsonFormatter().format(record)
    assert "\n" not in line  # newlines in messages cannot split a log entry
    entry = json.loads(line)
    assert entry["message"] == 'say "hi"\nthere' and entry["request_id"] == "abc"
    assert entry["level"] == "INFO" and entry["ts"].endswith("+00:00")


def test_access_log_records_each_request(fetch: CountingFetch, settings: Settings,
                                         caplog: pytest.LogCaptureFixture) -> None:
    app = create_app(settings, SystemInfoProvider(fetch))
    logging.getLogger("aether").propagate = True  # let caplog see the records
    try:
        with caplog.at_level(logging.INFO, logger="aether.api.access"), TestClient(app) as client:
            client.get("/api/v1/health?token=secret", headers={"X-Request-ID": "log-check"})
    finally:
        logging.getLogger("aether").propagate = False
    entries = [r.fields for r in caplog.records if r.name == "aether.api.access"]  # type: ignore[attr-defined]
    assert entries and entries[-1]["request_id"] == "log-check"
    assert entries[-1]["path"] == "/api/v1/health" and entries[-1]["status"] == 200
    assert "secret" not in json.dumps(entries)
