"""FastAPI application entry point."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src import __version__
from src.api.batch_routes import router as batch_router
from src.api.error_handlers import register_error_handlers
from src.api.export_routes import router as export_router
from src.api.generation import router as generation_router
from src.api.generation_runtime import shutdown_generation_runtime
from src.api.middleware import request_context_middleware
from src.api.monitoring_routes import router as monitoring_router
from src.api.qa_routes import router as qa_router
from src.api.queue_routes import router as queue_router
from src.api.settings_routes import router as settings_router
from src.api.voice_lab import release_engine, router as voice_lab_router
from src.api.routes import router as api_router
from src.api.schemas import HealthCheckResponse, StartupHealthSummary
from src.config import get_application_settings, reset_settings_manager, settings
from src.database import init_db, utc_now
from src.health_checks import run_all_health_checks
from src.logging_config import configure_logging

configure_logging(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)

def ensure_runtime_directories() -> None:
    """Create mutable runtime directories and log missing source paths."""

    for path in (settings.OUTPUTS_PATH, settings.VOICES_PATH):
        Path(path).mkdir(parents=True, exist_ok=True)
        logger.info("Ensured runtime directory exists: %s", path)

    for path in (settings.FORMATTED_MANUSCRIPTS_PATH, settings.MODELS_PATH):
        if not Path(path).exists():
            logger.warning("Configured path does not exist yet: %s", path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize runtime dependencies when the application starts."""

    logger.info("Starting Alexandria Audiobook Narrator %s", __version__)
    ensure_runtime_directories()
    init_db()
    reset_settings_manager()
    application_settings = get_application_settings()
    logger.info("Loaded application settings for narrator: %s", application_settings.narrator_name)
    startup_summary = await run_all_health_checks()
    app.state.startup_health = startup_summary
    yield
    await shutdown_generation_runtime()
    release_engine()


app = FastAPI(title="Alexandria Audiobook Narrator", version=__version__, lifespan=lifespan)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["localhost", "127.0.0.1", "*.local", "testserver"],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)
app.middleware("http")(request_context_middleware)
register_error_handlers(app)
app.include_router(api_router)
app.include_router(batch_router)
app.include_router(export_router)
app.include_router(generation_router)
app.include_router(monitoring_router)
app.include_router(qa_router)
app.include_router(queue_router)
app.include_router(settings_router)
app.include_router(voice_lab_router)


@app.get("/api/health", response_model=HealthCheckResponse)
async def health_check() -> HealthCheckResponse:
    """Return a basic health response."""

    startup_summary = getattr(app.state, "startup_health", None)
    if startup_summary is None:
        startup_summary = StartupHealthSummary(checked_at=utc_now(), checks=[], warnings=[], errors=[])

    if startup_summary.errors:
        status = "error"
    elif startup_summary.warnings:
        status = "degraded"
    else:
        status = "ok"
    return HealthCheckResponse(status=status, version=__version__, startup=startup_summary)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.main:app", host="0.0.0.0", port=8080, reload=False)
