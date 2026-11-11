# AETHER X backend: Python ↔ C++ benchmark integration

`aether_bench` runs the compiled C++ profiler (`cpp/build/aether_cpu_profile`)
as a subprocess, then parses and validates its `aether.cpu_profile.v2` JSON report
into typed Python objects. It uses only the Python standard library (tested
with Python 3.14).

## Quick start (from the repository root)

```bash
# 1. Build the C++ engine (Release by default)
cmake -S cpp -B cpp/build
cmake --build cpp/build

# 2. Run the profiler through Python and print a summary of the real results
PYTHONPATH=backend python3 -m aether_bench
PYTHONPATH=backend python3 -m aether_bench --json         # full validated report
PYTHONPATH=backend python3 -m aether_bench --timeout 60   # seconds (default 120)

# 3. Run the Python tests (unit tests + one integration test with the real binary)
python3 -m unittest discover -s backend -t backend -v
```

The integration test is skipped when the C++ profiler has not been built. Set
`AETHER_REQUIRE_INTEGRATION=1` to make that a failure instead (e.g. in CI).

Command-line exit codes: `0` success, `1` benchmark failed or produced an
invalid report, `2` profiler not built or invalid arguments, `3` timeout.

## Library use

```python
from aether_bench import BenchmarkError, run_cpu_profile

try:
    run = run_cpu_profile(timeout_s=60)
except BenchmarkError as error:
    ...  # see the exception types below
else:
    for record in run.report.records:
        print(record.workload, record.thread_count, record.statistics.median)
```

| Exception | Meaning |
|---|---|
| `ExecutableNotFoundError` | `cpp/build/aether_cpu_profile` is missing or not executable |
| `BenchmarkTimeoutError` | ran longer than `timeout_s`; the process was killed |
| `BenchmarkProcessError` | non-zero exit or killed by a signal (`.returncode`, `.stdout`, `.stderr`) |
| `BenchmarkOutputError` | exited 0, but the report is missing, too large, malformed or invalid |

All four derive from `BenchmarkError`. A returned `ProfileRun` is always a
validated success.

## Design notes

* **No caller-chosen executables.** Only names in `KNOWN_EXECUTABLES` run,
  resolved inside the build directory (default `cpp/build`).
* **No shell.** Commands are argument lists with `shell=False`. The only
  argument is a report path inside a private temporary directory.
* **Isolated child process:** minimal environment (`PATH`, `LC_ALL`), no stdin,
  explicit timeout, output decoded as UTF-8 with replacement.
* **Strict validation:** required fields, types (JSON `true` is not a count),
  ranges, UUID and timestamp formats, unique ids, and every statistic is
  recomputed from the raw samples and must match. Unknown extra fields are
  ignored, so additive schema changes stay compatible.
* **Test fixtures are synthetic and labelled** `TEST_FIXTURE`. Real
  measurements only ever come from the C++ profiler.
