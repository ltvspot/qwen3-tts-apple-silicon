"""Lifecycle management for long-running TTS model usage."""

from __future__ import annotations

import asyncio
import gc
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from src.config import get_application_settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ModelStats:
    """Track model usage and cooldown/reload state."""

    chunks_generated: int = 0
    chapters_generated: int = 0
    last_reload_time: float = field(default_factory=time.time)
    total_generation_seconds: float = 0.0
    peak_memory_mb: float = 0.0
    reload_count: int = 0


class ModelManager:
    """Manage a shared TTS engine instance with cooldown and reload support."""

    def __init__(
        self,
        engine_factory: Callable[[], Any],
        *,
        cooldown_chapter_threshold: int | None = None,
        cooldown_chunk_threshold: int | None = None,
        cooldown_time_threshold_seconds: int | None = None,
        memory_pressure_threshold_mb: float | None = None,
    ) -> None:
        """Initialize lifecycle management for a shared engine instance."""

        engine_config = get_application_settings().engine_config
        self._engine_factory = engine_factory
        self._engine: Any | None = None
        self._stats = ModelStats()
        self._lock = asyncio.Lock()
        self.cooldown_chapter_threshold = (
            engine_config.cooldown_chapter_threshold
            if cooldown_chapter_threshold is None
            else cooldown_chapter_threshold
        )
        self.cooldown_chunk_threshold = (
            engine_config.cooldown_chunk_threshold
            if cooldown_chunk_threshold is None
            else cooldown_chunk_threshold
        )
        self.cooldown_time_threshold_seconds = (
            engine_config.cooldown_time_threshold_seconds
            if cooldown_time_threshold_seconds is None
            else cooldown_time_threshold_seconds
        )
        self.memory_pressure_threshold_mb = (
            engine_config.memory_pressure_threshold_mb
            if memory_pressure_threshold_mb is None
            else memory_pressure_threshold_mb
        )

    @property
    def engine(self) -> Any | None:
        """Return the current engine instance, if loaded."""

        return self._engine

    @property
    def stats(self) -> ModelStats:
        """Return a live view of the current model statistics."""

        return self._stats

    async def get_engine(self) -> Any:
        """Return a loaded engine, reloading it when cooldown thresholds are hit."""

        async with self._lock:
            if self._engine is None:
                await self._load_engine_locked(reload_count=self._stats.reload_count)
            elif self._needs_cooldown():
                logger.info(
                    "Model cooldown triggered after %d chapters / %d chunks / %.0fs",
                    self._stats.chapters_generated,
                    self._stats.chunks_generated,
                    time.time() - self._stats.last_reload_time,
                )
                await self._reload_engine_locked()
            return self._engine

    def _needs_cooldown(self) -> bool:
        """Return whether the shared engine should be reloaded."""

        if self._stats.chapters_generated >= self.cooldown_chapter_threshold:
            return True
        if self._stats.chunks_generated >= self.cooldown_chunk_threshold:
            return True
        if time.time() - self._stats.last_reload_time >= self.cooldown_time_threshold_seconds:
            return True

        current_memory = self._get_process_memory_mb()
        if current_memory > self.memory_pressure_threshold_mb:
            logger.warning(
                "Model memory pressure %.1f MB exceeded threshold %.1f MB",
                current_memory,
                self.memory_pressure_threshold_mb,
            )
            return True
        return False

    async def _load_engine_locked(self, *, reload_count: int) -> None:
        """Create and load a fresh engine instance while holding the manager lock."""

        logger.info("Loading shared TTS engine")
        engine = self._engine_factory()
        if not getattr(engine, "loaded", False):
            engine.load()
        self._engine = engine
        self._stats = ModelStats(
            last_reload_time=time.time(),
            peak_memory_mb=self._get_process_memory_mb(),
            reload_count=reload_count,
        )
        logger.info("Shared TTS engine ready")

    async def _reload_engine_locked(self) -> None:
        """Unload the current engine and replace it with a fresh instance."""

        reload_count = self._stats.reload_count + 1
        old_engine = self._engine
        self._engine = None

        if old_engine is not None and getattr(old_engine, "loaded", False):
            old_engine.unload()

        del old_engine
        gc.collect()
        await asyncio.sleep(1.0)
        await self._load_engine_locked(reload_count=reload_count)

    def record_chunk(self, generation_seconds: float = 0.0) -> None:
        """Record one generated chunk for cooldown tracking."""

        self._stats.chunks_generated += 1
        self._stats.total_generation_seconds += generation_seconds
        self._stats.peak_memory_mb = max(self._stats.peak_memory_mb, self._get_process_memory_mb())

    def record_chapter(self) -> None:
        """Record one completed chapter for cooldown tracking."""

        self._stats.chapters_generated += 1
        self._stats.peak_memory_mb = max(self._stats.peak_memory_mb, self._get_process_memory_mb())

    def _get_process_memory_mb(self) -> float:
        """Return the current process RSS memory in megabytes when available."""

        try:
            import psutil

            process = psutil.Process()
            return process.memory_info().rss / (1024 * 1024)
        except Exception:
            return 0.0

    async def force_reload(self) -> None:
        """Reload the shared engine immediately."""

        async with self._lock:
            if self._engine is None:
                await self._load_engine_locked(reload_count=self._stats.reload_count)
                return
            await self._reload_engine_locked()

    async def shutdown(self) -> None:
        """Unload the managed engine and clear its state."""

        async with self._lock:
            self.release()

    def release(self) -> None:
        """Synchronously unload the managed engine and clear counters."""

        if self._engine is not None and getattr(self._engine, "loaded", False):
            self._engine.unload()
        self._engine = None
        self._stats = ModelStats(reload_count=self._stats.reload_count)

    def to_dict(self) -> dict[str, float | int]:
        """Return a serializable snapshot of the current model state."""

        seconds_since_reload = time.time() - self._stats.last_reload_time
        current_memory = self._get_process_memory_mb()
        return {
            "chunks_generated": self._stats.chunks_generated,
            "chapters_generated": self._stats.chapters_generated,
            "seconds_since_reload": round(seconds_since_reload, 1),
            "total_generation_seconds": round(self._stats.total_generation_seconds, 1),
            "process_memory_mb": round(current_memory, 1),
            "peak_memory_mb": round(max(self._stats.peak_memory_mb, current_memory), 1),
            "reload_count": self._stats.reload_count,
            "cooldown_threshold_chapters": self.cooldown_chapter_threshold,
            "cooldown_threshold_chunks": self.cooldown_chunk_threshold,
            "cooldown_threshold_seconds": self.cooldown_time_threshold_seconds,
            "memory_pressure_threshold_mb": round(self.memory_pressure_threshold_mb, 1),
        }
