"""Audiobook generation orchestration for chapter-by-chapter TTS output."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, selectinload

from src.config import settings
from src.database import Book, BookGenerationStatus, BookStatus, Chapter, ChapterStatus, ChapterType, utc_now
from src.engines import AudioStitcher, TTSEngine, TextChunker

logger = logging.getLogger(__name__)


class GenerationCancelled(Exception):
    """Raised when an in-flight generation job has been cancelled."""


def _slugify(value: str, *, fallback: str, max_length: int) -> str:
    """Return a filesystem-safe slug with predictable ASCII-only output."""

    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not normalized:
        normalized = fallback
    return normalized[:max_length].strip("-") or fallback


class AudiobookGenerator:
    """Generate chapter WAV files and persist progress to the database."""

    def __init__(self, engine: TTSEngine) -> None:
        """Initialize the generator with a configured TTS engine instance."""

        self.engine = engine
        self.output_path = Path(settings.OUTPUTS_PATH)

    def close(self) -> None:
        """Release the underlying engine if it has been loaded."""

        if getattr(self.engine, "loaded", False):
            self.engine.unload()

    async def generate_book(
        self,
        book_id: int,
        db_session: Session,
        progress_callback: Callable[[int, float], Awaitable[None]] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        force: bool = False,
        voice_name: str = "Ethan",
        emotion: str | None = None,
        speed: float = 1.0,
    ) -> dict[str, Any]:
        """
        Generate all chapters for a parsed book.

        Returns a summary dictionary with status, counts, duration, and errors.
        """

        self._ensure_engine_loaded()

        book = (
            db_session.query(Book)
            .options(selectinload(Book.chapters))
            .filter(Book.id == book_id)
            .first()
        )
        if book is None:
            raise ValueError(f"Book {book_id} not found")

        chapters = list(book.chapters)
        if not chapters:
            raise ValueError(f"No chapters found for book {book_id}")

        logger.info("Starting generation for book %s with %s chapters", book_id, len(chapters))
        book.status = BookStatus.GENERATING
        book.generation_status = BookGenerationStatus.GENERATING
        book.generation_started_at = utc_now()
        book.generation_eta_seconds = None
        db_session.commit()

        errors: list[str] = []
        failed_chapters: list[int] = []
        generated_chapters = 0
        total_duration = 0.0

        for chapter_index, chapter in enumerate(chapters):
            self._raise_if_cancelled(should_cancel)

            if chapter.status == ChapterStatus.GENERATED and not force:
                if progress_callback is not None:
                    await progress_callback(chapter.number, ((chapter_index + 1) / len(chapters)) * 100)
                continue

            async def chapter_progress_callback(chunk_progress: float) -> None:
                if progress_callback is None:
                    return
                overall_progress = ((chapter_index + chunk_progress) / len(chapters)) * 100
                await progress_callback(chapter.number, overall_progress)

            try:
                total_duration += await self.generate_chapter(
                    book_id,
                    chapter,
                    db_session,
                    progress_callback=chapter_progress_callback,
                    should_cancel=should_cancel,
                    force=force,
                    voice_name=voice_name,
                    emotion=emotion,
                    speed=speed,
                )
                generated_chapters += 1

                if progress_callback is not None:
                    await progress_callback(chapter.number, ((chapter_index + 1) / len(chapters)) * 100)
            except GenerationCancelled:
                book.status = BookStatus.PARSED
                book.generation_status = BookGenerationStatus.IDLE
                book.generation_eta_seconds = None
                db_session.commit()
                raise
            except Exception as exc:
                error_message = f"Chapter {chapter.number}: {exc}"
                logger.error(error_message)
                errors.append(error_message)
                failed_chapters.append(chapter.number)

        final_status = "success"
        if failed_chapters:
            final_status = "failed" if generated_chapters == 0 else "partial"

        book.status = BookStatus.GENERATED if not failed_chapters else BookStatus.PARSED
        book.generation_status = (
            BookGenerationStatus.IDLE if not failed_chapters else BookGenerationStatus.ERROR
        )
        book.generation_eta_seconds = 0 if not failed_chapters else None
        db_session.commit()

        logger.info(
            "Generation finished for book %s: status=%s generated=%s/%s duration=%.2fs",
            book_id,
            final_status,
            generated_chapters,
            len(chapters),
            total_duration,
        )

        return {
            "status": final_status,
            "total_chapters": len(chapters),
            "generated_chapters": generated_chapters,
            "failed_chapters": failed_chapters,
            "total_duration": total_duration,
            "errors": errors,
        }

    async def generate_chapter(
        self,
        book_id: int,
        chapter: Chapter,
        db_session: Session,
        progress_callback: Callable[[float], Awaitable[None]] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        force: bool = False,
        voice_name: str = "Ethan",
        emotion: str | None = None,
        speed: float = 1.0,
    ) -> float:
        """Generate and persist audio for a single chapter."""

        self._ensure_engine_loaded()

        text_content = (chapter.text_content or "").strip()
        if not text_content:
            raise ValueError(f"Chapter {chapter.number} has no text content")
        if chapter.status == ChapterStatus.GENERATED and not force:
            raise ValueError(f"Chapter {chapter.number} already has generated audio")

        logger.info(
            "Generating audio for book %s chapter %s (%s words)",
            book_id,
            chapter.number,
            chapter.word_count or 0,
        )

        chapter.status = ChapterStatus.GENERATING
        chapter.started_at = utc_now()
        chapter.completed_at = None
        chapter.error_message = None
        db_session.commit()

        try:
            chunks = TextChunker.chunk_text(text_content, self.engine.max_chunk_chars)
            audio_chunks = []
            chapter_speed = speed * self._chapter_speed(chapter)

            for chunk_index, chunk in enumerate(chunks):
                self._raise_if_cancelled(should_cancel)

                audio = await asyncio.to_thread(
                    self.engine.generate,
                    chunk,
                    voice_name,
                    emotion,
                    chapter_speed,
                )
                audio_chunks.append(audio)

                if progress_callback is not None:
                    await progress_callback((chunk_index + 1) / len(chunks))

            self._raise_if_cancelled(should_cancel)

            final_audio = AudioStitcher.stitch(audio_chunks)
            audio_path = self._get_chapter_audio_path(book_id, chapter)
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(final_audio.export, str(audio_path), format="wav")

            duration = len(final_audio) / 1000.0
            chapter.audio_path = str(audio_path.relative_to(self.output_path))
            chapter.duration_seconds = duration
            chapter.status = ChapterStatus.GENERATED
            chapter.completed_at = utc_now()
            chapter.audio_file_size_bytes = audio_path.stat().st_size
            db_session.commit()

            logger.info(
                "Generated book %s chapter %s -> %s (%.2fs)",
                book_id,
                chapter.number,
                chapter.audio_path,
                duration,
            )

            return duration
        except GenerationCancelled:
            chapter.status = ChapterStatus.PENDING
            chapter.started_at = None
            chapter.completed_at = None
            chapter.error_message = None
            db_session.commit()
            raise
        except Exception as exc:
            chapter.status = ChapterStatus.FAILED
            chapter.completed_at = utc_now()
            chapter.error_message = str(exc)
            db_session.commit()
            raise

    def _ensure_engine_loaded(self) -> None:
        """Load the engine on first generation request."""

        if not getattr(self.engine, "loaded", False):
            self.engine.load()

    def _raise_if_cancelled(self, should_cancel: Callable[[], bool] | None) -> None:
        """Raise a cancellation signal if the active job was cancelled."""

        if should_cancel is not None and should_cancel():
            raise GenerationCancelled("Generation cancelled.")

    def _chapter_speed(self, chapter: Chapter) -> float:
        """Return the speech speed for a chapter type."""

        if chapter.type in {ChapterType.OPENING_CREDITS, ChapterType.CLOSING_CREDITS}:
            return 0.9
        return 1.0

    def _chapter_audio_name(self, chapter: Chapter) -> str:
        """Return the stable audio filename for a chapter."""

        sequence = f"{chapter.number:02d}"

        if chapter.type == ChapterType.OPENING_CREDITS:
            return f"{sequence}-opening-credits.wav"
        if chapter.type == ChapterType.INTRODUCTION:
            return f"{sequence}-introduction.wav"
        if chapter.type == ChapterType.CLOSING_CREDITS:
            return f"{sequence}-closing-credits.wav"

        chapter_index = self._book_chapter_index(chapter)
        title_slug = _slugify(
            chapter.title or f"chapter-{chapter_index}",
            fallback=f"chapter-{chapter_index}",
            max_length=40,
        )
        return f"{sequence}-ch{chapter_index:02d}-{title_slug}.wav"

    def _book_chapter_index(self, chapter: Chapter) -> int:
        """Return the 1-based index among only true book chapters."""

        if chapter.book is None:
            return max(chapter.number, 1)

        chapter_count = 0
        for candidate in chapter.book.chapters:
            if candidate.type == ChapterType.CHAPTER:
                chapter_count += 1
            if candidate.id == chapter.id:
                return max(chapter_count, 1)

        return max(chapter.number, 1)

    def _get_chapter_audio_path(self, book_id: int, chapter: Chapter) -> Path:
        """Return the output path for a chapter WAV file."""

        book = chapter.book
        if book is None:
            raise ValueError("Chapter book relationship is not loaded")

        book_slug = _slugify(book.title, fallback=f"book-{book_id}", max_length=50)
        folder_name = f"{book_id}-{book_slug}"
        return self.output_path / folder_name / "chapters" / self._chapter_audio_name(chapter)
