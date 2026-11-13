"""POST /api/v1/benchmarks/run with a fake engine run (SYNTHETIC results)."""

from __future__ import annotations

import os
import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aether_api.benchmarks import BenchmarkBusyError, BenchmarkService
from aether_api.config import Settings
from aether_api.main import create_app
from aether_api.models import BenchmarkRunRequest
from aether_api.system_info import SystemInfoProvider
from aether_bench import (
    BenchmarkOutputError,
    BenchmarkProcessError,
    BenchmarkTimeoutError,
    ExecutableNotFoundError,
)

from .conftest import CountingFetch, FakeBenchmarkRun, RecordingStore

URL = "/api/v1/benchmarks/run"
VALID = {"workload": "vector_add_parallel", "size": 4096, "threads": 2,
         "warmup_runs": 1, "measured_runs": 5}


# ---------- success ----------


def test_successful_run_returns_results_config_and_hardware(local_client: TestClient,
                                                            fake_run: FakeBenchmarkRun) -> None:
    response = local_client.post(URL, json=VALID)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded" and body["time_unit"] == "ms"
    assert body["request"] == VALID
    result = body["result"]
    assert result["workload"] == "vector_add_parallel" and result["input_size"] == 4096
    assert result["config"]["measured_runs"] == 5 and result["config"]["warmup_runs"] == 1
    assert result["samples_ms"] == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert result["statistics"]["median"] == 3.0
    assert body["host"]["cpu_model"].startswith("TEST_FIXTURE")  # hardware comes from the run's report
    assert set(body["engine_build"]) >= {"compiler_id", "build_type"}

    (spec,) = fake_run.specs
    assert (spec.workload, spec.size, spec.threads, spec.warmup_runs, spec.measured_runs) == (
        "vector_add_parallel", 4096, 2, 1, 5)


def test_defaults_applied(local_client: TestClient, fake_run: FakeBenchmarkRun) -> None:
    response = local_client.post(URL, json={"workload": "row_sums_row_major", "size": 64})
    assert response.status_code == 200
    assert response.json()["request"] == {"workload": "row_sums_row_major", "size": 64,
                                          "threads": 1, "warmup_runs": 3, "measured_runs": 10}
    assert response.json()["result"]["input_size"] == 64 * 64
    assert fake_run.specs[0].measured_runs == 10


# ---------- invalid parameters ----------

CPU_COUNT = os.cpu_count() or 1


@pytest.mark.parametrize(("payload", "fragment"), [
    ({**VALID, "workload": "sh"}, "workload"),
    ({**VALID, "workload": "../../bin/sh"}, "workload"),
    ({**VALID, "size": 0}, "size"),
    ({**VALID, "size": (1 << 24) + 1}, "size must be <="),
    ({"workload": "row_sums_column_major", "size": 4097}, "matrix dimension"),
    ({"workload": "vector_add", "size": 64, "threads": 2}, "single-threaded"),
    ({**VALID, "threads": CPU_COUNT + 1}, "logical CPUs"),
    ({**VALID, "threads": 0}, "threads"),
    ({**VALID, "warmup_runs": 21}, "warmup_runs"),
    ({**VALID, "warmup_runs": -1}, "warmup_runs"),
    ({**VALID, "measured_runs": 0}, "measured_runs"),
    ({**VALID, "measured_runs": 101}, "measured_runs"),
    ({**VALID, "size": "4096"}, "size"),       # strict: no string coercion
    ({**VALID, "size": 4096.0}, "size"),       # strict: no float coercion
    ({**VALID, "threads": True}, "threads"),   # strict: bool is not an integer
    ({**VALID, "command": "rm -rf /"}, "command"),            # unknown fields forbidden
    ({**VALID, "executable": "/bin/sh"}, "executable"),
    ({"size": 4096}, "workload"),                              # missing required field
])
def test_invalid_parameters_rejected_before_running(local_client: TestClient, fake_run: FakeBenchmarkRun,
                                                    payload: dict[str, Any], fragment: str) -> None:
    response = local_client.post(URL, json=payload)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert fragment in str(error["details"])
    assert fake_run.specs == []  # nothing reached the engine


def test_validation_errors_do_not_echo_input(local_client: TestClient) -> None:
    response = local_client.post(URL, json={**VALID, "size": "SECRET-INPUT-VALUE"})
    assert response.status_code == 422 and "SECRET-INPUT-VALUE" not in response.text


@pytest.mark.parametrize(("content", "content_type"), [
    ('{"workload": "vector_add", "size": 64}', "text/plain"),  # CORS "simple" request type
    ("workload=vector_add&size=64", "application/x-www-form-urlencoded"),
    ("not json", "application/json"),
])
def test_non_json_bodies_rejected(local_client: TestClient, fake_run: FakeBenchmarkRun,
                                  content: str, content_type: str) -> None:
    response = local_client.post(URL, content=content, headers={"Content-Type": content_type})
    assert response.status_code == 422
    assert fake_run.specs == []


