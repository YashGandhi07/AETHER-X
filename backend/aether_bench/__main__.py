"""Command line: run the C++ profiler and print a summary of the real results.

    PYTHONPATH=backend python3 -m aether_bench [--timeout SECONDS] [--json]

Exit codes: 0 success, 1 benchmark failed or produced invalid output,
2 profiler not built / bad arguments, 3 timeout.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys

from .errors import (
    BenchmarkError,
    BenchmarkProcessError,
    BenchmarkTimeoutError,
    ExecutableNotFoundError,
)
from .runner import DEFAULT_TIMEOUT_S, run_cpu_profile


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aether_bench", description=__doc__.splitlines()[0])
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                        help=f"seconds before the profiler is killed (default {DEFAULT_TIMEOUT_S:g})")
    parser.add_argument("--json", action="store_true",
                        help="print the validated report as JSON instead of a table")
    args = parser.parse_args(argv)

    try:
        run = run_cpu_profile(timeout_s=args.timeout)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except ExecutableNotFoundError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except BenchmarkTimeoutError as error:
        print(f"error: {error}", file=sys.stderr)
        return 3
    except BenchmarkProcessError as error:
        print(f"error: {error}", file=sys.stderr)
        if error.stderr.strip():
            print(error.stderr.rstrip(), file=sys.stderr)
        return 1
    except BenchmarkError as error:  # invalid or missing report
        print(f"error: {error}", file=sys.stderr)
        return 1

    report = run.report
    if args.json:
        print(json.dumps(dataclasses.asdict(report), indent=2))
        return 0

    system, build = report.system, report.build
    print(f"run {report.run_id} at {report.generated_at_utc} ({run.wall_time_s:.2f} s wall time)")
    print(f"{system.cpu_model or 'unknown CPU'}, {system.logical_cpus or '?'} logical CPUs, "
          f"{system.os_family} {system.os_version or ''} {system.architecture}; "
          f"{build.compiler_id} {build.compiler_version} {build.build_type}")
    print(f"{'workload':<22} {'elements':>10} {'thr':>3} {'median_ms':>11} {'p95_ms':>11} {'cv':>7}")
    for record in report.records:
        s = record.statistics
        cv = f"{100 * s.stddev / s.mean:6.2f}%" if s.stddev is not None and s.mean > 0 else "    n/a"
        print(f"{record.workload:<22} {record.input_size:>10} {record.thread_count:>3} "
              f"{s.median:>11.6f} {s.p95:>11.6f} {cv:>7}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
