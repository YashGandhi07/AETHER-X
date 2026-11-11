"""Versioned API routes. Every v1 endpoint is mounted under /api/v1."""

from fastapi import APIRouter

from . import health, system

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(health.router)
api_v1.include_router(system.router)
