"""Whole-book mastering pipeline used before final export."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydub import AudioSegment, silence
from sqlalchemy.orm import Session

from src.database import Chapter, ChapterStatus
from src.pipeline.book_qa import BookQAReport, measure_integrated_lufs, run_book_qa
from src.pipeline.qa_checker import persist_qa_result, run_qa_checks_for_chapter

logger = logging.getLogger(__name__)


class MasteringReport(BaseModel):
    """Summary of the auto-fixes applied before export."""

    book_id: int
    mastered_chapters: int
    loudness_adjustments: list[dict[str, Any]]
    edge_normalized_chapters: list[int]
    peak_limited_chapters: list[int]
    notes: list[str]
    blockers: list[str]
    book_report: BookQAReport

    @property
    def has_blockers(self) -> bool:
        """Return whether mastering still found export-blocking issues."""

        return bool(self.blockers)


class BookMasteringPipeline:
    """Post-generation mastering to ensure consistent, professional quality."""

    TARGET_LUFS = -20.0
    TARGET_LEAD_IN_MS = 750
    TARGET_TRAIL_OUT_MS = 1500
    MAX_PEAK_DBFS = -3.0
    PEAK_TARGET_DBFS = -3.5

    async def master_book(self, book_id: int, db_session: Session) -> MasteringReport:
        """Async entrypoint used by API routes."""

        chapters = self._generated_chapters(book_id, db_session)
        loudness_adjustments = self._normalize_loudness(chapters, db_session)
        edge_normalized = self._normalize_chapter_edges(chapters, db_session)
        peak_limited = self._apply_final_peak_limit(chapters, db_session)
        notes = ["Sentence-boundary padding verified via chapter QA after mastering."]
        book_report = await self._verify_mastered_quality_async(book_id, chapters, db_session)
        return self._build_report(
            book_id,
            chapters,
            loudness_adjustments,
            edge_normalized,
            peak_limited,
            notes,
            book_report,
        )

    def master_book_sync(self, book_id: int, db_session: Session) -> MasteringReport:
        """Run all book-level auto-fixes and re-verify the mastered result."""

        chapters = self._generated_chapters(book_id, db_session)
        loudness_adjustments = self._normalize_loudness(chapters, db_session)
        edge_normalized = self._normalize_chapter_edges(chapters, db_session)
        peak_limited = self._apply_final_peak_limit(chapters, db_session)
        notes = ["Sentence-boundary padding verified via chapter QA after mastering."]
        book_report = asyncio.run(self._verify_mastered_quality_async(book_id, chapters, db_session))

        return self._build_report(
            book_id,
            chapters,
            loudness_adjustments,
            edge_normalized,
            peak_limited,
            notes,
            book_report,
        )

    @staticmethod
    def _build_report(
        book_id: int,
        chapters: list[Chapter],
        loudness_adjustments: list[dict[str, Any]],
        edge_normalized: list[int],
        peak_limited: list[int],
        notes: list[str],
        book_report: BookQAReport,
    ) -> MasteringReport:
        """Assemble the final mastering report."""

        return MasteringReport(
            book_id=book_id,
            mastered_chapters=len(chapters),
            loudness_adjustments=loudness_adjustments,
            edge_normalized_chapters=edge_normalized,
            peak_limited_chapters=peak_limited,
            notes=notes,
            blockers=book_report.export_blockers,
            book_report=book_report,
        )

    def _generated_chapters(self, book_id: int, db_session: Session) -> list[Chapter]:
        """Return generated chapters ready for mastering."""

        chapters = (
            db_session.query(Chapter)
            .filter(
                Chapter.book_id == book_id,
                Chapter.status == ChapterStatus.GENERATED,
                Chapter.audio_path.is_not(None),
            )
            .order_by(Chapter.number.asc(), Chapter.id.asc())
            .all()
        )
        if not chapters:
            raise ValueError("No generated chapter audio is available for mastering.")
        return chapters

    def _resolve_audio_path(self, chapter: Chapter) -> Path:
        """Resolve the chapter audio path under the configured outputs directory."""

        if not chapter.audio_path:
            raise ValueError(f"Chapter {chapter.number} is missing audio.")
        audio_path = Path(chapter.audio_path)
        if audio_path.is_absolute():
            return audio_path
        from src.config import settings

        return (Path(settings.OUTPUTS_PATH) / audio_path).resolve()

    def _normalize_loudness(self, chapters: list[Chapter], db_session: Session) -> list[dict[str, Any]]:
        """Adjust per-chapter gain to achieve consistent LUFS across the book."""

        adjustments: list[dict[str, Any]] = []

        for chapter in chapters:
            audio_path = self._resolve_audio_path(chapter)
            current_lufs = measure_integrated_lufs(audio_path)
            if current_lufs is None:
                continue

            gain_db = round(self.TARGET_LUFS - current_lufs, 3)
            if abs(gain_db) <= 0.5:
                continue

            audio = AudioSegment.from_file(audio_path).set_channels(1)
            adjusted = audio + gain_db
            adjusted.export(audio_path, format="wav")
            chapter.mastered = True
            chapter.duration_seconds = round(len(adjusted) / 1000.0, 3)
            chapter.audio_file_size_bytes = audio_path.stat().st_size
            adjustments.append(
                {
                    "chapter_n": chapter.number,
                    "from_lufs": current_lufs,
                    "gain_db": gain_db,
                }
            )

        db_session.flush()
        return adjustments

    def _normalize_chapter_edges(self, chapters: list[Chapter], db_session: Session) -> list[int]:
        """Ensure consistent lead-in and trail-out silence on every chapter."""

        normalized: list[int] = []
        for chapter in chapters:
            audio_path = self._resolve_audio_path(chapter)
            audio = AudioSegment.from_file(audio_path).set_channels(1)
            trimmed = self._strip_silence(audio)
            padded = (
                AudioSegment.silent(duration=self.TARGET_LEAD_IN_MS, frame_rate=audio.frame_rate).set_channels(1)
                + trimmed
                + AudioSegment.silent(duration=self.TARGET_TRAIL_OUT_MS, frame_rate=audio.frame_rate).set_channels(1)
            )
            if padded.raw_data != audio.raw_data:
                padded.export(audio_path, format="wav")
                chapter.mastered = True
                chapter.duration_seconds = round(len(padded) / 1000.0, 3)
                chapter.audio_file_size_bytes = audio_path.stat().st_size
                normalized.append(chapter.number)

        db_session.flush()
        return normalized

    def _apply_final_peak_limit(self, chapters: list[Chapter], db_session: Session) -> list[int]:
        """Ensure no chapter exceeds the ACX true-peak ceiling."""

        peak_limited: list[int] = []
        for chapter in chapters:
            audio_path = self._resolve_audio_path(chapter)
            audio = AudioSegment.from_file(audio_path).set_channels(1)
            peak_db = float(audio.max_dBFS)
            if peak_db <= self.MAX_PEAK_DBFS:
                continue

            reduction = peak_db - self.PEAK_TARGET_DBFS
            limited = audio - reduction
            limited.export(audio_path, format="wav")
            chapter.mastered = True
            chapter.duration_seconds = round(len(limited) / 1000.0, 3)
            chapter.audio_file_size_bytes = audio_path.stat().st_size
            peak_limited.append(chapter.number)

        db_session.flush()
        return peak_limited

    async def _verify_mastered_quality_async(
        self,
        book_id: int,
        chapters: list[Chapter],
        db_session: Session,
    ) -> BookQAReport:
        """Re-run Gate 2 per-chapter QA and then Gate 3 whole-book QA."""

        for chapter in chapters:
            qa_result = await run_qa_checks_for_chapter(chapter)
            persist_qa_result(db_session, chapter, qa_result)

        db_session.commit()
        return run_book_qa(book_id, db_session)

    @staticmethod
    def _strip_silence(audio: AudioSegment) -> AudioSegment:
        """Trim leading and trailing silence but keep internal pauses intact."""

        nonsilent = silence.detect_nonsilent(audio, min_silence_len=200, silence_thresh=-45)
        if not nonsilent:
            return audio

        start_ms = nonsilent[0][0]
        end_ms = nonsilent[-1][1]
        return audio[start_ms:end_ms]
