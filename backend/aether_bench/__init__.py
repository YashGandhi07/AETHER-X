"""Python interface to the AETHER X C++ benchmark engine."""

from .errors import (
    BenchmarkError,
    BenchmarkOutputError,
    BenchmarkProcessError,
    BenchmarkTimeoutError,
    ExecutableNotFoundError,
)
from .runner import DEFAULT_TIMEOUT_S, KNOWN_EXECUTABLES, ProfileRun, resolve_executable, run_cpu_profile
from .schema import SCHEMA_ID, BenchmarkRecord, ProfileReport, parse_report

__all__ = [
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
