"""Unit tests for the database layer. None of these need a running PostgreSQL:
failures use a refused local port or a stub engine, and session semantics use
an in-memory SQLite engine (stdlib) that is discarded after each test."""

from __future__ import annotations

import math
import socket
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool

import aether_api.database as database_module
from aether_api.benchmarks import BenchmarkService
from aether_api.config import ConfigurationError, Settings
from aether_api.database import Database, DatabaseSettings, DatabaseUnavailableError
from aether_api.main import create_app
from aether_api.system_info import SystemInfoProvider

SECRET = "Tr0ub4dor&3-not-real"


def _closed_local_port() -> int:
    # Bind then release an ephemeral port: nothing listens there afterwards,
    # so a connection is refused immediately instead of waiting for a timeout.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------- configuration ----------

def test_defaults_target_local_dev_database() -> None:
    s = DatabaseSettings.from_environment({})
    assert (s.host, s.port, s.name, s.user, s.password) == (
        "localhost", 5432, "aether_x_dev", "aether_x_app", None)
    assert (s.connect_timeout_s, s.statement_timeout_ms, s.pool_timeout_s) == (5, 10_000, 5.0)


def test_reads_every_variable_from_environment() -> None:
    s = DatabaseSettings.from_environment({
        "AETHER_DB_HOST": " 127.0.0.1 ", "AETHER_DB_PORT": "6543", "AETHER_DB_NAME": "other_db",
        "AETHER_DB_USER": "other_user", "AETHER_DB_PASSWORD": " pw with spaces ",
        "AETHER_DB_CONNECT_TIMEOUT_S": "3", "AETHER_DB_STATEMENT_TIMEOUT_MS": "250",
        "AETHER_DB_POOL_TIMEOUT_S": "1.5",
    })
    assert (s.host, s.port, s.name, s.user) == ("127.0.0.1", 6543, "other_db", "other_user")
    assert s.password == " pw with spaces "  # passwords are never trimmed
    assert (s.connect_timeout_s, s.statement_timeout_ms, s.pool_timeout_s) == (3, 250, 1.5)


@pytest.mark.parametrize("environ", [
    {"AETHER_DB_PORT": "abc"},
    {"AETHER_DB_PORT": "0"},
    {"AETHER_DB_PORT": "65536"},
    {"AETHER_DB_HOST": ""},
    {"AETHER_DB_HOST": "localhost/evil"},
    {"AETHER_DB_NAME": "drop table;"},
    {"AETHER_DB_NAME": "1starts_with_digit"},
    {"AETHER_DB_USER": "x" * 64},
    {"AETHER_DB_PASSWORD": ""},
    {"AETHER_DB_CONNECT_TIMEOUT_S": "1"},
    {"AETHER_DB_CONNECT_TIMEOUT_S": "2.5"},
    {"AETHER_DB_STATEMENT_TIMEOUT_MS": "0"},
    {"AETHER_DB_STATEMENT_TIMEOUT_MS": "600001"},
    {"AETHER_DB_POOL_TIMEOUT_S": "nan"},
    {"AETHER_DB_POOL_TIMEOUT_S": "0"},
])
def test_invalid_configuration_fails_fast(environ: dict[str, str]) -> None:
    with pytest.raises(ConfigurationError):
        DatabaseSettings.from_environment(environ)


def test_configuration_error_never_echoes_password() -> None:
    with pytest.raises(ConfigurationError) as info:
        DatabaseSettings.from_environment({"AETHER_DB_PASSWORD": SECRET, "AETHER_DB_PORT": "x"})
    assert SECRET not in str(info.value)


def test_password_is_masked_in_repr_url_and_description() -> None:
    s = DatabaseSettings(password=SECRET)
    for rendered in (repr(s), str(s), str(s.url()), repr(s.url()), s.describe()):
        assert SECRET not in rendered
    assert str(s.url()) == "postgresql+psycopg://aether_x_app:***@localhost:5432/aether_x_dev"
    assert s.url().password == SECRET  # still available to the driver


# ---------- engine construction ----------

