"""Host metadata provider backed by the C++ collector (via aether_bench).

Hardware does not change while the service runs, so the first successful
result is cached for the process lifetime. Failures are not cached: once the
engine is built, the next request succeeds without a restart.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from aether_bench import SystemReport, run_system_info

from .models import CoreTypeModel, EngineBuildModel, HostModel, SystemResponse


class SystemInfoProvider:
    def __init__(self, fetch: Callable[[], SystemReport]) -> None:
        self._fetch = fetch
        self._cached: Optional[SystemResponse] = None
        # Sync endpoints run in a thread pool; the lock also prevents a burst
        # of first requests from each starting its own engine process.
        self._lock = threading.Lock()

    @classmethod
    def from_engine(cls, timeout_s: float) -> SystemInfoProvider:
        return cls(lambda: run_system_info(timeout_s=timeout_s))

    def get(self) -> SystemResponse:
        """Returns cached metadata, collecting it on first use.

        Raises aether_bench.BenchmarkError subclasses if the engine fails.
        """
        with self._lock:
            if self._cached is None:
                self._cached = to_response(self._fetch())
            return self._cached


def to_response(report: SystemReport) -> SystemResponse:
    """Copies the intended fields field-by-field into the public models."""
    s, b = report.system, report.build
    return SystemResponse(
        collected_at_utc=report.generated_at_utc,
        host=HostModel(
            os_family=s.os_family,
            os_version=s.os_version,
            os_build=s.os_build,
            kernel_release=s.kernel_release,
            architecture=s.architecture,
            process_translated=s.process_translated,
            cpu_model=s.cpu_model,
            logical_cpus=s.logical_cpus,
            physical_cpus=s.physical_cpus,
            core_types=[CoreTypeModel(name=c.name, physical_cpus=c.physical_cpus) for c in s.core_types],
            cache_line_bytes=s.cache_line_bytes,
            memory_bytes=s.memory_bytes,
        ),
        engine_build=EngineBuildModel(
            compiler_id=b.compiler_id,
            compiler_version=b.compiler_version,
            cxx_standard=b.cxx_standard,
            build_type=b.build_type,
            cmake_cxx_flags=b.cmake_cxx_flags,
            assertions_enabled=b.assertions_enabled,
        ),
    )
