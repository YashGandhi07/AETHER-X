"""Runs compiled C++ engine tools and returns their validated reports.

Security model:
  * The executable is never taken from a caller. Only names in
    ``KNOWN_EXECUTABLES`` can run, resolved inside one build directory.
    The build directory is deployment configuration (it defaults to the
    repository's ``cpp/build``), not request input.
  * Commands are argument lists run without a shell, so no value can be
    interpreted as shell syntax. No caller-supplied value is ever passed.
  * The child gets a minimal environment, no stdin, and a hard timeout.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .errors import (
    BenchmarkOutputError,
    BenchmarkProcessError,
    BenchmarkTimeoutError,
    ExecutableNotFoundError,
)
from .schema import ProfileReport, SystemReport, parse_report, parse_system_report

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BUILD_DIR = REPOSITORY_ROOT / "cpp" / "build"

# Logical name -> file name inside the build directory (see cpp/CMakeLists.txt).
KNOWN_EXECUTABLES: dict[str, str] = {
    "cpu_profile": "aether_cpu_profile",
    "system_info": "aether_system_info",
    "run_benchmark": "aether_run_benchmark",
}

# Workload names accepted by aether_run_benchmark (cpp/src/workloads.cpp).
# Nothing outside this tuple is ever passed to the engine.
WORKLOADS: tuple[str, ...] = (
    "vector_add",
    "vector_add_parallel",
    "row_sums_row_major",
    "row_sums_column_major",
)
MATRIX_WORKLOADS = frozenset({"row_sums_row_major", "row_sums_column_major"})

# A full profile takes ~3 s on an Apple M5 (Section 2.6); the default leaves a
# wide margin for slower machines without letting a hung process block forever.
DEFAULT_TIMEOUT_S = 120.0
# aether_system_info only queries the OS (~0.15 s measured in Section 3.1).
DEFAULT_SYSTEM_INFO_TIMEOUT_S = 10.0
# Guards against reading an unexpectedly huge file into memory. A 9-record
# report is ~11 KB, so this is generous.
MAX_REPORT_BYTES = 16 * 1024 * 1024

# Only what the C++ programs need. Not inheriting the parent's environment
# keeps variables such as DYLD_INSERT_LIBRARIES or LD_PRELOAD from injecting
# code into the engine (or skewing its timings).
_CHILD_ENVIRONMENT = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}


@dataclass(frozen=True)
class ProfileRun:
    """A successful run: the validated report plus the raw process output."""

    report: ProfileReport
    stdout: str
    stderr: str
    wall_time_s: float


@dataclass(frozen=True)
class BenchmarkSpec:
    """One workload run. Types and the workload name are checked here; numeric
    limits are policy (enforced by callers such as the API) and are checked
    again by the C++ engine's own hard limits."""

    workload: str
    size: int  # elements, or matrix dimension n for row_sums_* (n x n)
    threads: int = 1
    warmup_runs: int = 3
    measured_runs: int = 10

    def __post_init__(self) -> None:
        if self.workload not in WORKLOADS:
            raise ValueError(f"unknown workload {self.workload!r}; known: {list(WORKLOADS)}")
        for name in ("size", "threads", "warmup_runs", "measured_runs"):
            value = getattr(self, name)
            # bool is an int subclass; True must not become "--size 1".
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer, got {value!r}")

    def to_arguments(self, report_path: Path) -> list[str]:
        """The exact argv tail for aether_run_benchmark: fixed flags, int values."""
        return ["--workload", self.workload, "--size", str(self.size),
                "--threads", str(self.threads), "--warmup-runs", str(self.warmup_runs),
                "--measured-runs", str(self.measured_runs), "--output", str(report_path)]


def resolve_executable(name: str, build_dir: Optional[Path] = None) -> Path:
    """Returns the absolute path of a known executable, or raises.

    Raises ValueError for names not in KNOWN_EXECUTABLES and
    ExecutableNotFoundError if the file is missing or not executable.
    """
    if name not in KNOWN_EXECUTABLES:
        raise ValueError(f"unknown executable {name!r}; known: {sorted(KNOWN_EXECUTABLES)}")
    directory = (build_dir or DEFAULT_BUILD_DIR).resolve()
    path = directory / KNOWN_EXECUTABLES[name]
    try:
        info = path.stat()
    except FileNotFoundError:
        raise ExecutableNotFoundError(
            f"{path} not found; build it first: cmake -S cpp -B cpp/build && cmake --build cpp/build"
        ) from None
    if not stat.S_ISREG(info.st_mode) or not os.access(path, os.X_OK):
        raise ExecutableNotFoundError(f"{path} is not an executable file")
    return path


