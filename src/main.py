"""FastAPI application entry point."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src import __version__
from src.api.batch_routes import router as batch_router
from src.api.error_handlers import register_error_handlers
from src.api.export_routes import router as export_router
from src.api.generation import router as generation_router
from src.api.middleware import request_context_middleware
from src.api.monitoring_routes import router as monitoring_router
from src.api.overseer_routes import router as overseer_router
from src.api.qa_routes import router as qa_router
from src.api.queue_routes import router as queue_router
from src.api.settings_routes import router as settings_router
from src.api.voice_lab import release_engine, router as voice_lab_router
from src.api.routes import router as api_router
from src.api.schemas import HealthCheckResponse, HealthDiskPayload, StartupHealthSummary
from src.config import get_application_settings, reset_settings_manager, settings
from src.database import init_db, utc_now
from src.health_checks import get_disk_space_snapshot, run_all_health_checks
from src.logging_config import configure_logging
from src.startup import (
    graceful_shutdown,
    install_signal_handlers,
    run_export_startup_cleanup,
    run_startup_cleanup,
    run_startup_recovery,
)

configure_logging(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)

ALLOWED_ORIGINS = [
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

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
    run_startup_cleanup()
    run_export_startup_cleanup()
    run_startup_recovery()
    reset_settings_manager()
    application_settings = get_application_settings()
    logger.info("Loaded application settings for narrator: %s", application_settings.narrator_name)
    install_signal_handlers()
    startup_summary = await run_all_health_checks()
    app.state.startup_health = startup_summary
    yield
    await graceful_shutdown("application shutdown")
    release_engine()


app = FastAPI(title="Alexandria Audiobook Narrator", version=__version__, lifespan=lifespan)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["localhost", "127.0.0.1", "*.local", "testserver"],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    max_age=3600,
)
app.middleware("http")(request_context_middleware)
register_error_handlers(app)
app.include_router(api_router)
app.include_router(batch_router)
app.include_router(export_router)
app.include_router(generation_router)
app.include_router(monitoring_router)
app.include_router(overseer_router)
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
    disk_snapshot = get_disk_space_snapshot()

    if startup_summary.errors:
        status = "error"
    elif startup_summary.warnings:
        status = "degraded"
    else:
        status = "ok"

    if disk_snapshot.percent_used > 95:
        status = "error"
    elif disk_snapshot.percent_used > 90 and status != "error":
        status = "degraded"

    return HealthCheckResponse(
        status=status,
        version=__version__,
        startup=startup_summary,
        disk=HealthDiskPayload(
            total_gb=disk_snapshot.total_gb,
            free_gb=disk_snapshot.free_gb,
            percent_used=disk_snapshot.percent_used,
        ),
    )


# --- Frontend static file serving ---
_frontend_build = Path(settings.FRONTEND_BUILD_DIR)

if _frontend_build.exists() and (_frontend_build / "index.html").exists():
    # Serve /static/js/*, /static/css/*, etc.
    app.mount("/static", StaticFiles(directory=str(_frontend_build / "static")), name="frontend-static")

    @app.get("/asset-manifest.json")
    async def asset_manifest():
        return FileResponse(str(_frontend_build / "asset-manifest.json"))

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Catch-all: serve index.html for any non-API route (React Router handles client routing)."""

        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail=f"API route '/{full_path}' not found.")

        file_path = _frontend_build / full_path
        if full_path and file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(_frontend_build / "index.html"))
else:
    logger.warning(
        "Frontend build not found at %s - run 'cd frontend && npm run build' first. "
        "API endpoints are still available.",
        _frontend_build,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.main:app", host="0.0.0.0", port=8080, reload=False)
