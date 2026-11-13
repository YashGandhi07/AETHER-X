"""Benchmark persistence and retrieval through the API.

Tests marked by the ``clean_test_db`` fixture COMMIT to the PostgreSQL TEST
database (rows are deleted before each test). The engine is a FAKE that
returns SYNTHETIC reports (TEST_FIXTURE metadata, placeholder samples); no
value here is a real measurement. Database-failure tests point at a closed
local port and need no server.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import socket
import uuid
from collections.abc import Iterator, Sequence
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from aether_api.benchmarks import BenchmarkService
from aether_api.config import Settings
from aether_api.database import Database, DatabaseSettings
from aether_api.db_models import BenchmarkRun, Environment, Experiment, RunStatus, TimingSample
from aether_api.main import create_app
from aether_api.models import BenchmarkRunRequest
from aether_api.persistence import BenchmarkStore
from aether_api.system_info import SystemInfoProvider
from aether_bench import (
    BenchmarkOutputError,
    BenchmarkProcessError,
    BenchmarkSpec,
    BenchmarkTimeoutError,
    ExecutableNotFoundError,
    ProfileRun,
    parse_report,
)

from ..fixtures import synthetic_record, synthetic_report
from .conftest import CountingFetch

RUN_URL = "/api/v1/benchmarks/run"
HISTORY_URL = "/api/v1/benchmarks"
# Placeholder values chosen to expose rounding: not exactly representable in
# decimal, many significant digits, a tiny power of two.
SAMPLES = (0.1, 1 / 3, 0.018896484375, 123456.78901234567, 2.0 ** -40)
REQUEST = {"workload": "vector_add", "size": 4096, "threads": 1,
           "warmup_runs": 1, "measured_runs": len(SAMPLES)}
SECRET = "Tr0ub4dor&3-not-real"


class SyntheticEngine:
    """FAKE engine: a schema-valid SYNTHETIC report per call, with fresh ids
    (like the real engine) unless ``fixed_ids`` is set."""

    def __init__(self, samples: Sequence[float] = SAMPLES) -> None:
        self.samples = list(samples)
        self.error: Exception | None = None
        self.fixed_ids = False
        self.calls = 0

    def __call__(self, spec: BenchmarkSpec) -> ProfileRun:
        self.calls += 1
        if self.error is not None:
            raise self.error
        document = synthetic_report()
        record = synthetic_record(
            "00000000-0000-4000-8000-000000000001" if self.fixed_ids else str(uuid.uuid4()),
            self.samples)
        if not self.fixed_ids:
            document["run_id"] = str(uuid.uuid4())
        record.update(workload=spec.workload, input_size=spec.size, thread_count=spec.threads)
        record["config"]["warmup_runs"] = spec.warmup_runs
        document["results"] = [record]
        return ProfileRun(report=parse_report(json.dumps(document)), stdout="", stderr="",
                          wall_time_s=0.123456789)


@pytest.fixture
def engine() -> SyntheticEngine:
    return SyntheticEngine()


def _client(database: Database, fetch: CountingFetch, engine: SyntheticEngine,
            host: str = "127.0.0.1") -> TestClient:
    store = BenchmarkStore(database)
    app = create_app(Settings(log_level="DEBUG"), SystemInfoProvider(fetch),
                     BenchmarkService(engine, store), database=database, benchmark_store=store)
    return TestClient(app, raise_server_exceptions=False, client=(host, 50000))


@pytest.fixture
def db_client(clean_test_db: Database, fetch: CountingFetch,
              engine: SyntheticEngine) -> Iterator[TestClient]:
    with _client(clean_test_db, fetch, engine) as client:
        yield client


def _count(database: Database, model: type[Any]) -> int:
    with database.session() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def _row_counts(database: Database) -> dict[str, int]:
    return {model.__tablename__: _count(database, model)
            for model in (Environment, BenchmarkRun, Experiment, TimingSample)}


# ---------- successful persistence and retrieval ----------


def test_run_is_saved_completely_and_exactly(db_client: TestClient, clean_test_db: Database) -> None:
    response = db_client.post(RUN_URL, json=REQUEST)
    assert response.status_code == 200, response.text
    body = response.json()

    with clean_test_db.session() as session:
        experiment = session.get(Experiment, uuid.UUID(body["result"]["experiment_id"]))
        assert experiment is not None
        run, environment = experiment.run, experiment.run.environment
        samples = [s.duration_ms for s in experiment.samples]

        assert run.status is RunStatus.SUCCEEDED and run.error_code is None
        assert str(run.engine_run_id) == body["run_id"]
        assert run.request == REQUEST and run.time_unit == "ms"
        assert run.report_schema == "aether.cpu_profile.v2"
        assert run.wall_time_s == 0.123456789          # exact; the API rounds to 6 digits
        assert body["wall_time_s"] == 0.123457
        assert run.started_at <= run.finished_at

        assert [v.hex() for v in samples] == [v.hex() for v in SAMPLES]  # bit-for-bit
        stats = body["result"]["statistics"]
        assert (experiment.min_ms, experiment.max_ms, experiment.mean_ms, experiment.median_ms,
                experiment.stddev_ms, experiment.p95_ms) == (
            stats["min"], stats["max"], stats["mean"], stats["median"], stats["stddev"], stats["p95"])
        assert (experiment.workload, experiment.input_size, experiment.measured_runs) == (
            "vector_add", 4096, len(SAMPLES))

        assert environment is not None and environment.cpu_model.startswith("TEST_FIXTURE")
        assert environment.core_types == [{"name": "TEST_FIXTURE_core", "physical_cpus": 2}]


def test_get_returns_the_same_response_as_the_run(db_client: TestClient) -> None:
    posted = db_client.post(RUN_URL, json=REQUEST).json()
    fetched = db_client.get(f"{HISTORY_URL}/{posted['result']['experiment_id']}")
    assert fetched.status_code == 200
    assert fetched.json() == posted


def test_single_sample_experiment_round_trips(clean_test_db: Database, fetch: CountingFetch) -> None:
    engine = SyntheticEngine(samples=[0.5])
    with _client(clean_test_db, fetch, engine) as client:
        posted = client.post(RUN_URL, json={**REQUEST, "measured_runs": 1}).json()
        fetched = client.get(f"{HISTORY_URL}/{posted['result']['experiment_id']}").json()
    assert fetched == posted and fetched["result"]["statistics"]["stddev"] is None


def test_environment_is_shared_between_runs(db_client: TestClient, clean_test_db: Database) -> None:
    for _ in range(3):
        assert db_client.post(RUN_URL, json=REQUEST).status_code == 200
    assert _row_counts(clean_test_db) == {"environments": 1, "benchmark_runs": 3,
                                          "experiments": 3, "timing_samples": 3 * len(SAMPLES)}


def test_history_is_paginated_newest_first(db_client: TestClient) -> None:
    ids = [db_client.post(RUN_URL, json=REQUEST).json()["result"]["experiment_id"] for _ in range(3)]

    first = db_client.get(HISTORY_URL, params={"limit": 2}).json()
    assert (first["total"], first["limit"], first["offset"], first["time_unit"]) == (3, 2, 0, "ms")
    assert [item["experiment_id"] for item in first["items"]] == ids[::-1][:2]
    assert "samples_ms" not in first["items"][0]
    assert first["items"][0]["statistics"]["count"] == len(SAMPLES)

    rest = db_client.get(HISTORY_URL, params={"limit": 2, "offset": 2}).json()
    assert [item["experiment_id"] for item in rest["items"]] == [ids[0]]
    beyond = db_client.get(HISTORY_URL, params={"offset": 10}).json()
    assert beyond["items"] == [] and beyond["total"] == 3


def test_empty_history(db_client: TestClient) -> None:
    body = db_client.get(HISTORY_URL).json()
    assert body == {"items": [], "total": 0, "limit": 20, "offset": 0, "time_unit": "ms"}


def test_unknown_experiment_is_404(db_client: TestClient) -> None:
    response = db_client.get(f"{HISTORY_URL}/{uuid.uuid4()}")
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"


# ---------- failed runs are never measurements ----------


@pytest.mark.parametrize(("error", "status", "code"), [
    (BenchmarkProcessError(1, "", "error: verification failed for vector_add"),
     RunStatus.FAILED, "engine_failed"),
    (BenchmarkOutputError("invalid report at $.results[0].samples: truncated"),
     RunStatus.FAILED, "engine_bad_output"),
    (BenchmarkTimeoutError(60.0, "", ""), RunStatus.TIMED_OUT, "engine_timeout"),
])
def test_engine_failure_is_recorded_as_failed_run_without_measurements(
        db_client: TestClient, clean_test_db: Database, engine: SyntheticEngine,
        error: Exception, status: RunStatus, code: str) -> None:
    engine.error = error
    response = db_client.post(RUN_URL, json=REQUEST)
    assert response.status_code in (502, 504) and response.json()["error"]["code"] == code
    assert "verification failed" not in response.text  # detail stays server-side

    with clean_test_db.session() as session:
        (run,) = session.scalars(select(BenchmarkRun)).all()
        assert (run.status, run.error_code, run.request) == (status, code, REQUEST)
        assert run.error_message == str(error) and run.finished_at is not None
        assert run.engine_run_id is None and run.environment_id is None
    assert _count(clean_test_db, Experiment) == 0
    assert db_client.get(HISTORY_URL).json()["total"] == 0


def test_missing_engine_records_nothing(db_client: TestClient, clean_test_db: Database,
                                        engine: SyntheticEngine) -> None:
    engine.error = ExecutableNotFoundError("cpp/build/aether_run_benchmark not found")
    assert db_client.post(RUN_URL, json=REQUEST).status_code == 503
    assert sum(_row_counts(clean_test_db).values()) == 0  # nothing ran, so there is no run


def test_invalid_request_reaches_neither_engine_nor_database(
        db_client: TestClient, clean_test_db: Database, engine: SyntheticEngine) -> None:
    assert db_client.post(RUN_URL, json={**REQUEST, "size": 0}).status_code == 422
    assert engine.calls == 0 and sum(_row_counts(clean_test_db).values()) == 0


# ---------- transactions ----------


def _incomplete_run(engine: SyntheticEngine) -> ProfileRun:
    """A report with one sample removed AFTER validation: the kind of data
    aether_bench rejects, here forced past it to test the database's own guard."""
    run = engine(BenchmarkSpec(workload="vector_add", size=4096, warmup_runs=1,
                               measured_runs=len(SAMPLES)))
    record = dataclasses.replace(run.report.records[0], samples_ms=run.report.records[0].samples_ms[:-1])
    return dataclasses.replace(run, report=dataclasses.replace(run.report, records=(record,)))


