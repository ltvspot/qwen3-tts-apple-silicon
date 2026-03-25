"""Centralized FastAPI exception handlers."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return a sanitized 500 response for unexpected errors."""

    logger.exception("Unhandled exception for %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": "An unexpected error occurred. Please try again.",
            "request_id": _request_id(request),
        },
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return structured field-level validation errors."""

    logger.warning("Validation error for %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=422,
        content={
            "error": "Validation error",
            "detail": "One or more request fields are invalid.",
            "request_id": _request_id(request),
            "fields": [_serialize_validation_error(error) for error in exc.errors()],
        },
    )


def register_error_handlers(app: FastAPI) -> None:
    """Register the application-wide exception handlers on a FastAPI app."""

    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, global_exception_handler)


def _request_id(request: Request) -> str:
    """Resolve the current request ID for debugging."""

    return getattr(request.state, "request_id", request.headers.get("X-Request-ID", "unknown"))


def _serialize_validation_error(error: dict[str, Any]) -> dict[str, str]:
    """Convert a raw FastAPI validation error into frontend-friendly shape."""

    location = error.get("loc", [])
    if len(location) >= 2:
        field_name = str(location[1])
    elif location:
        field_name = str(location[-1])
    else:
        field_name = "unknown"
    return {
        "field": field_name,
        "message": str(error.get("msg", "Invalid value")),
    }
