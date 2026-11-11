"""Structured logging: one JSON object per line on stderr.

Machine-parseable logs make it possible to filter by request id, status or
latency later without regex scraping. Context is attached with
``logger.info("...", extra={"fields": {...}})``.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            entry.update(fields)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        # default=str keeps logging from ever raising on an unexpected value type.
        return json.dumps(entry, default=str)


def configure_logging(level: str) -> None:
    """Routes the `aether` logger tree to stderr as JSON. Idempotent."""
    logger = logging.getLogger("aether")
    logger.setLevel(level)
    if not any(getattr(h, "_aether_json", False) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonFormatter())
        handler._aether_json = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.propagate = False
