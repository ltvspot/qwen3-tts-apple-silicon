"""Shared runtime singletons for generation APIs."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from src.engines.qwen3_tts import Qwen3TTS
from src.pipeline.generator import AudiobookGenerator
from src.pipeline.queue_manager import GenerationQueue

_generator: AudiobookGenerator | None = None
_queue: GenerationQueue | None = None


def get_generator() -> AudiobookGenerator:
    """Return the lazily constructed audiobook generator singleton."""

    global _generator
    if _generator is None:
        _generator = AudiobookGenerator(Qwen3TTS())
    return _generator


def get_queue() -> GenerationQueue:
    """Return the process-local generation queue singleton."""

    global _queue
    if _queue is None:
        _queue = GenerationQueue(max_workers=1)
    return _queue


async def ensure_queue_started(db: Session) -> GenerationQueue:
    """Start the generation queue using the current session bind when needed."""

    queue = get_queue()
    session_factory = sessionmaker(
        bind=db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    await queue.start(session_factory, get_generator())
    return queue


async def shutdown_generation_runtime() -> None:
    """Stop queue workers and release the cached generator."""

    global _generator, _queue

    if _queue is not None:
        await _queue.stop()
        _queue = None

    if _generator is not None:
        _generator.close()
        _generator = None
