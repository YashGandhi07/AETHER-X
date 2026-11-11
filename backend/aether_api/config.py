"""Service configuration from environment variables, validated at startup.

    AETHER_CORS_ORIGINS        comma-separated origins allowed to call the API
                               (default: the local Next.js dev server)
    AETHER_ENGINE_TIMEOUT_S    seconds before an engine call is killed (default 10)
    AETHER_LOG_LEVEL           DEBUG, INFO, WARNING or ERROR (default INFO)
    AETHER_ENABLE_BENCHMARKS   1/0: allow POST /api/v1/benchmarks/run from
                               localhost (default 1; never reachable remotely)
    AETHER_BENCHMARK_TIMEOUT_S seconds before a benchmark run is killed (default 60)

Invalid values fail fast with ConfigurationError instead of starting a
misconfigured service.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from typing import Mapping

DEFAULT_CORS_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")
_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})
# scheme://host[:port] with nothing after it; browsers send origins in exactly this form.
_ORIGIN = re.compile(r"https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?")


class ConfigurationError(ValueError):
    """An environment variable has an invalid value."""


@dataclass(frozen=True)
class Settings:
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    engine_timeout_s: float = 10.0
    log_level: str = "INFO"
    benchmarks_enabled: bool = True
    # The slowest request the API allows (row_sums_column_major, n = 4096,
    # 20 warm-up + 100 measured runs) took 5.32 s on an Apple M5 (Section 3.2);
    # 60 s leaves >10x headroom for slower machines.
    benchmark_timeout_s: float = 60.0

    def __post_init__(self) -> None:
        for origin in self.cors_origins:
            if origin == "*":
                raise ConfigurationError("wildcard CORS origin '*' is not allowed; list origins explicitly")
            if not _ORIGIN.fullmatch(origin):
                raise ConfigurationError(f"invalid CORS origin {origin!r}; expected scheme://host[:port]")
        if not (math.isfinite(self.engine_timeout_s) and 0 < self.engine_timeout_s <= 300):
            raise ConfigurationError(f"engine timeout must be in (0, 300] s, got {self.engine_timeout_s}")
        if self.log_level not in _LOG_LEVELS:
            raise ConfigurationError(f"log level must be one of {sorted(_LOG_LEVELS)}, got {self.log_level!r}")
        if not (math.isfinite(self.benchmark_timeout_s) and 0 < self.benchmark_timeout_s <= 600):
            raise ConfigurationError(
                f"benchmark timeout must be in (0, 600] s, got {self.benchmark_timeout_s}")

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] = os.environ) -> Settings:
        values: dict[str, object] = {}
        if (origins := environ.get("AETHER_CORS_ORIGINS")) is not None:
            values["cors_origins"] = tuple(o.strip() for o in origins.split(",") if o.strip())
        if (timeout := environ.get("AETHER_ENGINE_TIMEOUT_S")) is not None:
            try:
                values["engine_timeout_s"] = float(timeout)
            except ValueError:
                raise ConfigurationError(f"AETHER_ENGINE_TIMEOUT_S is not a number: {timeout!r}") from None
        if (level := environ.get("AETHER_LOG_LEVEL")) is not None:
            values["log_level"] = level.strip().upper()
        if (enabled := environ.get("AETHER_ENABLE_BENCHMARKS")) is not None:
            flag = enabled.strip().lower()
            if flag not in {"1", "0", "true", "false"}:
                raise ConfigurationError(f"AETHER_ENABLE_BENCHMARKS must be 1/0/true/false, got {enabled!r}")
            values["benchmarks_enabled"] = flag in {"1", "true"}
        if (bench_timeout := environ.get("AETHER_BENCHMARK_TIMEOUT_S")) is not None:
            try:
                values["benchmark_timeout_s"] = float(bench_timeout)
            except ValueError:
                raise ConfigurationError(
                    f"AETHER_BENCHMARK_TIMEOUT_S is not a number: {bench_timeout!r}") from None
        return cls(**values)  # type: ignore[arg-type]