def test_get_is_not_allowed(local_client: TestClient) -> None:
    assert local_client.get(URL).status_code == 405


# ---------- local-development-only access ----------


@pytest.mark.parametrize("host", ["testclient", "203.0.113.5", "192.168.1.20", "10.0.0.1", "::2"])
def test_remote_clients_are_refused(settings: Settings, fetch: CountingFetch,
                                    fake_run: FakeBenchmarkRun, host: str) -> None:
    app = create_app(settings, SystemInfoProvider(fetch), BenchmarkService(fake_run, RecordingStore()))  # type: ignore[arg-type]
    with TestClient(app, client=(host, 50000)) as remote:
        response = remote.post(URL, json=VALID)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "local_only"
    assert fake_run.specs == []


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "::ffff:127.0.0.1"])
def test_loopback_clients_are_allowed(settings: Settings, fetch: CountingFetch,
                                      fake_run: FakeBenchmarkRun, host: str) -> None:
    app = create_app(settings, SystemInfoProvider(fetch), BenchmarkService(fake_run, RecordingStore()))  # type: ignore[arg-type]
    with TestClient(app, client=(host, 50000)) as local:
        assert local.post(URL, json=VALID).status_code == 200


def test_foreign_browser_origin_refused(local_client: TestClient, fake_run: FakeBenchmarkRun) -> None:
    # A web page on another site can make a local browser send requests to localhost.
    refused = local_client.post(URL, json=VALID, headers={"Origin": "https://evil.example"})
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "origin_not_allowed"
    allowed = local_client.post(URL, json=VALID, headers={"Origin": "http://localhost:3000"})
    assert allowed.status_code == 200
    assert len(fake_run.specs) == 1


def test_disabled_by_configuration(fetch: CountingFetch, fake_run: FakeBenchmarkRun) -> None:
    app = create_app(Settings(benchmarks_enabled=False), SystemInfoProvider(fetch),
                     BenchmarkService(fake_run, RecordingStore()))  # type: ignore[arg-type]
    with TestClient(app, client=("127.0.0.1", 50000)) as local:
        response = local.post(URL, json=VALID)
    assert response.status_code == 403 and response.json()["error"]["code"] == "benchmarks_disabled"


# ---------- concurrency ----------


def test_concurrent_request_is_rejected_with_429(local_client: TestClient) -> None:
    service: BenchmarkService = local_client.app.state.benchmark_service  # type: ignore[attr-defined]
    started, release = threading.Event(), threading.Event()
    inner = service._run

    def slow_run(spec):  # type: ignore[no-untyped-def]
        started.set()
        release.wait(timeout=10)
        return inner(spec)

    service._run = slow_run
    first: dict[str, int] = {}
    worker = threading.Thread(target=lambda: first.update(status=local_client.post(URL, json=VALID).status_code))
    worker.start()
    assert started.wait(timeout=10)
    try:
        busy = local_client.post(URL, json=VALID)
        assert busy.status_code == 429
        assert busy.json()["error"]["code"] == "benchmark_busy"
        assert busy.headers["Retry-After"] == "5"
    finally:
        release.set()
        worker.join(timeout=10)
    assert first["status"] == 200
    assert local_client.post(URL, json=VALID).status_code == 200  # lock released afterwards


def test_service_lock_is_non_blocking(fake_run: FakeBenchmarkRun) -> None:
    service = BenchmarkService(fake_run, RecordingStore())  # type: ignore[arg-type]
    request = BenchmarkRunRequest(**VALID)
    assert service._lock.acquire(blocking=False)
    try:
        with pytest.raises(BenchmarkBusyError):
            service.run(request)
    finally:
        service._lock.release()
    assert service.run(request).status == "succeeded"


# ---------- engine failures ----------


@pytest.mark.parametrize(("error", "status", "code"), [
    (BenchmarkTimeoutError(60.0, "partial", "partial"), 504, "engine_timeout"),
    (BenchmarkProcessError(1, "", "error: verification failed for vector_add at /secret/path"), 502, "engine_failed"),
    (BenchmarkOutputError("report does not match the request: workload"), 502, "engine_bad_output"),
    (ExecutableNotFoundError("/secret/path/cpp/build/aether_run_benchmark not found"), 503, "engine_not_built"),
])
def test_engine_failures_are_safe_and_release_the_lock(local_client: TestClient, fake_run: FakeBenchmarkRun,
                                                       error: Exception, status: int, code: str) -> None:
    fake_run.error = error
    response = local_client.post(URL, json=VALID)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "/secret/path" not in response.text and "verification failed" not in response.text
    fake_run.error = None
    assert local_client.post(URL, json=VALID).status_code == 200  # a failure never leaves the lock held
