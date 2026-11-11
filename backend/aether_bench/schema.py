"""Typed, validated view of the C++ engine's ``aether.cpu_profile.v2`` JSON.

The C++ side (cpp/src/profiling.cpp) is the source of truth for the schema.
Validation here is strict about everything the schema guarantees: required
keys, types, ranges, identifier formats and internal consistency (each
record's statistics are recomputed from its raw samples). Unknown extra keys
are ignored so additive schema changes do not break older clients.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, TypeVar

from .errors import BenchmarkOutputError

SCHEMA_ID = "aether.cpu_profile.v2"
SYSTEM_INFO_SCHEMA_ID = "aether.system_info.v1"

_UUID_V4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
_UTC_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
# Statistics are recomputed in Python and must agree with the C++ values.
# Both use IEEE doubles; observed differences are ~1e-16, so 1e-9 only
# tolerates summation-order rounding, never a wrong formula.
_STATISTICS_RELATIVE_TOLERANCE = 1e-9

T = TypeVar("T")


# ---------- data model ----------


@dataclass(frozen=True)
class CoreType:
    name: str
    physical_cpus: Optional[int]


@dataclass(frozen=True)
class SystemInfo:
    os_family: str
    os_version: Optional[str]
    os_build: Optional[str]
    kernel_release: Optional[str]
    architecture: str
    process_translated: Optional[bool]
    cpu_model: Optional[str]
    logical_cpus: Optional[int]
    physical_cpus: Optional[int]
    core_types: tuple[CoreType, ...]
    cache_line_bytes: Optional[int]
    memory_bytes: Optional[int]


@dataclass(frozen=True)
class BuildInfo:
    compiler_id: str
    compiler_version: str
    cxx_standard: str
    build_type: str
    cmake_cxx_flags: Optional[str]
    assertions_enabled: bool


@dataclass(frozen=True)
class BenchmarkConfig:
    warmup_runs: int
    measured_runs: int
    min_warmup_ms: float
    min_sample_ms: float


@dataclass(frozen=True)
class Statistics:
    count: int
    min: float
    max: float
    mean: float
    median: float
    stddev: Optional[float]  # None when count == 1 (undefined)
    p95: float


@dataclass(frozen=True)
class BenchmarkRecord:
    experiment_id: str
    workload: str
    input_size: int
    thread_count: int
    timestamp_utc: str
    config: BenchmarkConfig
    warmup_runs_executed: int
    iterations_per_sample: int
    statistics: Statistics
    samples_ms: tuple[float, ...]


@dataclass(frozen=True)
class SystemReport:
    """Host and build metadata from ``aether_system_info`` (no benchmarks)."""

    schema: str
    generated_at_utc: str
    system: SystemInfo
    build: BuildInfo


@dataclass(frozen=True)
class ProfileReport:
    schema: str
    run_id: str
    generated_at_utc: str
    time_unit: str
    system: SystemInfo
    build: BuildInfo
    records: tuple[BenchmarkRecord, ...]


# ---------- field readers: each failure names the exact JSON path ----------


def _fail(path: str, message: str) -> BenchmarkOutputError:
    return BenchmarkOutputError(f"invalid report at {path}: {message}")


def _type_name(value: Any) -> str:
    return "null" if value is None else type(value).__name__


def _object(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise _fail(path, f"expected object, got {_type_name(value)}")
    return value


def _field(obj: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in obj:
        raise _fail(f"{path}.{key}", "missing required field")
    return obj[key]


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise _fail(path, f"expected string, got {_type_name(value)}")
    return value


def _non_empty_string(value: Any, path: str) -> str:
    text = _string(value, path)
    if not text:
        raise _fail(path, "must not be empty")
    return text


def _array(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise _fail(path, f"expected array, got {_type_name(value)}")
    return value


def _integer(value: Any, path: str, minimum: int = 0) -> int:
    # bool is a subclass of int in Python; JSON true/false must not pass as numbers.
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(path, f"expected integer, got {_type_name(value)}")
    if value < minimum:
        raise _fail(path, f"must be >= {minimum}, got {value}")
    return value


def _positive_integer(value: Any, path: str) -> int:
    return _integer(value, path, minimum=1)


def _number(value: Any, path: str, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(path, f"expected number, got {_type_name(value)}")
    number = float(value)
    if not math.isfinite(number) or number < minimum:
        raise _fail(path, f"must be a finite number >= {minimum:g}, got {value!r}")
    return number


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise _fail(path, f"expected boolean, got {_type_name(value)}")
    return value


def _nullable(reader: Callable[[Any, str], T]) -> Callable[[Any, str], Optional[T]]:
    def read(value: Any, path: str) -> Optional[T]:
        return None if value is None else reader(value, path)

    return read


def _matching(pattern: re.Pattern[str], description: str) -> Callable[[Any, str], str]:
    def read(value: Any, path: str) -> str:
        text = _string(value, path)
        if not pattern.fullmatch(text):
            raise _fail(path, f"expected {description}, got {text!r}")
        return text

    return read


_uuid = _matching(_UUID_V4, "a lowercase UUID v4")
_timestamp = _matching(_UTC_TIMESTAMP, "a UTC timestamp YYYY-MM-DDTHH:MM:SSZ")


# ---------- section parsers ----------


def _parse_system(obj: Mapping[str, Any], path: str) -> SystemInfo:
    def get(key: str, reader: Callable[[Any, str], T]) -> T:
        return reader(_field(obj, key, path), f"{path}.{key}")

    core_types = []
    for index, raw in enumerate(get("core_types", _array)):
        core_path = f"{path}.core_types[{index}]"
        core = _object(raw, core_path)
        core_types.append(
            CoreType(
                name=_non_empty_string(_field(core, "name", core_path), f"{core_path}.name"),
                physical_cpus=_nullable(_positive_integer)(
                    _field(core, "physical_cpus", core_path), f"{core_path}.physical_cpus"
                ),
            )
        )
    return SystemInfo(
        os_family=get("os_family", _non_empty_string),
        os_version=get("os_version", _nullable(_non_empty_string)),
        os_build=get("os_build", _nullable(_non_empty_string)),
        kernel_release=get("kernel_release", _nullable(_non_empty_string)),
        architecture=get("architecture", _non_empty_string),
        process_translated=get("process_translated", _nullable(_boolean)),
        cpu_model=get("cpu_model", _nullable(_non_empty_string)),
        logical_cpus=get("logical_cpus", _nullable(_positive_integer)),
        physical_cpus=get("physical_cpus", _nullable(_positive_integer)),
        core_types=tuple(core_types),
        cache_line_bytes=get("cache_line_bytes", _nullable(_positive_integer)),
        memory_bytes=get("memory_bytes", _nullable(_positive_integer)),
    )


def _parse_build(obj: Mapping[str, Any], path: str) -> BuildInfo:
    def get(key: str, reader: Callable[[Any, str], T]) -> T:
        return reader(_field(obj, key, path), f"{path}.{key}")

    return BuildInfo(
        compiler_id=get("compiler_id", _non_empty_string),
        compiler_version=get("compiler_version", _non_empty_string),
        cxx_standard=get("cxx_standard", _non_empty_string),
        build_type=get("build_type", _non_empty_string),
        cmake_cxx_flags=get("cmake_cxx_flags", _nullable(_string)),
        assertions_enabled=get("assertions_enabled", _boolean),
    )


def _check_statistics(stats: Statistics, samples: tuple[float, ...], path: str) -> None:
    """Recomputes every statistic from the raw samples (type-7 p95, n-1 stddev)."""
    expected = {
        "min": min(samples),
        "max": max(samples),
        "mean": statistics.fmean(samples),
        "median": statistics.median(samples),
        "p95": statistics.quantiles(samples, n=20, method="inclusive")[18]
        if len(samples) > 1
        else samples[0],
    }
    if len(samples) > 1:
        expected["stddev"] = statistics.stdev(samples)
    elif stats.stddev is not None:
        raise _fail(f"{path}.stddev", "must be null for a single sample")

    for name, reference in expected.items():
        reported = getattr(stats, name)
        if reported is None or not math.isclose(
            reported, reference, rel_tol=_STATISTICS_RELATIVE_TOLERANCE, abs_tol=1e-15
        ):
            raise _fail(
                f"{path}.{name}", f"{reported!r} does not match the samples (expected {reference!r})"
            )


def _parse_record(obj: Mapping[str, Any], path: str) -> BenchmarkRecord:
    def get(key: str, reader: Callable[[Any, str], T], parent: Mapping[str, Any] = obj,
            parent_path: str = path) -> T:
        return reader(_field(parent, key, parent_path), f"{parent_path}.{key}")

    config_path = f"{path}.config"
    config_obj = _object(_field(obj, "config", path), config_path)
    config = BenchmarkConfig(
        warmup_runs=get("warmup_runs", _integer, config_obj, config_path),
        measured_runs=get("measured_runs", _positive_integer, config_obj, config_path),
        min_warmup_ms=get("min_warmup_ms", _number, config_obj, config_path),
        min_sample_ms=get("min_sample_ms", _number, config_obj, config_path),
    )

    stats_path = f"{path}.statistics"
    stats_obj = _object(_field(obj, "statistics", path), stats_path)
    stats = Statistics(
        count=get("count", _positive_integer, stats_obj, stats_path),
        min=get("min", _number, stats_obj, stats_path),
        max=get("max", _number, stats_obj, stats_path),
        mean=get("mean", _number, stats_obj, stats_path),
        median=get("median", _number, stats_obj, stats_path),
        stddev=get("stddev", _nullable(_number), stats_obj, stats_path),
        p95=get("p95", _number, stats_obj, stats_path),
    )

    samples = tuple(
        _number(s, f"{path}.samples[{i}]") for i, s in enumerate(get("samples", _array))
    )
    if len(samples) != stats.count or len(samples) != config.measured_runs:
        raise _fail(
            f"{path}.samples",
            f"has {len(samples)} entries but statistics.count={stats.count} and "
            f"config.measured_runs={config.measured_runs}",
        )
    _check_statistics(stats, samples, stats_path)

    warmup_runs_executed = get("warmup_runs_executed", _integer)
    if warmup_runs_executed < config.warmup_runs:
        raise _fail(f"{path}.warmup_runs_executed",
                    f"{warmup_runs_executed} is below the configured minimum {config.warmup_runs}")

    return BenchmarkRecord(
        experiment_id=get("experiment_id", _uuid),
        workload=get("workload", _non_empty_string),
        input_size=get("input_size", _integer),
        thread_count=get("thread_count", _positive_integer),
        timestamp_utc=get("timestamp_utc", _timestamp),
        config=config,
        warmup_runs_executed=warmup_runs_executed,
        iterations_per_sample=get("iterations_per_sample", _positive_integer),
        statistics=stats,
        samples_ms=samples,
    )


# ---------- public API ----------


def _reject_constant(name: str) -> Any:
    # Python's json accepts NaN/Infinity by default; RFC 8259 JSON does not.
    raise ValueError(f"non-standard JSON constant {name}")


def _load_document(text: str, expected_schema: str) -> tuple[Mapping[str, Any], str]:
    """Strict JSON parse plus schema-id check; returns (root object, schema)."""
    if not text.strip():
        raise BenchmarkOutputError("report is empty")
    try:
        document = json.loads(text, parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError) as error:  # JSONDecodeError covers truncation
        raise BenchmarkOutputError(f"report is not valid JSON: {error}") from error

    root = _object(document, "$")
    schema = _string(_field(root, "schema", "$"), "$.schema")
    if schema != expected_schema:
        raise _fail("$.schema", f"unsupported schema {schema!r}, expected {expected_schema!r}")
    return root, schema


def parse_system_report(text: str) -> SystemReport:
    """Parses ``aether.system_info.v1``. Raises BenchmarkOutputError on any problem."""
    root, schema = _load_document(text, SYSTEM_INFO_SCHEMA_ID)
    return SystemReport(
        schema=schema,
        generated_at_utc=_timestamp(_field(root, "generated_at_utc", "$"), "$.generated_at_utc"),
        system=_parse_system(_object(_field(root, "system", "$"), "$.system"), "$.system"),
        build=_parse_build(_object(_field(root, "build", "$"), "$.build"), "$.build"),
    )


def parse_report(text: str) -> ProfileReport:
    """Parses and validates a report. Raises BenchmarkOutputError on any problem."""
    root, schema = _load_document(text, SCHEMA_ID)
    time_unit = _string(_field(root, "time_unit", "$"), "$.time_unit")
    if time_unit != "ms":
        raise _fail("$.time_unit", f"expected 'ms', got {time_unit!r}")

    environment = _object(_field(root, "environment", "$"), "$.environment")
    results = _array(_field(root, "results", "$"), "$.results")

    records = tuple(
        _parse_record(_object(raw, f"$.results[{i}]"), f"$.results[{i}]")
        for i, raw in enumerate(results)
    )
    run_id = _uuid(_field(root, "run_id", "$"), "$.run_id")
    ids = [record.experiment_id for record in records]
    if len(set(ids)) != len(ids) or run_id in ids:
        raise _fail("$.results", "experiment ids (and the run id) must all be distinct")

    return ProfileReport(
        schema=schema,
        run_id=run_id,
        generated_at_utc=_timestamp(_field(root, "generated_at_utc", "$"), "$.generated_at_utc"),
        time_unit=time_unit,
        system=_parse_system(
            _object(_field(environment, "system", "$.environment"), "$.environment.system"),
            "$.environment.system",
        ),
        build=_parse_build(
            _object(_field(environment, "build", "$.environment"), "$.environment.build"),
            "$.environment.build",
        ),
        records=records,
    )
