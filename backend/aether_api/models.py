"""Typed response models: the public contract of the HTTP API.

Every field is declared explicitly, so only intended metadata can ever be
serialized; anything else the engine reports is dropped, not forwarded.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Response(BaseModel):
    model_config = ConfigDict(frozen=True)


class HealthResponse(_Response):
    status: Literal["ok", "degraded"] = Field(
        description="'degraded' when the C++ engine tools are not built on this server")
    service: str = Field(examples=["aether-x-api"])
    version: str = Field(examples=["0.1.0"])
    engine_available: bool = Field(description="whether aether_system_info is built and executable")


class CoreTypeModel(_Response):
    name: str = Field(examples=["Super", "Efficiency"])
    physical_cpus: Optional[int]


class HostModel(_Response):
    """Hardware and OS facts. null means the platform could not report the value."""

    os_family: str
    os_version: Optional[str]
    os_build: Optional[str]
    kernel_release: Optional[str]
    architecture: str
    process_translated: Optional[bool] = Field(description="running under Rosetta 2 translation")
    cpu_model: Optional[str]
    logical_cpus: Optional[int]
    physical_cpus: Optional[int]
    core_types: list[CoreTypeModel]
    cache_line_bytes: Optional[int]
    memory_bytes: Optional[int]


class EngineBuildModel(_Response):
    """How the C++ engine binary was compiled."""

    compiler_id: str
    compiler_version: str
    cxx_standard: str
    build_type: str
    cmake_cxx_flags: Optional[str]
    assertions_enabled: bool


class SystemResponse(_Response):
    collected_at_utc: str = Field(description="when the engine collected this metadata (ISO 8601 UTC)")
    host: HostModel
    engine_build: EngineBuildModel


class ErrorBody(_Response):
    code: str = Field(examples=["engine_unavailable"])
    message: str
    request_id: Optional[str] = None
    details: Optional[list[dict[str, object]]] = None


class ErrorResponse(_Response):
    error: ErrorBody
