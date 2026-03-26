"""Pydantic schemas for API responses."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class HealthCheckItem(BaseModel):
    """One startup/runtime health check result."""

    name: str
    status: Literal["pass", "warn", "fail"]
    detail: str
    critical: bool = False


class StartupHealthSummary(BaseModel):
    """Structured startup health snapshot."""

    checked_at: datetime
    checks: list[HealthCheckItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class HealthDiskPayload(BaseModel):
    """Current disk usage for the output volume."""

    total_gb: int
    free_gb: int
    percent_used: float


class HealthCheckResponse(BaseModel):
    """Health check response payload."""

    status: str
    version: str
    startup: StartupHealthSummary | None = None
    disk: HealthDiskPayload | None = None
