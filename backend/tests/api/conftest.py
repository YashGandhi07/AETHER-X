"""Shared API test fixtures.

The metadata provider is replaced with SYNTHETIC data labelled TEST_FIXTURE,
so unit tests are deterministic and never mistaken for real hardware readings.
Only test_api_integration.py talks to the real C++ collector.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from aether_api.benchmarks import BenchmarkService
from aether_api.config import Settings
from aether_api.main import create_app
from aether_api.system_info import SystemInfoProvider
from aether_bench import (
    MATRIX_WORKLOADS,
    BenchmarkSpec,
    BuildInfo,
    CoreType,
    ProfileRun,
    SystemInfo,
    SystemReport,
    parse_report,
)

from ..fixtures import synthetic_record, synthetic_report


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


class FakeBenchmarkRun:
    """Stands in for aether_bench.run_benchmark: records the spec it receives
    and returns a SYNTHETIC one-record report shaped like that spec."""

    def __init__(self) -> None:
        self.specs: list[BenchmarkSpec] = []
        self.error: Exception | None = None

    def __call__(self, spec: BenchmarkSpec) -> ProfileRun:
        self.specs.append(spec)
        if self.error is not None:
            raise self.error
        document = synthetic_report()
        record = synthetic_record("00000000-0000-4000-8000-000000000001",
                                  [float(i + 1) for i in range(spec.measured_runs)])
        record.update(workload=spec.workload, thread_count=spec.threads,
                      input_size=spec.size * spec.size if spec.workload in MATRIX_WORKLOADS else spec.size)
        record["config"]["warmup_runs"] = spec.warmup_runs
        record["warmup_runs_executed"] = max(spec.warmup_runs, 1)
        document["results"] = [record]
        return ProfileRun(report=parse_report(json.dumps(document)), stdout="", stderr="",
                          wall_time_s=0.25)


@pytest.fixture
def fetch() -> CountingFetch:
    return CountingFetch()


@pytest.fixture
def fake_run() -> FakeBenchmarkRun:
    return FakeBenchmarkRun()


@pytest.fixture
def local_client(settings: Settings, fetch: CountingFetch,
                 fake_run: FakeBenchmarkRun) -> Iterator[TestClient]:
    """A client whose requests come from 127.0.0.1, like a local developer."""
    app = create_app(settings, SystemInfoProvider(fetch), BenchmarkService(fake_run))
    with TestClient(app, raise_server_exceptions=False, client=("127.0.0.1", 50000)) as test_client:
        yield test_client


@pytest.fixture
def settings() -> Settings:
    return Settings(log_level="DEBUG")


@pytest.fixture
def client(settings: Settings, fetch: CountingFetch,
           fake_run: FakeBenchmarkRun) -> Iterator[TestClient]:
    """Default TestClient: its client host is 'testclient', i.e. NOT loopback."""
    app = create_app(settings, SystemInfoProvider(fetch), BenchmarkService(fake_run))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
