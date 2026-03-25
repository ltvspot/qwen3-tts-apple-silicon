"""Tests for disk and memory resource monitoring."""

from __future__ import annotations

from pathlib import Path

from src.monitoring import ResourceMonitor, ResourceSnapshot, ResourceThresholds


def test_resource_check_blocks_on_high_memory(tmp_path: Path, monkeypatch) -> None:
    """High memory usage should prevent more generation work from starting."""

    monitor = ResourceMonitor(
        tmp_path,
        ResourceThresholds(min_disk_free_gb=5.0, max_memory_percent=80.0),
    )
    monkeypatch.setattr(
        monitor,
        "snapshot",
        lambda: ResourceSnapshot(
            disk_free_gb=50.0,
            disk_total_gb=100.0,
            disk_used_percent=50.0,
            memory_used_mb=12288.0,
            memory_total_mb=16384.0,
            memory_used_percent=90.0,
            cpu_percent=20.0,
        ),
    )

    can_proceed, warnings = monitor.check_can_proceed()

    assert can_proceed is False
    assert warnings == ["HIGH MEMORY: 90.0% used (maximum: 80.0%)"]


def test_capacity_estimate_reports_remaining_books(tmp_path: Path, monkeypatch) -> None:
    """Capacity estimation should reserve the configured disk headroom."""

    monitor = ResourceMonitor(
        tmp_path,
        ResourceThresholds(min_disk_free_gb=10.0, estimated_gb_per_book=0.5),
    )
    monkeypatch.setattr(
        monitor,
        "snapshot",
        lambda: ResourceSnapshot(
            disk_free_gb=60.0,
            disk_total_gb=100.0,
            disk_used_percent=40.0,
            memory_used_mb=4096.0,
            memory_total_mb=16384.0,
            memory_used_percent=25.0,
            cpu_percent=10.0,
        ),
    )

    capacity = monitor.estimate_remaining_capacity(books_remaining=80)

    assert capacity["estimated_gb_needed"] == 40.0
    assert capacity["estimated_books_can_fit"] == 100
    assert capacity["sufficient"] is True
