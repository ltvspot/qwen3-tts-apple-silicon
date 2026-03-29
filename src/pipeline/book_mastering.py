"""Whole-book mastering pipeline used before final export."""

from __future__ import annotations

import asyncio
import logging
import shutil
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel
from pydub import AudioSegment, silence
from pydub.effects import compress_dynamic_range, high_pass_filter
from scipy import signal
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
    TARGET_TRAIL_OUT_MS = 2000
    MAX_PEAK_DBFS = ACX_REQUIREMENTS["peak_max_db"]
    PEAK_TARGET_DBFS = -3.5
    NOISE_GATE_THRESHOLD_DBFS = -50.0
    NOISE_GATE_REDUCTION_DB = 18.0
    HIGH_PASS_HZ = 80
    ROOM_TONE_LEVEL_DBFS = -65.0
    ROOM_TONE_MIN_DBFS = -80.0
    ROOM_TONE_MAX_DBFS = -50.0
    PLOSIVE_THRESHOLD_DB = 12.0
    PLOSIVE_MAX_DURATION_MS = 20
    DE_ESS_THRESHOLD_DB = 6.0
    BREATH_TARGET_DBFS = -40.0
    BREATH_REDUCTION_TRIGGER_DBFS = -30.0
    COMPRESSION_THRESHOLD_DBFS = -20.0
    COMPRESSION_RATIO = 2.0
    EDGE_TOLERANCE_MS = 20
    CHAPTER_MASTERING_TIMEOUT_SECONDS = 300
    FAST_CHAIN_BOOK_DURATION_SECONDS = 1800.0
    FAST_CHAIN_BOOK_BYTES = 250 * 1024 * 1024
    FAST_CHAIN_LIMIT_LINEAR = 0.668
    ROOM_TONE_SEED = 53
    _room_tone_cache: dict[tuple[int, int], AudioSegment] = {}

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
        polish = self._apply_frequency_selective_processing(chapters, db_session)
        if polish["high_passed"]:
            notes.append(f"Applied an {self.HIGH_PASS_HZ} Hz high-pass filter to {len(polish['high_passed'])} chapters.")
        if polish["plosive_reduced"]:
            notes.append(f"Reduced low-band plosives in {len(polish['plosive_reduced'])} chapters.")
        if polish["de_essed"]:
            notes.append(f"Applied split-band de-essing to {len(polish['de_essed'])} chapters.")
        if polish["breath_normalized"]:
            notes.append(f"Softened loud breaths in {len(polish['breath_normalized'])} chapters.")
        gated = self._apply_noise_gate(chapters, db_session)
        if gated:
            notes.append(f"Noise-gated {len(gated)} chapters below {self.NOISE_GATE_THRESHOLD_DBFS:.0f} dBFS.")
        compressed = self._apply_compression(chapters, db_session)
        if compressed:
            notes.append(f"Compressed {len(compressed)} chapters for steadier narration dynamics.")
        loudness_adjustments = self._normalize_loudness(chapters, db_session)
        peak_limited = self._apply_final_peak_limit(chapters, db_session)
        edge_normalized = self._normalize_chapter_edges(chapters, db_session)
        if edge_normalized:
            notes.append(f"Applied ACX room tone padding to {len(edge_normalized)} chapters.")
        notes.append("Publishing mastering chain validated via chapter QA after mastering.")
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
                f"agate=threshold={self.NOISE_GATE_THRESHOLD_DBFS}dB:ratio=4:attack=10:release=100",
                "acompressor=threshold=0.063:ratio=2:attack=15:release=100:makeup=1",
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

        polish = self._apply_frequency_selective_processing(chapters, db_session)
        if polish["high_passed"]:
            notes.append(f"Applied an {self.HIGH_PASS_HZ} Hz high-pass filter to {len(polish['high_passed'])} chapters.")
        if polish["plosive_reduced"]:
            notes.append(f"Reduced low-band plosives in {len(polish['plosive_reduced'])} chapters before ffmpeg mastering.")
        if polish["de_essed"]:
            notes.append(f"Applied split-band de-essing to {len(polish['de_essed'])} chapters before ffmpeg mastering.")
        if polish["breath_normalized"]:
            notes.append(f"Softened loud breaths in {len(polish['breath_normalized'])} chapters before ffmpeg mastering.")
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
        notes.append(f"Applied fast ffmpeg gate/compression/loudness/peak mastering to {len(chapters)} chapters.")
        edge_normalized = self._normalize_chapter_edges(chapters, db_session)
        if edge_normalized:
            notes.append(f"Applied ACX room tone padding to {len(edge_normalized)} chapters.")
        notes.append("Publishing mastering chain validated via chapter QA after mastering.")
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

    @staticmethod
    def _audio_to_samples(audio: AudioSegment) -> np.ndarray:
        """Convert a mono AudioSegment into normalized float samples."""

        mono_audio = audio.set_channels(1)
        samples = np.array(mono_audio.get_array_of_samples(), dtype=np.float32)
        if samples.size == 0:
            return samples
        max_amplitude = float(1 << ((8 * mono_audio.sample_width) - 1))
        return samples / max_amplitude

    @staticmethod
    def _samples_to_audio(samples: np.ndarray, frame_rate: int) -> AudioSegment:
        """Convert normalized float samples back into a 16-bit mono AudioSegment."""

        clipped = np.clip(samples, -0.999969, 0.999969)
        int_samples = np.round(clipped * np.iinfo(np.int16).max).astype(np.int16)
        return AudioSegment(
            data=int_samples.tobytes(),
            sample_width=2,
            frame_rate=frame_rate,
            channels=1,
        )

    @staticmethod
    def _dbfs_from_amplitude(value: float) -> float:
        """Convert linear amplitude into dBFS with a practical floor."""

        if value <= 1e-9:
            return -100.0
        return float(20 * np.log10(value))

    @staticmethod
    def _linear_rms(samples: np.ndarray) -> float:
        """Return RMS amplitude for normalized samples."""

        if samples.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(samples))))

    @staticmethod
    def _spectral_flatness(samples: np.ndarray) -> float:
        """Estimate spectral flatness for breath/noise-like discrimination."""

        if samples.size == 0:
            return 0.0
        spectrum = np.abs(np.fft.rfft(samples.astype(np.float32))) + 1e-9
        geometric = float(np.exp(np.mean(np.log(spectrum))))
        arithmetic = float(np.mean(spectrum))
        if arithmetic <= 1e-9:
            return 0.0
        return geometric / arithmetic

    @staticmethod
    def _apply_sos_filter(
        samples: np.ndarray,
        frame_rate: int,
        *,
        low_hz: float | None = None,
        high_hz: float | None = None,
        order: int = 4,
    ) -> np.ndarray:
        """Apply a Butterworth high-pass or band-pass filter safely."""

        if samples.size == 0 or frame_rate <= 0:
            return samples.copy()

        nyquist = frame_rate / 2.0
        if low_hz is not None and high_hz is not None:
            low = max(low_hz / nyquist, 1e-5)
            high = min(high_hz / nyquist, 0.999)
            if low >= high:
                return samples.copy()
            sos = signal.butter(order, [low, high], btype="bandpass", output="sos")
        elif low_hz is not None:
            cutoff = max(low_hz / nyquist, 1e-5)
            sos = signal.butter(order, cutoff, btype="highpass", output="sos")
        else:
            return samples.copy()

        try:
            return signal.sosfiltfilt(sos, samples.astype(np.float32)).astype(np.float32)
        except ValueError:
            return signal.sosfilt(sos, samples.astype(np.float32)).astype(np.float32)

    @staticmethod
    def _frame_ranges(total_samples: int, frame_size: int) -> list[tuple[int, int]]:
        """Return sample ranges for fixed-size frame analysis."""

        return [
            (start, min(start + frame_size, total_samples))
            for start in range(0, total_samples, frame_size)
        ]

    def _frame_dbfs_series(self, samples: np.ndarray, frame_rate: int, *, frame_ms: int) -> list[float]:
        """Return RMS dBFS per analysis frame."""

        if samples.size == 0 or frame_rate <= 0:
            return []
        frame_size = max(int(frame_rate * (frame_ms / 1000.0)), 1)
        values: list[float] = []
        for start, end in self._frame_ranges(samples.size, frame_size):
            values.append(self._dbfs_from_amplitude(self._linear_rms(samples[start:end])))
        return values

    @staticmethod
    def _contiguous_regions(mask: list[bool]) -> list[tuple[int, int]]:
        """Collapse a boolean mask into inclusive frame ranges."""

        if not mask:
            return []
        regions: list[tuple[int, int]] = []
        start: int | None = None
        for index, active in enumerate(mask):
            if active and start is None:
                start = index
            elif not active and start is not None:
                regions.append((start, index - 1))
                start = None
        if start is not None:
            regions.append((start, len(mask) - 1))
        return regions

    @staticmethod
    def _frame_gain_to_sample_envelope(
        frame_gains: list[float],
        total_samples: int,
        frame_size: int,
    ) -> np.ndarray:
        """Expand frame-level gain values into a sample-rate envelope."""

        if not frame_gains:
            return np.ones(total_samples, dtype=np.float32)
        envelope = np.repeat(np.array(frame_gains, dtype=np.float32), frame_size)
        if envelope.size < total_samples:
            envelope = np.pad(envelope, (0, total_samples - envelope.size), constant_values=frame_gains[-1])
        return envelope[:total_samples]

    def _apply_de_esser_to_audio(self, audio: AudioSegment) -> tuple[AudioSegment, bool]:
        """Reduce short bursts of excessive 4-10kHz sibilance before compression."""

        samples = self._audio_to_samples(audio)
        if samples.size == 0:
            return audio, False

        frame_rate = audio.frame_rate
        sibilance = self._apply_sos_filter(samples, frame_rate, low_hz=4000.0, high_hz=10000.0)
        remainder = samples - sibilance
        frame_ms = 5
        frame_size = max(int(frame_rate * (frame_ms / 1000.0)), 1)
        frame_gains: list[float] = []
        current_gain = 1.0
        attack_frames = 1
        release_frames = max(int(round(50 / frame_ms)), 1)
        changed = False

        for start, end in self._frame_ranges(samples.size, frame_size):
            sib_rms = self._linear_rms(sibilance[start:end])
            rest_rms = self._linear_rms(remainder[start:end])
            excess_db = self._dbfs_from_amplitude(sib_rms) - self._dbfs_from_amplitude(max(rest_rms, 1e-9))
            if excess_db > self.DE_ESS_THRESHOLD_DB:
                reduction_db = min(max(3.0 + ((excess_db - self.DE_ESS_THRESHOLD_DB) * 0.35), 3.0), 6.0)
                target_gain = float(10 ** (-reduction_db / 20.0))
                changed = True
            else:
                target_gain = 1.0
            smoothing = attack_frames if target_gain < current_gain else release_frames
            current_gain += (target_gain - current_gain) / max(smoothing, 1)
            frame_gains.append(current_gain)

        if not changed:
            return audio, False

        gain_envelope = self._frame_gain_to_sample_envelope(frame_gains, samples.size, frame_size)
        processed = remainder + (sibilance * gain_envelope)
        return self._samples_to_audio(processed, frame_rate), True

    def _apply_plosive_reduction_to_audio(self, audio: AudioSegment) -> tuple[AudioSegment, bool]:
        """Attenuate short low-frequency bursts that read like plosive thumps."""

        samples = self._audio_to_samples(audio)
        if samples.size == 0:
            return audio, False

        frame_rate = audio.frame_rate
        low_band = self._apply_sos_filter(samples, frame_rate, low_hz=20.0, high_hz=300.0)
        remainder = samples - low_band
        frame_ms = 10
        frame_size = max(int(frame_rate * (frame_ms / 1000.0)), 1)
        low_db = self._frame_dbfs_series(low_band, frame_rate, frame_ms=frame_ms)
        if len(low_db) < 3:
            return audio, False

        frame_gains = [1.0] * len(low_db)
        changed = False
        for index, current_db in enumerate(low_db):
            start = max(index - 6, 0)
            end = min(index + 7, len(low_db))
            context = [low_db[i] for i in range(start, end) if i != index]
            if not context:
                continue
            context_db = float(np.median(np.array(context, dtype=np.float32)))
            burst_db = current_db - context_db
            if burst_db < self.PLOSIVE_THRESHOLD_DB or current_db < -45.0:
                continue
            attenuation_db = min(max(burst_db - 9.0, 3.0), 6.0)
            frame_gains[index] = min(frame_gains[index], float(10 ** (-attenuation_db / 20.0)))
            changed = True

        regions = self._contiguous_regions([gain < 0.999 for gain in frame_gains])
        valid_regions = {
            index
            for start, end in regions
            if ((end - start) + 1) * frame_ms <= self.PLOSIVE_MAX_DURATION_MS
            for index in range(start, end + 1)
        }
        if not valid_regions:
            return audio, False

        filtered_gains = [gain if index in valid_regions else 1.0 for index, gain in enumerate(frame_gains)]
        gain_envelope = self._frame_gain_to_sample_envelope(filtered_gains, samples.size, frame_size)
        processed = remainder + (low_band * gain_envelope)
        return self._samples_to_audio(processed, frame_rate), changed

    def _apply_breath_normalization_to_audio(self, audio: AudioSegment) -> tuple[AudioSegment, bool]:
        """Soften loud inhalations without removing natural breathing entirely."""

        samples = self._audio_to_samples(audio)
        if samples.size == 0:
            return audio, False

        frame_rate = audio.frame_rate
        breath_band = self._apply_sos_filter(samples, frame_rate, low_hz=100.0, high_hz=1000.0)
        frame_ms = 50
        frame_size = max(int(frame_rate * (frame_ms / 1000.0)), 1)
        breath_db = self._frame_dbfs_series(breath_band, frame_rate, frame_ms=frame_ms)
        full_db = self._frame_dbfs_series(samples, frame_rate, frame_ms=frame_ms)
        flatness_series = [
            self._spectral_flatness(breath_band[start:start + frame_size])
            for start in range(0, samples.size, frame_size)
        ]
        if not breath_db:
            return audio, False

        candidate_mask = [
            breath_db[index] > -48.0
            and full_db[index] < -20.0
            and flatness_series[index] > 0.05
            and abs(breath_db[index] - full_db[index]) >= 6.0
            for index in range(len(breath_db))
        ]
        changed = False
        processed = samples.copy()

        for start_frame, end_frame in self._contiguous_regions(candidate_mask):
            duration_ms = ((end_frame - start_frame) + 1) * frame_ms
            if duration_ms < 100 or duration_ms > 800:
                continue
            preceding_start = max(start_frame - 3, 0)
            preceding_db = max(full_db[preceding_start:start_frame] or [-100.0])
            if preceding_db > -30.0:
                continue
            sample_start = start_frame * frame_size
            sample_end = min((end_frame + 1) * frame_size, samples.size)
            segment = processed[sample_start:sample_end]
            if self._spectral_flatness(breath_band[sample_start:sample_end]) < 0.05:
                continue
            peak_db = self._dbfs_from_amplitude(float(np.max(np.abs(segment))))
            if peak_db <= self.BREATH_REDUCTION_TRIGGER_DBFS:
                continue
            attenuation_db = self.BREATH_TARGET_DBFS - peak_db
            gain = float(10 ** (attenuation_db / 20.0))
            processed[sample_start:sample_end] *= gain
            changed = True

        if not changed:
            return audio, False
        return self._samples_to_audio(processed, frame_rate), True

    def _apply_frequency_selective_processing(
        self,
        chapters: list[Chapter],
        db_session: Session,
    ) -> dict[str, list[int]]:
        """Run HPF, plosive control, de-essing, and breath softening in order."""

        results = {
            "high_passed": [],
            "plosive_reduced": [],
            "de_essed": [],
            "breath_normalized": [],
        }

        for chapter in chapters:
            audio_path = self._resolve_audio_path(chapter)
            original = AudioSegment.from_file(audio_path).set_channels(1).set_sample_width(2)
            processed = high_pass_filter(original, cutoff=self.HIGH_PASS_HZ)
            if processed.raw_data != original.raw_data:
                results["high_passed"].append(chapter.number)

            processed, plosive_changed = self._apply_plosive_reduction_to_audio(processed)
            if plosive_changed:
                results["plosive_reduced"].append(chapter.number)

            processed, de_ess_changed = self._apply_de_esser_to_audio(processed)
            if de_ess_changed:
                results["de_essed"].append(chapter.number)

            processed, breath_changed = self._apply_breath_normalization_to_audio(processed)
            if breath_changed:
                results["breath_normalized"].append(chapter.number)

            self._persist_audio_if_changed(chapter, audio_path, original, processed)

        db_session.flush()
        return results

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
                threshold=-24.0,
                ratio=self.COMPRESSION_RATIO,
                attack=15,
                release=100,
            )
            if self._persist_audio_if_changed(chapter, audio_path, audio, processed):
                compressed.append(chapter.number)

        db_session.flush()
        return compressed

    def _normalize_chapter_edges(self, chapters: list[Chapter], db_session: Session) -> list[int]:
        """Replace digital silence with deterministic low-level room tone padding."""

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
                head_db = audio[:500].dBFS if len(audio) >= 500 else audio.dBFS
                tail_db = audio[-1000:].dBFS if len(audio) >= 1000 else audio.dBFS
                if (
                    head_db != float("-inf")
                    and tail_db != float("-inf")
                    and self.ROOM_TONE_MIN_DBFS <= head_db <= self.ROOM_TONE_MAX_DBFS
                    and self.ROOM_TONE_MIN_DBFS <= tail_db <= self.ROOM_TONE_MAX_DBFS
                ):
                    continue
            trimmed = self._strip_silence(audio)
            head_tone = self._generate_room_tone(audio.frame_rate, self.TARGET_LEAD_IN_MS)
            tail_tone = self._generate_room_tone(audio.frame_rate, self.TARGET_TRAIL_OUT_MS)
            padded = (
                head_tone
                + trimmed
                + tail_tone
            )
            if self._persist_audio_if_changed(chapter, audio_path, audio, padded):
                normalized.append(chapter.number)

        db_session.flush()
        return normalized

    def _generate_room_tone(self, frame_rate: int, duration_ms: int) -> AudioSegment:
        """Return cached deterministic pink-noise room tone at the target level."""

        cache_key = (frame_rate, duration_ms)
        cached = self._room_tone_cache.get(cache_key)
        if cached is not None:
            return cached

        sample_count = max(int(frame_rate * (duration_ms / 1000.0)), 1)
        rng = np.random.default_rng(self.ROOM_TONE_SEED + frame_rate + duration_ms)
        white = rng.standard_normal(sample_count).astype(np.float32)
        spectrum = np.fft.rfft(white)
        frequencies = np.fft.rfftfreq(sample_count, d=1.0 / frame_rate)
        scale = np.ones_like(frequencies, dtype=np.float32)
        scale[1:] = 1.0 / np.sqrt(frequencies[1:])
        pink = np.fft.irfft(spectrum * scale, n=sample_count).astype(np.float32)
        peak = float(np.max(np.abs(pink))) if pink.size else 0.0
        if peak > 0:
            pink /= peak
        tone = self._samples_to_audio(pink, frame_rate)
        if tone.dBFS != float("-inf"):
            tone = tone.apply_gain(self.ROOM_TONE_LEVEL_DBFS - float(tone.dBFS))
        self._room_tone_cache[cache_key] = tone
        return tone

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
            audio = AudioSegment.from_file(audio_path).set_channels(1).set_sample_width(2)
            if frame_rate != ACX_SAMPLE_RATE:
                blockers.append(f"Chapter {chapter.number} is {frame_rate}Hz after mastering.")
            if channels != ACX_REQUIREMENTS["channels"]:
                blockers.append(f"Chapter {chapter.number} is not mono after mastering.")
            if sample_width * 8 != ACX_REQUIREMENTS["bit_depth"]:
                blockers.append(f"Chapter {chapter.number} is not 16-bit PCM after mastering.")
            trimmed = self._strip_silence(audio)
            rms_db = float(trimmed.dBFS) if len(trimmed) > 0 and trimmed.dBFS != float("-inf") else -100.0
            if not (ACX_REQUIREMENTS["rms_min_db"] <= rms_db <= ACX_REQUIREMENTS["rms_max_db"]):
                blockers.append(f"Chapter {chapter.number} RMS is {rms_db:.1f} dBFS after mastering.")
            if float(audio.max_dBFS) > self.MAX_PEAK_DBFS:
                blockers.append(f"Chapter {chapter.number} peak remains above {self.MAX_PEAK_DBFS:.1f} dBFS after mastering.")
            head_db = audio[:500].dBFS if len(audio) >= 500 else audio.dBFS
            tail_db = audio[-1000:].dBFS if len(audio) >= 1000 else audio.dBFS
            if head_db == float("-inf") or not (self.ROOM_TONE_MIN_DBFS <= head_db <= self.ROOM_TONE_MAX_DBFS):
                blockers.append(f"Chapter {chapter.number} is missing compliant head room tone.")
            if tail_db == float("-inf") or not (self.ROOM_TONE_MIN_DBFS <= tail_db <= self.ROOM_TONE_MAX_DBFS):
                blockers.append(f"Chapter {chapter.number} is missing compliant tail room tone.")

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
