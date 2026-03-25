"""Audiobook generation orchestration for chapter-by-chapter TTS output."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from pydub.audio_segment import AudioSegment
from sqlalchemy.orm import Session, selectinload

from src.config import settings
from src.database import (
    Book,
    BookGenerationStatus,
    BookStatus,
    Chapter,
    ChapterStatus,
    ChapterType,
    QAStatus,
    utc_now,
)
from src.engines import AudioStitcher, TTSEngine, TextChunker
from src.pipeline.qa_checker import persist_qa_result, run_qa_checks_for_chapter

logger = logging.getLogger(__name__)

TRANSIENT_GENERATION_ERRORS = (TimeoutError, MemoryError, OSError)
PERMANENT_GENERATION_ERRORS = (FileNotFoundError, ValueError)


class GenerationCancelled(Exception):
    """Raised when an in-flight generation job has been cancelled."""


class ChunkGenerationExhaustedError(RuntimeError):
    """Raised when a chunk fails generation or validation across all retry attempts."""


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
        self.retry_backoff_seconds: tuple[float, ...] = (0.5, 1.0)

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
            manual_review_notes: list[str] = []

            for chunk_index, chunk in enumerate(chunks):
                self._raise_if_cancelled(should_cancel)

                try:
                    audio = await self._generate_chunk_with_retry(
                        chunk,
                        chunk_index=chunk_index,
                        voice_name=voice_name,
                        emotion=emotion,
                        speed=chapter_speed,
                        chapter_number=chapter.number,
                        book_id=book_id,
                        should_cancel=should_cancel,
                    )
                except ChunkGenerationExhaustedError as exc:
                    note = (
                        f"Chunk {chunk_index} failed after 3 attempts and was skipped: {exc}"
                    )
                    manual_review_notes.append(note)
                    logger.error(
                        "Skipping book %s chapter %s chunk %s after exhausted retries: %s",
                        book_id,
                        chapter.number,
                        chunk_index,
                        exc,
                    )
                    continue

                audio_chunks.append(audio)

                if progress_callback is not None:
                    await progress_callback((chunk_index + 1) / len(chunks))

            self._raise_if_cancelled(should_cancel)
            if not audio_chunks:
                raise RuntimeError(
                    f"Chapter {chapter.number} produced no valid audio chunks after retries."
                )

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

            try:
                qa_result = await run_qa_checks_for_chapter(chapter)
                persist_qa_result(db_session, chapter, qa_result)
                self._flag_manual_review(chapter, manual_review_notes)
                db_session.commit()
            except Exception:
                db_session.rollback()
                self._flag_manual_review(chapter, manual_review_notes)
                db_session.commit()
                logger.exception(
                    "Automatic QA failed for book %s chapter %s after successful generation",
                    book_id,
                    chapter.number,
                )

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

    async def _generate_chunk_with_retry(
        self,
        text: str,
        *,
        chunk_index: int,
        voice_name: str,
        emotion: str | None,
        speed: float,
        chapter_number: int,
        book_id: int,
        should_cancel: Callable[[], bool] | None,
    ) -> Any:
        """Generate one chunk with retries for transient engine failures."""

        max_attempts = len(self.retry_backoff_seconds) + 1
        for attempt in range(1, max_attempts + 1):
            self._raise_if_cancelled(should_cancel)
            try:
                audio = await self._generate_chunk(
                    text,
                    voice_name=voice_name,
                    emotion=emotion,
                    speed=speed,
                )
            except PERMANENT_GENERATION_ERRORS:
                raise
            except TRANSIENT_GENERATION_ERRORS as exc:
                if attempt >= max_attempts:
                    logger.error(
                        "Transient generation error exhausted retries for book %s chapter %s chunk %s: %s",
                        book_id,
                        chapter_number,
                        chunk_index,
                        exc,
                    )
                    raise ChunkGenerationExhaustedError(str(exc)) from exc

                backoff = self.retry_backoff_seconds[attempt - 1]
                logger.warning(
                    "Retrying generation for book %s chapter %s chunk %s after transient error (%s/%s): %s",
                    book_id,
                    chapter_number,
                    chunk_index,
                    attempt,
                    max_attempts,
                    exc,
                )
                await asyncio.sleep(backoff)
                continue

            try:
                self._validate_chunk(audio, chunk_index, text)
                return audio
            except ValueError as exc:
                if attempt >= max_attempts:
                    logger.error(
                        "Chunk validation exhausted retries for book %s chapter %s chunk %s: %s",
                        book_id,
                        chapter_number,
                        chunk_index,
                        exc,
                    )
                    raise ChunkGenerationExhaustedError(str(exc)) from exc

                backoff = self.retry_backoff_seconds[attempt - 1]
                logger.warning(
                    "Retrying generation for book %s chapter %s chunk %s after validation failure (%s/%s): %s",
                    book_id,
                    chapter_number,
                    chunk_index,
                    attempt,
                    max_attempts,
                    exc,
                )
                await asyncio.sleep(backoff)

        raise RuntimeError("Chunk generation retry loop exited unexpectedly.")

    async def _generate_chunk(
        self,
        text: str,
        *,
        voice_name: str,
        emotion: str | None,
        speed: float,
    ) -> AudioSegment:
        """Generate a single audio chunk, using engine-level timeouts when available."""

        timeout_generate = getattr(self.engine, "generate_chunk_with_timeout", None)
        if callable(timeout_generate):
            return await timeout_generate(text, voice_name, emotion=emotion, speed=speed)

        return await asyncio.to_thread(
            self.engine.generate,
            text,
            voice_name,
            emotion,
            speed,
        )

    def _validate_chunk(self, chunk: AudioSegment, chunk_index: int, expected_text: str) -> None:
        """Validate one generated chunk before it is stitched into a chapter."""

        if len(chunk) < 100:
            raise ValueError(f"Chunk {chunk_index} too short: {len(chunk)}ms (min 100ms)")
        if len(chunk) > 120_000:
            raise ValueError(f"Chunk {chunk_index} too long: {len(chunk)}ms (max 120s)")
        if chunk.dBFS < -55:
            raise ValueError(f"Chunk {chunk_index} is nearly silent: {chunk.dBFS:.1f} dBFS")
        if chunk.max_dBFS > -0.1:
            raise ValueError(f"Chunk {chunk_index} is clipping: peak {chunk.max_dBFS:.1f} dBFS")

        word_count = len(expected_text.split())
        if word_count > 3:
            expected_max_ms = int((word_count / 0.5) * 1000)
            if len(chunk) > expected_max_ms:
                raise ValueError(
                    f"Chunk {chunk_index} duration {len(chunk)}ms is disproportionate "
                    f"to text length ({word_count} words)"
                )

    def _flag_manual_review(self, chapter: Chapter, notes: list[str]) -> None:
        """Mark chapters with skipped chunks for manual QA review."""

        if not notes:
            return

        chapter.qa_status = QAStatus.NEEDS_REVIEW
        chapter.qa_notes = "\n".join(notes)

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
