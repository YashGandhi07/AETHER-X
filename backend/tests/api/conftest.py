"""Shared API test fixtures.

The metadata provider is replaced with SYNTHETIC data labelled TEST_FIXTURE,
so unit tests are deterministic and never mistaken for real hardware readings.
Only test_api_integration.py talks to the real C++ collector.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from aether_api.config import Settings
from aether_api.main import create_app
from aether_api.system_info import SystemInfoProvider
from aether_bench import BuildInfo, CoreType, SystemInfo, SystemReport


def synthetic_system_report() -> SystemReport:
    return SystemReport(
        schema="aether.system_info.v1",
        generated_at_utc="2000-01-01T00:00:00Z",
        system=SystemInfo(
            os_family="TEST_FIXTURE_os",
            os_version=None,  # exercises the null path
            os_build="TEST_FIXTURE_build",
            kernel_release=None,
            architecture="TEST_FIXTURE_arch",
            process_translated=False,
            cpu_model="TEST_FIXTURE CPU (not a real reading)",
            logical_cpus=2,
            physical_cpus=2,
            core_types=(CoreType(name="TEST_FIXTURE_core", physical_cpus=2),),
            cache_line_bytes=None,
            memory_bytes=1024,
        ),
        build=BuildInfo(
            compiler_id="TEST_FIXTURE_compiler",
            compiler_version="0.0.0",
            cxx_standard="C++20",
            build_type="Release",
            cmake_cxx_flags=None,
            assertions_enabled=False,
        ),
    )


class CountingFetch:
    """A fetch function that records calls and can be told to fail."""

    def __init__(self, result: Callable[[], SystemReport] = synthetic_system_report) -> None:
        self.calls = 0
        self.result = result

    def __call__(self) -> SystemReport:
        self.calls += 1
        return self.result()


@pytest.fixture
def fetch() -> CountingFetch:
    return CountingFetch()


@pytest.fixture
def settings() -> Settings:
    return Settings(log_level="DEBUG")


@pytest.fixture
def client(settings: Settings, fetch: CountingFetch) -> Iterator[TestClient]:
    app = create_app(settings, SystemInfoProvider(fetch))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
