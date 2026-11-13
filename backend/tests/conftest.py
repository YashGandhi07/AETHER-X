"""Fixtures for tests that commit to the PostgreSQL TEST database (see db_support)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from aether_api.database import Database

from .db_support import db_test_settings, delete_all_rows, rebuild_schema, skip_unless_available


@pytest.fixture(scope="module")
def committed_test_db() -> Iterator[Database]:
    """The test database at the latest migration, for tests that COMMIT for real
    (deferred constraints are only checked at a real COMMIT)."""
    skip_unless_available()
    db = Database(db_test_settings())
    rebuild_schema(db)
    yield db
    db.dispose()


@pytest.fixture
def clean_test_db(committed_test_db: Database) -> Database:
    """The committed test database with every data row removed."""
    delete_all_rows(committed_test_db)
    return committed_test_db
