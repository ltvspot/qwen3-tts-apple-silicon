"""Tests for batch orchestration API routes."""

from __future__ import annotations

from dataclasses import dataclass

from src.api import batch_routes


@dataclass
class StubBatchOrchestrator:
    """Fake orchestrator for endpoint tests."""

    payload: dict

    async def start_batch(self, book_ids, priority: str, skip_already_exported: bool, strategy):
        self.payload["total_books"] = len(book_ids)
        self.payload["status"] = "running"
        self.payload["scheduling_strategy"] = strategy.value
        return self.payload

    async def pause(self, reason: str):
        self.payload["status"] = "paused"
        self.payload["pause_reason"] = reason

    async def resume(self):
        self.payload["status"] = "running"
        self.payload["pause_reason"] = None

    async def cancel(self):
        self.payload["status"] = "cancelled"

    def history(self):
        return [{"batch_id": self.payload["batch_id"], "status": self.payload["status"]}]

    def to_dict(self):
        return self.payload


def test_batch_endpoints_return_progress_and_history(client, monkeypatch) -> None:
    """Batch endpoints should proxy orchestrator state cleanly."""

    payload = {
        "avg_seconds_per_book": 0.0,
        "avgBookTimeSeconds": 0.0,
        "avgChapterTimeSeconds": 12.5,
        "batch_id": "batch_20260325_120000",
        "book_results": [],
        "books_completed": 0,
        "books_failed": 0,
        "books_in_progress": 0,
        "books_skipped": 0,
        "booksCompleted": 0,
        "booksTotal": 2,
        "currentBook": "Self-Reliance",
        "currentChapter": "Chapter 3",
        "current_book_id": None,
        "current_book_title": None,
        "elapsed_seconds": 0.0,
        "estimated_completion": None,
        "estimatedTimeRemainingSeconds": 3600,
        "memoryUsageMB": 2100.0,
        "model_reloads": 0,
        "pause_reason": None,
        "percent_complete": 0.0,
        "resource_warnings": [],
        "started_at": "2026-03-25T12:00:00+00:00",
        "status": "pending",
        "total_books": 0,
    }
    orchestrator = StubBatchOrchestrator(payload)

    async def fake_orchestrator(_db):
        return orchestrator

    monkeypatch.setattr(batch_routes, "ensure_batch_orchestrator", fake_orchestrator)
    monkeypatch.setattr(batch_routes, "_default_batch_book_ids", lambda _db: [11, 22])

    start_response = client.post("/api/batch/start", json={"priority": "normal", "skip_already_exported": True})
    assert start_response.status_code == 200
    assert start_response.json()["total_books"] == 2
    assert start_response.json()["estimatedTimeRemainingSeconds"] == 3600
    assert start_response.json()["currentBook"] == "Self-Reliance"

    pause_response = client.post("/api/batch/pause", json={"reason": "Testing pause"})
    assert pause_response.status_code == 200
    assert pause_response.json()["status"] == "paused"

    resume_response = client.post("/api/batch/resume")
    assert resume_response.status_code == 200
    assert resume_response.json()["status"] == "running"

    cancel_response = client.post("/api/batch/cancel")
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"

    history_response = client.get("/api/batch/history")
    assert history_response.status_code == 200
    assert history_response.json()["batches"][0]["batch_id"] == payload["batch_id"]
