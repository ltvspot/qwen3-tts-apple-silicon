"""API tests for persisted application settings."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from src.api import settings_routes
from src.config import SettingsManager
from src.database import AudioQAResult, Book, Chapter, ChapterType
from src.engines.pronunciation_dictionary import PronunciationDictionary


def _manager(test_db: Session, config_file: Path) -> SettingsManager:
    """Create a settings manager bound to the isolated test database."""

    session_factory = sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    return SettingsManager(session_factory=session_factory, config_file=config_file)


def _pronunciation_dictionary(tmp_path: Path) -> PronunciationDictionary:
    return PronunciationDictionary(tmp_path / "pronunciation.json")


def test_get_settings_returns_current_shape(client, test_db: Session, tmp_path: Path, monkeypatch) -> None:
    """GET /api/settings should serialize the current nested settings payload."""

    manager = _manager(test_db, tmp_path / "config.json")
    monkeypatch.setattr(settings_routes, "get_settings_manager", lambda: manager)

    response = client.get("/api/settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["narrator_name"] == "Kent Zimering"
    assert payload["default_voice"]["name"] == "Ethan"
    assert payload["output_preferences"]["mp3_bitrate"] == 192
    assert payload["engine_config"]["chunk_timeout_seconds"] == 120


def test_put_settings_partial_update_deep_merges(client, test_db: Session, tmp_path: Path, monkeypatch) -> None:
    """Partial updates should merge nested structures instead of replacing them."""

    manager = _manager(test_db, tmp_path / "config.json")
    monkeypatch.setattr(settings_routes, "get_settings_manager", lambda: manager)

    response = client.put(
        "/api/settings",
        json={
            "output_preferences": {
                "mp3_bitrate": 256,
                "silence_duration_chapters": 2.5,
            },
            "default_voice": {
                "speed": 1.2,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert "output_preferences.mp3_bitrate" in payload["updated_fields"]
    assert payload["settings"]["output_preferences"]["mp3_bitrate"] == 256
    assert payload["settings"]["output_preferences"]["sample_rate"] == 44100
    assert payload["settings"]["default_voice"]["name"] == "Ethan"
    assert payload["settings"]["default_voice"]["speed"] == 1.2


def test_put_settings_full_update_replaces_payload(client, test_db: Session, tmp_path: Path, monkeypatch) -> None:
    """Full updates should persist the supplied settings payload."""

    manager = _manager(test_db, tmp_path / "config.json")
    monkeypatch.setattr(settings_routes, "get_settings_manager", lambda: manager)

    response = client.put(
        "/api/settings",
        json={
            "narrator_name": "Alex Narrator",
            "manuscript_source_folder": "/books/manuscripts",
            "default_voice": {
                "name": "Nova",
                "emotion": "calm",
                "speed": 0.9,
            },
            "engine_config": {
                "model_path": "models/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit",
                "chunk_timeout_seconds": 180,
            },
            "output_preferences": {
                "mp3_bitrate": 320,
                "sample_rate": 48000,
                "silence_duration_chapters": 3.0,
                "silence_duration_opening": 4.0,
                "silence_duration_closing": 4.5,
                "include_album_art": False,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["settings"]["narrator_name"] == "Alex Narrator"
    assert payload["settings"]["default_voice"]["name"] == "Nova"
    assert payload["settings"]["engine_config"]["chunk_timeout_seconds"] == 180
    assert payload["settings"]["output_preferences"]["sample_rate"] == 48000
    assert payload["settings"]["output_preferences"]["include_album_art"] is False


def test_get_settings_schema_returns_json_schema(client) -> None:
    """Schema endpoint should expose enum and nested property metadata."""

    response = client.get("/api/settings/schema")

    assert response.status_code == 200
    payload = response.json()
    assert payload["type"] == "object"
    voice_name = payload["$defs"]["VoiceSettings"]["properties"]["name"]
    assert "Ethan" in voice_name["enum"]
    assert payload["$defs"]["OutputSettings"]["properties"]["mp3_bitrate"]["enum"] == [128, 192, 256, 320]
    assert payload["$defs"]["OutputSettings"]["properties"]["m4b_bitrate"]["enum"] == ["64k", "96k", "128k", "192k", "256k"]
    assert payload["$defs"]["EngineSettings"]["properties"]["chunk_timeout_seconds"]["default"] == 120


def test_put_settings_invalid_values_returns_400(client, test_db: Session, tmp_path: Path, monkeypatch) -> None:
    """Invalid updates should surface a validation error."""

    manager = _manager(test_db, tmp_path / "config.json")
    monkeypatch.setattr(settings_routes, "get_settings_manager", lambda: manager)

    response = client.put(
        "/api/settings",
        json={
            "default_voice": {
                "speed": 4.0,
            },
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]


def test_get_pronunciation_dictionary_returns_global_and_book_entries(
    client,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Pronunciation dictionary endpoint should serialize both scopes."""

    dictionary = _pronunciation_dictionary(tmp_path)
    dictionary.upsert_global("Thoreau", "thuh-ROH")
    dictionary.upsert_book(12, "Walden", "WAWL-den")
    monkeypatch.setattr(settings_routes, "_pronunciation_dictionary", lambda: dictionary)

    response = client.get("/api/pronunciation")

    assert response.status_code == 200
    payload = response.json()
    assert payload["global"]["Thoreau"] == "thuh-ROH"
    assert payload["per_book"]["12"]["Walden"] == "WAWL-den"


