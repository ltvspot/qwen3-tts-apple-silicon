"""Tests for pronunciation dictionary persistence and QA suggestions."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from src.database import AudioQAResult, Book, Chapter, ChapterType
from src.engines.chunker import TextChunker
from src.engines.pronunciation_dictionary import PronunciationDictionary


def _create_book(test_db: Session, *, title: str = "Pronunciation Book") -> Book:
    book = Book(
        title=title,
        author="Dictionary Author",
        folder_path=title.lower().replace(" ", "-"),
    )
    test_db.add(book)
    test_db.commit()
    test_db.refresh(book)
    return book


def _create_chapter(test_db: Session, *, book_id: int, number: int = 1, text: str) -> Chapter:
    chapter = Chapter(
        book_id=book_id,
        number=number,
        title=f"Chapter {number}",
        type=ChapterType.CHAPTER,
        text_content=text,
        word_count=len(text.split()),
    )
    test_db.add(chapter)
    test_db.commit()
    test_db.refresh(chapter)
    return chapter


def _dictionary(tmp_path: Path) -> PronunciationDictionary:
    return PronunciationDictionary(tmp_path / "pronunciation.json")


def test_replace_text_applies_global_pronunciations(tmp_path: Path) -> None:
    """Global dictionary entries should be substituted before synthesis."""

    dictionary = _dictionary(tmp_path)
    dictionary.upsert_global("Thoreau", "thuh-ROH")

    replaced = dictionary.replace_text("Thoreau wrote Walden.")

    assert replaced == "thuh-ROH wrote Walden."


def test_replace_text_prefers_per_book_override(tmp_path: Path) -> None:
    """Book-specific entries should override matching global entries."""

    dictionary = _dictionary(tmp_path)
    dictionary.upsert_global("Aramis", "AIR-uh-miss")
    dictionary.upsert_book(9, "Aramis", "ah-rah-MEE")

    replaced = dictionary.replace_text("Aramis arrived.", book_id=9)

    assert replaced == "ah-rah-MEE arrived."


def test_dictionary_crud_is_case_insensitive_and_persists(tmp_path: Path) -> None:
    """Updates should replace case-insensitive duplicates and survive reloads."""

    dictionary = _dictionary(tmp_path)
    dictionary.upsert_global("Chatelet", "sha-teh-LAY")
    dictionary.upsert_global("chatelet", "SHA-teh-lay")
    dictionary.upsert_book(4, "Ariadne", "air-ee-AD-nee")
    dictionary.delete_book(4, "ariadne")

    reloaded = PronunciationDictionary(tmp_path / "pronunciation.json")

    assert reloaded.lookup("Chatelet") == "SHA-teh-lay"
    assert reloaded.lookup("Ariadne", book_id=4) is None


def test_preprocess_for_tts_returns_original_text_without_dictionary() -> None:
    """Chunk preprocessing should be a no-op when no dictionary is supplied."""

    text = "Emerson and Thoreau walked together."

    assert TextChunker.preprocess_for_tts(text) == text


def test_preprocess_for_tts_uses_dictionary_replacements(tmp_path: Path) -> None:
    """Chunk preprocessing should apply the pronunciation dictionary."""

    dictionary = _dictionary(tmp_path)
    dictionary.upsert_global("Emerson", "EM-er-sun")

    replaced = TextChunker.preprocess_for_tts(
        "Emerson writes essays.",
        book_id=2,
        pronunciation_dictionary=dictionary,
    )

    assert replaced == "EM-er-sun writes essays."


def test_suggestion_payload_extracts_proper_nouns_from_qa_mismatch(test_db: Session, tmp_path: Path) -> None:
    """Deep-QA diff mismatches should surface proper-noun pronunciation suggestions."""

    dictionary = _dictionary(tmp_path)
    book = _create_book(test_db, title="Walden")
    chapter = _create_chapter(
        test_db,
        book_id=book.id,
        text="Thoreau visited Concord with Emerson.",
    )
    test_db.add(
        AudioQAResult(
            book_id=book.id,
            chapter_id=chapter.id,
            chapter_n=chapter.number,
            overall_score=81.0,
            report_json=json.dumps(
                {
                    "transcription": {
                        "word_error_rate": 0.23,
                        "diff": [
                            {"expected": "Concord", "actual": "conquered"},
                            {"expected": "Thoreau", "actual": "throw"},
                        ],
                    }
                }
            ),
        )
    )
    test_db.commit()

    suggestions = dictionary.suggestion_payload(test_db)

    assert any(suggestion["book_id"] == book.id for suggestion in suggestions)
    assert any(suggestion["word"] == "Concord" for suggestion in suggestions)
    assert all("proper noun" in suggestion["reason"].lower() for suggestion in suggestions)
