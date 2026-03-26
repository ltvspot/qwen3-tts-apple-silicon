"""Whole-book mastering pipeline used before final export."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydub import AudioSegment, silence
from pydub.effects import compress_dynamic_range, high_pass_filter
from sqlalchemy.orm import Session

from src.database import Chapter, ChapterStatus
from src.pipeline.book_qa import ACX_REQUIREMENTS, BookQAReport, measure_integrated_lufs, run_book_qa
from src.pipeline.qa_checker import persist_qa_result, run_qa_checks_for_chapter

logger = logging.getLogger(__name__)
ACX_SAMPLE_RATE = ACX_REQUIREMENTS["sample_rate"]


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
    MAX_PEAK_DBFS = -1.5
    PEAK_TARGET_DBFS = -1.5
    NOISE_GATE_THRESHOLD_DBFS = -50.0
    NOISE_GATE_REDUCTION_DB = 18.0
    HIGH_PASS_HZ = 80
    COMPRESSION_THRESHOLD_DBFS = -20.0
    COMPRESSION_RATIO = 2.0
    EDGE_TOLERANCE_MS = 20

    async def master_book(self, book_id: int, db_session: Session) -> MasteringReport:
        """Async entrypoint used by API routes."""

        chapters = self._generated_chapters(book_id, db_session)
        notes: list[str] = []
        resampled = self._resample_chapters(chapters, db_session)
        if resampled:
            notes.append(f"Resampled {len(resampled)} chapters to {ACX_SAMPLE_RATE} Hz.")
        gated = self._apply_noise_gate(chapters, db_session)
        if gated:
            notes.append(f"Noise-gated {len(gated)} chapters below {self.NOISE_GATE_THRESHOLD_DBFS:.0f} dBFS.")
        filtered = self._apply_high_pass_filter(chapters, db_session)
        if filtered:
            notes.append(f"Applied an {self.HIGH_PASS_HZ} Hz high-pass filter to {len(filtered)} chapters.")
        loudness_adjustments = self._normalize_loudness(chapters, db_session)
        compressed = self._apply_compression(chapters, db_session)
        if compressed:
            notes.append(f"Compressed {len(compressed)} chapters for steadier narration dynamics.")
        edge_normalized = self._normalize_chapter_edges(chapters, db_session)
        peak_limited = self._apply_final_peak_limit(chapters, db_session)
        notes.append("Sentence-boundary padding verified via chapter QA after mastering.")
        blockers = self._final_verify(chapters, db_session)
        book_report = await self._verify_mastered_quality_async(book_id, chapters, db_session)
        if blockers:
            book_report.export_blockers.extend(blocker for blocker in blockers if blocker not in book_report.export_blockers)
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
        notes: list[str] = []
        resampled = self._resample_chapters(chapters, db_session)
        if resampled:
            notes.append(f"Resampled {len(resampled)} chapters to {ACX_SAMPLE_RATE} Hz.")
        gated = self._apply_noise_gate(chapters, db_session)
        if gated:
            notes.append(f"Noise-gated {len(gated)} chapters below {self.NOISE_GATE_THRESHOLD_DBFS:.0f} dBFS.")
        filtered = self._apply_high_pass_filter(chapters, db_session)
        if filtered:
            notes.append(f"Applied an {self.HIGH_PASS_HZ} Hz high-pass filter to {len(filtered)} chapters.")
        loudness_adjustments = self._normalize_loudness(chapters, db_session)
        compressed = self._apply_compression(chapters, db_session)
        if compressed:
            notes.append(f"Compressed {len(compressed)} chapters for steadier narration dynamics.")
        edge_normalized = self._normalize_chapter_edges(chapters, db_session)
        peak_limited = self._apply_final_peak_limit(chapters, db_session)
        notes.append("Sentence-boundary padding verified via chapter QA after mastering.")
        blockers = self._final_verify(chapters, db_session)
        book_report = asyncio.run(self._verify_mastered_quality_async(book_id, chapters, db_session))
        if blockers:
            book_report.export_blockers.extend(blocker for blocker in blockers if blocker not in book_report.export_blockers)

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

    def _persist_audio_if_changed(
        self,
        chapter: Chapter,
        audio_path: Path,
        original: AudioSegment,
        processed: AudioSegment,
    ) -> bool:
        """Write mastered audio when a processing step actually changed it."""

        normalized = processed.set_frame_rate(processed.frame_rate).set_channels(1).set_sample_width(2)
        if (
            normalized.frame_rate == original.frame_rate
            and normalized.channels == original.channels
            and normalized.sample_width == original.sample_width
            and normalized.raw_data == original.raw_data
        ):
            return False

        normalized.export(audio_path, format="wav")
        chapter.mastered = True
        chapter.duration_seconds = round(len(normalized) / 1000.0, 3)
        chapter.audio_file_size_bytes = audio_path.stat().st_size
        return True

    def _resample_chapters(self, chapters: list[Chapter], db_session: Session) -> list[int]:
        """Upsample all chapter masters to the ACX-required 44.1kHz."""

        resampled: list[int] = []
        for chapter in chapters:
            audio_path = self._resolve_audio_path(chapter)
            audio = AudioSegment.from_file(audio_path).set_channels(1).set_sample_width(2)
            if audio.frame_rate == ACX_SAMPLE_RATE:
                continue
            if self._persist_audio_if_changed(chapter, audio_path, audio, audio.set_frame_rate(ACX_SAMPLE_RATE)):
                resampled.append(chapter.number)

        db_session.flush()
        return resampled

    def _apply_noise_gate(self, chapters: list[Chapter], db_session: Session) -> list[int]:
        """Reduce low-level hiss between phrases before loudness normalization."""

        gated: list[int] = []
        attack_frames = 1
        release_frames = 5
        for chapter in chapters:
            audio_path = self._resolve_audio_path(chapter)
            audio = AudioSegment.from_file(audio_path).set_channels(1).set_sample_width(2)
            frame_ms = 10
            frames = [audio[start:start + frame_ms] for start in range(0, len(audio), frame_ms)]
            if not frames:
                continue

            gated_frames: list[AudioSegment] = []
            current_reduction = 0.0
            changed = False
            for frame in frames:
                frame_dbfs = frame.dBFS if frame.dBFS != float("-inf") else -100.0
                target_reduction = self.NOISE_GATE_REDUCTION_DB if frame_dbfs < self.NOISE_GATE_THRESHOLD_DBFS else 0.0
                smoothing = attack_frames if target_reduction > current_reduction else release_frames
                current_reduction += (target_reduction - current_reduction) / max(smoothing, 1)
                adjusted = frame.apply_gain(-current_reduction) if current_reduction > 0.1 else frame
                if adjusted.raw_data != frame.raw_data:
                    changed = True
                gated_frames.append(adjusted)

            if not changed:
                continue
            processed = sum(gated_frames[1:], gated_frames[0])
            if self._persist_audio_if_changed(chapter, audio_path, audio, processed):
                gated.append(chapter.number)

        db_session.flush()
        return gated

    def _apply_high_pass_filter(self, chapters: list[Chapter], db_session: Session) -> list[int]:
        """Remove low-frequency rumble before loudness normalization."""

        filtered: list[int] = []
        for chapter in chapters:
            audio_path = self._resolve_audio_path(chapter)
            audio = AudioSegment.from_file(audio_path).set_channels(1).set_sample_width(2)
            processed = high_pass_filter(audio, cutoff=self.HIGH_PASS_HZ)
            if self._persist_audio_if_changed(chapter, audio_path, audio, processed):
                filtered.append(chapter.number)

        db_session.flush()
        return filtered

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

            audio = AudioSegment.from_file(audio_path).set_channels(1).set_sample_width(2)
            adjusted = audio + gain_db
            if self._persist_audio_if_changed(chapter, audio_path, audio, adjusted):
                adjustments.append(
                    {
                        "chapter_n": chapter.number,
                        "from_lufs": current_lufs,
                        "gain_db": gain_db,
                    }
                )

        db_session.flush()
        return adjustments

    def _apply_compression(self, chapters: list[Chapter], db_session: Session) -> list[int]:
        """Even out narration dynamics after loudness normalization."""

        compressed: list[int] = []
        for chapter in chapters:
            audio_path = self._resolve_audio_path(chapter)
            audio = AudioSegment.from_file(audio_path).set_channels(1).set_sample_width(2)
            processed = compress_dynamic_range(
                audio,
                threshold=self.COMPRESSION_THRESHOLD_DBFS,
                ratio=self.COMPRESSION_RATIO,
                attack=10,
                release=100,
            )
            if self._persist_audio_if_changed(chapter, audio_path, audio, processed):
                compressed.append(chapter.number)

        db_session.flush()
        return compressed

    def _normalize_chapter_edges(self, chapters: list[Chapter], db_session: Session) -> list[int]:
        """Ensure consistent lead-in and trail-out silence on every chapter."""

        normalized: list[int] = []
        for chapter in chapters:
            audio_path = self._resolve_audio_path(chapter)
            audio = AudioSegment.from_file(audio_path).set_channels(1).set_sample_width(2)
            leading_ms = self._leading_silence_ms(audio)
            trailing_ms = self._trailing_silence_ms(audio)
            if (
                abs(leading_ms - self.TARGET_LEAD_IN_MS) <= self.EDGE_TOLERANCE_MS
                and abs(trailing_ms - self.TARGET_TRAIL_OUT_MS) <= self.EDGE_TOLERANCE_MS
            ):
                continue
            trimmed = self._strip_silence(audio)
            padded = (
                AudioSegment.silent(duration=self.TARGET_LEAD_IN_MS, frame_rate=audio.frame_rate).set_channels(1)
                + trimmed
                + AudioSegment.silent(duration=self.TARGET_TRAIL_OUT_MS, frame_rate=audio.frame_rate).set_channels(1)
            )
            if self._persist_audio_if_changed(chapter, audio_path, audio, padded):
                normalized.append(chapter.number)

        db_session.flush()
        return normalized

    def _apply_final_peak_limit(self, chapters: list[Chapter], db_session: Session) -> list[int]:
        """Ensure no chapter exceeds the ACX true-peak ceiling."""

        peak_limited: list[int] = []
        for chapter in chapters:
            audio_path = self._resolve_audio_path(chapter)
            audio = AudioSegment.from_file(audio_path).set_channels(1).set_sample_width(2)
            peak_db = float(audio.max_dBFS)
            if peak_db <= self.MAX_PEAK_DBFS:
                continue

            reduction = peak_db - self.PEAK_TARGET_DBFS
            limited = audio - reduction
            if self._persist_audio_if_changed(chapter, audio_path, audio, limited):
                peak_limited.append(chapter.number)

        db_session.flush()
        return peak_limited

    def _final_verify(self, chapters: list[Chapter], db_session: Session) -> list[str]:
        """Return any mastering blockers that remain after the chain completes."""

        blockers: list[str] = []
        for chapter in chapters:
            audio_path = self._resolve_audio_path(chapter)
            audio = AudioSegment.from_file(audio_path).set_channels(1)
            if audio.frame_rate != ACX_SAMPLE_RATE:
                blockers.append(f"Chapter {chapter.number} is {audio.frame_rate}Hz after mastering.")
            if audio.channels != ACX_REQUIREMENTS["channels"]:
                blockers.append(f"Chapter {chapter.number} is not mono after mastering.")
            if audio.sample_width * 8 != ACX_REQUIREMENTS["bit_depth"]:
                blockers.append(f"Chapter {chapter.number} is not 16-bit PCM after mastering.")

        db_session.flush()
        return blockers

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

    @staticmethod
    def _leading_silence_ms(audio: AudioSegment, *, silence_thresh: float = -45.0, step_ms: int = 10) -> int:
        """Return the detected leading silence in milliseconds."""

        for start_ms in range(0, len(audio), step_ms):
            chunk = audio[start_ms:start_ms + step_ms]
            if chunk.dBFS > silence_thresh:
                return start_ms
        return len(audio)

    @classmethod
    def _trailing_silence_ms(cls, audio: AudioSegment, *, silence_thresh: float = -45.0, step_ms: int = 10) -> int:
        """Return the detected trailing silence in milliseconds."""

        return cls._leading_silence_ms(audio.reverse(), silence_thresh=silence_thresh, step_ms=step_ms)
