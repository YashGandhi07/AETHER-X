"""Benchmark execution service: one engine run at a time, real results only.

Concurrency policy: a second request while a run is in progress is rejected
immediately (BenchmarkBusyError -> HTTP 429) rather than queued. Queuing would
tie up server threads for minutes, and two benchmarks running at once would
compete for cores and memory bandwidth and corrupt each other's timings.
"""

from __future__ import annotations

import threading
from typing import Callable

from aether_bench import BenchmarkSpec, ProfileRun, run_benchmark

from .models import (
    BenchmarkResultModel,
    BenchmarkRunRequest,
    BenchmarkRunResponse,
    RunConfigModel,
    StatisticsModel,
)
from .system_info import engine_build_model, host_model


class BenchmarkBusyError(Exception):
    """Another benchmark is already running."""


class BenchmarkService:
    def __init__(self, run: Callable[[BenchmarkSpec], ProfileRun]) -> None:
        self._run = run
        self._lock = threading.Lock()

    @classmethod
    def from_engine(cls, timeout_s: float) -> BenchmarkService:
        return cls(lambda spec: run_benchmark(spec, timeout_s=timeout_s))

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def run(self, request: BenchmarkRunRequest) -> BenchmarkRunResponse:
        """Runs the requested workload. Raises BenchmarkBusyError, or the
        aether_bench.BenchmarkError subclasses on engine failure."""
        if not self._lock.acquire(blocking=False):
            raise BenchmarkBusyError()
        try:
            run = self._run(BenchmarkSpec(
                workload=request.workload, size=request.size, threads=request.threads,
                warmup_runs=request.warmup_runs, measured_runs=request.measured_runs))
        finally:
            self._lock.release()
        return to_response(request, run)


def to_response(request: BenchmarkRunRequest, run: ProfileRun) -> BenchmarkRunResponse:
    report = run.report
    record = report.records[0]  # aether_bench verified exactly one matching record
    s, c = record.statistics, record.config
    return BenchmarkRunResponse(
        status="succeeded",
        run_id=report.run_id,
        generated_at_utc=report.generated_at_utc,
        time_unit="ms",
        wall_time_s=round(run.wall_time_s, 6),
        request=request,
        result=BenchmarkResultModel(
            experiment_id=record.experiment_id,
            workload=record.workload,
            input_size=record.input_size,
            thread_count=record.thread_count,
            timestamp_utc=record.timestamp_utc,
            config=RunConfigModel(warmup_runs=c.warmup_runs, measured_runs=c.measured_runs,
                                  min_warmup_ms=c.min_warmup_ms, min_sample_ms=c.min_sample_ms),
            warmup_runs_executed=record.warmup_runs_executed,
            iterations_per_sample=record.iterations_per_sample,
            statistics=StatisticsModel(count=s.count, min=s.min, max=s.max, mean=s.mean,
                                       median=s.median, stddev=s.stddev, p95=s.p95),
            samples_ms=list(record.samples_ms),
        ),
        host=host_model(report.system),
        engine_build=engine_build_model(report.build),
    )
