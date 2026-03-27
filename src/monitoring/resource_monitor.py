"""Runtime resource monitoring for long-running audiobook production."""

from __future__ import annotations

import logging
import shutil
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.notifications import send_disk_warning_notification

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """Return the current UTC timestamp."""

    return datetime.now(timezone.utc)


@dataclass(slots=True)
class ResourceSnapshot:
    """Point-in-time view of the system resources relevant to generation."""

    timestamp: datetime = field(default_factory=_utc_now)
    disk_free_gb: float = 0.0
    disk_total_gb: float = 0.0
    disk_used_percent: float = 0.0
    memory_used_mb: float = 0.0
    memory_total_mb: float = 0.0
    memory_used_percent: float = 0.0
    throughput_chapters_per_hour: float = 0.0
    output_directory_size_gb: float = 0.0
    cpu_percent: float | None = None
    gpu_memory_mb: float | None = None

    def to_dict(self) -> dict[str, float | str | None]:
        """Return a JSON-friendly dictionary."""

        payload = asdict(self)
        payload["timestamp"] = self.timestamp.isoformat()
        return payload


@dataclass(slots=True)
class ResourceThresholds:
    """Thresholds that gate long-running generation work."""

    min_disk_free_gb: float = 2.0
    max_memory_percent: float = 80.0
    max_cpu_percent: float = 95.0
    estimated_gb_per_book: float = 0.5