def test_incomplete_experiment_is_rolled_back_entirely(clean_test_db: Database,
                                                       engine: SyntheticEngine) -> None:
    store = BenchmarkStore(clean_test_db)
    request = BenchmarkRunRequest(**REQUEST)
    run = _incomplete_run(engine)
    now = datetime.now(timezone.utc)
    with pytest.raises(IntegrityError, match="has 4 samples"):
        store.save_succeeded(request, run, now, now)
    # Not even the environment row, inserted first in the same transaction, survives.
    assert sum(_row_counts(clean_test_db).values()) == 0


def test_rollback_over_http_returns_database_error(db_client: TestClient, clean_test_db: Database,
                                                   engine: SyntheticEngine) -> None:
    incomplete = _incomplete_run(engine)
    engine_calls = engine.calls

    def broken_engine(spec: BenchmarkSpec) -> ProfileRun:
        return incomplete

    db_client.app.state.benchmark_service._run = broken_engine  # type: ignore[attr-defined]
    response = db_client.post(RUN_URL, json=REQUEST)
    assert response.status_code == 500 and response.json()["error"]["code"] == "database_error"
    assert "samples" not in response.text  # no SQL or constraint detail leaks
    assert sum(_row_counts(clean_test_db).values()) == 0 and engine.calls == engine_calls


