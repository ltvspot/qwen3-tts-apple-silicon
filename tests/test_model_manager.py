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
