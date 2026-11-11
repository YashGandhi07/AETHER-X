"""Endpoint behaviour with the synthetic metadata provider."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import aether_api.routes.health as health_route
from aether_api import __version__
from aether_bench import (
    BenchmarkOutputError,
    BenchmarkProcessError,
    BenchmarkTimeoutError,
    ExecutableNotFoundError,
)

from .conftest import CountingFetch


# ---------- /api/v1/health ----------


def test_health_reports_version_and_engine_status(client: TestClient,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_route, "_engine_available", lambda: True)
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "aether-x-api",
                               "version": __version__, "engine_available": True}


def test_health_is_degraded_without_engine(client: TestClient,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_route, "_engine_available", lambda: False)
    body = client.get("/api/v1/health").json()
    assert body["status"] == "degraded" and body["engine_available"] is False


def test_health_never_calls_the_engine(client: TestClient, fetch: CountingFetch) -> None:
    client.get("/api/v1/health")
    assert fetch.calls == 0


# ---------- /api/v1/system ----------


def test_system_returns_typed_metadata(client: TestClient) -> None:
    response = client.get("/api/v1/system")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"collected_at_utc", "host", "engine_build"}
    assert body["collected_at_utc"] == "2000-01-01T00:00:00Z"
    assert body["host"]["cpu_model"] == "TEST_FIXTURE CPU (not a real reading)"
    assert body["host"]["core_types"] == [{"name": "TEST_FIXTURE_core", "physical_cpus": 2}]
    assert body["engine_build"]["cxx_standard"] == "C++20"


def test_system_unavailable_values_are_explicit_nulls(client: TestClient) -> None:
    host = client.get("/api/v1/system").json()["host"]
    for field in ("os_version", "kernel_release", "cache_line_bytes"):
        assert field in host and host[field] is None


def test_system_exposes_only_intended_fields(client: TestClient) -> None:
    body = client.get("/api/v1/system").json()
    assert set(body["host"]) == {
        "os_family", "os_version", "os_build", "kernel_release", "architecture",
        "process_translated", "cpu_model", "logical_cpus", "physical_cpus", "core_types",
        "cache_line_bytes", "memory_bytes"}
    assert set(body["engine_build"]) == {
        "compiler_id", "compiler_version", "cxx_standard", "build_type", "cmake_cxx_flags",
        "assertions_enabled"}


def test_system_metadata_is_collected_once(client: TestClient, fetch: CountingFetch) -> None:
    for _ in range(5):
        assert client.get("/api/v1/system").status_code == 200
    assert fetch.calls == 1


@pytest.mark.parametrize(("error", "status", "code"), [
    (ExecutableNotFoundError("/secret/path/cpp/build/aether_system_info not found"), 503, "engine_not_built"),
    (BenchmarkTimeoutError(10.0, "", ""), 504, "engine_timeout"),
    (BenchmarkProcessError(1, "", "error: /secret/path exploded"), 502, "engine_failed"),
    (BenchmarkOutputError("invalid report at $.system.cpu_model"), 502, "engine_bad_output"),
])
def test_engine_failures_map_to_safe_errors(client: TestClient, fetch: CountingFetch,
                                            error: Exception, status: int, code: str) -> None:
    def fail() -> None:
        raise error

    fetch.result = fail  # type: ignore[assignment]
    response = client.get("/api/v1/system")
    assert response.status_code == status
    body = response.json()
    assert body["error"]["code"] == code
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]
    assert "/secret/path" not in response.text and "$.system" not in response.text


def test_failures_are_not_cached(client: TestClient, fetch: CountingFetch) -> None:
    from .conftest import synthetic_system_report

    def fail():  # type: ignore[no-untyped-def]
        raise ExecutableNotFoundError("not built")

    fetch.result = fail
    assert client.get("/api/v1/system").status_code == 503
    fetch.result = synthetic_system_report  # engine built in the meantime
    assert client.get("/api/v1/system").status_code == 200
    assert fetch.calls == 2
