"""Tests for PROMPT-41 system status endpoints."""

from __future__ import annotations

from src.api import system_routes


class StubResourceMonitor:
    """Simple resource monitor stub for system routes."""

    def to_dict(self):
        return {
            "timestamp": "2026-03-27T12:00:00+00:00",
            "disk_free_gb": 42.5,
            "disk_total_gb": 100.0,
            "disk_used_percent": 57.5,
            "memory_used_mb": 2048.0,
            "memory_total_mb": 16384.0,
            "memory_used_percent": 12.5,
            "throughput_chapters_per_hour": 9.5,
            "output_directory_size_gb": 1.25,
            "cpu_percent": 15.0,
            "gpu_memory_mb": None,
        }

    def history(self):
        return [
            {
                "timestamp": "2026-03-27T11:55:00+00:00",
                "disk_free_gb": 43.0,
                "memory_used_percent": 12.0,
            },
            {
                "timestamp": "2026-03-27T12:00:00+00:00",
                "disk_free_gb": 42.5,
                "memory_used_percent": 12.5,
            },
        ]


class StubModelManager:
    """Model manager stub exposing the new restart fields."""

    def to_dict(self):
        return {
            "chapters_since_restart": 17,
            "restart_interval": 50,
            "memory_usage_mb": 1536.5,
            "model_loaded": True,
        }


def test_system_resources_endpoint_returns_latest_snapshot(client, monkeypatch) -> None:
    """`/api/system/resources` should proxy the resource monitor payload."""

    monkeypatch.setattr(system_routes, "get_resource_monitor", lambda: StubResourceMonitor())

    response = client.get("/api/system/resources")

    assert response.status_code == 200
    payload = response.json()
    assert payload["disk_free_gb"] == 42.5
    assert payload["throughput_chapters_per_hour"] == 9.5


def test_system_resources_history_endpoint_returns_retained_samples(client, monkeypatch) -> None:
    """`/api/system/resources/history` should expose the retained history."""

    monkeypatch.setattr(system_routes, "get_resource_monitor", lambda: StubResourceMonitor())

    response = client.get("/api/system/resources/history")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    assert payload[-1]["memory_used_percent"] == 12.5


def test_system_model_status_endpoint_normalizes_restart_fields(client, monkeypatch) -> None:
    """`/api/system/model-status` should expose the new model cooldown fields."""

    monkeypatch.setattr(system_routes, "get_model_manager", lambda: StubModelManager())

    response = client.get("/api/system/model-status")

    assert response.status_code == 200
    assert response.json() == {
        "chapters_since_restart": 17,
        "restart_interval": 50,
        "memory_usage_mb": 1536.5,
        "model_loaded": True,
    }
