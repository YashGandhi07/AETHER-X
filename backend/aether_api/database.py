"""PostgreSQL connection layer: settings, engine, sessions and a connectivity check.

Configuration comes from environment variables; nothing credential-bearing is
hardcoded or stored in the repository:

    AETHER_DB_HOST               default localhost
    AETHER_DB_PORT               default 5432
    AETHER_DB_NAME               default aether_x_dev
    AETHER_DB_USER               default aether_x_app
    AETHER_DB_PASSWORD           optional; when unset, libpq reads ~/.pgpass
    AETHER_DB_CONNECT_TIMEOUT_S  seconds to establish a connection (default 5)
    AETHER_DB_STATEMENT_TIMEOUT_MS  server-side limit per statement (default 10000)
    AETHER_DB_POOL_TIMEOUT_S     seconds to wait for a free pooled connection (default 5)

The engine connects lazily: creating a ``Database`` never touches the server,
so the API starts (and its tests run) without PostgreSQL. Failures surface as
``DatabaseUnavailableError`` whose message never contains the password.
"""

from __future__ import annotations

import math
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Mapping, Optional

from sqlalchemy import URL, Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from .config import ConfigurationError

APPLICATION_NAME = "aether-x-api"
# Unquoted PostgreSQL identifiers; anything else is almost certainly a typo.
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")
_HOSTNAME = re.compile(r"[A-Za-z0-9.:-]{1,253}")


class DatabaseUnavailableError(RuntimeError):
    """The database could not be reached or rejected the connection."""


@dataclass(frozen=True)
class DatabaseSettings:
    host: str = "localhost"
    port: int = 5432
    name: str = "aether_x_dev"
    user: str = "aether_x_app"
    # repr=False keeps the password out of logs, tracebacks and debug output.
    password: Optional[str] = field(default=None, repr=False)
    connect_timeout_s: int = 5
    statement_timeout_ms: int = 10_000
    pool_timeout_s: float = 5.0

    def __post_init__(self) -> None:
        if not _HOSTNAME.fullmatch(self.host):
            raise ConfigurationError(f"invalid database host {self.host!r}")
        if not 1 <= self.port <= 65535:
            raise ConfigurationError(f"database port must be in [1, 65535], got {self.port}")
        for label, value in (("database name", self.name), ("database user", self.user)):
            if not _IDENTIFIER.fullmatch(value):
                raise ConfigurationError(f"invalid {label} {value!r}")
        if self.password == "":
            raise ConfigurationError("AETHER_DB_PASSWORD is set but empty; unset it to use ~/.pgpass")
        # libpq treats connect_timeout below 2 s as 2 s, so reject values it would silently change.
        if not 2 <= self.connect_timeout_s <= 60:
            raise ConfigurationError(f"connect timeout must be in [2, 60] s, got {self.connect_timeout_s}")
        if not 1 <= self.statement_timeout_ms <= 600_000:
            raise ConfigurationError(
                f"statement timeout must be in [1, 600000] ms, got {self.statement_timeout_ms}")
        if not (math.isfinite(self.pool_timeout_s) and 0 < self.pool_timeout_s <= 60):
            raise ConfigurationError(f"pool timeout must be in (0, 60] s, got {self.pool_timeout_s}")

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] = os.environ) -> DatabaseSettings:
        values: dict[str, object] = {}
        for key, attr in (("AETHER_DB_HOST", "host"), ("AETHER_DB_NAME", "name"),
                          ("AETHER_DB_USER", "user")):
            if (value := environ.get(key)) is not None:
                values[attr] = value.strip()
        if (password := environ.get("AETHER_DB_PASSWORD")) is not None:
            values["password"] = password  # not stripped: spaces may be part of it
        for key, attr, kind in (("AETHER_DB_PORT", "port", int),
                                ("AETHER_DB_CONNECT_TIMEOUT_S", "connect_timeout_s", int),
                                ("AETHER_DB_STATEMENT_TIMEOUT_MS", "statement_timeout_ms", int),
                                ("AETHER_DB_POOL_TIMEOUT_S", "pool_timeout_s", float)):
            if (raw := environ.get(key)) is not None:
                try:
                    values[attr] = kind(raw)
                except ValueError:
                    raise ConfigurationError(f"{key} is not a valid {kind.__name__}: {raw!r}") from None
        return cls(**values)  # type: ignore[arg-type]

    def url(self) -> URL:
        """SQLAlchemy URL; its str() and repr() mask the password as ***."""
        return URL.create("postgresql+psycopg", username=self.user, password=self.password,
                          host=self.host, port=self.port, database=self.name)

    def describe(self) -> str:
        """Credential-free target description for logs and error messages."""
        return f"{self.user}@{self.host}:{self.port}/{self.name}"


class Database:
    """Owns one connection pool. Create one per process and ``dispose()`` it on shutdown."""

    def __init__(self, settings: DatabaseSettings, engine: Optional[Engine] = None) -> None:
        self.settings = settings
        self.engine = engine or create_engine(
            settings.url(),
            # Validates a pooled connection before use, so a server restart costs
            # one quick reconnect instead of an error in the next request.
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            pool_timeout=settings.pool_timeout_s,
            pool_recycle=1800,
            connect_args={
                "connect_timeout": settings.connect_timeout_s,
                "application_name": APPLICATION_NAME,
                # Enforced by the server, so a runaway query cannot hold a connection forever.
                "options": f"-c statement_timeout={settings.statement_timeout_ms}",
            },
        )
        self._sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] = os.environ) -> Database:
        return cls(DatabaseSettings.from_environment(environ))

    @contextmanager
    def session(self) -> Iterator[Session]:
        """One unit of work: commits on success, rolls back on any exception."""
        session = self._sessions()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def check_connection(self) -> float:
        """Runs ``SELECT 1`` and returns the round-trip time in milliseconds.

        Raises DatabaseUnavailableError with a password-free message on failure.
        """
        started = time.perf_counter()
        try:
            with self.engine.connect() as connection:
                value = connection.execute(text("SELECT 1")).scalar_one()
        except SQLAlchemyError as exc:
            # `from None`: the driver error is summarized (and redacted) here
            # rather than chained, so a traceback cannot carry more than this.
            raise DatabaseUnavailableError(
                f"cannot reach PostgreSQL at {self.settings.describe()}: {self.safe_reason(exc)}"
            ) from None
        if value != 1:
            raise DatabaseUnavailableError(f"unexpected SELECT 1 result: {value!r}")
        return (time.perf_counter() - started) * 1000

    def dispose(self) -> None:
        self.engine.dispose()

    def safe_reason(self, exc: SQLAlchemyError) -> str:
        # The driver's own message (not SQLAlchemy's wrapper, which may append
        # SQL and a docs link), first line only, with the password removed in
        # case a server or driver ever echoes it.
        driver_error = getattr(exc, "orig", None) or exc
        lines = str(driver_error).strip().splitlines()
        reason = lines[0] if lines else type(driver_error).__name__
        if self.settings.password:
            reason = reason.replace(self.settings.password, "***")
        return reason
