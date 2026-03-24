"""Pydantic schemas for API responses."""

from pydantic import BaseModel


class HealthCheckResponse(BaseModel):
    """Health check response payload."""

    status: str
    version: str
