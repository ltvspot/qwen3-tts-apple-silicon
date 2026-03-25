"""HTTP middleware for request tracing, access logging, and security headers."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import Request, Response
from starlette.middleware.base import RequestResponseEndpoint

from src.api.error_handlers import global_exception_handler

api_logger = logging.getLogger("api_requests")

SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-XSS-Protection": "1; mode=block",
}


async def request_context_middleware(request: Request, call_next: RequestResponseEndpoint) -> Response:
    """Attach a request ID, emit access logs, and set response security headers."""

    started = time.perf_counter()
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    request.state.request_id = request_id

    try:
        response = await call_next(request)
    except Exception as exc:
        elapsed = time.perf_counter() - started
        api_logger.error("[%s] %s %s failed in %.3fs", request_id, request.method, request.url.path, elapsed)
        response = await global_exception_handler(request, exc)

    elapsed = time.perf_counter() - started
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time"] = f"{elapsed:.3f}"
    for header_name, header_value in SECURITY_HEADERS.items():
        response.headers.setdefault(header_name, header_value)

    api_logger.info(
        "[%s] %s %s %s (%.3fs)",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        elapsed,
    )
    return response
