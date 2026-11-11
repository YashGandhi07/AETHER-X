"""TEST FIXTURES ONLY: synthetic reports and fake executables.

Nothing here is a benchmark measurement. Synthetic reports use the
TEST_FIXTURE_ prefix in names and placeholder sample values (1.0, 2.0, ...)
chosen only to exercise validation. Real measurements come exclusively from
the integration test, which runs the actual C++ profiler.
"""

from __future__ import annotations

import copy
import json
import stat
import statistics
import sys
import textwrap
from pathlib import Path
from typing import Any

FIXTURE_LABEL = "TEST_FIXTURE"


def synthetic_record(experiment_id: str, samples: list[float]) -> dict[str, Any]:
    """A schema-valid record whose statistics are computed from `samples`."""
    return {
        "experiment_id": experiment_id,
        "workload": f"{FIXTURE_LABEL}_vector_add",
        "input_size": 4096,
        "thread_count": 1,
        "timestamp_utc": "2000-01-01T00:00:00Z",
        "config": {"warmup_runs": 3, "measured_runs": len(samples),
                   "min_warmup_ms": 100, "min_sample_ms": 1},
        "warmup_runs_executed": 5,
        "iterations_per_sample": 1,
        "statistics": {
            "count": len(samples),
            "min": min(samples),
            "max": max(samples),
            "mean": statistics.fmean(samples),
            "median": statistics.median(samples),
            "stddev": statistics.stdev(samples) if len(samples) > 1 else None,
            "p95": statistics.quantiles(samples, n=20, method="inclusive")[18]
            if len(samples) > 1 else samples[0],
        },
        "samples": samples,
    }


def synthetic_report() -> dict[str, Any]:
    """A complete aether.cpu_profile.v2 document with obviously fake values."""
    return {
        "schema": "aether.cpu_profile.v2",
        "run_id": "00000000-0000-4000-8000-000000000000",
        "generated_at_utc": "2000-01-01T00:00:01Z",
        "time_unit": "ms",
        "environment": {
            "system": {
                "os_family": f"{FIXTURE_LABEL}_os",
                "os_version": None,
                "os_build": None,
                "kernel_release": None,
                "architecture": f"{FIXTURE_LABEL}_arch",
                "process_translated": None,
                "cpu_model": f"{FIXTURE_LABEL} CPU (not a real measurement)",
                "logical_cpus": 2,
                "physical_cpus": 2,
                "core_types": [{"name": f"{FIXTURE_LABEL}_core", "physical_cpus": 2}],
                "cache_line_bytes": None,
                "memory_bytes": None,
            },
            "build": {
                "compiler_id": f"{FIXTURE_LABEL}_compiler",
                "compiler_version": "0.0.0",
                "cxx_standard": "C++20",
                "build_type": "Release",
                "cmake_cxx_flags": None,
                "assertions_enabled": False,
            },
        },
        "results": [
            synthetic_record("00000000-0000-4000-8000-000000000001", [1.0, 2.0, 3.0, 4.0]),
            synthetic_record("00000000-0000-4000-8000-000000000002", [5.0]),
        ],
    }


def mutated(report: dict[str, Any], path: str, value: Any = None, *, delete: bool = False) -> str:
    """JSON text of a deep copy of `report` with one field replaced or deleted.

    `path` is dot-separated; integers index lists, e.g. "results.0.statistics.p95".
    """
    document = copy.deepcopy(report)
    *parents, last = path.split(".")
    target: Any = document
    for part in parents:
        target = target[int(part)] if isinstance(target, list) else target[part]
    key: Any = int(last) if isinstance(target, list) else last
    if delete:
        del target[key]
    else:
        target[key] = value
    return json.dumps(document)


def write_fake_profiler(build_dir: Path, body: str) -> Path:
    """Installs a FAKE `aether_cpu_profile` (a Python script) in `build_dir`.

    `body` runs with `report_path` (the single CLI argument) and `json`,
    `os`, `sys`, `time` in scope.
    """
    build_dir.mkdir(parents=True, exist_ok=True)
    script = build_dir / "aether_cpu_profile"
    script.write_text(
        f"#!{sys.executable}\n"
        "# FAKE aether_cpu_profile for tests: not the real C++ benchmark.\n"
        "import json, os, sys, time\n"
        "report_path = sys.argv[1] if len(sys.argv) > 1 else None\n"
        + textwrap.dedent(body),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script