class ResourceMonitor:
    """Track capacity, throughput, and output growth for production runs."""

    HISTORY_RETENTION_HOURS = 24
    HISTORY_SAMPLE_INTERVAL = timedelta(minutes=5)
    THROUGHPUT_WINDOW = timedelta(hours=1)

    def __init__(self, output_dir: Path, thresholds: ResourceThresholds | None = None) -> None:
        self.output_dir = output_dir
        self.thresholds = thresholds or ResourceThresholds()
        self._last_snapshot: ResourceSnapshot | None = None
        self._history: list[ResourceSnapshot] = []
        self._chapter_events: deque[datetime] = deque()

    def record_chapter_completed(self, completed_at: datetime | None = None) -> None:
        """Record one completed chapter for rolling throughput calculations."""

        event_time = completed_at or _utc_now()
        self._chapter_events.append(event_time)
        self._prune_chapter_events(now=event_time)

    def snapshot(self) -> ResourceSnapshot:
        """Capture a fresh resource snapshot and retain it in rolling history."""

        self.output_dir.mkdir(parents=True, exist_ok=True)
        now = _utc_now()
        disk = shutil.disk_usage(self.output_dir)
        disk_free_gb = disk.free / (1024**3)
        disk_total_gb = disk.total / (1024**3)
        disk_used_percent = (disk.used / disk.total) * 100 if disk.total else 0.0

        process_memory_mb = 0.0
        total_memory_mb = 0.0
        memory_percent = 0.0
        cpu_percent = None
        try:
            import psutil

            process = psutil.Process()
            process_memory_mb = process.memory_info().rss / (1024**2)
            virtual_memory = psutil.virtual_memory()
            total_memory_mb = virtual_memory.total / (1024**2)
            if total_memory_mb > 0:
                memory_percent = (process_memory_mb / total_memory_mb) * 100
            cpu_percent = psutil.cpu_percent(interval=0.0)
        except Exception:
            logger.exception("Unable to capture process memory statistics")

        snapshot = ResourceSnapshot(
            timestamp=now,
            disk_free_gb=round(disk_free_gb, 2),
            disk_total_gb=round(disk_total_gb, 2),
            disk_used_percent=round(disk_used_percent, 1),
            memory_used_mb=round(process_memory_mb, 1),
            memory_total_mb=round(total_memory_mb, 1),
            memory_used_percent=round(memory_percent, 1),
            throughput_chapters_per_hour=round(self._throughput_chapters_per_hour(now=now), 2),
            output_directory_size_gb=round(self._output_directory_size_gb(), 3),
            cpu_percent=round(cpu_percent, 1) if cpu_percent is not None else None,
        )
        self._last_snapshot = snapshot
        self._append_history(snapshot)
        return snapshot

    def _append_history(self, snapshot: ResourceSnapshot) -> None:
        """Persist one snapshot in the in-memory rolling history."""

        cutoff = snapshot.timestamp - timedelta(hours=self.HISTORY_RETENTION_HOURS)
        self._history = [entry for entry in self._history if entry.timestamp >= cutoff]
        if self._history and snapshot.timestamp - self._history[-1].timestamp < self.HISTORY_SAMPLE_INTERVAL:
            self._history[-1] = snapshot
            return
        self._history.append(snapshot)

    def history(self) -> list[dict[str, float | str | None]]:
        """Return the retained resource history as JSON-ready dictionaries."""

        if self._last_snapshot is None:
            self.snapshot()
        return [entry.to_dict() for entry in self._history]

    def check_can_proceed(self) -> tuple[bool, list[str]]:
        """Return whether generation may continue under the current resource state."""

        snapshot = self.snapshot()
        warnings: list[str] = []
        can_proceed = True

        if snapshot.disk_free_gb < self.thresholds.min_disk_free_gb:
            warnings.append(
                f"LOW DISK: {snapshot.disk_free_gb:.1f} GB free "
                f"(minimum: {self.thresholds.min_disk_free_gb:.1f} GB)"
            )
            can_proceed = False
            send_disk_warning_notification(
                free_gb=snapshot.disk_free_gb,
                percent_used=snapshot.disk_used_percent,
                critical=snapshot.disk_free_gb < 1.0,
            )
        elif snapshot.disk_used_percent > 90:
            warnings.append(f"HIGH DISK USAGE: {snapshot.disk_used_percent:.1f}% used")
            send_disk_warning_notification(
                free_gb=snapshot.disk_free_gb,
                percent_used=snapshot.disk_used_percent,
                critical=snapshot.disk_used_percent > 95,
            )

        if snapshot.memory_used_percent >= self.thresholds.max_memory_percent:
            warnings.append(
                f"HIGH MEMORY: {snapshot.memory_used_percent:.1f}% used "
                f"(maximum: {self.thresholds.max_memory_percent:.1f}%)"
            )
            can_proceed = False

        if snapshot.cpu_percent is not None and snapshot.cpu_percent > self.thresholds.max_cpu_percent:
            warnings.append(
                f"HIGH CPU: {snapshot.cpu_percent:.1f}% "
                f"(maximum: {self.thresholds.max_cpu_percent:.1f}%)"
            )

        return can_proceed, warnings

    def estimate_remaining_capacity(self, books_remaining: int) -> dict[str, float | int | bool]:
        """Estimate how many more books can fit on disk with the current reserve."""

        snapshot = self._last_snapshot or self.snapshot()
        needed_gb = books_remaining * self.thresholds.estimated_gb_per_book
        available_gb = max(snapshot.disk_free_gb - self.thresholds.min_disk_free_gb, 0.0)
        books_can_fit = int(available_gb / max(self.thresholds.estimated_gb_per_book, 0.001))
        return {
            "books_remaining": books_remaining,
            "estimated_gb_needed": round(needed_gb, 1),
            "disk_free_gb": snapshot.disk_free_gb,
            "estimated_books_can_fit": max(0, books_can_fit),
            "sufficient": available_gb >= needed_gb,
        }

    def to_dict(self) -> dict[str, float | str | None]:
        """Return the latest resource snapshot in API form."""

        snapshot = self._last_snapshot or self.snapshot()
        return snapshot.to_dict()

    def _prune_chapter_events(self, *, now: datetime) -> None:
        """Drop throughput events outside the rolling one-hour window."""

        cutoff = now - self.THROUGHPUT_WINDOW
        while self._chapter_events and self._chapter_events[0] < cutoff:
            self._chapter_events.popleft()

    def _throughput_chapters_per_hour(self, *, now: datetime) -> float:
        """Return the rolling chapter throughput over the last hour."""

        self._prune_chapter_events(now=now)
        if not self._chapter_events:
            return 0.0
        first_event = self._chapter_events[0]
        elapsed_seconds = max((now - first_event).total_seconds(), 60.0)
        return len(self._chapter_events) / (elapsed_seconds / 3600.0)

    def _output_directory_size_gb(self) -> float:
        """Return the recursive size of the output directory."""

        total_bytes = 0
        if not self.output_dir.exists():
            return 0.0
        for path in self.output_dir.rglob("*"):
            if path.is_file():
                try:
                    total_bytes += path.stat().st_size
                except OSError:
                    logger.warning("Unable to measure output artifact size for %s", path)
        return total_bytes / (1024**3)
