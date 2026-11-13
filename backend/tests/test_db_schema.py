"""Schema, constraint, relationship and migration tests against a REAL
PostgreSQL test database (never the development database).

Target: AETHER_DB_* settings with the name replaced by AETHER_TEST_DB_NAME
(default aether_x_test). The name must end in ``_test`` because the session
fixture rebuilds the schema from scratch (downgrade base, upgrade head).
Skipped when unreachable; AETHER_REQUIRE_DATABASE=1 makes that a failure.

Each test runs in a transaction that is rolled back. Constraint triggers are
deferred to COMMIT, so ``check_deferred`` forces them with SET CONSTRAINTS
ALL IMMEDIATE; one test exercises a real COMMIT. All values are SYNTHETIC
fixtures (labelled TEST_FIXTURE), never real measurements.
"""

from __future__ import annotations

import math
import os
import statistics
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection, delete, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aether_api.database import Database, DatabaseSettings, DatabaseUnavailableError
from aether_api.db_models import (
    Base,
    BenchmarkRun,
    Environment,
    Experiment,
    RunStatus,
    TimingSample,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_DB_NAME = os.environ.get("AETHER_TEST_DB_NAME", "aether_x_test")
EXPECTED_TABLES = {"alembic_version", "environments", "benchmark_runs", "experiments", "timing_samples"}
T0 = datetime(2000, 1, 1, tzinfo=timezone.utc)  # obviously synthetic timestamps


def _settings() -> DatabaseSettings:
    if not TEST_DB_NAME.endswith("_test"):
        raise RuntimeError(f"refusing to rebuild schema in {TEST_DB_NAME!r}: name must end in '_test'")
    return DatabaseSettings.from_environment({**os.environ, "AETHER_DB_NAME": TEST_DB_NAME})


def _unavailable_reason() -> str | None:
    db = Database(_settings())
    try:
        db.check_connection()
        return None
    except DatabaseUnavailableError as exc:
        return str(exc)
    finally:
        db.dispose()


_REASON = _unavailable_reason()
pytestmark = pytest.mark.skipif(
    _REASON is not None and os.environ.get("AETHER_REQUIRE_DATABASE") != "1",
    reason=f"test database not reachable: {_REASON}")


def _alembic(connection: Connection) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


@pytest.fixture(scope="module")
def database() -> Iterator[Database]:
    db = Database(_settings())
    with db.engine.begin() as connection:  # fresh schema at the latest revision
        command.downgrade(_alembic(connection), "base")
        command.upgrade(_alembic(connection), "head")
    yield db
    db.dispose()


@pytest.fixture
def connection(database: Database) -> Iterator[Connection]:
    with database.engine.connect() as conn:
        transaction = conn.begin()
        yield conn
        transaction.rollback()


@pytest.fixture
def session(connection: Connection) -> Iterator[Session]:
    # Savepoint mode: session.commit() inside a test cannot end the outer
    # transaction, so every test leaves the database untouched.
    with Session(bind=connection, join_transaction_mode="create_savepoint") as s:
        yield s


def check_deferred(session: Session) -> None:
    session.flush()
    session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    session.execute(text("SET CONSTRAINTS ALL DEFERRED"))


def violated(error: pytest.ExceptionInfo[IntegrityError]) -> str | None:
    return error.value.orig.diag.constraint_name  # type: ignore[union-attr]


# ---------- synthetic factories ----------

def make_environment(**overrides: Any) -> Environment:
    values: dict[str, Any] = dict(
        os_family="TEST_FIXTURE_os", os_version=None, os_build="TEST_FIXTURE_build",
        kernel_release=None, architecture="TEST_FIXTURE_arch", process_translated=False,
        cpu_model="TEST_FIXTURE CPU", logical_cpus=2, physical_cpus=2,
        core_types=[{"name": "TEST_FIXTURE_core", "physical_cpus": 2}], cache_line_bytes=64,
        memory_bytes=1024, compiler_id="TEST_FIXTURE_cc", compiler_version="0.0.0",
        cxx_standard="C++20", build_type="Release", cmake_cxx_flags=None, assertions_enabled=False)
    values.update(overrides)
    return Environment(**values)


def make_run(environment: Environment | None = None, **overrides: Any) -> BenchmarkRun:
    values: dict[str, Any] = dict(
        status=RunStatus.SUCCEEDED, request={"workload": "vector_add", "size": 4},
        started_at=T0, finished_at=T0 + timedelta(seconds=1), wall_time_s=0.5,
        engine_run_id=uuid.uuid4(), report_schema="aether.cpu_profile.v2",
        report_generated_at=T0, environment=environment)
    values.update(overrides)
    return BenchmarkRun(**values)


def make_experiment(run: BenchmarkRun, samples: list[float], **overrides: Any) -> Experiment:
    n = len(samples)
    values: dict[str, Any] = dict(
        id=uuid.uuid4(), run=run, workload="vector_add", input_size=4, thread_count=1,
        measured_at=T0, warmup_runs=1, measured_runs=n, min_warmup_ms=100.0, min_sample_ms=1.0,
        warmup_runs_executed=1, iterations_per_sample=8,
        min_ms=min(samples), max_ms=max(samples), mean_ms=statistics.fmean(samples),
        median_ms=statistics.median(samples),
        stddev_ms=statistics.stdev(samples) if n > 1 else None,
        p95_ms=statistics.quantiles(samples, n=20, method="inclusive")[18] if n > 1 else samples[0],
        samples=[TimingSample(sample_index=i, duration_ms=v) for i, v in enumerate(samples)])
    values.update(overrides)
    return Experiment(**values)


def complete_run(session: Session, samples: list[float] = [1.0, 2.0, 3.0]) -> BenchmarkRun:
    run = make_run(make_environment())
    make_experiment(run, list(samples))
    session.add(run)
    check_deferred(session)
    return run


# ---------- migration ----------

def test_migration_creates_tables_indexes_and_triggers(connection: Connection) -> None:
    inspector = inspect(connection)
    assert set(inspector.get_table_names()) == EXPECTED_TABLES
    assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0001"

    indexes = {ix["name"] for table in EXPECTED_TABLES for ix in inspector.get_indexes(table)}
    assert {"ix_benchmark_runs_status_started_at", "ix_benchmark_runs_environment_id",
            "ix_experiments_run_id",
            "ix_experiments_workload_input_size_thread_count_measured_at"} <= indexes

    foreign_keys = {
        (table, tuple(fk["constrained_columns"]), fk["referred_table"], fk["options"].get("ondelete"))
        for table in EXPECTED_TABLES for fk in inspector.get_foreign_keys(table)}
    assert foreign_keys == {
        ("benchmark_runs", ("environment_id",), "environments", "RESTRICT"),
        ("experiments", ("run_id",), "benchmark_runs", "CASCADE"),
        ("timing_samples", ("experiment_id",), "experiments", "CASCADE"),
    }

    triggers = connection.execute(text("""
        SELECT tgname, tgdeferrable, tginitdeferred FROM pg_trigger
        WHERE tgname LIKE '%sample_count' ORDER BY tgname""")).all()
    assert [tuple(t) for t in triggers] == [
        ("experiments_sample_count", True, True), ("timing_samples_sample_count", True, True)]

    double_columns = connection.execute(text("""
        SELECT table_name || '.' || column_name FROM information_schema.columns
        WHERE column_name IN ('duration_ms', 'mean_ms') AND data_type = 'double precision'
        ORDER BY 1""")).scalars().all()
    assert double_columns == ["experiments.mean_ms", "timing_samples.duration_ms"]


def test_models_and_migration_do_not_drift(connection: Connection) -> None:
    context = MigrationContext.configure(connection, opts={
        "compare_type": True, "compare_server_default": True})
    assert compare_metadata(context, Base.metadata) == []


def test_downgrade_removes_schema_and_upgrade_is_repeatable(connection: Connection) -> None:
    # Transactional DDL: the whole round trip is rolled back by the fixture.
    command.downgrade(_alembic(connection), "base")
    assert set(inspect(connection).get_table_names()) == {"alembic_version"}
    assert connection.execute(text(
        "SELECT count(*) FROM pg_proc WHERE proname = 'check_experiment_sample_count'")).scalar_one() == 0

    command.upgrade(_alembic(connection), "head")
    assert set(inspect(connection).get_table_names()) == EXPECTED_TABLES


# ---------- defaults, relationships, precision ----------

def test_server_defaults(session: Session) -> None:
    run = BenchmarkRun(request={"workload": "vector_add"})
    session.add(run)
    session.flush()
    session.refresh(run)
    assert isinstance(run.id, uuid.UUID)
    assert run.status is RunStatus.RUNNING
    assert run.time_unit == "ms"
    assert run.started_at.tzinfo is not None and run.finished_at is None


def test_relationships_round_trip_in_order(session: Session) -> None:
    environment = make_environment()
    run = make_run(environment)
    experiment = make_experiment(run, [3.0, 1.0, 2.0])
    experiment.samples.reverse()  # insert out of order; reads must come back by index
    session.add(run)
    check_deferred(session)
    session.expire_all()

    loaded = session.get(Experiment, experiment.id)
    assert loaded is not None
    assert [s.sample_index for s in loaded.samples] == [0, 1, 2]
    assert [s.duration_ms for s in loaded.samples] == [3.0, 1.0, 2.0]
    assert loaded.run.environment.runs == [loaded.run]
    assert loaded.run.experiments == [loaded]


def test_samples_are_stored_bit_for_bit(session: Session) -> None:
    # Values that are not exactly representable in decimal, the smallest
    # subnormal, and many significant digits: any rounding would change them.
    values = [0.1, 1 / 3, 5e-324, 0.018896484375, 123456.78901234567, 2.0 ** -40]
    experiment = make_experiment(make_run(make_environment()), values)
    session.add(experiment)
    check_deferred(session)
    session.expire_all()

    stored = session.scalars(select(TimingSample.duration_ms)
                             .where(TimingSample.experiment_id == experiment.id)
                             .order_by(TimingSample.sample_index)).all()
    assert [v.hex() for v in stored] == [v.hex() for v in values]


def test_sql_statistics_agree_with_stored_engine_statistics(session: Session) -> None:
    run = complete_run(session, [0.25, 0.5, 0.75, 1.0, 4.0])
    experiment = run.experiments[0]
    row = session.execute(text("""
        SELECT min(duration_ms), max(duration_ms), avg(duration_ms), stddev_samp(duration_ms),
               percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms),
               percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)
        FROM timing_samples WHERE experiment_id = :id"""), {"id": experiment.id}).one()
    expected = (experiment.min_ms, experiment.max_ms, experiment.mean_ms, experiment.stddev_ms,
                experiment.median_ms, experiment.p95_ms)
    assert all(math.isclose(a, b, rel_tol=1e-12) for a, b in zip(row, expected))


# ---------- CHECK, UNIQUE and FOREIGN KEY constraints ----------

@pytest.mark.parametrize("bad", [-1.0, math.nan, math.inf, -math.inf])
def test_sample_duration_must_be_finite_and_non_negative(session: Session, bad: float) -> None:
    run = complete_run(session)
    session.add(TimingSample(experiment=run.experiments[0], sample_index=99, duration_ms=bad))
    with pytest.raises(IntegrityError) as error:
        session.flush()
    assert violated(error) == "ck_timing_samples_duration_finite_non_negative"


@pytest.mark.parametrize(("overrides", "constraint"), [
    ({"mean_ms": math.nan}, "ck_experiments_statistics_ordered"),
    ({"max_ms": math.inf}, "ck_experiments_statistics_ordered"),
    ({"p95_ms": 99.0}, "ck_experiments_statistics_ordered"),
    ({"stddev_ms": None}, "ck_experiments_stddev_valid"),
    ({"workload": "Vector Add"}, "ck_experiments_workload_format"),
    ({"thread_count": 0}, "ck_experiments_thread_count_positive"),
    ({"warmup_runs": 5, "warmup_runs_executed": 4}, "ck_experiments_warmup_runs_valid"),
    ({"min_sample_ms": math.nan}, "ck_experiments_config_durations_finite"),
])
def test_experiment_constraints(session: Session, overrides: dict[str, Any], constraint: str) -> None:
    session.add(make_experiment(make_run(make_environment()), [1.0, 2.0], **overrides))
    with pytest.raises(IntegrityError) as error:
        session.flush()
    assert violated(error) == constraint


@pytest.mark.parametrize(("overrides", "constraint"), [
    ({"engine_run_id": None}, "ck_benchmark_runs_succeeded_has_report"),
    ({"environment": None}, "ck_benchmark_runs_succeeded_has_report"),
    ({"error_code": "engine_failed"}, "ck_benchmark_runs_error_code_iff_failed"),
    ({"status": RunStatus.FAILED}, "ck_benchmark_runs_error_code_iff_failed"),
    ({"status": RunStatus.RUNNING}, "ck_benchmark_runs_finished_iff_not_running"),
    ({"finished_at": T0 - timedelta(seconds=1)}, "ck_benchmark_runs_finished_after_started"),
    ({"wall_time_s": math.nan}, "ck_benchmark_runs_wall_time_finite_non_negative"),
    ({"time_unit": "s"}, "ck_benchmark_runs_time_unit_supported"),
    ({"request": ["not", "an", "object"]}, "ck_benchmark_runs_request_is_object"),
])
def test_run_constraints(session: Session, overrides: dict[str, Any], constraint: str) -> None:
    session.add(make_run(**{"environment": make_environment(), **overrides}))
    with pytest.raises(IntegrityError) as error:
        session.flush()
    assert violated(error) == constraint


def test_unknown_status_rejected(connection: Connection) -> None:
    with pytest.raises(IntegrityError) as error:
        connection.execute(text(
            "INSERT INTO benchmark_runs (status, request, finished_at) VALUES ('paused', '{}', now())"))
    assert violated(error) == "ck_benchmark_runs_status"


def test_failed_run_needs_only_error_details(session: Session) -> None:
    session.add(make_run(None, status=RunStatus.TIMED_OUT, engine_run_id=None, report_schema=None,
                         report_generated_at=None, wall_time_s=None, error_code="engine_timeout",
                         error_message="The C++ engine did not respond in time."))
    check_deferred(session)


def test_engine_run_id_is_unique(session: Session) -> None:
    shared = uuid.uuid4()
    environment = make_environment()
    session.add_all([make_run(environment, engine_run_id=shared),
                     make_run(environment, engine_run_id=shared)])
    with pytest.raises(IntegrityError) as error:
        session.flush()
    assert violated(error) == "uq_benchmark_runs_engine_run_id"


def test_identical_environments_deduplicate_even_with_nulls(session: Session) -> None:
    session.add(make_environment())  # os_version, kernel_release, cmake_cxx_flags are NULL
    session.flush()
    session.add(make_environment())
    with pytest.raises(IntegrityError) as error:
        session.flush()
    assert violated(error) == "uq_environments_identity"


def test_environment_in_use_cannot_be_deleted(session: Session) -> None:
    run = complete_run(session)
    with pytest.raises(IntegrityError) as error:
        session.execute(delete(Environment).where(Environment.id == run.environment_id))
    assert violated(error) == "fk_benchmark_runs_environment_id_environments"


def test_deleting_run_cascades_to_experiments_and_samples(session: Session) -> None:
    run = complete_run(session)
    experiment_id = run.experiments[0].id
    session.execute(delete(BenchmarkRun).where(BenchmarkRun.id == run.id))
    check_deferred(session)  # the sample-count trigger must tolerate cascaded deletes
    assert session.scalar(select(Experiment).where(Experiment.id == experiment_id)) is None
    assert session.scalars(select(TimingSample).where(
        TimingSample.experiment_id == experiment_id)).all() == []


# ---------- deferred sample-count trigger ----------

@pytest.mark.parametrize("indices", [[0, 1], [0, 1, 3], [1, 2, 3], [0, 1, 2, 3]])
def test_samples_must_be_exactly_0_to_n_minus_1(session: Session, indices: list[int]) -> None:
    experiment = make_experiment(make_run(make_environment()), [1.0, 2.0, 3.0])
    experiment.samples = [TimingSample(sample_index=i, duration_ms=1.0) for i in indices]
    session.add(experiment)
    with pytest.raises(IntegrityError, match="expected 3"):
        check_deferred(session)


def test_removing_a_sample_from_a_complete_experiment_is_rejected(session: Session) -> None:
    run = complete_run(session)
    session.execute(delete(TimingSample).where(
        TimingSample.experiment_id == run.experiments[0].id, TimingSample.sample_index == 2))
    with pytest.raises(IntegrityError, match="has 2 samples"):
        check_deferred(session)


def test_incomplete_experiment_fails_at_real_commit(database: Database) -> None:
    environment_signature = f"TEST_FIXTURE_commit_{uuid.uuid4().hex}"
    with Session(database.engine) as session:
        experiment = make_experiment(make_run(make_environment(os_family=environment_signature)),
                                     [1.0, 2.0])
        experiment.samples.pop()  # one sample short
        session.add(experiment)
        session.flush()  # rows inserted; nothing checked yet (deferred)
        with pytest.raises(IntegrityError, match="has 1 samples"):
            session.commit()
    with database.engine.connect() as conn:  # the whole transaction was rolled back
        assert conn.execute(text("SELECT count(*) FROM environments WHERE os_family = :s"),
                            {"s": environment_signature}).scalar_one() == 0
