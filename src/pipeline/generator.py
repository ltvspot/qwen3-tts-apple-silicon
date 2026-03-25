"""Audiobook generation orchestration for chapter-by-chapter TTS output."""

from __future__ import annotations

import asyncio
import json
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
from src.engines import AudioStitcher, ModelManager, TTSEngine, TextChunker
from src.pipeline.chunk_validator import (
    SEVERITY_ORDER,
    ChunkValidationReport,
    ChunkValidator,
    ValidationSeverity,
)
from src.pipeline.pause_trimmer import PauseTrimmer
from src.pipeline.pronunciation_watchlist import PronunciationWatchlist
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

    def __init__(self, engine: TTSEngine | None = None, *, model_manager: ModelManager | None = None) -> None:
        """Initialize the generator with a configured TTS engine instance."""

        if engine is None and model_manager is None:
            raise ValueError("AudiobookGenerator requires either an engine or a model manager.")

        self.engine = engine
        self.model_manager = model_manager
        self.output_path = Path(settings.OUTPUTS_PATH)
        self.retry_backoff_seconds: tuple[float, ...] = (0.5, 1.0)
        self.chunk_validator = ChunkValidator()
        self.pronunciation_watchlist = PronunciationWatchlist()

    def close(self) -> None:
        """Release the underlying engine if it has been loaded."""

        if self.model_manager is not None:
            return
        if self.engine is not None and getattr(self.engine, "loaded", False):
            self.engine.unload()

    async def generate_book(
        self,
        book_id: int,
        db_session: Session,
        progress_callback: Callable[[int, float], Awaitable[None]] | None = None,
        chapter_completed_callback: Callable[[Chapter], Awaitable[None]] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        force: bool = False,
        voice_name: str = "Ethan",
        emotion: str = "neutral",
        speed: float = 1.0,
    ) -> dict[str, Any]:
        """
        Generate all chapters for a parsed book.

        Returns a summary dictionary with status, counts, duration, and errors.
        """

        await self._get_engine()

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
                if chapter_completed_callback is not None:
                    await chapter_completed_callback(chapter)

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
        emotion: str = "neutral",
        speed: float = 1.0,
    ) -> float:
        """Generate and persist audio for a single chapter."""

        engine = await self._get_engine()

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

        chunks = TextChunker.chunk_text(text_content, engine.max_chunk_chars)
        chapter.status = ChapterStatus.GENERATING
        chapter.started_at = utc_now()
        chapter.completed_at = None
        chapter.error_message = None
        chapter.current_chunk = 0
        chapter.total_chunks = len(chunks)
        chapter.chunk_boundaries = None
        chapter.generation_metadata = None
        db_session.commit()

        try:
            audio_chunks = []
            chapter_speed = speed * self._chapter_speed(chapter)
            manual_review_notes: list[str] = []
            gate1_summary = {
                "chunks_total": len(chunks),
                "chunks_pass_first_attempt": 0,
                "chunks_regenerated": 0,
                "chunks_with_warnings": 0,
                "chunks_failed_final": 0,
                "validation_issue_chunks": 0,
                "avg_wer": None,
            }
            wer_values: list[float] = []
            watchlist_matches = self.pronunciation_watchlist.check_text(text_content)
            if watchlist_matches:
                watchlist_terms = ", ".join(match["word"] for match in watchlist_matches)
                logger.warning(
                    "Pronunciation watchlist flagged book %s chapter %s: %s",
                    book_id,
                    chapter.number,
                    watchlist_terms,
                )
                manual_review_notes.extend(
                    [
                        (
                            "Pronunciation watchlist: "
                            f"{match['word']} -> {match['pronunciation_guide']}. "
                            f"{match['context']}"
                        )
                        for match in watchlist_matches
                    ]
                )

            for chunk_index, chunk in enumerate(chunks):
                self._raise_if_cancelled(should_cancel)

                try:
                    audio, validation_report, failed_validation, attempts_used = await self._generate_chunk_with_retry(
                        chunk,
                        chunk_index=chunk_index,
                        voice_name=voice_name,
                        emotion=emotion,
                        speed=chapter_speed,
                        chapter_number=chapter.number,
                        book_id=book_id,
                        should_cancel=should_cancel,
                        expected_sample_rate=getattr(engine, "sample_rate", None),
                    )
                except ChunkGenerationExhaustedError as exc:
                    note = (
                        f"Chunk {chunk_index} failed after 3 attempts and was skipped: {exc}"
                    )
                    gate1_summary["chunks_failed_final"] += 1
                    gate1_summary["validation_issue_chunks"] += 1
                    manual_review_notes.append(note)
                    logger.error(
                        "Skipping book %s chapter %s chunk %s after exhausted retries: %s",
                        book_id,
                        chapter.number,
                        chunk_index,
                        exc,
                    )
                    continue

                if attempts_used == 1 and not validation_report.issues:
                    gate1_summary["chunks_pass_first_attempt"] += 1
                if attempts_used > 1:
                    gate1_summary["chunks_regenerated"] += 1

                audio, pauses_trimmed = PauseTrimmer.trim_excessive_pauses(audio)
                if pauses_trimmed > 0:
                    logger.info(
                        "Chunk %s for book %s chapter %s trimmed %s excessive pauses",
                        chunk_index,
                        book_id,
                        chapter.number,
                        pauses_trimmed,
                    )

                warning_messages = self._validation_messages(
                    validation_report,
                    minimum_severity=ValidationSeverity.WARNING,
                )
                fail_messages = self._validation_messages(
                    validation_report,
                    minimum_severity=ValidationSeverity.FAIL,
                )

                if warning_messages:
                    gate1_summary["chunks_with_warnings"] += 1
                    logger.warning(
                        "Chunk %d validation issues for book %s ch %s: %s",
                        chunk_index,
                        book_id,
                        chapter.number,
                        "; ".join(warning_messages),
                    )

                if validation_report.issues:
                    gate1_summary["validation_issue_chunks"] += 1

                for result in validation_report.results:
                    if result.check != "text_alignment" or not result.details:
                        continue
                    wer = result.details.get("wer")
                    if isinstance(wer, (int, float)):
                        wer_values.append(float(wer))

                if failed_validation and fail_messages:
                    gate1_summary["chunks_failed_final"] += 1
                    manual_review_notes.append(
                        f"Chunk {validation_report.chunk_index} FAILED: {'; '.join(fail_messages)}"
                    )
                elif warning_messages:
                    manual_review_notes.append(
                        f"Chunk {validation_report.chunk_index} validation warnings: {'; '.join(warning_messages)}"
                    )

                audio_chunks.append(audio)
                chapter.current_chunk = chunk_index + 1
                db_session.commit()

                if progress_callback is not None:
                    await progress_callback((chunk_index + 1) / len(chunks))

            self._raise_if_cancelled(should_cancel)
            if not audio_chunks:
                raise RuntimeError(
                    f"Chapter {chapter.number} produced no valid audio chunks after retries."
                )

            stitch_result = AudioStitcher.stitch_with_metadata(audio_chunks)
            final_audio = stitch_result.audio
            audio_path = self._get_chapter_audio_path(book_id, chapter)
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(final_audio.export, str(audio_path), format="wav")

            duration = len(final_audio) / 1000.0
            chapter.audio_path = str(audio_path.relative_to(self.output_path))
            chapter.duration_seconds = duration
            chapter.status = ChapterStatus.GENERATED
            chapter.current_chunk = len(chunks)
            chapter.total_chunks = len(chunks)
            chapter.chunk_boundaries = json.dumps(stitch_result.chunk_boundaries)
            if wer_values:
                gate1_summary["avg_wer"] = round(sum(wer_values) / len(wer_values), 4)
            chapter.generation_metadata = json.dumps({"gate1": gate1_summary})
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
            if self.model_manager is not None:
                self.model_manager.record_chapter()

            return duration
        except GenerationCancelled:
            chapter.status = ChapterStatus.PENDING
            chapter.started_at = None
            chapter.completed_at = None
            chapter.error_message = None
            chapter.current_chunk = None
            chapter.total_chunks = None
            chapter.chunk_boundaries = None
            chapter.generation_metadata = None
            db_session.commit()
            raise
        except Exception as exc:
            chapter.status = ChapterStatus.FAILED
            chapter.completed_at = utc_now()
            chapter.error_message = str(exc)
            chapter.current_chunk = None
            chapter.total_chunks = None
            chapter.chunk_boundaries = None
            chapter.generation_metadata = None
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
        expected_sample_rate: int | None,
    ) -> tuple[Any, ChunkValidationReport, bool, int]:
        """Generate and validate one chunk with retries for transient failures and hard QA failures."""

        max_attempts = len(self.retry_backoff_seconds) + 1
        for attempt in range(1, max_attempts + 1):
            self._raise_if_cancelled(should_cancel)
            try:
                audio = await self._generate_chunk(
                    self._vary_retry_text(text, attempt),
                    voice_name=voice_name,
                    emotion=emotion,
                    speed=self._vary_retry_speed(speed, attempt),
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

            validation_report = self.chunk_validator.validate(
                audio,
                text,
                voice_name,
                speed,
                chunk_index=chunk_index,
                expected_sample_rate=expected_sample_rate,
            )
            if validation_report.needs_regeneration and attempt < max_attempts:
                logger.warning(
                    "Chunk %s for book %s chapter %s failed validation, regenerating (%s/%s): %s",
                    chunk_index,
                    book_id,
                    chapter_number,
                    attempt + 1,
                    max_attempts,
                    "; ".join(
                        self._validation_messages(
                            validation_report,
                            minimum_severity=ValidationSeverity.FAIL,
                        )
                    ),
                )
                continue

            if validation_report.needs_regeneration:
                logger.error(
                    "Chunk %s for book %s chapter %s failed validation after %s attempts, marking for manual review",
                    chunk_index,
                    book_id,
                    chapter_number,
                    max_attempts,
                )
                return (audio, validation_report, True, attempt)

            return (audio, validation_report, False, attempt)

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

        engine = await self._get_engine()
        started_at = asyncio.get_running_loop().time()
        timeout_generate = getattr(engine, "generate_chunk_with_timeout", None)
        if callable(timeout_generate):
            audio = await timeout_generate(text, voice_name, emotion=emotion, speed=speed)
        else:
            audio = await asyncio.to_thread(
                engine.generate,
                text,
                voice_name,
                emotion,
                speed,
            )

        if self.model_manager is not None:
            self.model_manager.record_chunk(asyncio.get_running_loop().time() - started_at)
        return audio

    def _flag_manual_review(self, chapter: Chapter, notes: list[str]) -> None:
        """Mark chapters with generation warnings for manual QA review."""

        if not notes:
            return

        chapter.qa_status = QAStatus.NEEDS_REVIEW
        existing_notes = (chapter.qa_notes or "").strip()
        next_notes = "\n".join(notes)
        chapter.qa_notes = next_notes if not existing_notes else f"{existing_notes}\n{next_notes}"

    def _validation_messages(
        self,
        report: ChunkValidationReport,
        *,
        minimum_severity: ValidationSeverity,
    ) -> list[str]:
        """Return formatted validation messages at or above the requested severity."""

        return [
            f"{result.check}: {result.message}"
            for result in report.results
            if SEVERITY_ORDER[result.severity] >= SEVERITY_ORDER[minimum_severity]
        ]

    def _vary_retry_speed(self, speed: float, attempt: int) -> float:
        """Apply a tiny speed variation on retries to avoid identical outputs."""

        if attempt <= 1:
            return speed

        variation = 0.02 * (1 if attempt % 2 == 0 else -1)
        return max(0.5, min(2.0, speed + variation))

    def _vary_retry_text(self, text: str, attempt: int) -> str:
        """Apply a tiny textual perturbation on retries to diversify generation."""

        if attempt <= 1:
            return text
        return f"{text}{' ' * (attempt - 1)}"

    async def _get_engine(self) -> TTSEngine:
        """Return the active engine, loading it lazily when needed."""

        if self.model_manager is not None:
            engine = await self.model_manager.get_engine()
            self.engine = engine
            return engine

        if self.engine is None:
            raise RuntimeError("No TTS engine is configured.")
        if not getattr(self.engine, "loaded", False):
            self.engine.load()
        return self.engine

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