def test_duplicate_engine_ids_are_rejected_and_first_run_kept(
        db_client: TestClient, clean_test_db: Database, engine: SyntheticEngine) -> None:
    engine.fixed_ids = True
    first = db_client.post(RUN_URL, json=REQUEST)
    assert first.status_code == 200
    second = db_client.post(RUN_URL, json=REQUEST)
    assert second.status_code == 500 and second.json()["error"]["code"] == "database_error"
    assert _row_counts(clean_test_db)["benchmark_runs"] == 1
    assert db_client.get(f"{HISTORY_URL}/{first.json()['result']['experiment_id']}").json() == first.json()
    assert db_client.post(RUN_URL, json=REQUEST).status_code == 500  # lock was released


# ---------- database unavailable (no server needed) ----------


def _closed_local_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def offline_client(fetch: CountingFetch, engine: SyntheticEngine) -> Iterator[TestClient]:
    database = Database(DatabaseSettings(host="127.0.0.1", port=_closed_local_port(),
                                         password=SECRET, connect_timeout_s=2))
    with _client(database, fetch, engine) as client:
        yield client


def test_unsaved_run_returns_503_without_results(offline_client: TestClient) -> None:
    response = offline_client.post(RUN_URL, json=REQUEST)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
    assert "samples_ms" not in response.text and SECRET not in response.text
    # The lock is released: the next request fails the same way instead of 429.
    assert offline_client.post(RUN_URL, json=REQUEST).status_code == 503


