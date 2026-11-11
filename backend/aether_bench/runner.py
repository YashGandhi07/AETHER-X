"""Runs the compiled C++ profiler (``aether_cpu_profile``) and returns its report.

Security model:
  * The executable is never taken from a caller. Only names in
    ``KNOWN_EXECUTABLES`` can run, resolved inside one build directory.
    The build directory is deployment configuration (it defaults to the
    repository's ``cpp/build``), not request input.
  * The command is an argument list run without a shell, so no value can be
    interpreted as shell syntax. The only argument is an output path inside a
    private temporary directory created here.
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
from .schema import ProfileReport, parse_report

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BUILD_DIR = REPOSITORY_ROOT / "cpp" / "build"

# Logical name -> file name inside the build directory (see cpp/CMakeLists.txt).
KNOWN_EXECUTABLES: dict[str, str] = {"cpu_profile": "aether_cpu_profile"}

# A full profile takes ~3 s on an Apple M5 (Section 2.6); the default leaves a
# wide margin for slower machines without letting a hung process block forever.
DEFAULT_TIMEOUT_S = 120.0
# Guards against reading an unexpectedly huge file into memory. A 9-record
# report is ~11 KB, so this is generous.
MAX_REPORT_BYTES = 16 * 1024 * 1024

# Only what the C++ program needs. Not inheriting the parent's environment
# keeps variables such as DYLD_INSERT_LIBRARIES or LD_PRELOAD from injecting
# code into the benchmark (or skewing its timings).
_CHILD_ENVIRONMENT = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}


@dataclass(frozen=True)
class ProfileRun:
    """A successful run: the validated report plus the raw process output."""

    report: ProfileReport
    stdout: str
    stderr: str
    wall_time_s: float


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
    if not (isinstance(timeout_s, (int, float)) and 0 < timeout_s < float("inf")):
        raise ValueError(f"timeout_s must be a positive finite number, got {timeout_s!r}")
    executable = resolve_executable("cpu_profile", build_dir)

    with tempfile.TemporaryDirectory(prefix="aether_profile_") as workdir:
        report_path = Path(workdir) / "report.json"
        command = [str(executable), str(report_path)]
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
        report = parse_report(_read_report(report_path))

    return ProfileRun(
        report=report, stdout=completed.stdout, stderr=completed.stderr, wall_time_s=wall_time_s
    )


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
