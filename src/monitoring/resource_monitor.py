"""Runtime resource monitoring for large batch generation."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ResourceSnapshot:
    """Point-in-time view of process and disk utilization."""

    disk_free_gb: float
    disk_total_gb: float
    disk_used_percent: float
    memory_used_mb: float
    memory_total_mb: float
    memory_used_percent: float
    cpu_percent: float | None = None
    gpu_memory_mb: float | None = None


@dataclass(slots=True)
class ResourceThresholds:
    """Thresholds that gate long-running generation work."""

    min_disk_free_gb: float = 10.0
    max_memory_percent: float = 85.0
    max_cpu_percent: float = 95.0
    estimated_gb_per_book: float = 0.5


class ResourceMonitor:
    """Monitor disk, memory, and CPU capacity for queue and batch orchestration."""

    def __init__(self, output_dir: Path, thresholds: ResourceThresholds | None = None) -> None:
        """Bind monitoring to the configured output volume."""

        self.output_dir = output_dir
        self.thresholds = thresholds or ResourceThresholds()
        self._last_snapshot: ResourceSnapshot | None = None

    def snapshot(self) -> ResourceSnapshot:
        """Capture a fresh resource snapshot."""

        self.output_dir.mkdir(parents=True, exist_ok=True)
        disk = shutil.disk_usage(self.output_dir)
        disk_free_gb = disk.free / (1024**3)
        disk_total_gb = disk.total / (1024**3)
        disk_used_percent = (disk.used / disk.total) * 100 if disk.total else 0.0

        try:
            import psutil

            memory = psutil.virtual_memory()
            memory_used_mb = memory.used / (1024**2)
            memory_total_mb = memory.total / (1024**2)
            memory_used_percent = float(memory.percent)
            cpu_percent = psutil.cpu_percent(interval=0.1)
        except Exception:
            memory_used_mb = 0.0
            memory_total_mb = 0.0
            memory_used_percent = 0.0
            cpu_percent = None

        snapshot = ResourceSnapshot(
            disk_free_gb=round(disk_free_gb, 2),
            disk_total_gb=round(disk_total_gb, 2),
            disk_used_percent=round(disk_used_percent, 1),
            memory_used_mb=round(memory_used_mb, 1),
            memory_total_mb=round(memory_total_mb, 1),
            memory_used_percent=round(memory_used_percent, 1),
            cpu_percent=round(cpu_percent, 1) if cpu_percent is not None else None,
        )
        self._last_snapshot = snapshot
        return snapshot

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

        if snapshot.memory_used_percent > self.thresholds.max_memory_percent:
            warnings.append(
                f"HIGH MEMORY: {snapshot.memory_used_percent:.1f}% used "
                f"(maximum: {self.thresholds.max_memory_percent:.1f}%)"
            )
            can_proceed = False

        if (
            snapshot.cpu_percent is not None
            and snapshot.cpu_percent > self.thresholds.max_cpu_percent
        ):
            warnings.append(
                f"HIGH CPU: {snapshot.cpu_percent:.1f}% "
                f"(maximum: {self.thresholds.max_cpu_percent:.1f}%)"
            )

        return can_proceed, warnings

    def estimate_remaining_capacity(self, books_remaining: int) -> dict[str, float | int | bool]:
        """Estimate how many more books can fit on disk with the current reserve."""

        snapshot = self.snapshot()
        needed_gb = books_remaining * self.thresholds.estimated_gb_per_book
        available_gb = max(snapshot.disk_free_gb - self.thresholds.min_disk_free_gb, 0.0)
        books_can_fit = int(available_gb / self.thresholds.estimated_gb_per_book)
        return {
            "books_remaining": books_remaining,
            "estimated_gb_needed": round(needed_gb, 1),
            "disk_free_gb": snapshot.disk_free_gb,
            "estimated_books_can_fit": max(0, books_can_fit),
            "sufficient": available_gb >= needed_gb,
        }

    def to_dict(self) -> dict[str, float | None]:
        """Return the latest resource snapshot in API form."""

        snapshot = self._last_snapshot or self.snapshot()
        return {
            "disk_free_gb": snapshot.disk_free_gb,
            "disk_total_gb": snapshot.disk_total_gb,
            "disk_used_percent": snapshot.disk_used_percent,
            "memory_used_mb": snapshot.memory_used_mb,
            "memory_total_mb": snapshot.memory_total_mb,
            "memory_used_percent": snapshot.memory_used_percent,
            "cpu_percent": snapshot.cpu_percent,
            "gpu_memory_mb": snapshot.gpu_memory_mb,
        }
