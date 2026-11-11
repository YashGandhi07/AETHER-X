"""Python interface to the AETHER X C++ benchmark engine."""

from .errors import (
    BenchmarkError,
    BenchmarkOutputError,
    BenchmarkProcessError,
    BenchmarkTimeoutError,
    ExecutableNotFoundError,
)
from .runner import (
    DEFAULT_TIMEOUT_S,
    KNOWN_EXECUTABLES,
    ProfileRun,
    resolve_executable,
    run_cpu_profile,
    run_system_info,
)
from .schema import (
    SCHEMA_ID,
    SYSTEM_INFO_SCHEMA_ID,
    BenchmarkRecord,
    BuildInfo,
    CoreType,
    ProfileReport,
    SystemInfo,
    SystemReport,
    parse_report,
    parse_system_report,
)

__all__ = [
    "BuildInfo",
    "CoreType",
    "SYSTEM_INFO_SCHEMA_ID",
    "SystemInfo",
    "SystemReport",
    "parse_system_report",
    "run_system_info",
    "BenchmarkError",
    "BenchmarkOutputError",
    "BenchmarkProcessError",
    "BenchmarkRecord",
    "BenchmarkTimeoutError",
    "DEFAULT_TIMEOUT_S",
    "ExecutableNotFoundError",
    "KNOWN_EXECUTABLES",
    "ProfileReport",
    "ProfileRun",
    "SCHEMA_ID",
    "parse_report",
    "resolve_executable",
    "run_cpu_profile",
]
