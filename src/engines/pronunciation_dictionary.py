"""JSON-backed pronunciation dictionary with global and per-book overrides."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from src.database import AudioQAResult, Book, Chapter

PRONUNCIATION_PATH = Path("data/pronunciation.json")
DEFAULT_PRONUNCIATION_DICTIONARY = {
    "global": {
        "Château": "shah-TOH",
        "naïve": "nah-EEV",
        "résumé": "REH-zoo-may",
    },
    "per_book": {
        "29": {
            "Thoreau": "thuh-ROH",
        }
    },
}
_COMMON_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "but",
    "by",
    "for",
    "from",
    "he",
    "her",
    "his",
    "i",
    "in",
    "it",
    "its",
    "of",
    "on",
    "or",
    "our",
    "she",
    "the",
    "their",
    "them",
    "they",
    "to",
    "we",
    "with",
    "you",
}
_TOKEN_PATTERN = re.compile(r"\b[\w'’-]+\b", re.UNICODE)


@dataclass(slots=True)
class PronunciationSuggestion:
    """One pronunciation dictionary suggestion derived from QA mismatches."""

    book_id: int
    book_title: str
    chapter_n: int
    word: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable suggestion payload."""

        return {
            "book_id": self.book_id,
            "book_title": self.book_title,
            "chapter_n": self.chapter_n,
            "word": self.word,
            "reason": self.reason,
        }


