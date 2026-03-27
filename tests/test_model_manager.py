"""Tests for shared TTS model lifecycle management."""

from __future__ import annotations

import pytest

from src.engines.model_manager import ModelManager


class StubEngine:
    """Minimal engine stub for lifecycle tests."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.loaded = False

    def load(self) -> None:
        self.loaded = True

    def unload(self) -> None:
        self.loaded = False

    @classmethod
    def perform_restart_cleanup(cls) -> dict[str, float | bool]:
        return {"before_mb": 1.0, "after_mb": 0.5, "metal_cache_cleared": False}


@pytest.mark.asyncio
async def test_model_manager_reuses_loaded_engine() -> None:
    """Repeated calls should return the same engine until cooldown is triggered."""

    created_engines: list[StubEngine] = []

    def factory() -> StubEngine:
        engine = StubEngine(f"engine-{len(created_engines)}")
        created_engines.append(engine)
        return engine

    manager = ModelManager(
        factory,
        cooldown_chapter_threshold=99,
        cooldown_chunk_threshold=999,
        cooldown_time_threshold_seconds=9999,
        memory_pressure_threshold_mb=999999,
    )

    first_engine = await manager.get_engine()
    second_engine = await manager.get_engine()

    assert first_engine is second_engine
    assert len(created_engines) == 1


@pytest.mark.asyncio
async def test_model_manager_reloads_after_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """Crossing the chapter threshold should reload the managed engine."""

    created_engines: list[StubEngine] = []

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("src.engines.model_manager.asyncio.sleep", fake_sleep)

    def factory() -> StubEngine:
        engine = StubEngine(f"engine-{len(created_engines)}")
        created_engines.append(engine)
        return engine

    manager = ModelManager(
        factory,
        cooldown_chapter_threshold=1,
        cooldown_chunk_threshold=999,
        cooldown_time_threshold_seconds=9999,
        memory_pressure_threshold_mb=999999,
    )

    first_engine = await manager.get_engine()
    manager.record_chapter()
    second_engine = await manager.get_engine()

    assert first_engine is not second_engine
    assert len(created_engines) == 2
    assert manager.stats.reload_count == 1


@pytest.mark.asyncio
async def test_cooldown_if_needed_returns_false_before_threshold() -> None:
    """Proactive cooldown should do nothing when thresholds have not been reached."""

    manager = ModelManager(
        lambda: StubEngine("engine"),
        cooldown_chapter_threshold=10,
        cooldown_chunk_threshold=999,
        cooldown_time_threshold_seconds=9999,
        memory_pressure_threshold_mb=999999,
    )

    await manager.get_engine()

    assert await manager.cooldown_if_needed() is False


@pytest.mark.asyncio
async def test_wait_for_restart_blocks_until_restart_finishes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wait helper should poll until the in-flight restart flag clears."""

    async def fake_sleep(_seconds: float) -> None:
        manager._restart_in_progress = False

    manager = ModelManager(
        lambda: StubEngine("engine"),
        cooldown_chapter_threshold=10,
        cooldown_chunk_threshold=999,
        cooldown_time_threshold_seconds=9999,
        memory_pressure_threshold_mb=999999,
    )
    manager._restart_in_progress = True
    monkeypatch.setattr("src.engines.model_manager.asyncio.sleep", fake_sleep)

    await manager.wait_for_restart(timeout_seconds=1.0)

    assert manager._restart_in_progress is False


@pytest.mark.asyncio
async def test_model_manager_to_dict_exposes_restart_fields() -> None:
    """Serialized model stats should include the new restart metadata."""

    manager = ModelManager(
        lambda: StubEngine("engine"),
        cooldown_chapter_threshold=50,
        cooldown_chunk_threshold=500,
        cooldown_time_threshold_seconds=9999,
        memory_pressure_threshold_mb=999999,
    )
    await manager.get_engine()
    manager.record_chapter()

    payload = manager.to_dict()

    assert payload["model_loaded"] is True
    assert payload["chapters_since_restart"] == 1
    assert payload["restart_interval"] == 50