@pytest.mark.parametrize("path", [HISTORY_URL, f"{HISTORY_URL}/{uuid.uuid4()}"])
def test_reads_return_503_when_database_is_down(offline_client: TestClient, path: str) -> None:
    response = offline_client.get(path)
    assert response.status_code == 503 and response.json()["error"]["code"] == "database_unavailable"
    assert SECRET not in response.text and "127.0.0.1" not in response.text


def test_engine_error_is_not_masked_by_database_outage(offline_client: TestClient,
                                                       engine: SyntheticEngine) -> None:
    engine.error = BenchmarkProcessError(1, "", "verification failed")
    response = offline_client.post(RUN_URL, json=REQUEST)
    assert response.status_code == 502 and response.json()["error"]["code"] == "engine_failed"


def test_database_error_log_is_redacted(offline_client: TestClient,
                                        caplog: pytest.LogCaptureFixture) -> None:
    logging.getLogger("aether").propagate = True
    try:
        with caplog.at_level(logging.ERROR, logger="aether.api.errors"):
            offline_client.get(HISTORY_URL)
    finally:
        logging.getLogger("aether").propagate = False
    entries = [r.fields for r in caplog.records if r.name == "aether.api.errors"]  # type: ignore[attr-defined]
    assert entries and all(e["error_type"] == "OperationalError" and e["path"] == HISTORY_URL
                           for e in entries)
    assert "Connection refused" in entries[-1]["reason"]
    assert SECRET not in json.dumps(entries)


# ---------- validation and access ----------


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 101}, {"limit": "ten"}, {"limit": 2.5},
    {"offset": -1}, {"offset": 100_001},
])
def test_pagination_is_bounded(offline_client: TestClient, params: dict[str, Any]) -> None:
    # Rejected before any database access (which would be a 503 here).
    response = offline_client.get(HISTORY_URL, params=params)
    assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("path", [f"{HISTORY_URL}/not-a-uuid", f"{HISTORY_URL}/123",
                                  f"{HISTORY_URL}/{uuid.uuid4().hex}x"])
def test_malformed_experiment_id_is_404(offline_client: TestClient, path: str) -> None:
    assert offline_client.get(path).status_code == 404


def test_run_path_still_rejects_get(offline_client: TestClient) -> None:
    assert offline_client.get(RUN_URL).status_code == 405


@pytest.mark.parametrize("path", [HISTORY_URL, f"{HISTORY_URL}/{uuid.uuid4()}"])
def test_reads_are_localhost_only(fetch: CountingFetch, engine: SyntheticEngine, path: str) -> None:
    database = Database(DatabaseSettings(port=_closed_local_port()))
    with _client(database, fetch, engine, host="203.0.113.5") as remote:
        response = remote.get(path)
    assert response.status_code == 403 and response.json()["error"]["code"] == "local_only"


def test_reads_refuse_foreign_origins(offline_client: TestClient) -> None:
    response = offline_client.get(HISTORY_URL, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "origin_not_allowed"


def test_reads_work_when_execution_is_disabled(clean_test_db: Database, fetch: CountingFetch,
                                               engine: SyntheticEngine) -> None:
    store = BenchmarkStore(clean_test_db)
    app = create_app(Settings(benchmarks_enabled=False), SystemInfoProvider(fetch),
                     BenchmarkService(engine, store), database=clean_test_db, benchmark_store=store)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        assert client.post(RUN_URL, json=REQUEST).status_code == 403
        assert client.get(HISTORY_URL).status_code == 200