class PronunciationDictionary:
    """Persist and apply simple phonetic respellings for TTS pre-processing."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or PRONUNCIATION_PATH
        self._data = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        if self.path.exists():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return self._normalize_payload(payload)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        normalized = self._normalize_payload(DEFAULT_PRONUNCIATION_DICTIONARY)
        self.path.write_text(json.dumps(normalized, indent=2, ensure_ascii=False), encoding="utf-8")
        return normalized

    def _normalize_payload(self, payload: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(payload, dict):
            payload = {}

        global_entries = payload.get("global", {})
        per_book_entries = payload.get("per_book", {})

        normalized_global: dict[str, str] = {}
        if isinstance(global_entries, dict):
            for word, pronunciation in global_entries.items():
                cleaned_word = str(word).strip()
                cleaned_pronunciation = str(pronunciation).strip()
                if cleaned_word and cleaned_pronunciation:
                    normalized_global[cleaned_word] = cleaned_pronunciation

        normalized_per_book: dict[str, dict[str, str]] = {}
        if isinstance(per_book_entries, dict):
            for raw_book_id, entries in per_book_entries.items():
                if not isinstance(entries, dict):
                    continue
                book_id = str(raw_book_id).strip()
                normalized_entries: dict[str, str] = {}
                for word, pronunciation in entries.items():
                    cleaned_word = str(word).strip()
                    cleaned_pronunciation = str(pronunciation).strip()
                    if cleaned_word and cleaned_pronunciation:
                        normalized_entries[cleaned_word] = cleaned_pronunciation
                if normalized_entries:
                    normalized_per_book[book_id] = normalized_entries

        return {
            "global": dict(sorted(normalized_global.items(), key=lambda item: item[0].lower())),
            "per_book": {
                book_id: dict(sorted(entries.items(), key=lambda item: item[0].lower()))
                for book_id, entries in sorted(normalized_per_book.items(), key=lambda item: item[0])
            },
        }

    def save(self) -> None:
        """Persist the current pronunciation dictionary atomically enough for local use."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")

    def full_dictionary(self) -> dict[str, dict[str, Any]]:
        """Return the current pronunciation dictionary payload."""

        return {
            "global": dict(self._data["global"]),
            "per_book": {
                book_id: dict(entries)
                for book_id, entries in self._data["per_book"].items()
            },
        }

    def entries(self) -> list[dict[str, Any]]:
        """Return flattened entries for UI tables."""

        flattened: list[dict[str, Any]] = []
        for word, pronunciation in self._data["global"].items():
            flattened.append(
                {
                    "scope": "global",
                    "book_id": None,
                    "word": word,
                    "pronunciation": pronunciation,
                }
            )
        for book_id, entries in self._data["per_book"].items():
            for word, pronunciation in entries.items():
                flattened.append(
                    {
                        "scope": "book",
                        "book_id": int(book_id),
                        "word": word,
                        "pronunciation": pronunciation,
                    }
                )
        return flattened

    def lookup(self, word: str, *, book_id: int | None = None) -> str | None:
        """Return the effective pronunciation for one word, honoring per-book overrides."""

        cleaned = word.strip()
        if not cleaned:
            return None

        if book_id is not None:
            book_entries = self._data["per_book"].get(str(book_id), {})
            for candidate, pronunciation in book_entries.items():
                if candidate.casefold() == cleaned.casefold():
                    return pronunciation

        for candidate, pronunciation in self._data["global"].items():
            if candidate.casefold() == cleaned.casefold():
                return pronunciation
        return None

    def replace_text(self, text: str, *, book_id: int | None = None) -> str:
        """Replace dictionary words with phonetic respellings before synthesis."""

        if not text.strip():
            return text

        effective_entries = dict(self._data["global"])
        if book_id is not None:
            effective_entries.update(self._data["per_book"].get(str(book_id), {}))
        if not effective_entries:
            return text

        ordered_entries = sorted(effective_entries.items(), key=lambda item: len(item[0]), reverse=True)
        replaced = text
        for word, pronunciation in ordered_entries:
            pattern = re.compile(rf"(?<!\w){re.escape(word)}(?!\w)", re.IGNORECASE)
            replaced = pattern.sub(pronunciation, replaced)
        return replaced

    def upsert_global(self, word: str, pronunciation: str) -> dict[str, dict[str, Any]]:
        """Add or update one global pronunciation entry."""

        self._upsert_entry(self._data["global"], word, pronunciation)
        self.save()
        return self.full_dictionary()

    def upsert_book(self, book_id: int, word: str, pronunciation: str) -> dict[str, dict[str, Any]]:
        """Add or update one per-book pronunciation entry."""

        book_key = str(book_id)
        entries = self._data["per_book"].setdefault(book_key, {})
        self._upsert_entry(entries, word, pronunciation)
        self._data["per_book"][book_key] = dict(sorted(entries.items(), key=lambda item: item[0].lower()))
        self.save()
        return self.full_dictionary()

    def delete_global(self, word: str) -> dict[str, dict[str, Any]]:
        """Delete one global pronunciation entry when present."""

        self._delete_entry(self._data["global"], word)
        self.save()
        return self.full_dictionary()

    def delete_book(self, book_id: int, word: str) -> dict[str, dict[str, Any]]:
        """Delete one per-book pronunciation entry when present."""

        entries = self._data["per_book"].get(str(book_id))
        if entries is not None:
            self._delete_entry(entries, word)
            if not entries:
                self._data["per_book"].pop(str(book_id), None)
        self.save()
        return self.full_dictionary()

    @staticmethod
    def _upsert_entry(entries: dict[str, str], word: str, pronunciation: str) -> None:
        cleaned_word = word.strip()
        cleaned_pronunciation = pronunciation.strip()
        if not cleaned_word or not cleaned_pronunciation:
            raise ValueError("Both word and pronunciation are required.")

        existing = next((candidate for candidate in entries if candidate.casefold() == cleaned_word.casefold()), None)
        if existing is not None and existing != cleaned_word:
            entries.pop(existing, None)
        entries[cleaned_word] = cleaned_pronunciation

    @staticmethod
    def _delete_entry(entries: dict[str, str], word: str) -> None:
        existing = next((candidate for candidate in entries if candidate.casefold() == word.strip().casefold()), None)
        if existing is not None:
            entries.pop(existing, None)

    def suggestion_payload(self, db: Session, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return pronunciation suggestions derived from stored deep-QA mismatches."""

        suggestions = self.suggest_from_audio_qa(db, limit=limit)
        return [suggestion.to_dict() for suggestion in suggestions]

    def suggest_from_audio_qa(self, db: Session, *, limit: int = 50) -> list[PronunciationSuggestion]:
        """Suggest proper nouns that appear in mismatched deep-QA transcripts."""

        rows = (
            db.query(AudioQAResult, Chapter, Book)
            .join(Chapter, Chapter.id == AudioQAResult.chapter_id)
            .join(Book, Book.id == AudioQAResult.book_id)
            .order_by(AudioQAResult.checked_at.desc())
            .limit(max(limit * 3, limit))
            .all()
        )

        seen_words: set[tuple[int, str]] = set()
        suggestions: list[PronunciationSuggestion] = []

        for audio_qa, chapter, book in rows:
            try:
                payload = json.loads(audio_qa.report_json)
            except json.JSONDecodeError:
                continue

            transcription = payload.get("transcription", {})
            if float(transcription.get("word_error_rate") or 0.0) <= 0:
                continue

            original_words = self._capitalized_tokens(chapter.text_content or "")
            dictionary_words = {entry["word"].casefold() for entry in self.entries()}
            for diff_entry in transcription.get("diff", []):
                expected = str(diff_entry.get("expected") or "").strip()
                if not expected:
                    continue
                for token in expected.split():
                    normalized = token.strip(".,!?;:\"'()[]{}")
                    if not normalized:
                        continue
                    original = original_words.get(normalized.casefold())
                    if original is None:
                        continue
                    if original.casefold() in dictionary_words:
                        continue
                    if not self._looks_like_proper_noun(original):
                        continue

                    key = (book.id, original.casefold())
                    if key in seen_words:
                        continue
                    seen_words.add(key)
                    suggestions.append(
                        PronunciationSuggestion(
                            book_id=book.id,
                            book_title=book.title,
                            chapter_n=chapter.number,
                            word=original,
                            reason="Detected in a deep-QA transcription mismatch and looks like a proper noun.",
                        )
                    )
                    if len(suggestions) >= limit:
                        return suggestions

        return suggestions

    @staticmethod
    def _capitalized_tokens(text: str) -> dict[str, str]:
        lookup: dict[str, str] = {}
        for token in _TOKEN_PATTERN.findall(text):
            normalized = token.strip()
            if normalized and normalized[0].isupper():
                lookup.setdefault(normalized.casefold(), normalized)
        return lookup

    @staticmethod
    def _looks_like_proper_noun(word: str) -> bool:
        cleaned = word.strip(".,!?;:\"'()[]{}")
        if len(cleaned) < 3:
            return False
        if cleaned.casefold() in _COMMON_WORDS:
            return False
        return bool(cleaned[:1].isupper() and any(character.isalpha() for character in cleaned))
