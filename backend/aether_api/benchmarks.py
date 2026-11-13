"""Benchmark execution service: one engine run at a time, real results only.

Concurrency policy: a second request while a run is in progress is rejected
immediately (BenchmarkBusyError -> HTTP 429) rather than queued. Queuing would
tie up server threads for minutes, and two benchmarks running at once would
compete for cores and memory bandwidth and corrupt each other's timings.

Persistence: every engine run is recorded. A validated report is saved
completely (one transaction) before the response is returned, so a 200
response means the measurement is in the database. An engine failure is
recorded as a failed or timed-out run, never as a measurement.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.exc import SQLAlchemyError

from aether_bench import (
    BenchmarkError,
    BenchmarkSpec,
    BenchmarkTimeoutError,
    ExecutableNotFoundError,
    ProfileRun,
    run_benchmark,
)

from .db_models import RunStatus
from .errors import BenchmarkBusyError, engine_error_info
from .models import (
    BenchmarkResultModel,
    BenchmarkRunRequest,
    BenchmarkRunResponse,
    RunConfigModel,
    StatisticsModel,
)
from .persistence import BenchmarkStore, response_wall_time
from .system_info import engine_build_model, host_model

__all__ = ["BenchmarkBusyError", "BenchmarkService", "to_response"]

logger = logging.getLogger("aether.api.benchmarks")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BenchmarkService:
    def __init__(self, run: Callable[[BenchmarkSpec], ProfileRun], store: BenchmarkStore) -> None:
        self._run = run
        self._store = store
        self._lock = threading.Lock()

    @classmethod
    def from_engine(cls, timeout_s: float, store: BenchmarkStore) -> BenchmarkService:
        return cls(lambda spec: run_benchmark(spec, timeout_s=timeout_s), store)

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def run(self, request: BenchmarkRunRequest) -> BenchmarkRunResponse:
        """Runs the requested workload and saves the result.

        Raises BenchmarkBusyError, the aether_bench.BenchmarkError subclasses
        on engine failure, or SQLAlchemyError if the result cannot be saved.
        """
        if not self._lock.acquire(blocking=False):
            raise BenchmarkBusyError()
        # Saving inside the lock keeps runs and their rows strictly ordered;
        # it adds milliseconds to a run that takes seconds.
        try:
            started_at = _utc_now()
            try:
                run = self._run(BenchmarkSpec(
                    workload=request.workload, size=request.size, threads=request.threads,
                    warmup_runs=request.warmup_runs, measured_runs=request.measured_runs))
            except BenchmarkError as error:
                self._record_failure(request, error, started_at)
                raise
            finished_at = _utc_now()
            try:
                self._store.save_succeeded(request, run, started_at, finished_at)
            except SQLAlchemyError:
                # The measurement is discarded with the rolled-back transaction;
                # the client gets a database error, not an unsaved result.
                logger.error("benchmark result not saved", extra={"fields": {
                    "engine_run_id": run.report.run_id}})
                raise
        finally:
            self._lock.release()
        return to_response(request, run)

    def _record_failure(self, request: BenchmarkRunRequest, error: BenchmarkError,
                        started_at: datetime) -> None:
        if isinstance(error, ExecutableNotFoundError):
            return  # nothing was executed, so there is no run to record
        status = RunStatus.TIMED_OUT if isinstance(error, BenchmarkTimeoutError) else RunStatus.FAILED
        _, code, _ = engine_error_info(error)
        try:
            # Server-side detail (may include engine stderr); the API never returns it.
            self._store.save_failed(request, status, code, str(error), started_at, _utc_now())
        except SQLAlchemyError as db_error:
            # The engine failure is what the client must see; losing its
            # record is logged rather than allowed to mask it.
            logger.warning("failed benchmark run not recorded", extra={"fields": {
                "error_code": code, "database_error": type(db_error).__name__}})


def to_response(request: BenchmarkRunRequest, run: ProfileRun) -> BenchmarkRunResponse:
    report = run.report
    record = report.records[0]  # aether_bench verified exactly one matching record
    s, c = record.statistics, record.config
    return BenchmarkRunResponse(
        status="succeeded",
        run_id=report.run_id,
        generated_at_utc=report.generated_at_utc,
        time_unit="ms",
        wall_time_s=response_wall_time(run.wall_time_s),
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
