"""FastAPI application entry point."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint

from src import __version__
from src.api.generation import router as generation_router, shutdown_generation_runtime
from src.api.voice_lab import release_engine, router as voice_lab_router
from src.api.routes import router as api_router
from src.api.schemas import HealthCheckResponse
from src.config import settings
from src.database import init_db


def configure_logging() -> None:
    """Configure application logging once for the process."""

    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


configure_logging()
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
async def lifespan(_: FastAPI):
    """Initialize runtime dependencies when the application starts."""

    logger.info("Starting Alexandria Audiobook Narrator %s", __version__)
    ensure_runtime_directories()
    init_db()
    yield
    await shutdown_generation_runtime()
    release_engine()


app = FastAPI(title="Alexandria Audiobook Narrator", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)
app.include_router(api_router)
app.include_router(generation_router)
app.include_router(voice_lab_router)


@app.middleware("http")
async def unhandled_exception_middleware(
    request: Request,
    call_next: RequestResponseEndpoint,
) -> Response:
    """Return JSON for unexpected server errors."""

    try:
        return await call_next(request)
    except Exception:
        logger.exception("Unhandled application error for %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/api/health", response_model=HealthCheckResponse)
async def health_check() -> HealthCheckResponse:
    """Return a basic health response."""

    return HealthCheckResponse(status="ok", version=__version__)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.main:app", host="0.0.0.0", port=8080, reload=False)
