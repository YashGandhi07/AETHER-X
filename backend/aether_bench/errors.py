"""Exception hierarchy for running the C++ benchmark engine.

Every failure mode raises a distinct subclass of BenchmarkError, so callers
can tell "the benchmark could not run" from "it ran and failed" from "it
claimed success but produced unusable output". A successful run never raises.
"""

from __future__ import annotations

import signal


class BenchmarkError(Exception):
    """Base class for all benchmark integration failures."""


class ExecutableNotFoundError(BenchmarkError):
    """The expected C++ executable is missing or not executable (not built yet?)."""


class BenchmarkTimeoutError(BenchmarkError):
    """The process exceeded its time limit and was killed."""

    def __init__(self, timeout_s: float, stdout: str, stderr: str) -> None:
        super().__init__(f"benchmark exceeded the {timeout_s:g} s timeout and was killed")
        self.timeout_s = timeout_s
        self.stdout = stdout
        self.stderr = stderr


class BenchmarkProcessError(BenchmarkError):
    """The process ran but exited unsuccessfully (non-zero code or a signal)."""

    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        super().__init__(_describe_exit(returncode, stderr))
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class BenchmarkOutputError(BenchmarkError):
    """The process exited 0 but its JSON report is missing, malformed or invalid."""


def _describe_exit(returncode: int, stderr: str) -> str:
    if returncode < 0:  # POSIX: terminated by signal -returncode
        try:
            reason = f"killed by {signal.Signals(-returncode).name}"
        except ValueError:
            reason = f"killed by signal {-returncode}"
    else:
        reason = f"exited with code {returncode}"
    last_line = next((line for line in reversed(stderr.splitlines()) if line.strip()), "")
    return f"benchmark {reason}" + (f": {last_line}" if last_line else "")
