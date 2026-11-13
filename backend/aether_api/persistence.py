"""Benchmark persistence: validated engine reports in, saved experiments out.

Write path. ``save_succeeded`` stores one engine run (environment, run,
experiment and every timing sample) in ONE transaction. The schema's deferred
constraint trigger checks the sample count at COMMIT, after all rows exist, so
an experiment is either saved completely or not at all; any failure rolls back
everything, including a newly inserted environment row.

Only reports that ``aether_bench`` has already validated (schema, types,
statistics recomputed from the samples, match against the request) reach
``save_succeeded``. Engine failures are stored by ``save_failed`` as
``failed``/``timed_out`` runs without experiments, so they can never be read
back as measurements. Values are copied as reported: samples and statistics
are IEEE doubles stored in ``double precision`` columns, unchanged.

Read path. Only experiments of ``succeeded`` runs are returned, rebuilt into
the same response model POST /benchmarks/run returns, so a saved experiment
can be compared with the original response field for field.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, contains_eager, joinedload, selectinload

from aether_bench import BuildInfo, ProfileRun, SystemInfo

from .database import Database
from .db_models import BenchmarkRun, Environment, Experiment, RunStatus, TimingSample
from .models import (
    BenchmarkHistoryItem,
    BenchmarkResultModel,
    BenchmarkRunRequest,
    BenchmarkRunResponse,
    CoreTypeModel,
    EngineBuildModel,
    HostModel,
    RunConfigModel,
    StatisticsModel,
)

# The engine's timestamp format (aether_bench validates it on input).
_UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
# Failure messages are server-side diagnostics; bound them so a runaway
# stderr line cannot bloat the table.
_MAX_ERROR_MESSAGE = 2000


@dataclass(frozen=True)
class HistoryPage:
    items: list[BenchmarkHistoryItem]
    total: int


def parse_utc(value: str) -> datetime:
    return datetime.strptime(value, _UTC_FORMAT).replace(tzinfo=timezone.utc)


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime(_UTC_FORMAT)


def response_wall_time(wall_time_s: float) -> float:
    """The API reports wall time to the microsecond; the database keeps the exact value."""
    return round(wall_time_s, 6)


class BenchmarkStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    # ---------- write ----------

    def save_succeeded(self, request: BenchmarkRunRequest, run: ProfileRun,
                       started_at: datetime, finished_at: datetime) -> uuid.UUID:
        """Stores a validated single-record run and returns its experiment id.

        Raises SQLAlchemyError if the database is unreachable or rejects the
        data; nothing is stored in that case.
        """
        report = run.report
        # run_benchmark guarantees one record; refuse anything else rather
        # than silently storing part of a report.
        if len(report.records) != 1:
            raise ValueError(f"expected exactly 1 record, got {len(report.records)}")
        record = report.records[0]
        config, stats = record.config, record.statistics

        experiment = Experiment(
            id=uuid.UUID(record.experiment_id),
            workload=record.workload,
            input_size=record.input_size,
            thread_count=record.thread_count,
            measured_at=parse_utc(record.timestamp_utc),
            warmup_runs=config.warmup_runs,
            measured_runs=config.measured_runs,
            min_warmup_ms=config.min_warmup_ms,
            min_sample_ms=config.min_sample_ms,
            warmup_runs_executed=record.warmup_runs_executed,
            iterations_per_sample=record.iterations_per_sample,
            min_ms=stats.min, max_ms=stats.max, mean_ms=stats.mean,
            median_ms=stats.median, stddev_ms=stats.stddev, p95_ms=stats.p95,
            samples=[TimingSample(sample_index=index, duration_ms=value)
                     for index, value in enumerate(record.samples_ms)],
        )
        with self._database.session() as session:
            session.add(BenchmarkRun(
                status=RunStatus.SUCCEEDED,
                request=request.model_dump(),
                started_at=started_at,
                finished_at=finished_at,
                wall_time_s=run.wall_time_s,
                engine_run_id=uuid.UUID(report.run_id),
                report_schema=report.schema,
                report_generated_at=parse_utc(report.generated_at_utc),
                time_unit=report.time_unit,
                environment_id=self._environment_id(session, report.system, report.build),
                experiments=[experiment],
            ))
        # Leaving the block commits; the deferred sample-count trigger runs here.
        return experiment.id

    def save_failed(self, request: BenchmarkRunRequest, status: RunStatus, error_code: str,
                    error_message: str, started_at: datetime, finished_at: datetime) -> None:
        """Records an engine run that produced no usable report."""
        if status not in (RunStatus.FAILED, RunStatus.TIMED_OUT):
            raise ValueError(f"not a failure status: {status}")
        with self._database.session() as session:
            session.add(BenchmarkRun(
                status=status, request=request.model_dump(), started_at=started_at,
                finished_at=finished_at, error_code=error_code,
                error_message=error_message[:_MAX_ERROR_MESSAGE]))

    @staticmethod
    def _environment_id(session: Session, system: SystemInfo, build: BuildInfo) -> int:
        values = _environment_values(system, build)
        # Insert-or-reuse against the NULLS NOT DISTINCT identity constraint:
        # safe under concurrent writers, and rolled back with the rest of the
        # transaction if a later insert fails.
        inserted = session.scalar(
            insert(Environment).values(**values)
            .on_conflict_do_nothing(constraint="uq_environments_identity")
            .returning(Environment.id))
        if inserted is not None:
            return inserted
        return session.scalars(select(Environment.id).where(
            *(getattr(Environment, column).is_not_distinct_from(value)
              for column, value in values.items()))).one()

    # ---------- read ----------

    def get(self, experiment_id: uuid.UUID) -> BenchmarkRunResponse | None:
        with self._database.session() as session:
            experiment = session.scalars(
                select(Experiment)
                .join(Experiment.run)
                .where(Experiment.id == experiment_id,
                       BenchmarkRun.status == RunStatus.SUCCEEDED)
                .options(contains_eager(Experiment.run).joinedload(BenchmarkRun.environment),
                         selectinload(Experiment.samples))
            ).one_or_none()
            return None if experiment is None else _to_response(experiment)

    def history(self, limit: int, offset: int) -> HistoryPage:
        """Saved experiments, newest run first. Samples are not loaded."""
        succeeded = (select(Experiment).join(Experiment.run)
                     .where(BenchmarkRun.status == RunStatus.SUCCEEDED))
        with self._database.session() as session:
            total = session.scalar(select(func.count()).select_from(succeeded.subquery()))
            experiments = session.scalars(
                succeeded.options(contains_eager(Experiment.run))
                # (status, started_at) is indexed; the id breaks ties deterministically.
                .order_by(BenchmarkRun.started_at.desc(), Experiment.id.desc())
                .limit(limit).offset(offset)
            ).all()
            return HistoryPage(items=[_to_history_item(e) for e in experiments], total=total or 0)


# ---------- mapping ----------


def _environment_values(system: SystemInfo, build: BuildInfo) -> dict[str, Any]:
    return dict(
        os_family=system.os_family, os_version=system.os_version, os_build=system.os_build,
        kernel_release=system.kernel_release, architecture=system.architecture,
        process_translated=system.process_translated, cpu_model=system.cpu_model,
        logical_cpus=system.logical_cpus, physical_cpus=system.physical_cpus,
        core_types=[{"name": c.name, "physical_cpus": c.physical_cpus} for c in system.core_types],
        cache_line_bytes=system.cache_line_bytes, memory_bytes=system.memory_bytes,
        compiler_id=build.compiler_id, compiler_version=build.compiler_version,
        cxx_standard=build.cxx_standard, build_type=build.build_type,
        cmake_cxx_flags=build.cmake_cxx_flags, assertions_enabled=build.assertions_enabled,
    )


def _statistics(experiment: Experiment) -> StatisticsModel:
    # count == measured_runs: the sample-count trigger guarantees it for every stored row.
    return StatisticsModel(count=experiment.measured_runs, min=experiment.min_ms,
                           max=experiment.max_ms, mean=experiment.mean_ms,
                           median=experiment.median_ms, stddev=experiment.stddev_ms,
                           p95=experiment.p95_ms)


def _to_response(experiment: Experiment) -> BenchmarkRunResponse:
    run = experiment.run
    environment = run.environment
    # A succeeded run always has these (ck_benchmark_runs_succeeded_has_report).
    assert environment is not None and run.wall_time_s is not None
    assert run.engine_run_id is not None and run.report_generated_at is not None
    return BenchmarkRunResponse(
        status="succeeded",
        run_id=str(run.engine_run_id),
        generated_at_utc=format_utc(run.report_generated_at),
        time_unit="ms",
        wall_time_s=response_wall_time(run.wall_time_s),
        # Validated when it was submitted. Not re-validated: host-dependent
        # limits (threads <= this machine's CPUs) must not reject saved history.
        request=BenchmarkRunRequest.model_construct(**run.request),
        result=BenchmarkResultModel(
            experiment_id=str(experiment.id),
            workload=experiment.workload,
            input_size=experiment.input_size,
            thread_count=experiment.thread_count,
            timestamp_utc=format_utc(experiment.measured_at),
            config=RunConfigModel(warmup_runs=experiment.warmup_runs,
                                  measured_runs=experiment.measured_runs,
                                  min_warmup_ms=experiment.min_warmup_ms,
                                  min_sample_ms=experiment.min_sample_ms),
            warmup_runs_executed=experiment.warmup_runs_executed,
            iterations_per_sample=experiment.iterations_per_sample,
            statistics=_statistics(experiment),
            samples_ms=[sample.duration_ms for sample in experiment.samples],
        ),
        host=HostModel(
            os_family=environment.os_family, os_version=environment.os_version,
            os_build=environment.os_build, kernel_release=environment.kernel_release,
            architecture=environment.architecture,
            process_translated=environment.process_translated,
            cpu_model=environment.cpu_model, logical_cpus=environment.logical_cpus,
            physical_cpus=environment.physical_cpus,
            core_types=[CoreTypeModel(**core) for core in environment.core_types],
            cache_line_bytes=environment.cache_line_bytes,
            memory_bytes=environment.memory_bytes,
        ),
        engine_build=EngineBuildModel(
            compiler_id=environment.compiler_id, compiler_version=environment.compiler_version,
            cxx_standard=environment.cxx_standard, build_type=environment.build_type,
            cmake_cxx_flags=environment.cmake_cxx_flags,
            assertions_enabled=environment.assertions_enabled,
        ),
    )


def _to_history_item(experiment: Experiment) -> BenchmarkHistoryItem:
    return BenchmarkHistoryItem(
        experiment_id=str(experiment.id),
        run_id=str(experiment.run.engine_run_id),
        workload=experiment.workload,
        input_size=experiment.input_size,
        thread_count=experiment.thread_count,
        timestamp_utc=format_utc(experiment.measured_at),
        measured_runs=experiment.measured_runs,
        statistics=_statistics(experiment),
    )
