"""Integration test: runs the REAL compiled C++ profiler and validates its report.

Skipped (with a reason) when cpp/build/aether_cpu_profile has not been built.
Set AETHER_REQUIRE_INTEGRATION=1 to turn that skip into a failure, e.g. in CI.
"""

from __future__ import annotations

import os
import platform
import unittest
from datetime import datetime, timedelta, timezone

from aether_bench import ExecutableNotFoundError, resolve_executable, run_cpu_profile


def _profiler_available() -> bool:
    try:
        resolve_executable("cpu_profile")
        return True
    except ExecutableNotFoundError:
        return False


_REQUIRED = os.environ.get("AETHER_REQUIRE_INTEGRATION") == "1"


@unittest.skipUnless(_profiler_available() or _REQUIRED,
                     "C++ profiler not built (cmake -S cpp -B cpp/build && cmake --build cpp/build)")
class RealProfilerTest(unittest.TestCase):
    def test_real_profile_run(self) -> None:
        started = datetime.now(timezone.utc).replace(microsecond=0)
        run = run_cpu_profile(timeout_s=120.0)
        report = run.report

        # Fresh output from this run, not a stale or canned file.
        generated = datetime.strptime(report.generated_at_utc, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
        self.assertGreaterEqual(generated, started)
        self.assertLess(generated - started, timedelta(seconds=120))

        # The environment describes this machine.
        self.assertEqual(report.system.logical_cpus, os.cpu_count())
        machine = platform.machine().lower()
        self.assertEqual(report.system.architecture,
                         {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine))
        if platform.system() == "Darwin":
            self.assertEqual(report.system.os_family, "macOS")
            self.assertEqual(report.system.os_version, platform.mac_ver()[0])

        # The workloads compiled into cpp/bench/cpu_profile.cpp, each with real samples.
        workloads = {record.workload for record in report.records}
        self.assertEqual(workloads, {"vector_add", "vector_add_parallel",
                                     "row_sums_row_major", "row_sums_column_major"})
        for record in report.records:
            self.assertEqual(len(record.samples_ms), record.config.measured_runs)
            self.assertTrue(all(sample > 0 for sample in record.samples_ms))

        # Physics sanity check from Section 2.2: column-major traversal of the
        # same 4096x4096 matrix is slower than row-major.
        by_name = {record.workload: record for record in report.records
                   if record.workload.startswith("row_sums")}
        self.assertGreater(by_name["row_sums_column_major"].statistics.median,
                           by_name["row_sums_row_major"].statistics.median)


if __name__ == "__main__":
    unittest.main()
