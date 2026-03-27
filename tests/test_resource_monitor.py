"""Tests for disk and memory resource monitoring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


def test_record_chapter_completed_updates_rolling_throughput(tmp_path: Path) -> None:
    """Completed chapter events should contribute to the rolling throughput metric."""

    monitor = ResourceMonitor(tmp_path)
    now = datetime.now(timezone.utc)
    monitor.record_chapter_completed(now - timedelta(minutes=30))
    monitor.record_chapter_completed(now - timedelta(minutes=10))

    throughput = monitor._throughput_chapters_per_hour(now=now)

    assert throughput > 0
    assert throughput == 4.0


def test_output_directory_size_is_reported(tmp_path: Path) -> None:
    """Snapshots should include the recursive output directory size."""

    audio_dir = tmp_path / "book-1"
    audio_dir.mkdir(parents=True)
    (audio_dir / "chapter.wav").write_bytes(b"a" * 1024 * 1024)

    monitor = ResourceMonitor(tmp_path)

    assert monitor._output_directory_size_gb() > 0


def test_history_returns_retained_snapshots(tmp_path: Path, monkeypatch) -> None:
    """History should retain the latest snapshots in JSON-ready form."""

    monitor = ResourceMonitor(tmp_path)
    base_time = datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc)
    snapshots = [
        ResourceSnapshot(timestamp=base_time, disk_free_gb=12.0),
        ResourceSnapshot(timestamp=base_time + timedelta(minutes=6), disk_free_gb=11.5),
    ]

    def fake_snapshot():
        snapshot = snapshots.pop(0)
        monitor._last_snapshot = snapshot
        monitor._append_history(snapshot)
        return snapshot

    monkeypatch.setattr(monitor, "snapshot", fake_snapshot)

    first_snapshot = monitor.snapshot()
    second_snapshot = monitor.snapshot()
    history = monitor.history()

    assert first_snapshot.disk_free_gb == 12.0
    assert second_snapshot.disk_free_gb == 11.5
    assert history[-1]["disk_free_gb"] == 11.5