def test_put_pronunciation_global_updates_dictionary(client, tmp_path: Path, monkeypatch) -> None:
    """Saving a global pronunciation should update the persisted dictionary payload."""

    dictionary = _pronunciation_dictionary(tmp_path)
    monkeypatch.setattr(settings_routes, "_pronunciation_dictionary", lambda: dictionary)

    response = client.put("/api/pronunciation/global/Emerson", json={"pronunciation": "EM-er-sun"})

    assert response.status_code == 200
    assert response.json()["global"]["Emerson"] == "EM-er-sun"
    assert dictionary.lookup("Emerson") == "EM-er-sun"


def test_put_pronunciation_book_requires_existing_book(client, tmp_path: Path, monkeypatch) -> None:
    """Book-scoped pronunciation entries should reject unknown book ids."""

    dictionary = _pronunciation_dictionary(tmp_path)
    monkeypatch.setattr(settings_routes, "_pronunciation_dictionary", lambda: dictionary)

    response = client.put("/api/pronunciation/book/999/Thoreau", json={"pronunciation": "thuh-ROH"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Book 999 not found"


def test_delete_pronunciation_book_removes_entry(client, test_db: Session, tmp_path: Path, monkeypatch) -> None:
    """Deleting a book-scoped pronunciation should remove the stored entry."""

    dictionary = _pronunciation_dictionary(tmp_path)
    book = Book(title="Delete Me", author="A", folder_path="delete-me")
    test_db.add(book)
    test_db.commit()
    test_db.refresh(book)
    dictionary.upsert_book(book.id, "Ariadne", "air-ee-AD-nee")
    monkeypatch.setattr(settings_routes, "_pronunciation_dictionary", lambda: dictionary)

    response = client.delete(f"/api/pronunciation/book/{book.id}/Ariadne")

    assert response.status_code == 200
    assert str(book.id) not in response.json()["per_book"]
    assert dictionary.lookup("Ariadne", book_id=book.id) is None


def test_get_pronunciation_suggestions_returns_qa_candidates(
    client,
    test_db: Session,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Suggestion endpoint should surface proper nouns from deep-QA mismatches."""

    dictionary = _pronunciation_dictionary(tmp_path)
    book = Book(title="Walden", author="Henry", folder_path="walden")
    test_db.add(book)
    test_db.commit()
    test_db.refresh(book)
    chapter = Chapter(
        book_id=book.id,
        number=1,
        title="Chapter 1",
        type=ChapterType.CHAPTER,
        text_content="Thoreau returned to Concord.",
        word_count=4,
    )
    test_db.add(chapter)
    test_db.commit()
    test_db.refresh(chapter)
    test_db.add(
        AudioQAResult(
            book_id=book.id,
            chapter_id=chapter.id,
            chapter_n=1,
            overall_score=79.0,
            report_json=json.dumps(
                {
                    "transcription": {
                        "word_error_rate": 0.14,
                        "diff": [{"expected": "Concord", "actual": "conquered"}],
                    }
                }
            ),
        )
    )
    test_db.commit()
    monkeypatch.setattr(settings_routes, "_pronunciation_dictionary", lambda: dictionary)

    response = client.get("/api/pronunciation/suggestions")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["book_id"] == book.id
    assert payload[0]["word"] == "Concord"
