"""Alembic environment: connects with the application's own DatabaseSettings.

Target selection (highest priority first):
  1. a Connection passed in ``config.attributes["connection"]`` (used by tests);
  2. the AETHER_DB_* environment variables, exactly as the API reads them.

PostgreSQL has transactional DDL, so a failed migration rolls back completely.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, text

from aether_api.database import Database, DatabaseSettings
from aether_api.db_models import Base

config = context.config
target_metadata = Base.metadata

# CLI runs log via alembic.ini; callers that pass a connection keep their own logging.
if config.config_file_name is not None and "connection" not in config.attributes:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Fail fast instead of queueing behind a long-running query's locks.
_LOCK_TIMEOUT = "5s"


def _configure_and_run(connection: Connection) -> None:
    connection.execute(text(f"SET lock_timeout = '{_LOCK_TIMEOUT}'"))
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    """``alembic upgrade --sql``: renders SQL for review; never connects."""
    url = DatabaseSettings.from_environment().url().set(password=None)
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied = config.attributes.get("connection")
    if supplied is not None:
        _configure_and_run(supplied)
        return
    database = Database.from_environment()
    try:
        with database.engine.connect() as connection:
            _configure_and_run(connection)
            connection.commit()
    finally:
        database.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
