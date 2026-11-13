"""Typed response models: the public contract of the HTTP API.

Every field is declared explicitly, so only intended metadata can ever be
serialized; anything else the engine reports is dropped, not forwarded.
"""

from __future__ import annotations

import os
from typing import Literal, Optional, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aether_bench import MATRIX_WORKLOADS, WORKLOADS


class _Response(BaseModel):
    model_config = ConfigDict(frozen=True)


class HealthResponse(_Response):
    status: Literal["ok", "degraded"] = Field(
        description="'degraded' when the C++ engine tools are not built on this server")
    service: str = Field(examples=["aether-x-api"])
    version: str = Field(examples=["0.1.0"])
    engine_available: bool = Field(description="whether aether_system_info is built and executable")


class CoreTypeModel(_Response):
    name: str = Field(examples=["Super", "Efficiency"])
    physical_cpus: Optional[int]


class HostModel(_Response):
    """Hardware and OS facts. null means the platform could not report the value."""

    os_family: str
    os_version: Optional[str]
    os_build: Optional[str]
    kernel_release: Optional[str]
    architecture: str
    process_translated: Optional[bool] = Field(description="running under Rosetta 2 translation")
    cpu_model: Optional[str]
    logical_cpus: Optional[int]
    physical_cpus: Optional[int]
    core_types: list[CoreTypeModel]
    cache_line_bytes: Optional[int]
    memory_bytes: Optional[int]


class EngineBuildModel(_Response):
    """How the C++ engine binary was compiled."""

    compiler_id: str
    compiler_version: str
    cxx_standard: str
    build_type: str
    cmake_cxx_flags: Optional[str]
    assertions_enabled: bool


class SystemResponse(_Response):
    collected_at_utc: str = Field(description="when the engine collected this metadata (ISO 8601 UTC)")
    host: HostModel
    engine_build: EngineBuildModel


# ---------- benchmark execution ----------

WorkloadName = Literal["vector_add", "vector_add_parallel", "row_sums_row_major", "row_sums_column_major"]
# The API enum and the engine allowlist must never drift apart. A real check,
# not an assert, so it also runs under `python -O`.
if set(get_args(WorkloadName)) != set(WORKLOADS):
    raise RuntimeError("WorkloadName is out of sync with aether_bench.WORKLOADS")

# Conservative API limits (stricter than the C++ engine's hard limits) that
# bound one run to roughly 200 MB of memory and a few seconds of CPU time.
MAX_VECTOR_ELEMENTS = 1 << 24      # 3 float arrays = 192 MiB
MAX_MATRIX_DIMENSION = 4096        # 4096 x 4096 floats = 64 MiB
MAX_WARMUP_RUNS = 20
MAX_MEASURED_RUNS = 100


def hardware_threads() -> int:
    return os.cpu_count() or 1


class BenchmarkRunRequest(BaseModel):
    """Which predefined workload to run, and how. Unknown fields are rejected,
    and strict mode refuses coercion (\"100\", 100.0 and true are not integers)."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    workload: WorkloadName
    size: int = Field(ge=1, description=(
        f"vector workloads: element count (<= {MAX_VECTOR_ELEMENTS}); "
        f"row_sums_*: matrix dimension n of an n x n matrix (<= {MAX_MATRIX_DIMENSION})"))
    threads: int = Field(default=1, ge=1, description=(
        "vector_add_parallel: 1..logical CPUs of this host; every other workload: exactly 1"))
    warmup_runs: int = Field(default=3, ge=0, le=MAX_WARMUP_RUNS, description=(
        "minimum warm-up batches (warm-up also lasts at least 100 ms)"))
    measured_runs: int = Field(default=10, ge=1, le=MAX_MEASURED_RUNS)

    @model_validator(mode="after")
    def _check_workload_limits(self) -> BenchmarkRunRequest:
        if self.workload in MATRIX_WORKLOADS:
            if self.size > MAX_MATRIX_DIMENSION:
                raise ValueError(f"size (matrix dimension) must be <= {MAX_MATRIX_DIMENSION} for {self.workload}")
        elif self.size > MAX_VECTOR_ELEMENTS:
            raise ValueError(f"size must be <= {MAX_VECTOR_ELEMENTS} for {self.workload}")
        if self.workload == "vector_add_parallel":
            # More threads than CPUs only adds contention (oversubscription).
            if self.threads > (limit := hardware_threads()):
                raise ValueError(f"threads must be <= {limit} (logical CPUs on this host)")
        elif self.threads != 1:
            raise ValueError(f"{self.workload} is single-threaded; threads must be 1")
        return self


class StatisticsModel(_Response):
    count: int
    min: float
    max: float
    mean: float
    median: float
    stddev: Optional[float] = Field(description="sample standard deviation; null for a single sample")
    p95: float = Field(description="95th percentile, linear interpolation (type 7)")


class RunConfigModel(_Response):
    warmup_runs: int
    measured_runs: int
    min_warmup_ms: float
    min_sample_ms: float


class BenchmarkResultModel(_Response):
    experiment_id: str
    workload: str
    input_size: int = Field(description="elements processed per call (n*n for row_sums_*)")
    thread_count: int = Field(description="threads actually used")
    timestamp_utc: str
    config: RunConfigModel
    warmup_runs_executed: int
    iterations_per_sample: int
    statistics: StatisticsModel
    samples_ms: list[float] = Field(description="per-call time of every measured sample, in run order")


class BenchmarkRunResponse(_Response):
    status: Literal["succeeded"]
    run_id: str
    generated_at_utc: str
    time_unit: Literal["ms"]
    wall_time_s: float = Field(description="end-to-end engine process time, including warm-up")
    request: BenchmarkRunRequest
    result: BenchmarkResultModel
    host: HostModel
    engine_build: EngineBuildModel


class BenchmarkHistoryItem(_Response):
    """A saved experiment without its samples (GET /benchmarks/{experiment_id} has them)."""

    experiment_id: str
    run_id: str
    workload: str
    input_size: int
    thread_count: int
    timestamp_utc: str
    measured_runs: int
    statistics: StatisticsModel


MAX_HISTORY_PAGE_SIZE = 100
MAX_HISTORY_OFFSET = 100_000


class BenchmarkHistoryResponse(_Response):
    items: list[BenchmarkHistoryItem] = Field(description="newest run first")
    total: int = Field(description="saved experiments in total, across all pages")
    limit: int
    offset: int
    time_unit: Literal["ms"]


class ErrorBody(_Response):
    code: str = Field(examples=["engine_unavailable"])
    message: str
    request_id: Optional[str] = None
    details: Optional[list[dict[str, object]]] = None


class ErrorResponse(_Response):
    error: ErrorBody
