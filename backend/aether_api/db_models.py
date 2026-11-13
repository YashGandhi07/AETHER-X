"""SQLAlchemy models for reproducible benchmark experiments (PostgreSQL).

Mirrors the engine's ``aether.cpu_profile.v2`` report:

    environments     1 ─< benchmark_runs   host + engine build snapshot, shared by runs
    benchmark_runs   1 ─< experiments      one engine invocation; status lives here
    experiments      1 ─< timing_samples   one result record (workload, config, statistics)
    timing_samples                         every measured sample, in run order

Integrity is enforced by the database, not only by this code: CHECK
constraints reject impossible values (including NaN/Infinity, which
PostgreSQL's float8 accepts and orders above every number), and a deferred
constraint trigger (created in the initial migration) guarantees that each
experiment has exactly ``measured_runs`` samples numbered 0..n-1 at commit.

Timing values use ``double precision``: the engine measures and emits IEEE 754
doubles in shortest round-trip form, so float8 stores them bit-for-bit.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Double,
    Enum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime

# Deterministic constraint names: migrations can refer to (and drop) them reliably.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _finite(column: str) -> str:
    # NaN compares greater than every number in PostgreSQL, so range checks
    # alone do not reject it; -Infinity is excluded by the >= 0 bounds.
    return f"{column} <> 'NaN'::float8 AND {column} <> 'Infinity'::float8"


def _finite_non_negative(column: str) -> str:
    return f"{column} >= 0 AND {_finite(column)}"


class RunStatus(enum.StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class Environment(Base):
    """A distinct (host, engine build) combination. Rows are deduplicated by a
    NULLS NOT DISTINCT unique constraint over every descriptive column, so a
    metadata value that is unknown (NULL) still identifies the same machine."""

    __tablename__ = "environments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # ---- host (environment.system) ----
    os_family: Mapped[str] = mapped_column(Text)
    os_version: Mapped[Optional[str]] = mapped_column(Text)
    os_build: Mapped[Optional[str]] = mapped_column(Text)
    kernel_release: Mapped[Optional[str]] = mapped_column(Text)
    architecture: Mapped[str] = mapped_column(Text)
    process_translated: Mapped[Optional[bool]]
    cpu_model: Mapped[Optional[str]] = mapped_column(Text)
    logical_cpus: Mapped[Optional[int]] = mapped_column(Integer)
    physical_cpus: Mapped[Optional[int]] = mapped_column(Integer)
    # [{"name": "Super", "physical_cpus": 4}, ...]: a small descriptive list
    # that is never queried on its own, so it does not justify a table.
    core_types: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"))
    cache_line_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    memory_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    # ---- engine build (environment.build) ----
    compiler_id: Mapped[str] = mapped_column(Text)
    compiler_version: Mapped[str] = mapped_column(Text)
    cxx_standard: Mapped[str] = mapped_column(Text)
    build_type: Mapped[str] = mapped_column(Text)
    cmake_cxx_flags: Mapped[Optional[str]] = mapped_column(Text)
    assertions_enabled: Mapped[bool]

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    runs: Mapped[list[BenchmarkRun]] = relationship(back_populates="environment")

    __table_args__ = (
        UniqueConstraint(
            "os_family", "os_version", "os_build", "kernel_release", "architecture",
            "process_translated", "cpu_model", "logical_cpus", "physical_cpus", "core_types",
            "cache_line_bytes", "memory_bytes", "compiler_id", "compiler_version",
            "cxx_standard", "build_type", "cmake_cxx_flags", "assertions_enabled",
            name="uq_environments_identity", postgresql_nulls_not_distinct=True),
        CheckConstraint("os_family <> '' AND architecture <> '' AND compiler_id <> '' "
                        "AND compiler_version <> '' AND cxx_standard <> '' AND build_type <> ''",
                        name="required_text_not_empty"),
        CheckConstraint("logical_cpus > 0 AND physical_cpus > 0 AND cache_line_bytes > 0 "
                        "AND memory_bytes > 0", name="hardware_counts_positive"),
        CheckConstraint("jsonb_typeof(core_types) = 'array'", name="core_types_is_array"),
    )


class BenchmarkRun(Base):
    """One engine invocation, from request to outcome.

    A run exists before the engine reports anything (status ``running``), so
    engine-provided fields are nullable and become mandatory on success.
    """

    __tablename__ = "benchmark_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="status", native_enum=False, create_constraint=True, length=16,
             values_callable=lambda members: [m.value for m in members]),
        server_default=RunStatus.RUNNING.value)
    # The validated request as submitted, e.g. {"workload": ..., "size": ...};
    # kept even when the run fails, which is when it matters most.
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    wall_time_s: Mapped[Optional[float]] = mapped_column(Double)
    # ---- from the engine report (required once succeeded) ----
    engine_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), unique=True)
    report_schema: Mapped[Optional[str]] = mapped_column(Text)
    report_generated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    time_unit: Mapped[str] = mapped_column(String(8), server_default="ms")
    environment_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("environments.id", ondelete="RESTRICT"))
    # ---- failure details (client-safe code, e.g. engine_timeout) ----
    error_code: Mapped[Optional[str]] = mapped_column(Text)
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    environment: Mapped[Optional[Environment]] = relationship(back_populates="runs")
    experiments: Mapped[list[Experiment]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True,
        order_by="Experiment.measured_at")

    __table_args__ = (
        CheckConstraint("jsonb_typeof(request) = 'object'", name="request_is_object"),
        CheckConstraint("time_unit = 'ms'", name="time_unit_supported"),
        CheckConstraint(f"wall_time_s IS NULL OR ({_finite_non_negative('wall_time_s')})",
                        name="wall_time_finite_non_negative"),
        CheckConstraint("finished_at IS NULL OR finished_at >= started_at",
                        name="finished_after_started"),
        CheckConstraint("(status = 'running') = (finished_at IS NULL)",
                        name="finished_iff_not_running"),
        CheckConstraint(
            "status <> 'succeeded' OR (engine_run_id IS NOT NULL AND report_schema IS NOT NULL "
            "AND report_generated_at IS NOT NULL AND environment_id IS NOT NULL "
            "AND wall_time_s IS NOT NULL)",
            name="succeeded_has_report"),
        CheckConstraint("(status IN ('failed', 'timed_out')) = (error_code IS NOT NULL)",
                        name="error_code_iff_failed"),
        Index(None, "status", "started_at"),
        Index(None, "environment_id"),
    )


class Experiment(Base):
    """One engine result record: the configuration actually used, the engine's
    summary statistics (validated against the samples on ingestion) and,
    through ``samples``, every measurement."""

    __tablename__ = "experiments"

    # The engine's experiment_id (UUID v4): globally unique, so it is the key.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("benchmark_runs.id", ondelete="CASCADE"))
    workload: Mapped[str] = mapped_column(Text)
    input_size: Mapped[int] = mapped_column(BigInteger)
    thread_count: Mapped[int] = mapped_column(Integer)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # ---- configuration ----
    warmup_runs: Mapped[int] = mapped_column(Integer)
    measured_runs: Mapped[int] = mapped_column(Integer)
    min_warmup_ms: Mapped[float] = mapped_column(Double)
    min_sample_ms: Mapped[float] = mapped_column(Double)
    warmup_runs_executed: Mapped[int] = mapped_column(Integer)
    iterations_per_sample: Mapped[int] = mapped_column(Integer)
    # ---- engine-reported statistics over the samples (ms) ----
    min_ms: Mapped[float] = mapped_column(Double)
    max_ms: Mapped[float] = mapped_column(Double)
    mean_ms: Mapped[float] = mapped_column(Double)
    median_ms: Mapped[float] = mapped_column(Double)
    stddev_ms: Mapped[Optional[float]] = mapped_column(Double)  # NULL for a single sample
    p95_ms: Mapped[float] = mapped_column(Double)

    run: Mapped[BenchmarkRun] = relationship(back_populates="experiments")
    samples: Mapped[list[TimingSample]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan", passive_deletes=True,
        order_by="TimingSample.sample_index")

    __table_args__ = (
        CheckConstraint("workload ~ '^[a-z][a-z0-9_]{0,63}$'", name="workload_format"),
        CheckConstraint("input_size >= 0", name="input_size_non_negative"),
        CheckConstraint("thread_count > 0", name="thread_count_positive"),
        CheckConstraint("warmup_runs >= 0 AND warmup_runs_executed >= warmup_runs",
                        name="warmup_runs_valid"),
        CheckConstraint("measured_runs > 0", name="measured_runs_positive"),
        CheckConstraint("iterations_per_sample > 0", name="iterations_per_sample_positive"),
        CheckConstraint(f"{_finite_non_negative('min_warmup_ms')} "
                        f"AND {_finite_non_negative('min_sample_ms')}",
                        name="config_durations_finite"),
        # min and max finite plus the ordering below also rules out NaN/Infinity
        # in mean, median and p95 (NaN would compare greater than max_ms).
        CheckConstraint(f"{_finite_non_negative('min_ms')} AND {_finite('max_ms')} "
                        "AND min_ms <= median_ms AND median_ms <= max_ms "
                        "AND min_ms <= mean_ms AND mean_ms <= max_ms "
                        "AND min_ms <= p95_ms AND p95_ms <= max_ms",
                        name="statistics_ordered"),
        CheckConstraint(f"(stddev_ms IS NULL) = (measured_runs = 1) "
                        f"AND (stddev_ms IS NULL OR ({_finite_non_negative('stddev_ms')}))",
                        name="stddev_valid"),
        Index(None, "run_id"),
        # Compare like-for-like configurations over time.
        Index(None, "workload", "input_size", "thread_count", "measured_at"),
    )


class TimingSample(Base):
    """One measured sample: mean per-call time over ``iterations_per_sample``
    calls, in milliseconds, at position ``sample_index`` in run order."""

    __tablename__ = "timing_samples"

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"), primary_key=True)
    sample_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    duration_ms: Mapped[float] = mapped_column(Double)

    experiment: Mapped[Experiment] = relationship(back_populates="samples")

    __table_args__ = (
        CheckConstraint("sample_index >= 0", name="sample_index_non_negative"),
        CheckConstraint(_finite_non_negative("duration_ms"), name="duration_finite_non_negative"),
    )
