"""Tests for API middleware and centralized exception handlers."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from src.api.error_handlers import register_error_handlers
from src.api.middleware import request_context_middleware


class SamplePayload(BaseModel):
    """Simple request model used to trigger validation errors."""

    count: int


def create_test_app() -> FastAPI:
    """Create an isolated FastAPI app with the production hardening stack."""

    app = FastAPI()
    app.middleware("http")(request_context_middleware)
    register_error_handlers(app)

    @app.get("/boom")
    async def boom() -> dict[str, bool]:
        raise RuntimeError("sensitive stack detail")

    @app.post("/payload")
    async def create_payload(payload: SamplePayload) -> dict[str, int]:
        return payload.model_dump()

    return app


def test_global_exception_handler_returns_sanitized_payload_and_headers() -> None:
    """Unhandled errors should be sanitized while preserving request tracing headers."""

    with TestClient(create_test_app(), raise_server_exceptions=False) as client:
        response = client.get("/boom", headers={"X-Request-ID": "req-123"})

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "req-123"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "same-origin"
    assert "X-Process-Time" in response.headers

    payload = response.json()
    assert payload == {
        "error": "Internal server error",
        "detail": "An unexpected error occurred. Please try again.",
        "request_id": "req-123",
    }


def test_validation_exception_handler_returns_field_errors_and_request_id() -> None:
    """Validation failures should include user-facing field information."""

    with TestClient(create_test_app(), raise_server_exceptions=False) as client:
        response = client.post("/payload", json={"count": "oops"}, headers={"X-Request-ID": "req-422"})

    assert response.status_code == 422
    assert response.headers["X-Request-ID"] == "req-422"

    payload = response.json()
    assert payload["error"] == "Validation error"
    assert payload["detail"] == "One or more request fields are invalid."
    assert payload["request_id"] == "req-422"
    assert len(payload["fields"]) == 1
    assert payload["fields"][0]["field"] == "count"
    assert "integer" in payload["fields"][0]["message"].lower()
