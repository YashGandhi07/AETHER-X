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
