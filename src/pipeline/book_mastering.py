"""Whole-book mastering pipeline used before final export."""

from __future__ import annotations

import asyncio
import logging
import shutil
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydub import AudioSegment, silence
from pydub.effects import compress_dynamic_range, high_pass_filter
from sqlalchemy.orm import Session, sessionmaker

from src.database import Chapter, ChapterStatus
from src.pipeline.book_qa import ACX_REQUIREMENTS, BookQAReport, measure_integrated_lufs, run_book_qa
from src.pipeline.qa_checker import persist_qa_result, run_qa_checks_for_chapter
from src.utils.subprocess_utils import run_ffmpeg

logger = logging.getLogger(__name__)
ACX_SAMPLE_RATE = ACX_REQUIREMENTS["sample_rate"]
MasteringProgressCallback = Callable[[str, int, int, Chapter], None]


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
    MAX_PEAK_DBFS = ACX_REQUIREMENTS["peak_max_db"]
    PEAK_TARGET_DBFS = -3.5
    NOISE_GATE_THRESHOLD_DBFS = -50.0
    NOISE_GATE_REDUCTION_DB = 18.0
    HIGH_PASS_HZ = 80
    COMPRESSION_THRESHOLD_DBFS = -20.0
    COMPRESSION_RATIO = 2.0
    EDGE_TOLERANCE_MS = 20
    CHAPTER_MASTERING_TIMEOUT_SECONDS = 300
    FAST_CHAIN_BOOK_DURATION_SECONDS = 1800.0
    FAST_CHAIN_BOOK_BYTES = 250 * 1024 * 1024
    FAST_CHAIN_LIMIT_LINEAR = 0.668

    async def master_book(
        self,
        book_id: int,
        db_session: Session,
        *,
        prefer_fast_chain: bool | None = None,
        export_mode: bool = False,
        progress_callback: MasteringProgressCallback | None = None,
        session_factory: sessionmaker[Session] | None = None,
    ) -> MasteringReport:
        """Async entrypoint used by API routes."""

        chapters = self._generated_chapters(book_id, db_session)
        notes: list[str] = []
        loudness_adjustments, edge_normalized, peak_limited, blockers = self._master_with_selected_chain(
            book_id,
            chapters,
            db_session,
            notes,
            prefer_fast_chain=prefer_fast_chain,
            progress_callback=progress_callback,
        )
        db_session.commit()
        book_report = await self._verify_mastered_quality_async(
            book_id,
            chapters,
            db_session,
            export_mode=export_mode,
            progress_callback=progress_callback,
            session_factory=session_factory,
        )
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

    def master_book_sync(
        self,
        book_id: int,
        db_session: Session,
        *,
        prefer_fast_chain: bool | None = None,
        export_mode: bool = False,
        progress_callback: MasteringProgressCallback | None = None,
        session_factory: sessionmaker[Session] | None = None,
    ) -> MasteringReport:
        """Run all book-level auto-fixes and re-verify the mastered result."""

        chapters = self._generated_chapters(book_id, db_session)
        notes: list[str] = []
        loudness_adjustments, edge_normalized, peak_limited, blockers = self._master_with_selected_chain(
            book_id,
            chapters,
            db_session,
            notes,
            prefer_fast_chain=prefer_fast_chain,
            progress_callback=progress_callback,
        )
        db_session.commit()
        book_report = asyncio.run(
            self._verify_mastered_quality_async(
                book_id,
                chapters,
                db_session,
                export_mode=export_mode,
                progress_callback=progress_callback,
                session_factory=session_factory,
            )
        )
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

    def _master_with_selected_chain(
        self,
        book_id: int,
        chapters: list[Chapter],
        db_session: Session,
        notes: list[str],
        *,
        prefer_fast_chain: bool | None,
        progress_callback: MasteringProgressCallback | None,
    ) -> tuple[list[dict[str, Any]], list[int], list[int], list[str]]:
        """Run the appropriate mastering chain and fall back when the rich chain fails."""

        use_fast_chain = self._should_use_fast_chain(chapters) if prefer_fast_chain is None else prefer_fast_chain
        if use_fast_chain:
            notes.append("Using fast ffmpeg mastering chain for export-scale audio.")
            return self._master_book_fast(book_id, chapters, db_session, notes, progress_callback=progress_callback)

        try:
            return self._master_book_rich(chapters, db_session, notes, progress_callback=progress_callback)
        except Exception:
            logger.warning(
                "Rich mastering chain failed for book %s, switching to ffmpeg fallback",
                book_id,
                exc_info=True,
            )
            notes.append("Rich mastering failed; applied ffmpeg fallback chain.")
            return self._master_book_fast(book_id, chapters, db_session, notes, progress_callback=progress_callback)

    def _master_book_rich(
        self,
        chapters: list[Chapter],
        db_session: Session,
        notes: list[str],
        progress_callback: MasteringProgressCallback | None = None,
    ) -> tuple[list[dict[str, Any]], list[int], list[int], list[str]]:
        """Run the original higher-touch mastering chain."""

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
        if progress_callback is not None and chapters:
            progress_callback("mastering", len(chapters), len(chapters), chapters[-1])
        blockers = self._final_verify(chapters, db_session)
        return loudness_adjustments, edge_normalized, peak_limited, blockers

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

    def _should_use_fast_chain(self, chapters: list[Chapter]) -> bool:
        """Return whether this book is large enough to prefer the ffmpeg-only path."""

        total_duration_seconds = sum(chapter.duration_seconds or 0.0 for chapter in chapters)
        total_bytes = 0
        for chapter in chapters:
            if chapter.audio_file_size_bytes:
                total_bytes += chapter.audio_file_size_bytes
                continue
            audio_path = self._resolve_audio_path(chapter)
            if audio_path.exists():
                total_bytes += audio_path.stat().st_size

        return (
            total_duration_seconds >= self.FAST_CHAIN_BOOK_DURATION_SECONDS
            or total_bytes >= self.FAST_CHAIN_BOOK_BYTES
        )

    @staticmethod
    def _read_wav_metadata(audio_path: Path) -> tuple[int, int, int, float]:
        """Return the WAV format metadata without decoding the full file."""

        with wave.open(str(audio_path), "rb") as wav_file:
            frame_rate = wav_file.getframerate()
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            frame_count = wav_file.getnframes()
        duration_seconds = frame_count / frame_rate if frame_rate else 0.0
        return frame_rate, channels, sample_width, duration_seconds

    def _refresh_chapter_file_metadata(self, chapter: Chapter, audio_path: Path) -> None:
        """Refresh persisted chapter metadata after an ffmpeg rewrite."""

        _, _, _, duration_seconds = self._read_wav_metadata(audio_path)
        chapter.mastered = True
        chapter.duration_seconds = round(duration_seconds, 3)
        chapter.audio_file_size_bytes = audio_path.stat().st_size

    def _master_chapter_fast(self, chapter: Chapter, audio_path: Path) -> bool:
        """Master one chapter with ffmpeg so large WAVs do not bottleneck Python loops."""

        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path is None:
            raise RuntimeError("ffmpeg is required for mastering.")

        temporary_output = audio_path.with_suffix(".mastered.tmp.wav")
        filter_chain = ",".join(
            [
                "agate=threshold=-60dB:ratio=4:attack=0.5:release=50",
                f"loudnorm=I={self.TARGET_LUFS}:TP={self.PEAK_TARGET_DBFS}:LRA=11",
                f"alimiter=limit={self.FAST_CHAIN_LIMIT_LINEAR}",
            ]
        )
        try:
            run_ffmpeg(
                [
                    ffmpeg_path,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(audio_path),
                    "-af",
                    filter_chain,
                    "-ar",
                    str(ACX_SAMPLE_RATE),
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    str(temporary_output),
                ],
                timeout=self.CHAPTER_MASTERING_TIMEOUT_SECONDS,
            )
            shutil.move(str(temporary_output), str(audio_path))
            self._refresh_chapter_file_metadata(chapter, audio_path)
            return True
        finally:
            if temporary_output.exists():
                temporary_output.unlink()

    def _master_book_fast(
        self,
        book_id: int,
        chapters: list[Chapter],
        db_session: Session,
        notes: list[str],
        *,
        progress_callback: MasteringProgressCallback | None = None,
    ) -> tuple[list[dict[str, Any]], list[int], list[int], list[str]]:
        """Run a bounded ffmpeg mastering chain suitable for large export jobs."""

        resampled: list[int] = []
        peak_limited: list[int] = []

        for index, chapter in enumerate(chapters, start=1):
            audio_path = self._resolve_audio_path(chapter)
            original_rate, _, _, _ = self._read_wav_metadata(audio_path)
            logger.info(
                "Fast mastering chapter %s/%s for book %s (%s)",
                index,
                len(chapters),
                book_id,
                audio_path.name,
            )
            self._master_chapter_fast(chapter, audio_path)
            peak_limited.append(chapter.number)
            if original_rate != ACX_SAMPLE_RATE:
                resampled.append(chapter.number)
            if progress_callback is not None:
                progress_callback("mastering", index, len(chapters), chapter)
            logger.info(
                "Fast mastering complete for chapter %s/%s for book %s",
                index,
                len(chapters),
                book_id,
            )

        db_session.flush()
        if resampled:
            notes.append(f"Resampled {len(resampled)} chapters to {ACX_SAMPLE_RATE} Hz.")
        notes.append(f"Applied fast ffmpeg loudness/peak mastering to {len(chapters)} chapters.")
        edge_normalized = self._normalize_chapter_edges(chapters, db_session)
        if edge_normalized:
            notes.append(f"Normalized chapter edge silence for {len(edge_normalized)} chapters.")
        notes.append("Sentence-boundary padding verified via chapter QA after mastering.")
        blockers = self._final_verify(chapters, db_session)
        return [], edge_normalized, peak_limited, blockers

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
            frame_rate, channels, sample_width, _ = self._read_wav_metadata(audio_path)
            if frame_rate != ACX_SAMPLE_RATE:
                blockers.append(f"Chapter {chapter.number} is {frame_rate}Hz after mastering.")
            if channels != ACX_REQUIREMENTS["channels"]:
                blockers.append(f"Chapter {chapter.number} is not mono after mastering.")
            if sample_width * 8 != ACX_REQUIREMENTS["bit_depth"]:
                blockers.append(f"Chapter {chapter.number} is not 16-bit PCM after mastering.")

        db_session.flush()
        return blockers

    async def _verify_mastered_quality_async(
        self,
        book_id: int,
        chapters: list[Chapter],
        db_session: Session,
        *,
        export_mode: bool = False,
        progress_callback: MasteringProgressCallback | None = None,
        session_factory: sessionmaker[Session] | None = None,
    ) -> BookQAReport:
        """Re-run Gate 2 per-chapter QA and then Gate 3 whole-book QA."""

        verification_session_factory = session_factory or sessionmaker(
            bind=db_session.get_bind(),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )

        for index, chapter in enumerate(chapters, start=1):
            audio_label = Path(chapter.audio_path).name if chapter.audio_path else f"chapter-{chapter.number}.wav"
            logger.info(
                "QA analyzing chapter %s/%s (%s, %.1fs)...",
                index,
                len(chapters),
                audio_label,
                float(chapter.duration_seconds or 0.0),
            )
            qa_result = await run_qa_checks_for_chapter(chapter)
            with verification_session_factory() as qa_session:
                persisted_chapter = (
                    qa_session.query(Chapter)
                    .filter(Chapter.book_id == chapter.book_id, Chapter.number == chapter.number)
                    .first()
                )
                if persisted_chapter is None:
                    raise ValueError(f"Chapter {chapter.number} disappeared during QA verification.")
                persist_qa_result(qa_session, persisted_chapter, qa_result)
                qa_session.commit()
            if progress_callback is not None:
                progress_callback("qa", index, len(chapters), chapter)

        with verification_session_factory() as book_session:
            return run_book_qa(book_id, book_session, export_mode=export_mode)

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
