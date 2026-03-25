"""Tests for monitoring and model lifecycle APIs."""

from __future__ import annotations

from src.api import monitoring_routes
from src.database import Book, BookExportStatus


class StubResourceMonitor:
    """Simple resource monitor stub."""

    def to_dict(self):
        return {
            "disk_free_gb": 123.4,
            "disk_total_gb": 500.0,
            "disk_used_percent": 75.3,
            "memory_used_mb": 4096.0,
            "memory_total_mb": 16384.0,
            "memory_used_percent": 25.0,
            "cpu_percent": 12.0,
            "gpu_memory_mb": None,
        }

    def estimate_remaining_capacity(self, books_remaining: int):
        return {
            "books_remaining": books_remaining,
            "estimated_gb_needed": round(books_remaining * 0.5, 1),
            "disk_free_gb": 123.4,
            "estimated_books_can_fit": 200,
            "sufficient": True,
        }


class StubModelManager:
    """Model manager stub for API tests."""

    def __init__(self) -> None:
        self.reloaded = False

    def to_dict(self):
        return {
            "chunks_generated": 10,
            "chapters_generated": 3,
            "reload_count": 1,
        }

    async def force_reload(self):
        self.reloaded = True


def test_monitoring_endpoints_return_resources_model_stats_and_capacity(client, test_db, monkeypatch) -> None:
    """Monitoring routes should expose resource, model, and capacity data."""

    test_db.add_all(
        [
            Book(title="Remaining One", author="A", folder_path="remaining-one", export_status=BookExportStatus.IDLE),
            Book(title="Remaining Two", author="B", folder_path="remaining-two", export_status=BookExportStatus.ERROR),
            Book(title="Done", author="C", folder_path="done", export_status=BookExportStatus.COMPLETED),
        ]
    )
    test_db.commit()

    model_manager = StubModelManager()
    monkeypatch.setattr(monitoring_routes, "get_resource_monitor", lambda: StubResourceMonitor())
    monkeypatch.setattr(monitoring_routes, "get_model_manager", lambda: model_manager)

    resources_response = client.get("/api/monitoring/resources")
    assert resources_response.status_code == 200
    assert resources_response.json()["disk_free_gb"] == 123.4

    model_response = client.get("/api/monitoring/model")
    assert model_response.status_code == 200
    assert model_response.json()["reload_count"] == 1

    reload_response = client.post("/api/monitoring/model/reload")
    assert reload_response.status_code == 200
    assert reload_response.json()["status"] == "reloaded"
    assert model_manager.reloaded is True

    capacity_response = client.get("/api/monitoring/capacity")
    assert capacity_response.status_code == 200
    assert capacity_response.json()["books_remaining"] == 2
