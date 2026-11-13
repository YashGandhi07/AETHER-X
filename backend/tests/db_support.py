"""Shared access to the PostgreSQL TEST database (never the development one).

Target: the AETHER_DB_* settings with the name replaced by AETHER_TEST_DB_NAME
(default aether_x_test). The name must end in ``_test`` because tests rebuild
its schema and delete its rows. Tests that need it skip when it is
unreachable; AETHER_REQUIRE_DATABASE=1 turns that skip into a failure.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, text

from aether_api.database import Database, DatabaseSettings, DatabaseUnavailableError

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_DB_NAME = os.environ.get("AETHER_TEST_DB_NAME", "aether_x_test")
REQUIRE_DATABASE = os.environ.get("AETHER_REQUIRE_DATABASE") == "1"
DATA_TABLES = ("timing_samples", "experiments", "benchmark_runs", "environments")


def db_test_settings() -> DatabaseSettings:
    if not TEST_DB_NAME.endswith("_test"):
        raise RuntimeError(f"refusing to modify {TEST_DB_NAME!r}: name must end in '_test'")
    return DatabaseSettings.from_environment({**os.environ, "AETHER_DB_NAME": TEST_DB_NAME})


@functools.cache
def unavailable_reason() -> str | None:
    db = Database(db_test_settings())
    try:
        db.check_connection()
        return None
    except DatabaseUnavailableError as exc:
        return str(exc)
    finally:
        db.dispose()


def skip_unless_available() -> None:
    if (reason := unavailable_reason()) is not None and not REQUIRE_DATABASE:
        pytest.skip(f"test database not reachable: {reason}")


def alembic_config(connection: Connection) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


def rebuild_schema(db: Database) -> None:
    """Drops everything and migrates to head: each module starts from a known schema."""
    with db.engine.begin() as connection:
        command.downgrade(alembic_config(connection), "base")
        command.upgrade(alembic_config(connection), "head")


def delete_all_rows(db: Database) -> None:
    with db.engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {', '.join(DATA_TABLES)} RESTART IDENTITY"))