def test_engine_gets_timeouts_pool_limits_and_application_name(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_create_engine(url: Any, **kwargs: Any) -> object:
        captured.update(url=url, **kwargs)
        return object()

    monkeypatch.setattr(database_module, "create_engine", fake_create_engine)
    Database(DatabaseSettings(connect_timeout_s=3, statement_timeout_ms=1234, pool_timeout_s=2.0))

    assert captured["url"].drivername == "postgresql+psycopg"
    assert captured["pool_pre_ping"] is True
    assert captured["pool_timeout"] == 2.0
    assert captured["connect_args"] == {
        "connect_timeout": 3,
        "application_name": "aether-x-api",
        "options": "-c statement_timeout=1234",
    }


def test_creating_database_does_not_connect() -> None:
    # Nothing listens on this port; construction must still succeed.
    Database(DatabaseSettings(port=_closed_local_port())).dispose()


# ---------- connectivity check failures ----------

def test_unreachable_server_raises_safe_error() -> None:
    port = _closed_local_port()
    db = Database(DatabaseSettings(host="127.0.0.1", port=port, password=SECRET, connect_timeout_s=2))
    try:
        with pytest.raises(DatabaseUnavailableError) as info:
            db.check_connection()
    finally:
        db.dispose()
    message = str(info.value)
    assert SECRET not in message
    assert f"aether_x_app@127.0.0.1:{port}/aether_x_dev" in message
    assert "\n" not in message
    # Not chained: a logged traceback cannot reveal more than the message.
    assert info.value.__cause__ is None and info.value.__suppress_context__


class _FailingEngine:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def connect(self) -> Any:
        raise self.error

    def dispose(self) -> None:
        pass


def test_password_echoed_by_driver_is_redacted() -> None:
    driver_error = Exception(f'password "{SECRET}" rejected\nDETAIL: second line')
    engine = _FailingEngine(OperationalError("SELECT 1", {}, driver_error))
    db = Database(DatabaseSettings(password=SECRET), engine=engine)  # type: ignore[arg-type]
    with pytest.raises(DatabaseUnavailableError) as info:
        db.check_connection()
    assert SECRET not in str(info.value)
    assert 'password "***" rejected' in str(info.value)
    assert "second line" not in str(info.value)


class _WrongResultEngine:
    """connect() yields a connection whose SELECT 1 returns 2."""

    def connect(self) -> Any:
        engine = self

        class _Connection:
            def __enter__(self) -> Any:
                return self

            def __exit__(self, *exc: object) -> None:
                pass

            def execute(self, _: Any) -> Any:
                return engine

        return _Connection()

    def scalar_one(self) -> int:
        return 2

    def dispose(self) -> None:
        pass


def test_unexpected_select_result_is_an_error() -> None:
    db = Database(DatabaseSettings(), engine=_WrongResultEngine())  # type: ignore[arg-type]
    with pytest.raises(DatabaseUnavailableError, match="unexpected SELECT 1 result: 2"):
        db.check_connection()


def test_check_connection_returns_round_trip_ms() -> None:
    db = Database(DatabaseSettings(), engine=create_engine("sqlite://"))
    elapsed = db.check_connection()
    db.dispose()
    assert math.isfinite(elapsed) and elapsed >= 0


# ---------- session lifecycle (in-memory SQLite) ----------

@pytest.fixture
def sqlite_db() -> Iterator[Database]:
    # StaticPool: every session shares the one in-memory connection.
    engine = create_engine("sqlite://", poolclass=StaticPool)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE items (name TEXT)"))
    db = Database(DatabaseSettings(), engine=engine)
    yield db
    db.dispose()


def _names(db: Database) -> list[str]:
    with db.session() as session:
        return list(session.execute(text("SELECT name FROM items ORDER BY name")).scalars())


def test_session_commits_on_success(sqlite_db: Database) -> None:
    with sqlite_db.session() as session:
        session.execute(text("INSERT INTO items VALUES ('kept')"))
    assert _names(sqlite_db) == ["kept"]


def test_session_rolls_back_on_error(sqlite_db: Database) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with sqlite_db.session() as session:
            session.execute(text("INSERT INTO items VALUES ('discarded')"))
            raise RuntimeError("boom")
    assert _names(sqlite_db) == []


# ---------- application wiring ----------

class _RecordingDatabase:
    def __init__(self) -> None:
        self.disposed = 0

    def dispose(self) -> None:
        self.disposed += 1


def test_app_holds_database_and_disposes_it_on_shutdown(fetch: Any, fake_run: Any) -> None:
    database = _RecordingDatabase()
    app = create_app(Settings(), SystemInfoProvider(fetch), BenchmarkService(fake_run),
                     database=database)  # type: ignore[arg-type]
    with TestClient(app) as client:
        assert app.state.database is database
        assert client.get("/api/v1/health").status_code == 200  # no database needed
        assert database.disposed == 0
    assert database.disposed == 1
