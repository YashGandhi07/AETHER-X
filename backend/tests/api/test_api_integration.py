"""Integration: the real API backed by the real C++ collector (aether_system_info).

Skipped when the C++ engine is not built; AETHER_REQUIRE_INTEGRATION=1 makes
that a failure instead.
"""

from __future__ import annotations

import os
import platform

import pytest
from fastapi.testclient import TestClient

from aether_api.config import Settings
from aether_api.main import create_app
from aether_bench import ExecutableNotFoundError, resolve_executable


def _engine_built() -> bool:
    try:
        resolve_executable("system_info")
        resolve_executable("run_benchmark")
        return True
    except ExecutableNotFoundError:
        return False


pytestmark = pytest.mark.skipif(
    not _engine_built() and os.environ.get("AETHER_REQUIRE_INTEGRATION") != "1",
    reason="C++ engine not built (cmake -S cpp -B cpp/build && cmake --build cpp/build)")


def test_real_health_and_system() -> None:
    with TestClient(create_app(Settings())) as client:
        health = client.get("/api/v1/health").json()
        assert health["status"] == "ok" and health["engine_available"] is True

        response = client.get("/api/v1/system")
        assert response.status_code == 200
        host = response.json()["host"]

    # Cross-check against Python's independent view of this machine.
    assert host["logical_cpus"] == os.cpu_count()
    machine = platform.machine().lower()
    assert host["architecture"] == {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    if platform.system() == "Darwin":
        assert host["os_family"] == "macOS"
        assert host["os_version"] == platform.mac_ver()[0]
        assert host["cpu_model"]  # detected, non-empty


def test_real_benchmark_run_over_http() -> None:
    """Runs the real C++ engine through the HTTP endpoint (small, fast workload)."""
    request = {"workload": "vector_add_parallel", "size": 65536, "threads": 2,
               "warmup_runs": 1, "measured_runs": 5}
    with TestClient(create_app(Settings()), client=("127.0.0.1", 50000)) as client:
        response = client.post("/api/v1/benchmarks/run", json=request)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "succeeded" and body["request"] == request
    result = body["result"]
    assert result["workload"] == "vector_add_parallel" and result["input_size"] == 65536
    assert 1 <= result["thread_count"] <= 2
    assert len(result["samples_ms"]) == 5 and all(s > 0 for s in result["samples_ms"])
    assert result["warmup_runs_executed"] >= 1
    assert body["host"]["logical_cpus"] == os.cpu_count()  # measured on this machine
    assert not body["host"]["cpu_model"].startswith("TEST_FIXTURE")