def run_cpu_profile(
    *, timeout_s: float = DEFAULT_TIMEOUT_S, build_dir: Optional[Path] = None
) -> ProfileRun:
    """Runs the C++ CPU profiler and returns its validated report.

    Raises:
      ValueError: timeout_s is not a positive finite number.
      ExecutableNotFoundError: the profiler has not been built.
      BenchmarkTimeoutError: it ran longer than timeout_s (it is killed).
      BenchmarkProcessError: it exited non-zero or was killed by a signal,
        e.g. a kernel failed verification (the C++ side then refuses to
        write a report).
      BenchmarkOutputError: it exited 0 but the report is missing, too
        large, malformed, or fails validation.
    """
    _validate_timeout(timeout_s)
    executable = resolve_executable("cpu_profile", build_dir)

    with tempfile.TemporaryDirectory(prefix="aether_profile_") as workdir:
        report_path = Path(workdir) / "report.json"
        completed, wall_time_s = _execute([str(executable), str(report_path)], timeout_s, workdir)
        report = parse_report(_read_report(report_path))

    return ProfileRun(
        report=report, stdout=completed.stdout, stderr=completed.stderr, wall_time_s=wall_time_s
    )


def run_benchmark(
    spec: BenchmarkSpec, *, timeout_s: float = DEFAULT_TIMEOUT_S, build_dir: Optional[Path] = None
) -> ProfileRun:
    """Runs one workload through ``aether_run_benchmark`` and returns its report.

    Raises the same exceptions as run_cpu_profile. In addition, a report whose
    single record does not match `spec` (wrong workload, size, run counts, or
    more threads than requested) is rejected with BenchmarkOutputError.
    """
    _validate_timeout(timeout_s)
    executable = resolve_executable("run_benchmark", build_dir)
    with tempfile.TemporaryDirectory(prefix="aether_benchmark_") as workdir:
        report_path = Path(workdir) / "report.json"
        completed, wall_time_s = _execute(
            [str(executable), *spec.to_arguments(report_path)], timeout_s, workdir)
        report = parse_report(_read_report(report_path))
    _check_matches_spec(report, spec)
    return ProfileRun(
        report=report, stdout=completed.stdout, stderr=completed.stderr, wall_time_s=wall_time_s
    )


def _check_matches_spec(report: ProfileReport, spec: BenchmarkSpec) -> None:
    if len(report.records) != 1:
        raise BenchmarkOutputError(f"expected exactly 1 record, got {len(report.records)}")
    record = report.records[0]
    expected_input = spec.size * spec.size if spec.workload in MATRIX_WORKLOADS else spec.size
    mismatches = [
        name for name, ok in [
            ("workload", record.workload == spec.workload),
            ("input_size", record.input_size == expected_input),
            ("thread_count", 1 <= record.thread_count <= spec.threads),  # may be capped
            ("warmup_runs", record.config.warmup_runs == spec.warmup_runs),
            ("measured_runs", record.config.measured_runs == spec.measured_runs),
        ] if not ok
    ]
    if mismatches:
        raise BenchmarkOutputError(f"report does not match the request: {', '.join(mismatches)}")


def run_system_info(
    *, timeout_s: float = DEFAULT_SYSTEM_INFO_TIMEOUT_S, build_dir: Optional[Path] = None
) -> SystemReport:
    """Runs ``aether_system_info`` (no benchmarks) and returns host/build metadata.

    Raises the same exceptions as run_cpu_profile; the JSON comes from stdout.
    """
    _validate_timeout(timeout_s)
    executable = resolve_executable("system_info", build_dir)
    with tempfile.TemporaryDirectory(prefix="aether_sysinfo_") as workdir:
        completed, _ = _execute([str(executable)], timeout_s, workdir)
    if len(completed.stdout.encode("utf-8", errors="replace")) > MAX_REPORT_BYTES:
        raise BenchmarkOutputError(f"system info output exceeds {MAX_REPORT_BYTES} bytes")
    return parse_system_report(completed.stdout)


def _validate_timeout(timeout_s: float) -> None:
    if not (isinstance(timeout_s, (int, float)) and 0 < timeout_s < float("inf")):
        raise ValueError(f"timeout_s must be a positive finite number, got {timeout_s!r}")


def _execute(
    command: list[str], timeout_s: float, workdir: str
) -> tuple[subprocess.CompletedProcess[str], float]:
    """Runs `command` with the hardened settings; raises on timeout or failure."""
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",  # never fail on stray bytes in diagnostics
            timeout=timeout_s,
            cwd=workdir,
            env=_CHILD_ENVIRONMENT,
            check=False,
        )
    except subprocess.TimeoutExpired as expired:
        # subprocess.run has already killed and reaped the child.
        raise BenchmarkTimeoutError(
            timeout_s, _as_text(expired.stdout), _as_text(expired.stderr)
        ) from None
    wall_time_s = time.monotonic() - started
    if completed.returncode != 0:
        raise BenchmarkProcessError(completed.returncode, completed.stdout, completed.stderr)
    return completed, wall_time_s


def _read_report(path: Path) -> str:
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        raise BenchmarkOutputError("benchmark exited successfully but wrote no report") from None
    if size > MAX_REPORT_BYTES:
        raise BenchmarkOutputError(f"report is {size} bytes, above the {MAX_REPORT_BYTES} limit")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise BenchmarkOutputError(f"report is not valid UTF-8: {error}") from error


def _as_text(output: object) -> str:
    # TimeoutExpired carries bytes (or None) even when text=True was requested.
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return str(output)
