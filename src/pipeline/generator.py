"""Audiobook generation orchestration for chapter-by-chapter TTS output."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
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
from src.engines.pronunciation_dictionary import PronunciationDictionary
from src.engines.qwen3_tts import Qwen3TTS, RAW_WAV_TARGET_LUFS
from src.notifications import send_book_complete_notification, send_qa_failure_notification
from src.pipeline.chunk_validator import (
    SEVERITY_ORDER,
    ChunkValidationReport,
    ChunkValidator,
    ValidationResult,
    ValidationSeverity,
)
from src.pipeline.pause_trimmer import PauseTrimmer
from src.pipeline.pronunciation_watchlist import PronunciationWatchlist
from src.pipeline.qa_checker import QACheckResult, QAResult, persist_qa_result, run_qa_checks_for_chapter

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
        self.pronunciation_dictionary = PronunciationDictionary()

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

            if chapter.status == ChapterStatus.GENERATED and not force and self.chapter_audio_exists(book_id, chapter):
                if progress_callback is not None:
                    await progress_callback(chapter.number, ((chapter_index + 1) / len(chapters)) * 100)
                continue

            async def chapter_progress_callback(chunk_progress: float) -> None:
                if progress_callback is None:
                    return
                overall_progress = ((chapter_index + chunk_progress) / len(chapters)) * 100
                await progress_callback(chapter.number, overall_progress)

            try:
                chapter_duration = await self.generate_chapter(
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
                total_duration += chapter_duration
                if chapter.status == ChapterStatus.GENERATED:
                    generated_chapters += 1
                else:
                    qa_gap_message = (
                        chapter.error_message
                        or "Automatic QA did not complete for this generated chapter."
                    )
                    error_message = f"Chapter {chapter.number}: {qa_gap_message}"
                    logger.error(error_message)
                    errors.append(error_message)
                    failed_chapters.append(chapter.number)
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

        if generated_chapters > 0:
            self._post_generation_loudness_check(book_id, chapters, db_session)

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

        if final_status == "success":
            flagged_chapters = sum(chapter.qa_status == QAStatus.NEEDS_REVIEW for chapter in chapters)
            send_book_complete_notification(
                book_title=book.title,
                ready_for_export=flagged_chapters == 0,
                flagged_chapters=flagged_chapters,
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

        text_content = (chapter.text_content or "").strip()
        if not text_content:
            raise ValueError(f"Chapter {chapter.number} has no text content")
        if not force:
            recovered_duration = await self._recover_existing_chapter_audio(book_id, chapter, db_session)
            if recovered_duration is not None:
                logger.info(
                    "Recovered existing WAV for book %s chapter %s - skipping regeneration",
                    book_id,
                    chapter.number,
                )
                return recovered_duration
            if chapter.status == ChapterStatus.GENERATED:
                logger.warning(
                    "Book %s chapter %s was marked generated but no reusable WAV was found; regenerating",
                    book_id,
                    chapter.number,
                )

        engine = await self._get_engine()

        logger.info(
            "Generating audio for book %s chapter %s (%s words)",
            book_id,
            chapter.number,
            chapter.word_count or 0,
        )

        chunk_plans = TextChunker.chunk_text_with_metadata(text_content, engine.max_chunk_chars)
        pause_settings = self._pause_settings()
        chapter.status = ChapterStatus.GENERATING
        chapter.started_at = utc_now()
        chapter.completed_at = None
        chapter.error_message = None
        chapter.current_chunk = 0
        chapter.total_chunks = len(chunk_plans)
        chapter.chunk_boundaries = None
        chapter.generation_metadata = None
        db_session.commit()

        try:
            audio_chunks = []
            chapter_speed = speed * self._chapter_speed(chapter)
            manual_review_notes: list[str] = []
            checkpoint_hits = 0
            chunk_files_written = 0
            gate1_summary = {
                "chunks_total": len(chunk_plans),
                "chunks_pass_first_attempt": 0,
                "chunks_regenerated": 0,
                "chunks_with_warnings": 0,
                "chunks_failed_final": 0,
                "validation_issue_chunks": 0,
                "avg_wer": None,
            }
            wer_values: list[float] = []
            custom_watchlist = self._custom_watchlist_entries(chapter)
            watchlist_matches = self.pronunciation_watchlist.check_text(
                text_content,
                custom_entries=custom_watchlist,
            )
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

            for chunk_index, chunk_plan in enumerate(chunk_plans):
                self._raise_if_cancelled(should_cancel)
                validation_text = chunk_plan.text
                chunk_text = TextChunker.preprocess_for_tts(
                    validation_text,
                    book_id=book_id,
                    pronunciation_dictionary=self.pronunciation_dictionary,
                )
                checkpoint_path = self._get_chunk_checkpoint_path(book_id, chapter, chunk_index)
                checkpoint_audio = self._load_chunk_checkpoint(checkpoint_path)

                if checkpoint_audio is not None and not force:
                    checkpoint_hits += 1
                    audio = checkpoint_audio
                    validation_report = self.chunk_validator.validate(
                        audio,
                        validation_text,
                        voice_name,
                        chapter_speed,
                        chunk_index=chunk_index,
                        expected_sample_rate=getattr(engine, "sample_rate", None),
                    )
                    failed_validation = self._is_noncritical_validation_failure(validation_report)
                    attempts_used = 1
                    logger.info(
                        "Reusing checkpointed chunk %s for book %s chapter %s",
                        chunk_index,
                        book_id,
                        chapter.number,
                    )
                else:
                    chunk_prompt = self.pronunciation_watchlist.inject_phonetic_hints(
                        chunk_text,
                        custom_entries=custom_watchlist,
                    )
                    try:
                        audio, validation_report, failed_validation, attempts_used = await self._generate_chunk_with_retry(
                            chunk_prompt,
                            validation_text=validation_text,
                            chunk_index=chunk_index,
                            voice_name=voice_name,
                            emotion=emotion,
                            speed=chapter_speed,
                            chapter_number=chapter.number,
                            book_id=book_id,
                            should_cancel=should_cancel,
                            expected_sample_rate=getattr(engine, "sample_rate", None),
                            custom_watchlist=custom_watchlist,
                        )
                    except ChunkGenerationExhaustedError as exc:
                        error_message = (
                            f"Chunk {chunk_index + 1} failed after {self._max_chunk_attempts()} attempts: "
                            f"{exc}. Chapter cannot be completed with missing audio."
                        )
                        chapter.status = ChapterStatus.FAILED
                        chapter.completed_at = utc_now()
                        chapter.error_message = error_message
                        db_session.commit()
                        gate1_summary["chunks_failed_final"] += 1
                        gate1_summary["validation_issue_chunks"] += 1
                        manual_review_notes.append(error_message)
                        logger.error(
                            "CRITICAL: Chunk %s permanently failed. Chapter generation aborted to prevent incomplete audiobook.",
                            chunk_index + 1,
                        )
                        raise ChunkGenerationExhaustedError(error_message) from exc

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

                if checkpoint_audio is None or force or pauses_trimmed > 0:
                    await asyncio.to_thread(self._save_chunk_checkpoint, checkpoint_path, audio)
                    chunk_files_written += 1

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
                    await progress_callback((chunk_index + 1) / len(chunk_plans))

            self._raise_if_cancelled(should_cancel)
            if not audio_chunks:
                raise RuntimeError(
                    f"Chapter {chapter.number} produced no valid audio chunks after retries."
                )

            stitch_result = AudioStitcher.stitch_with_metadata_and_pauses(
                audio_chunks,
                pause_after_ms=self._chunk_pause_map(chunk_plans, pause_settings),
            )
            final_audio, raw_lufs = self._normalize_raw_wav_audio(stitch_result.audio)
            audio_path = self._get_chapter_audio_path(book_id, chapter)
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(final_audio.export, str(audio_path), format="wav")

            duration = len(final_audio) / 1000.0
            chapter.audio_path = str(audio_path.relative_to(self.output_path))
            chapter.duration_seconds = duration
            chapter.status = ChapterStatus.GENERATED
            chapter.current_chunk = len(chunk_plans)
            chapter.total_chunks = len(chunk_plans)
            chapter.chunk_boundaries = json.dumps(stitch_result.chunk_boundaries)
            if wer_values:
                gate1_summary["avg_wer"] = round(sum(wer_values) / len(wer_values), 4)
            chapter.generation_metadata = json.dumps(
                {
                    "gate1": gate1_summary,
                    "checkpoints": {
                        "checkpoint_hits": checkpoint_hits,
                        "chunk_files_written": chunk_files_written,
                    },
                    "raw_wav_lufs": raw_lufs,
                }
            )
            chapter.completed_at = utc_now()
            chapter.audio_file_size_bytes = audio_path.stat().st_size

            qa_notification_reason: str | None = None
            try:
                qa_result = await run_qa_checks_for_chapter(chapter)
                chapter.status = ChapterStatus.GENERATED
                if qa_result.has_failures:
                    qa_notification_reason = next(
                        (
                            check.message
                            for check in qa_result.checks
                            if check.status == "fail" and check.message
                        ),
                        "Automatic QA flagged the chapter for review.",
                    )
            except Exception as exc:
                qa_notification_reason = self._format_qa_exception(exc)
                manual_review_notes.append(qa_notification_reason)
                chapter.status = ChapterStatus.GENERATED_NO_QA
                chapter.error_message = qa_notification_reason
                logger.exception(
                    "Automatic QA failed for book %s chapter %s after successful generation",
                    book_id,
                    chapter.number,
                )
                qa_result = self._build_qa_error_result(chapter, qa_notification_reason)

            persist_qa_result(db_session, chapter, qa_result)
            self._flag_manual_review(chapter, manual_review_notes)
            db_session.commit()
            if qa_notification_reason is not None:
                send_qa_failure_notification(
                    book_id=book_id,
                    chapter_number=chapter.number,
                    reason=qa_notification_reason,
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
            chapter.error_message = chapter.error_message or str(exc)
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
        validation_text: str,
        chunk_index: int,
        voice_name: str,
        emotion: str | None,
        speed: float,
        chapter_number: int,
        book_id: int,
        should_cancel: Callable[[], bool] | None,
        expected_sample_rate: int | None,
        custom_watchlist: list[dict[str, str]] | None = None,
        allow_split_retry: bool = True,
    ) -> tuple[Any, ChunkValidationReport, bool, int]:
        """Generate and validate one chunk with retries for transient failures and hard QA failures."""

        max_attempts = self._max_chunk_attempts()
        terminal_error: Exception | None = None
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
                    terminal_error = ChunkGenerationExhaustedError(str(exc))
                    break

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

            if audio is None:
                terminal_error = ChunkGenerationExhaustedError("Chunk generation timed out")
                logger.warning(
                    "Chunk %s for book %s chapter %s timed out; retrying with smaller text if possible",
                    chunk_index,
                    book_id,
                    chapter_number,
                )
                break

            validation_report = self.chunk_validator.validate(
                audio,
                validation_text,
                voice_name,
                speed,
                chunk_index=chunk_index,
                expected_sample_rate=expected_sample_rate,
            )
            validation_report = self._with_chunk_sanity_validation(
                audio,
                validation_text,
                validation_report,
            )
            self._log_chunk_validation(
                validation_report,
                book_id=book_id,
                chapter_number=chapter_number,
                attempt=attempt,
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
                terminal_error = ChunkGenerationExhaustedError(
                    "; ".join(
                        self._validation_messages(
                            validation_report,
                            minimum_severity=ValidationSeverity.FAIL,
                        )
                    )
                )
                if self._is_noncritical_validation_failure(validation_report):
                    logger.error(
                        "Chunk %s for book %s chapter %s failed non-critical validation after %s attempts, keeping for review",
                        chunk_index,
                        book_id,
                        chapter_number,
                        max_attempts,
                    )
                    return (audio, validation_report, True, attempt)
                break

            return (audio, validation_report, False, attempt)

        if allow_split_retry:
            split = TextChunker.split_for_retry(validation_text)
            if split is not None:
                logger.warning(
                    "Retrying book %s chapter %s chunk %s by splitting at a sentence boundary",
                    book_id,
                    chapter_number,
                    chunk_index,
                )
                left_text, right_text = split
                left_audio, _, left_failed_validation, left_attempts = await self._generate_chunk_with_retry(
                    self.pronunciation_watchlist.inject_phonetic_hints(
                        left_text,
                        custom_entries=custom_watchlist,
                    ),
                    validation_text=left_text,
                    chunk_index=chunk_index,
                    voice_name=voice_name,
                    emotion=emotion,
                    speed=speed,
                    chapter_number=chapter_number,
                    book_id=book_id,
                    should_cancel=should_cancel,
                    expected_sample_rate=expected_sample_rate,
                    custom_watchlist=custom_watchlist,
                    allow_split_retry=False,
                )
                right_audio, _, right_failed_validation, right_attempts = await self._generate_chunk_with_retry(
                    self.pronunciation_watchlist.inject_phonetic_hints(
                        right_text,
                        custom_entries=custom_watchlist,
                    ),
                    validation_text=right_text,
                    chunk_index=chunk_index,
                    voice_name=voice_name,
                    emotion=emotion,
                    speed=speed,
                    chapter_number=chapter_number,
                    book_id=book_id,
                    should_cancel=should_cancel,
                    expected_sample_rate=expected_sample_rate,
                    custom_watchlist=custom_watchlist,
                    allow_split_retry=False,
                )
                stitched_audio = AudioStitcher.stitch(
                    [left_audio, right_audio],
                    pause_after_ms=[self._pause_settings()["sentence_pause_ms"]],
                )
                stitched_report = self.chunk_validator.validate(
                    stitched_audio,
                    validation_text,
                    voice_name,
                    speed,
                    chunk_index=chunk_index,
                    expected_sample_rate=expected_sample_rate,
                )
                stitched_report = self._with_chunk_sanity_validation(
                    stitched_audio,
                    validation_text,
                    stitched_report,
                )
                if left_failed_validation or right_failed_validation:
                    stitched_report = ChunkValidationReport(
                        chunk_index=stitched_report.chunk_index,
                        text=stitched_report.text,
                        duration_ms=stitched_report.duration_ms,
                        results=[
                            ValidationResult(
                                check="chunk_placeholder",
                                severity=ValidationSeverity.FAIL,
                                message="Split retry required a silence placeholder for part of the chunk",
                            ),
                            *stitched_report.results,
                        ],
                    )
                if stitched_report.needs_regeneration and not self._is_noncritical_validation_failure(stitched_report):
                    return self._placeholder_chunk_response(
                        validation_text=validation_text,
                        chunk_index=chunk_index,
                        speed=speed,
                        expected_sample_rate=expected_sample_rate,
                        reason="; ".join(
                            self._validation_messages(
                                stitched_report,
                                minimum_severity=ValidationSeverity.FAIL,
                            )
                        ),
                        attempts_used=max(max_attempts + 1, left_attempts + right_attempts),
                    )
                return (
                    stitched_audio,
                    stitched_report,
                    self._is_noncritical_validation_failure(stitched_report),
                    max(max_attempts + 1, left_attempts + right_attempts),
                )

        if terminal_error is not None:
            return self._placeholder_chunk_response(
                validation_text=validation_text,
                chunk_index=chunk_index,
                speed=speed,
                expected_sample_rate=expected_sample_rate,
                reason=str(terminal_error),
                attempts_used=max_attempts,
            )
        raise RuntimeError("Chunk generation retry loop exited unexpectedly.")

    async def _generate_chunk(
        self,
        text: str,
        *,
        voice_name: str,
        emotion: str | None,
        speed: float,
    ) -> AudioSegment | None:
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

        if audio is not None and self.model_manager is not None:
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

    def _validate_chunk(self, chunk: AudioSegment, expected_text: str) -> tuple[bool, str]:
        """Validate a generated chunk against baseline production audio thresholds."""

        stripped_text = expected_text.strip()
        if len(chunk) < 100 and len(stripped_text) > 5:
            return (False, f"Too short: {len(chunk)}ms for {len(stripped_text)} chars")

        if chunk.rms < 10:
            return (False, f"Silent chunk: RMS={chunk.rms}")

        peak_dbfs = chunk.max_dBFS if chunk.max_dBFS != float("-inf") else -100.0
        if peak_dbfs > -0.3:
            return (False, f"Clipping detected: peak={peak_dbfs:.1f} dBFS")

        word_count = len(stripped_text.split())
        if word_count > 0:
            ms_per_word = len(chunk) / word_count
            if ms_per_word > 2000:
                return (False, f"Suspected hallucination: {ms_per_word:.0f}ms/word")
            if ms_per_word < 50:
                return (False, f"Impossibly fast: {ms_per_word:.0f}ms/word")

        return (True, "OK")

    def _with_chunk_sanity_validation(
        self,
        audio: AudioSegment,
        expected_text: str,
        report: ChunkValidationReport,
    ) -> ChunkValidationReport:
        """Merge basic chunk sanity validation into the richer chunk-quality report."""

        is_valid, reason = self._validate_chunk(audio, expected_text)
        if is_valid:
            return report

        return ChunkValidationReport(
            chunk_index=report.chunk_index,
            text=report.text,
            duration_ms=report.duration_ms,
            results=[
                ValidationResult(
                    check="baseline_audio_sanity",
                    severity=ValidationSeverity.FAIL,
                    message=reason,
                ),
                *report.results,
            ],
        )

    def _placeholder_chunk_response(
        self,
        *,
        validation_text: str,
        chunk_index: int,
        speed: float,
        expected_sample_rate: int | None,
        reason: str,
        attempts_used: int,
    ) -> tuple[AudioSegment, ChunkValidationReport, bool, int]:
        """Insert a short silence placeholder when a chunk never yields valid audio."""

        placeholder = self._build_placeholder_chunk(
            validation_text,
            speed=speed,
            frame_rate=expected_sample_rate,
        )
        logger.error(
            "Skipping chunk %s after %s failed attempts; inserting silence placeholder: %s",
            chunk_index,
            attempts_used,
            reason,
        )
        report = ChunkValidationReport(
            chunk_index=chunk_index + 1,
            text=validation_text,
            duration_ms=len(placeholder),
            results=[
                ValidationResult(
                    check="chunk_placeholder",
                    severity=ValidationSeverity.FAIL,
                    message=reason,
                )
            ],
        )
        return (placeholder, report, True, attempts_used)

    def _build_placeholder_chunk(
        self,
        expected_text: str,
        *,
        speed: float,
        frame_rate: int | None,
    ) -> AudioSegment:
        """Build a silence placeholder sized to a short snippet of missing narration."""

        resolved_speed = max(speed, 0.1)
        word_count = max(len(expected_text.split()), 1)
        duration_ms = int(max(400, min(3000, (word_count * 150) / resolved_speed)))
        resolved_frame_rate = frame_rate or getattr(self.engine, "sample_rate", 22050) or 22050
        return AudioSegment.silent(duration=duration_ms, frame_rate=resolved_frame_rate).set_channels(1)

    def _log_chunk_validation(
        self,
        report: ChunkValidationReport,
        *,
        book_id: int,
        chapter_number: int,
        attempt: int,
    ) -> None:
        """Emit one structured log line for every chunk validation attempt."""

        logger.info(
            "Chunk validation for book %s chapter %s chunk %s attempt %s: %s",
            book_id,
            chapter_number,
            report.chunk_index,
            attempt,
            "; ".join(report.issues or ["OK"]),
        )

    def _build_qa_error_result(self, chapter: Chapter, message: str) -> QAResult:
        """Persist a synthetic QA failure record when automatic QA crashes outright."""

        return QAResult(
            chapter_n=chapter.number,
            book_id=chapter.book_id,
            timestamp=utc_now(),
            checks=[
                QACheckResult(
                    name="automatic_qa_runtime",
                    status="fail",
                    message=message,
                )
            ],
            overall_status="fail",
            notes=message,
        )

    @staticmethod
    def _format_qa_exception(exc: Exception) -> str:
        """Return a stable operator-facing message for QA runtime failures."""

        detail = str(exc).strip() or exc.__class__.__name__
        return f"Automatic QA crashed: {detail}"

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

    def _is_noncritical_validation_failure(self, report: ChunkValidationReport) -> bool:
        """Return whether a failed chunk can be preserved for manual review."""

        fail_checks = {
            result.check
            for result in report.results
            if result.severity == ValidationSeverity.FAIL
        }
        return bool(fail_checks) and fail_checks.issubset({"text_alignment", "duration"})

    def _max_chunk_attempts(self) -> int:
        """Return the total number of first-pass attempts allowed for a chunk."""

        return len(self.retry_backoff_seconds) + 1

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

    def _get_chunk_checkpoint_dir(self, book_id: int, chapter: Chapter) -> Path:
        """Return the checkpoint directory for one chapter."""

        book = chapter.book
        if book is None:
            raise ValueError("Chapter book relationship is not loaded")

        book_slug = _slugify(book.title, fallback=f"book-{book_id}", max_length=50)
        folder_name = f"{book_id}-{book_slug}"
        return self.output_path / folder_name / "chapters" / f"{chapter.number:02d}" / "chunks"

    def _get_chunk_checkpoint_path(self, book_id: int, chapter: Chapter, chunk_index: int) -> Path:
        """Return the stable checkpoint path for one chunk."""

        return self._get_chunk_checkpoint_dir(book_id, chapter) / f"{chunk_index:04d}.wav"

    def chapter_audio_exists(self, book_id: int, chapter: Chapter) -> bool:
        """Return whether a reusable chapter WAV already exists on disk."""

        candidate = self._existing_chapter_audio_path(book_id, chapter)
        if candidate is None:
            return False
        try:
            return candidate.exists() and candidate.stat().st_size > 0
        except OSError:
            return False

    def _existing_chapter_audio_path(self, book_id: int, chapter: Chapter) -> Path | None:
        """Return the best on-disk chapter audio candidate, if one exists."""

        stored_path = self._resolve_stored_audio_path(chapter.audio_path)
        if stored_path is not None and stored_path.exists():
            return stored_path

        try:
            expected_path = self._get_chapter_audio_path(book_id, chapter)
        except ValueError:
            return stored_path
        return expected_path if expected_path.exists() else stored_path

    async def _recover_existing_chapter_audio(
        self,
        book_id: int,
        chapter: Chapter,
        db_session: Session,
    ) -> float | None:
        """Restore chapter state from an existing WAV on disk without regenerating audio."""

        audio_path = self._existing_chapter_audio_path(book_id, chapter)
        if audio_path is None:
            return None

        try:
            audio = await asyncio.to_thread(AudioSegment.from_file, audio_path)
        except Exception:
            logger.warning("Discarding unreadable chapter WAV at %s", audio_path, exc_info=True)
            audio_path.unlink(missing_ok=True)
            return None

        duration = len(audio) / 1000.0
        if duration <= 0:
            logger.warning("Discarding empty chapter WAV at %s", audio_path)
            audio_path.unlink(missing_ok=True)
            return None

        chunk_checkpoint_count = len(list(self._get_chunk_checkpoint_dir(book_id, chapter).glob("*.wav")))
        chapter.status = ChapterStatus.GENERATED
        chapter.audio_path = str(audio_path.resolve().relative_to(self.output_path.resolve()))
        chapter.duration_seconds = duration
        chapter.started_at = chapter.started_at or chapter.completed_at or utc_now()
        chapter.completed_at = utc_now()
        chapter.error_message = None
        chapter.current_chunk = chunk_checkpoint_count or chapter.total_chunks
        if chapter.total_chunks is None and chunk_checkpoint_count:
            chapter.total_chunks = chunk_checkpoint_count
        chapter.audio_file_size_bytes = audio_path.stat().st_size

        qa_notification_reason: str | None = None
        manual_review_notes: list[str] = []
        try:
            qa_result = await run_qa_checks_for_chapter(chapter)
            chapter.status = ChapterStatus.GENERATED
            if qa_result.has_failures:
                qa_notification_reason = next(
                    (
                        check.message
                        for check in qa_result.checks
                        if check.status == "fail" and check.message
                    ),
                    "Automatic QA flagged the chapter for review.",
                )
        except Exception as exc:
            qa_notification_reason = self._format_qa_exception(exc)
            manual_review_notes.append(qa_notification_reason)
            chapter.status = ChapterStatus.GENERATED_NO_QA
            chapter.error_message = qa_notification_reason
            logger.exception(
                "Automatic QA failed while recovering book %s chapter %s from disk",
                book_id,
                chapter.number,
            )
            qa_result = self._build_qa_error_result(chapter, qa_notification_reason)

        persist_qa_result(db_session, chapter, qa_result)
        self._flag_manual_review(chapter, manual_review_notes)
        self._merge_generation_metadata(chapter, {"recovered_from_existing_wav": True})
        db_session.commit()

        if qa_notification_reason is not None:
            send_qa_failure_notification(
                book_id=book_id,
                chapter_number=chapter.number,
                reason=qa_notification_reason,
            )

        return duration

    def _load_chunk_checkpoint(self, checkpoint_path: Path) -> AudioSegment | None:
        """Load a previously saved chunk checkpoint when one exists."""

        if not checkpoint_path.exists():
            return None
        try:
            return AudioSegment.from_file(checkpoint_path).set_channels(1)
        except Exception:
            logger.warning("Discarding unreadable chunk checkpoint at %s", checkpoint_path)
            checkpoint_path.unlink(missing_ok=True)
            return None

    def _save_chunk_checkpoint(self, checkpoint_path: Path, audio: AudioSegment) -> None:
        """Persist one chunk checkpoint to disk immediately after generation."""

        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        audio.export(checkpoint_path, format="wav")

    def delete_chapter_artifacts(self, book_id: int, chapter: Chapter) -> None:
        """Delete a chapter's final audio file and any chunk checkpoints."""

        if chapter.audio_path:
            audio_path = Path(chapter.audio_path)
            if not audio_path.is_absolute():
                audio_path = (self.output_path / audio_path).resolve()
            audio_path.unlink(missing_ok=True)

        shutil.rmtree(self._get_chunk_checkpoint_dir(book_id, chapter), ignore_errors=True)

    def _normalize_raw_wav_audio(self, audio: AudioSegment) -> tuple[AudioSegment, float | None]:
        """Normalize raw generation output toward the chapter/credits LUFS target."""

        return Qwen3TTS.normalize_audio_to_lufs(audio, target_lufs=RAW_WAV_TARGET_LUFS)

    def _resolve_stored_audio_path(self, audio_path: str | None) -> Path | None:
        """Resolve a stored chapter audio path against the outputs directory."""

        if audio_path is None or not audio_path.strip():
            return None
        candidate = Path(audio_path)
        if candidate.is_absolute():
            return candidate
        return (self.output_path / candidate).resolve()

    def _post_generation_loudness_check(
        self,
        book_id: int,
        chapters: list[Chapter],
        db_session: Session,
    ) -> None:
        """Re-normalize outlier chapter WAVs after a book has finished generating."""

        del book_id
        measured: list[tuple[Chapter, float]] = []
        content_lufs: list[float] = []
        for chapter in chapters:
            resolved_path = self._resolve_stored_audio_path(chapter.audio_path)
            if resolved_path is None or not resolved_path.exists():
                continue
            lufs = Qwen3TTS.measure_audio_lufs(AudioSegment.from_file(resolved_path))
            if lufs is None:
                continue
            measured.append((chapter, lufs))
            if chapter.type in {ChapterType.CHAPTER, ChapterType.INTRODUCTION}:
                content_lufs.append(lufs)

        if not content_lufs:
            return

        chapter_mean = sum(content_lufs) / len(content_lufs)
        for chapter, lufs in measured:
            deviation = abs(lufs - chapter_mean)
            if chapter.type in {ChapterType.OPENING_CREDITS, ChapterType.CLOSING_CREDITS} and deviation > 1.0:
                logger.warning(
                    "Credits loudness drift detected for book %s chapter %s: %.2f LU from chapter mean %.2f LUFS",
                    chapter.book_id,
                    chapter.number,
                    deviation,
                    chapter_mean,
                )

            if deviation <= 1.5:
                continue

            resolved_path = self._resolve_stored_audio_path(chapter.audio_path)
            if resolved_path is None or not resolved_path.exists():
                continue
            renormalized_lufs = Qwen3TTS.normalize_wav_path(resolved_path, target_lufs=chapter_mean)
            logger.warning(
                "Re-normalized book %s chapter %s from %.2f LUFS toward %.2f LUFS (measured %.2f LUFS).",
                chapter.book_id,
                chapter.number,
                lufs,
                chapter_mean,
                renormalized_lufs if renormalized_lufs is not None else float("nan"),
            )
            try:
                chapter.audio_file_size_bytes = resolved_path.stat().st_size
            except OSError:
                logger.warning("Unable to refresh audio size after re-normalizing %s", resolved_path)
            self._persist_chapter_loudness_metadata(chapter, renormalized_lufs)

        db_session.commit()

    def _persist_chapter_loudness_metadata(self, chapter: Chapter, lufs: float | None) -> None:
        """Persist measured raw-WAV loudness in generation metadata when available."""

        if lufs is None:
            return
        try:
            payload = json.loads(chapter.generation_metadata) if chapter.generation_metadata else {}
        except json.JSONDecodeError:
            payload = {}
        payload["raw_wav_lufs"] = round(lufs, 3)
        chapter.generation_metadata = json.dumps(payload)

    def _merge_generation_metadata(self, chapter: Chapter, updates: dict[str, Any]) -> None:
        """Merge generation metadata updates without discarding existing keys."""

        try:
            payload = json.loads(chapter.generation_metadata) if chapter.generation_metadata else {}
        except json.JSONDecodeError:
            payload = {}
        payload.update(updates)
        chapter.generation_metadata = json.dumps(payload)

    def _custom_watchlist_entries(self, chapter: Chapter) -> list[dict[str, str]]:
        """Return the merged per-book watchlist entries for a chapter."""

        book = chapter.book
        if book is None:
            return []
        return self.pronunciation_watchlist.custom_entries_from_payload(book.pronunciation_watchlist)

    def _pause_settings(self) -> dict[str, int]:
        """Return the current configurable sentence and paragraph pause durations."""

        from src.config import get_application_settings

        output_preferences = get_application_settings().output_preferences
        return {
            "sentence_pause_ms": int(output_preferences.sentence_pause_ms),
            "paragraph_pause_ms": int(output_preferences.paragraph_pause_ms),
            "chapter_gap_ms": int(output_preferences.chapter_gap_ms),
        }

    def _chunk_pause_map(
        self,
        chunk_plans: list[TextChunker.ChunkPlan],
        pause_settings: dict[str, int],
    ) -> list[int]:
        """Return the intentional pause to apply after each chunk."""

        pauses: list[int] = []
        for chunk_plan in chunk_plans[:-1]:
            if chunk_plan.ends_paragraph:
                pauses.append(pause_settings["paragraph_pause_ms"])
            elif chunk_plan.ends_sentence:
                pauses.append(pause_settings["sentence_pause_ms"])
            else:
                pauses.append(0)
        return pauses
