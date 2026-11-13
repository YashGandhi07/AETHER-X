"""Integration tests against the REAL local PostgreSQL (AETHER_DB_* settings,
password from AETHER_DB_PASSWORD or ~/.pgpass). Read-only: no tables are created.

Skipped when the database is unreachable; set AETHER_REQUIRE_DATABASE=1 to
turn that skip into a failure.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from aether_api.database import Database, DatabaseSettings, DatabaseUnavailableError


def _unavailable_reason() -> str | None:
    db = Database.from_environment()
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
    reason=f"PostgreSQL not reachable: {_REASON}")


@pytest.fixture
def db() -> Iterator[Database]:
    database = Database.from_environment()
    yield database
    database.dispose()


def test_select_1_round_trip(db: Database) -> None:
    elapsed_ms = db.check_connection()
    assert 0 <= elapsed_ms < db.settings.connect_timeout_s * 1000


def test_connects_as_configured_role_to_configured_database(db: Database) -> None:
    with db.session() as session:
        row = session.execute(text(
            "SELECT current_database(), current_user, "
            "current_setting('application_name'), "
            "(SELECT setting::int FROM pg_settings WHERE name = 'statement_timeout'), "
            "current_setting('server_version_num')::int")).one()
    assert row[0] == db.settings.name
    assert row[1] == db.settings.user
    assert row[2] == "aether-x-api"
    assert row[3] == db.settings.statement_timeout_ms  # raw unit is ms
    assert row[4] >= 180000  # PostgreSQL 18


def test_application_role_is_not_privileged(db: Database) -> None:
    with db.session() as session:
        superuser, createdb, createrole = session.execute(text(
            "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = current_user")).one()
    assert (superuser, createdb, createrole) == (False, False, False)


def test_statement_timeout_is_enforced_by_server() -> None:
    settings = DatabaseSettings.from_environment({**os.environ, "AETHER_DB_STATEMENT_TIMEOUT_MS": "200"})
    db = Database(settings)
    try:
        with pytest.raises(OperationalError, match="statement timeout"):
            with db.session() as session:
                session.execute(text("SELECT pg_sleep(2)"))
        db.check_connection()  # the pool recovers after the cancelled statement
    finally:
        db.dispose()


def test_wrong_password_is_rejected_without_leaking_it() -> None:
    wrong = "definitely-wrong-password-123"
    db = Database(DatabaseSettings.from_environment({**os.environ, "AETHER_DB_PASSWORD": wrong}))
    try:
        with pytest.raises(DatabaseUnavailableError) as info:
            db.check_connection()
    finally:
        db.dispose()
    assert "password authentication failed" in str(info.value)
    assert wrong not in str(info.value)
