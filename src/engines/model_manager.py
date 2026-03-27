"""Lifecycle management for long-running TTS model usage."""

from __future__ import annotations

import asyncio
import gc
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from src.config import get_application_settings

logger = logging.getLogger(__name__)
CANARY_TEXT = "The old lighthouse keeper closed his journal and set it on the windowsill."


@dataclass(slots=True)
class ModelStats:
    """Track model usage and cooldown/reload state."""

    chunks_generated: int = 0
    chapters_generated: int = 0
    manager_started_at: float = field(default_factory=time.time)
    last_reload_time: float = field(default_factory=time.time)
    total_generation_seconds: float = 0.0
    peak_memory_mb: float = 0.0
    reload_count: int = 0
    last_canary_status: str = "not_run"
    last_canary_checked_at: float | None = None
    last_canary_deviation_percent: float | None = None


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
        self._restart_in_progress = False
        self.cooldown_chapter_threshold = (
            int(os.environ.get("TTS_MODEL_RESTART_INTERVAL", engine_config.cooldown_chapter_threshold))
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
        self._baseline_spectral_centroid: float | None = None

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
                await self._reload_engine_locked()
            return self._engine

    async def cooldown_if_needed(self) -> bool:
        """Proactively restart the engine when the cooldown threshold has been reached."""

        async with self._lock:
            if self._engine is None or not self._needs_cooldown():
                return False
            await self._reload_engine_locked()
            return True

    async def wait_for_restart(self, timeout_seconds: float = 10.0) -> None:
        """Wait briefly for an in-flight restart to complete."""

        deadline = time.monotonic() + timeout_seconds
        while self._restart_in_progress and time.monotonic() < deadline:
            await asyncio.sleep(0.05)

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
            await asyncio.to_thread(engine.load)
        self._engine = engine
        self._stats = ModelStats(
            manager_started_at=self._stats.manager_started_at,
            last_reload_time=time.time(),
            peak_memory_mb=self._get_process_memory_mb(),
            reload_count=reload_count,
            last_canary_status=self._stats.last_canary_status,
            last_canary_checked_at=self._stats.last_canary_checked_at,
            last_canary_deviation_percent=self._stats.last_canary_deviation_percent,
        )
        logger.info("Shared TTS engine ready")

    async def _reload_engine_locked(self) -> None:
        """Unload the current engine and replace it with a fresh instance."""

        logger.info("Model cooldown: restarting after %d chapters", self._stats.chapters_generated)
        reload_count = self._stats.reload_count + 1
        before_memory_mb = self._get_process_memory_mb()
        self._restart_in_progress = True
        try:
            await self._replace_engine_locked(reload_count=reload_count)
        finally:
            self._restart_in_progress = False
        after_memory_mb = self._get_process_memory_mb()
        logger.info(
            "Model restart complete. Memory before=%.1fMB after=%.1fMB",
            before_memory_mb,
            after_memory_mb,
        )
        await self._run_quality_canary()

    async def _replace_engine_locked(self, *, reload_count: int) -> None:
        """Unload the current engine, wait for memory release, and load a fresh instance."""

        old_engine = self._engine
        self._engine = None

        if old_engine is not None and getattr(old_engine, "loaded", False):
            old_engine.unload()

        cleanup = getattr(type(old_engine), "perform_restart_cleanup", None) if old_engine is not None else None
        if callable(cleanup):
            cleanup()
        del old_engine
        gc.collect()
        await asyncio.sleep(3.0)
        gc.collect()
        await self._load_engine_locked(reload_count=reload_count)

    async def _run_quality_canary(self, *, allow_rereload: bool = True) -> None:
        """Generate a canary phrase after reload and compare quality to the baseline."""

        engine = self._engine
        if engine is None or not hasattr(engine, "generate"):
            return

        try:
            audio = await asyncio.to_thread(engine.generate, CANARY_TEXT, "Ethan", "neutral", 1.0)
            if audio is None or len(audio) < 1000:
                self._stats.last_canary_status = "failed"
                self._stats.last_canary_checked_at = time.time()
                self._stats.last_canary_deviation_percent = None
                logger.warning("Quality canary: generation failed, triggering another reload")
                if allow_rereload:
                    await self._replace_engine_locked(reload_count=self._stats.reload_count + 1)
                    await self._run_quality_canary(allow_rereload=False)
                return

            centroid = self._compute_spectral_centroid(audio)
            if centroid is None:
                self._stats.last_canary_status = "unavailable"
                self._stats.last_canary_checked_at = time.time()
                self._stats.last_canary_deviation_percent = None
                logger.warning("Quality canary: could not compute spectral centroid")
                return

            if self._baseline_spectral_centroid is None:
                self._baseline_spectral_centroid = centroid
                self._stats.last_canary_status = "baseline"
                self._stats.last_canary_checked_at = time.time()
                self._stats.last_canary_deviation_percent = 0.0
                logger.info("Quality canary: baseline spectral centroid = %.1f Hz", centroid)
                return

            deviation = abs(centroid - self._baseline_spectral_centroid) / self._baseline_spectral_centroid
            self._stats.last_canary_checked_at = time.time()
            self._stats.last_canary_deviation_percent = round(deviation * 100.0, 3)
            if deviation > 0.15:
                self._stats.last_canary_status = "degraded"
                logger.warning(
                    "Quality canary: spectral centroid %.1f Hz deviates %.1f%% from baseline %.1f Hz — triggering re-reload",
                    centroid,
                    deviation * 100,
                    self._baseline_spectral_centroid,
                )
                if allow_rereload:
                    await self._replace_engine_locked(reload_count=self._stats.reload_count + 1)
                    await self._run_quality_canary(allow_rereload=False)
                return

            self._stats.last_canary_status = "ok"
            logger.info("Quality canary: OK (deviation %.1f%%)", deviation * 100)
        except Exception as exc:
            self._stats.last_canary_status = "error"
            self._stats.last_canary_checked_at = time.time()
            logger.error("Quality canary failed: %s", exc)

    def _compute_spectral_centroid(self, audio: Any) -> float | None:
        """Compute a simple spectral centroid for canary comparison."""

        try:
            import numpy as np
        except Exception:
            return None

        samples = np.array(audio.get_array_of_samples(), dtype=np.float64)
        if samples.size == 0:
            return None

        frame_size = min(max(256, samples.size), 1024)
        frame = samples[:frame_size]
        if frame.size < frame_size:
            frame = np.pad(frame, (0, frame_size - frame.size))

        window = np.hanning(frame_size)
        frequencies = np.arange((frame_size // 2) + 1, dtype=np.float64)[:, None]
        times = np.arange(frame_size, dtype=np.float64)[None, :]
        exponent = (-2j * np.pi * frequencies * times) / float(frame_size)
        basis = np.exp(exponent)
        magnitude = np.abs(basis @ (frame * window))
        freqs = (frequencies[:, 0] * audio.frame_rate) / frame_size
        denominator = float(np.sum(magnitude))
        if denominator <= 0:
            return None
        return float(np.sum(freqs * magnitude) / denominator)

    def record_chunk(self, generation_seconds: float = 0.0) -> None:
        """Record one generated chunk for cooldown tracking."""

        self._stats.chunks_generated += 1
        self._stats.total_generation_seconds += generation_seconds
        self._stats.peak_memory_mb = max(self._stats.peak_memory_mb, self._get_process_memory_mb())

    def record_chapter(self) -> None:
        """Record one completed chapter for cooldown tracking."""

        self._stats.chapters_generated += 1
        self._stats.peak_memory_mb = max(self._stats.peak_memory_mb, self._get_process_memory_mb())
        if self._engine is not None:
            record_completed_chapter = getattr(self._engine, "record_completed_chapter", None)
            if callable(record_completed_chapter):
                record_completed_chapter()

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

    async def run_canary(self) -> str:
        """Run the quality canary on demand and return the resulting status."""

        async with self._lock:
            await self._run_quality_canary()
            return self._stats.last_canary_status

    async def shutdown(self) -> None:
        """Unload the managed engine and clear its state."""

        async with self._lock:
            self.release()

    def release(self) -> None:
        """Synchronously unload the managed engine and clear counters."""

        if self._engine is not None and getattr(self._engine, "loaded", False):
            self._engine.unload()
        self._engine = None
        self._restart_in_progress = False
        self._stats = ModelStats(
            manager_started_at=self._stats.manager_started_at,
            reload_count=self._stats.reload_count,
            last_canary_status=self._stats.last_canary_status,
            last_canary_checked_at=self._stats.last_canary_checked_at,
            last_canary_deviation_percent=self._stats.last_canary_deviation_percent,
        )

    def to_dict(self) -> dict[str, float | int | str | bool | None]:
        """Return a serializable snapshot of the current model state."""

        seconds_since_reload = time.time() - self._stats.last_reload_time
        uptime_seconds = time.time() - self._stats.manager_started_at
        current_memory = self._get_process_memory_mb()
        return {
            "loaded": self._engine is not None,
            "model_loaded": self._engine is not None,
            "chunks_generated": self._stats.chunks_generated,
            "chapters_generated": self._stats.chapters_generated,
            "chapters_since_restart": self._stats.chapters_generated,
            "restart_interval": self.cooldown_chapter_threshold,
            "seconds_since_reload": round(seconds_since_reload, 1),
            "uptime_seconds": round(uptime_seconds, 1),
            "last_reload_at": round(self._stats.last_reload_time, 3),
            "total_generation_seconds": round(self._stats.total_generation_seconds, 1),
            "process_memory_mb": round(current_memory, 1),
            "memory_usage_mb": round(current_memory, 1),
            "peak_memory_mb": round(max(self._stats.peak_memory_mb, current_memory), 1),
            "reload_count": self._stats.reload_count,
            "restart_in_progress": self._restart_in_progress,
            "last_canary_status": self._stats.last_canary_status,
            "last_canary_checked_at": (
                round(self._stats.last_canary_checked_at, 3)
                if self._stats.last_canary_checked_at is not None
                else None
            ),
            "last_canary_deviation_percent": self._stats.last_canary_deviation_percent,
            "cooldown_threshold_chapters": self.cooldown_chapter_threshold,
            "cooldown_threshold_chunks": self.cooldown_chunk_threshold,
            "cooldown_threshold_seconds": self.cooldown_time_threshold_seconds,
            "memory_pressure_threshold_mb": round(self.memory_pressure_threshold_mb, 1),
        }
