"""Shared runtime singletons for generation, monitoring, and batch APIs."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from src.engines import ModelManager, Qwen3TTS
from src.monitoring import ResourceMonitor
from src.pipeline.batch_orchestrator import BatchOrchestrator
from src.pipeline.generator import AudiobookGenerator
from src.pipeline.queue_manager import GenerationQueue
from src.config import settings

_generator: AudiobookGenerator | None = None
_queue: GenerationQueue | None = None
_model_manager: ModelManager | None = None
_resource_monitor: ResourceMonitor | None = None
_batch_orchestrator: BatchOrchestrator | None = None


def _session_factory_for(db: Session) -> sessionmaker[Session]:
    """Create a detached session factory using the request-bound engine."""

    return sessionmaker(
        bind=db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def get_model_manager() -> ModelManager:
    """Return the shared model lifecycle manager."""

    global _model_manager
    if _model_manager is None:
        _model_manager = ModelManager(lambda: Qwen3TTS())
    return _model_manager


def release_model_manager() -> None:
    """Synchronously clear the shared model manager and unload its engine."""

    global _model_manager
    if _model_manager is not None:
        _model_manager.release()
        _model_manager = None


def get_generator() -> AudiobookGenerator:
    """Return the lazily constructed audiobook generator singleton."""

    global _generator
    if _generator is None:
        _generator = AudiobookGenerator(model_manager=get_model_manager())
    return _generator


def get_queue() -> GenerationQueue:
    """Return the process-local generation queue singleton."""

    global _queue
    if _queue is None:
        _queue = GenerationQueue(max_workers=1)
    return _queue


def peek_queue() -> GenerationQueue | None:
    """Return the existing generation queue singleton without constructing one."""

    return _queue


def get_resource_monitor() -> ResourceMonitor:
    """Return the shared resource monitor for the output volume."""

    global _resource_monitor
    if _resource_monitor is None:
        _resource_monitor = ResourceMonitor(Path(settings.OUTPUTS_PATH))
    return _resource_monitor


async def ensure_queue_started(db: Session) -> GenerationQueue:
    """Start the generation queue using the current session bind when needed."""

    queue = get_queue()
    await queue.start(
        _session_factory_for(db),
        get_generator(),
        resource_monitor=get_resource_monitor(),
    )
    return queue


async def ensure_batch_orchestrator(db: Session) -> BatchOrchestrator:
    """Return a batch orchestrator bound to the request's session factory."""

    global _batch_orchestrator
    queue = await ensure_queue_started(db)
    session_factory = _session_factory_for(db)

    if _batch_orchestrator is None:
        _batch_orchestrator = BatchOrchestrator(
            queue,
            get_model_manager(),
            get_resource_monitor(),
            session_factory,
        )
    return _batch_orchestrator


async def shutdown_generation_runtime() -> None:
    """Stop queue workers, batch tasks, and unload shared model resources."""

    global _generator, _queue, _batch_orchestrator, _resource_monitor

    if _batch_orchestrator is not None:
        await _batch_orchestrator.cancel()
        await _batch_orchestrator.wait()
        _batch_orchestrator = None

    if _queue is not None:
        await _queue.stop()
        _queue = None

    if _model_manager is not None:
        await _model_manager.shutdown()

    _generator = None
    _resource_monitor = None
    release_model_manager()
