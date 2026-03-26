"""Regression tests for runtime hardening features."""

from __future__ import annotations

import wave
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src import notifications
from src.database import Book, BookStatus, Chapter, ChapterStatus, ChapterType


def _write_test_wav(path: Path) -> None:
    """Write a tiny valid PCM WAV file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24_000)
        wav_file.writeframes(b"\x00\x00" * 2_400)


def _create_book_with_chapter(test_db: Session, *, audio_path: str | None) -> tuple[Book, Chapter]:
    """Persist a simple book/chapter pair for API tests."""

    book = Book(
        title="Previewable Book",
        author="Test Author",
        folder_path="previewable-book",
        status=BookStatus.PARSED,
    )
    test_db.add(book)
    test_db.commit()
    test_db.refresh(book)

    chapter = Chapter(
        book_id=book.id,
        number=1,
        title="Chapter One",
        type=ChapterType.CHAPTER,
        text_content="Preview body text.",
        word_count=3,
        status=ChapterStatus.GENERATED if audio_path else ChapterStatus.PENDING,
        audio_path=audio_path,
        duration_seconds=0.1 if audio_path else None,
    )
    test_db.add(chapter)
    test_db.commit()
    test_db.refresh(chapter)
    return book, chapter


def test_preview_endpoint_streams_generated_audio(
    client: TestClient,
    test_db: Session,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Preview streaming should return audio bytes and browser-friendly headers."""

    outputs_path = tmp_path / "outputs"
    monkeypatch.setattr("src.api.routes.settings.OUTPUTS_PATH", str(outputs_path))
    relative_audio_path = "1-previewable-book/chapters/01-chapter-one.wav"
    _write_test_wav(outputs_path / relative_audio_path)
    book, chapter = _create_book_with_chapter(test_db, audio_path=relative_audio_path)

    response = client.get(f"/api/book/{book.id}/chapter/{chapter.number}/preview")

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["cache-control"] == "no-cache"
    assert response.content[:4] == b"RIFF"


def test_preview_endpoint_returns_404_when_audio_not_generated(client: TestClient, test_db: Session) -> None:
    """Preview streaming should fail clearly until a chapter has audio."""

    book, chapter = _create_book_with_chapter(test_db, audio_path=None)

    response = client.get(f"/api/book/{book.id}/chapter/{chapter.number}/preview")

    assert response.status_code == 404
    assert response.json()["detail"] == "Audio not yet generated for this chapter"


def test_send_macos_notification_is_safe_on_non_darwin(monkeypatch) -> None:
    """Notification helpers should no-op off macOS instead of crashing."""

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(notifications.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        notifications.subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    delivered = notifications.send_macos_notification("Title", "Message")

    assert delivered is False
    assert calls == []


def test_health_endpoint_allows_localhost_origins_and_security_headers(client: TestClient) -> None:
    """Allowed localhost origins should receive CORS headers alongside security headers."""

    response = client.get("/api/health", headers={"Origin": "http://localhost:3000"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-xss-protection"] == "1; mode=block"


def test_health_endpoint_rejects_non_localhost_origins(client: TestClient) -> None:
    """Non-local origins should not receive CORS access headers."""

    response = client.get("/api/health", headers={"Origin": "https://evil.example"})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
