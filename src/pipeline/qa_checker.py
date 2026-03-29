"""Automated audio QA checks and persistence helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel
from pydub import AudioSegment, silence
from scipy import signal
from sqlalchemy.orm import Session

from src.config import settings
from src.database import Chapter, ChapterQARecord, QAAutomaticStatus, QAManualStatus, QAStatus, SessionLocal, utc_now
from src.engines.chunker import TextChunker

logger = logging.getLogger(__name__)

QA_THRESHOLDS = {
    "duration_tolerance_percent": 20,
    "words_per_second": 0.4,
    "clipping_threshold": 0.95,
    "silence_threshold_dbfs": -40,
    "silence_min_duration_ms": 3000,
    "silence_start_allowance_ms": 1000,
    "silence_end_allowance_ms": 2000,
    "silence_max_acceptable_seconds": 5.0,
    "volume_deviation_threshold_db": 3.0,
    "chunk_duration_seconds": 1,
}

STATUS_SEVERITY = {
    QAAutomaticStatus.PASS.value: 0,
    QAAutomaticStatus.WARNING.value: 1,
    QAAutomaticStatus.FAIL.value: 2,
}

CHECK_NAMES = (
    "file_exists",
    "duration_check",
    "clipping_detection",
    "contextual_silence",
    "volume_consistency",
    "voice_consistency",
    "stitch_quality",
    "pacing_detailed",
    "spectral_quality",
    "plosive_artifacts",
    "breath_levels",
    "room_tone_padding",
    "lufs_compliance",
)
_DFT_MATRIX_CACHE: dict[int, np.ndarray] = {}
QA_CHAPTER_TIMEOUT_SECONDS = 60
QA_FAST_PATH_DURATION_SECONDS = 300.0
QA_LUFS_TIMEOUT_SECONDS = 45
CONTEXT_SILENCE_RULES = {
    "mid_sentence": {"min_ms": 0, "max_ms": 800, "expected_ms": 200},
    "sentence_boundary": {"min_ms": 300, "max_ms": 1500, "expected_ms": 600},
    "paragraph_boundary": {"min_ms": 600, "max_ms": 2500, "expected_ms": 1200},
    "dialogue_transition": {"min_ms": 400, "max_ms": 1200, "expected_ms": 700},
    "chapter_start": {"min_ms": 500, "max_ms": 2000, "expected_ms": 1000},
    "chapter_end": {"min_ms": 500, "max_ms": 3000, "expected_ms": 1500},
}


class QACheckResult(BaseModel):
    """One automatic QA check outcome."""

    name: str
    status: str
    message: str
    value: float | None = None
    details: dict[str, Any] | None = None


class QAResult(BaseModel):
    """Aggregate QA outcome for a generated chapter."""

    chapter_n: int
    book_id: int
    timestamp: datetime
    checks: list[QACheckResult]
    overall_status: str
    notes: str = ""
    chapter_report: dict[str, Any] | None = None

    @property
    def has_warnings(self) -> bool:
        """Return True when at least one check emitted a warning."""

        return any(check.status == QAAutomaticStatus.WARNING.value for check in self.checks)

    @property
    def has_failures(self) -> bool:
        """Return True when at least one check failed."""

        return any(check.status == QAAutomaticStatus.FAIL.value for check in self.checks)


class _AudioAnalysis(BaseModel):
    """Decoded audio ready for QA analysis."""

    audio: Any
    normalized_samples: Any
    actual_duration: float
    peak_amplitude: float
    chunk_rms_dbfs: list[float]
    mid_chapter_silences_ms: list[int]

    model_config = {"arbitrary_types_allowed": True}


@dataclass(slots=True)
class ChapterQAReport:
    """Structured chapter-level QA summary."""

    chapter_number: int
    chapter_title: str
    duration_seconds: float
    total_checks: int
    passed: int
    warnings: int
    failures: int
    results: list[QACheckResult]
    pacing_stats: dict[str, Any]
    silence_stats: dict[str, Any]
    stitch_quality: dict[str, Any]

    @property
    def overall_grade(self) -> str:
        """Return the audiobook-grade summary for the chapter."""

        if self.failures > 0:
            return "F"
        if self.warnings > 3:
            return "C"
        if self.warnings > 0:
            return "B"
        return "A"

    @property
    def ready_for_export(self) -> bool:
        """Return whether the chapter is export-ready without manual intervention."""

        return self.overall_grade in ("A", "B")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""

        return {
            "chapter_number": self.chapter_number,
            "chapter_title": self.chapter_title,
            "duration_seconds": round(self.duration_seconds, 4),
            "total_checks": self.total_checks,
            "passed": self.passed,
            "warnings": self.warnings,
            "failures": self.failures,
            "results": [result.model_dump(mode="json") for result in self.results],
            "pacing_stats": self.pacing_stats,
            "silence_stats": self.silence_stats,
            "stitch_quality": self.stitch_quality,
            "overall_grade": self.overall_grade,
            "ready_for_export": self.ready_for_export,
        }


def _resolve_audio_path(audio_path: str | None) -> Path | None:
    """Resolve a stored chapter audio path to an absolute output path."""

    if audio_path is None or not audio_path.strip():
        return None

    candidate = Path(audio_path)
    if candidate.is_absolute():
        return candidate
    return (Path(settings.OUTPUTS_PATH) / candidate).resolve()


def _file_exists_result(audio_path: Path | None) -> QACheckResult:
    """Return the file existence check result for a resolved path."""

    if audio_path is None:
        return QACheckResult(
            name="file_exists",
            status=QAAutomaticStatus.FAIL.value,
            message="Audio file path is not recorded.",
            value=0,
        )

    if not audio_path.exists():
        return QACheckResult(
            name="file_exists",
            status=QAAutomaticStatus.FAIL.value,
            message="Audio file does not exist.",
            value=0,
        )

    size_bytes = audio_path.stat().st_size
    if size_bytes <= 0:
        return QACheckResult(
            name="file_exists",
            status=QAAutomaticStatus.FAIL.value,
            message="Audio file is empty.",
            value=0,
        )

    return QACheckResult(
        name="file_exists",
        status=QAAutomaticStatus.PASS.value,
        message=f"File exists ({size_bytes} bytes).",
        value=float(size_bytes),
    )


def check_file_exists(audio_path: str | Path | None) -> QACheckResult:
    """Verify that a generated audio file exists and is non-empty."""

    resolved_path = None if audio_path is None else Path(audio_path)
    return _file_exists_result(resolved_path)


def _mono_samples(audio: AudioSegment) -> np.ndarray:
    """Return mono float32 samples for the provided audio segment."""

    mono_audio = audio.set_channels(1)
    samples = np.array(mono_audio.get_array_of_samples(), dtype=np.float32)
    if samples.size == 0:
        return samples

    sample_width = mono_audio.sample_width
    max_amplitude = float(1 << ((8 * sample_width) - 1))
    return samples / max_amplitude


def _chunk_rms_dbfs(normalized_samples: np.ndarray, frame_rate: int, chunk_seconds: int) -> list[float]:
    """Calculate chunk RMS levels in dBFS for the supplied samples."""

    chunk_size = max(frame_rate * chunk_seconds, 1)
    chunk_levels: list[float] = []

    for start in range(0, normalized_samples.size, chunk_size):
        chunk = normalized_samples[start:start + chunk_size]
        if chunk.size == 0:
            continue

        rms = float(np.sqrt(np.mean(np.square(chunk))))
        if rms <= 0:
            continue

        chunk_levels.append(20 * np.log10(rms))

    return chunk_levels


def _dbfs_from_amplitude(value: float) -> float:
    """Convert a normalized amplitude into dBFS with a floor for silence."""

    if value <= 1e-9:
        return -100.0
    return float(20 * np.log10(value))


def _mid_chapter_silences(audio: AudioSegment) -> list[int]:
    """Return silent gap durations inside the chapter body in milliseconds."""

    silent_segments = silence.detect_silence(
        audio,
        min_silence_len=QA_THRESHOLDS["silence_min_duration_ms"],
        silence_thresh=QA_THRESHOLDS["silence_threshold_dbfs"],
    )

    middle_start = QA_THRESHOLDS["silence_start_allowance_ms"]
    middle_end = max(len(audio) - QA_THRESHOLDS["silence_end_allowance_ms"], middle_start)
    durations: list[int] = []

    for segment_start, segment_end in silent_segments:
        overlap_start = max(segment_start, middle_start)
        overlap_end = min(segment_end, middle_end)
        if overlap_end > overlap_start:
            durations.append(overlap_end - overlap_start)

    return durations


def _active_speech_ms(audio: AudioSegment) -> int:
    """Return the approximate amount of active speech in milliseconds."""

    active_segments = silence.detect_nonsilent(
        audio,
        min_silence_len=250,
        silence_thresh=QA_THRESHOLDS["silence_threshold_dbfs"],
    )
    return sum(end - start for start, end in active_segments)


def _load_audio_analysis(audio_path: str | Path) -> _AudioAnalysis:
    """Decode WAV audio and pre-compute reusable QA metrics."""

    audio = AudioSegment.from_wav(audio_path).set_channels(1)
    normalized_samples = _mono_samples(audio)

    peak_amplitude = 0.0
    if normalized_samples.size > 0:
        peak_amplitude = float(np.max(np.abs(normalized_samples)))

    return _AudioAnalysis(
        audio=audio,
        normalized_samples=normalized_samples,
        actual_duration=len(audio) / 1000.0,
        peak_amplitude=peak_amplitude,
        chunk_rms_dbfs=_chunk_rms_dbfs(
            normalized_samples,
            audio.frame_rate,
            int(QA_THRESHOLDS["chunk_duration_seconds"]),
        ),
        mid_chapter_silences_ms=_mid_chapter_silences(audio),
    )


def _wav_duration_seconds(audio_path: str | Path) -> float:
    """Read WAV duration from the file header without decoding the entire file."""

    with wave.open(str(audio_path), "rb") as wav_file:
        frame_rate = wav_file.getframerate()
        frame_count = wav_file.getnframes()
    return frame_count / frame_rate if frame_rate else 0.0


def _stream_peak_amplitude(audio_path: str | Path) -> float:
    """Measure peak amplitude by streaming WAV samples instead of loading the full file."""

    with wave.open(str(audio_path), "rb") as wav_file:
        channels = max(wav_file.getnchannels(), 1)
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        frame_count = wav_file.getnframes()
        total_duration = frame_count / sample_rate if sample_rate else 0.0
        max_amplitude = float(1 << ((8 * sample_width) - 1))
        sample_stride = max(1, int(sample_rate / 8_000)) if total_duration >= QA_FAST_PATH_DURATION_SECONDS else 1
        peak_amplitude = 0.0

        dtype_map: dict[int, Any] = {
            1: np.int8,
            2: np.int16,
            4: np.int32,
        }
        dtype = dtype_map.get(sample_width)
        if dtype is None:
            raise ValueError(f"Unsupported sample width for clipping analysis: {sample_width}")

        chunk_frames = max(sample_rate * 10, 1)
        while True:
            frame_data = wav_file.readframes(chunk_frames)
            if not frame_data:
                break
            samples = np.frombuffer(frame_data, dtype=dtype)
            if channels > 1:
                samples = samples[::channels]
            if sample_stride > 1:
                samples = samples[::sample_stride]
            if samples.size == 0:
                continue
            chunk_peak = float(np.max(np.abs(samples.astype(np.float32)))) / max_amplitude
            if chunk_peak > peak_amplitude:
                peak_amplitude = chunk_peak

    return peak_amplitude


def _magnitude_spectrum(samples: np.ndarray) -> np.ndarray:
    """Return a real-spectrum magnitude without relying on ``numpy.fft``."""

    frame_length = samples.size
    if frame_length == 0:
        return np.zeros(0, dtype=np.float32)

    if frame_length not in _DFT_MATRIX_CACHE:
        frequencies = np.arange((frame_length // 2) + 1, dtype=np.float32)[:, None]
        times = np.arange(frame_length, dtype=np.float32)[None, :]
        exponent = (-2j * np.pi * frequencies * times) / float(frame_length)
        _DFT_MATRIX_CACHE[frame_length] = np.exp(exponent).astype(np.complex64)

    return np.abs(_DFT_MATRIX_CACHE[frame_length] @ samples.astype(np.float32))


def _average_frame_spectrum(
    samples: np.ndarray,
    sample_rate: int,
    *,
    frame_size: int = 1024,
    hop_size: int = 512,
) -> tuple[np.ndarray, np.ndarray]:
    """Return average magnitude spectrum across non-silent frames."""

    if samples.size == 0:
        return (np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32))

    if samples.size < frame_size:
        samples = np.pad(samples, (0, frame_size - samples.size))

    window = np.hanning(frame_size).astype(np.float32)
    spectra: list[np.ndarray] = []

    for start in range(0, samples.size - frame_size + 1, hop_size):
        frame = samples[start:start + frame_size]
        if np.sqrt(np.mean(np.square(frame))) <= 1e-4:
            continue
        spectra.append(_magnitude_spectrum(frame * window))

    if not spectra:
        spectra = [_magnitude_spectrum(samples[:frame_size] * window)]

    mean_spectrum = np.mean(np.stack(spectra), axis=0)
    frequencies = (np.arange(mean_spectrum.size, dtype=np.float32) * sample_rate) / float(frame_size)
    return (frequencies, mean_spectrum.astype(np.float32))


def _spectral_centroid(samples: np.ndarray, sample_rate: int) -> float | None:
    """Return the spectral centroid for the provided samples."""

    frequencies, spectrum = _average_frame_spectrum(samples, sample_rate)
    if spectrum.size == 0:
        return None

    energy = float(np.sum(spectrum))
    if energy <= 1e-8:
        return None
    return float(np.sum(frequencies * spectrum) / energy)


def _linear_rms(samples: np.ndarray) -> float:
    """Return linear RMS amplitude for normalized samples."""

    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples))))


def _band_limited_signal(
    samples: np.ndarray,
    sample_rate: int,
    *,
    low_hz: float,
    high_hz: float,
    order: int = 4,
) -> np.ndarray:
    """Return a band-limited copy of the signal for artifact-specific analysis."""

    if samples.size == 0 or sample_rate <= 0:
        return samples.copy()

    nyquist = sample_rate / 2.0
    low = max(low_hz / nyquist, 1e-5)
    high = min(high_hz / nyquist, 0.999)
    if low >= high:
        return samples.copy()

    sos = signal.butter(order, [low, high], btype="bandpass", output="sos")
    try:
        return signal.sosfiltfilt(sos, samples.astype(np.float32)).astype(np.float32)
    except ValueError:
        return signal.sosfilt(sos, samples.astype(np.float32)).astype(np.float32)


def _frame_dbfs_series(samples: np.ndarray, sample_rate: int, *, frame_ms: int) -> list[float]:
    """Return RMS dBFS values for a fixed analysis frame size."""

    if samples.size == 0 or sample_rate <= 0:
        return []
    frame_size = max(int(sample_rate * (frame_ms / 1000.0)), 1)
    values: list[float] = []
    for start in range(0, samples.size, frame_size):
        values.append(_dbfs_from_amplitude(_linear_rms(samples[start:start + frame_size])))
    return values


def _contiguous_regions(mask: list[bool]) -> list[tuple[int, int]]:
    """Collapse a boolean series into inclusive contiguous frame ranges."""

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


def _spectral_flatness(samples: np.ndarray) -> float:
    """Return spectral flatness to separate broadband breaths from tonal speech."""

    if samples.size == 0:
        return 0.0
    spectrum = np.abs(np.fft.rfft(samples.astype(np.float32))) + 1e-9
    geometric = float(np.exp(np.mean(np.log(spectrum))))
    arithmetic = float(np.mean(spectrum))
    if arithmetic <= 1e-9:
        return 0.0
    return geometric / arithmetic


def _detect_plosive_events(audio: AudioSegment) -> list[dict[str, float]]:
    """Detect short 20-300Hz bursts that behave like plosive pops."""

    samples = _mono_samples(audio)
    if samples.size == 0:
        return []

    low_band = _band_limited_signal(samples, audio.frame_rate, low_hz=20.0, high_hz=300.0)
    frame_ms = 10
    frame_db = _frame_dbfs_series(low_band, audio.frame_rate, frame_ms=frame_ms)
    if len(frame_db) < 3:
        return []

    candidate_mask: list[bool] = []
    context_deltas: list[float] = []
    for index, current_db in enumerate(frame_db):
        start = max(index - 6, 0)
        end = min(index + 7, len(frame_db))
        context = [frame_db[i] for i in range(start, end) if i != index]
        context_db = float(np.median(np.array(context, dtype=np.float32))) if context else -100.0
        delta = current_db - context_db
        context_deltas.append(delta)
        candidate_mask.append(delta >= 15.0 and current_db > -45.0)

    events: list[dict[str, float]] = []
    for start_frame, end_frame in _contiguous_regions(candidate_mask):
        duration_ms = ((end_frame - start_frame) + 1) * frame_ms
        if duration_ms > 20:
            continue
        peak_delta = max(context_deltas[start_frame:end_frame + 1])
        events.append(
            {
                "start_ms": float(start_frame * frame_ms),
                "end_ms": float((end_frame + 1) * frame_ms),
                "duration_ms": float(duration_ms),
                "peak_diff_db": round(float(peak_delta), 3),
            }
        )
    return events


def _detect_breath_events(audio: AudioSegment) -> list[dict[str, float]]:
    """Detect breath-like segments using band-limited energy, silence lead-in, and flatness."""

    samples = _mono_samples(audio)
    if samples.size == 0:
        return []

    breath_band = _band_limited_signal(samples, audio.frame_rate, low_hz=100.0, high_hz=1000.0)
    frame_ms = 50
    frame_size = max(int(audio.frame_rate * (frame_ms / 1000.0)), 1)
    band_db = _frame_dbfs_series(breath_band, audio.frame_rate, frame_ms=frame_ms)
    full_db = _frame_dbfs_series(samples, audio.frame_rate, frame_ms=frame_ms)
    flatness_series = [
        _spectral_flatness(breath_band[start:start + frame_size])
        for start in range(0, samples.size, frame_size)
    ]
    if not band_db:
        return []

    candidate_mask = [
        band_db[index] > -48.0
        and full_db[index] < -20.0
        and flatness_series[index] > 0.05
        and abs(band_db[index] - full_db[index]) >= 6.0
        for index in range(len(band_db))
    ]

    events: list[dict[str, float]] = []
    for start_frame, end_frame in _contiguous_regions(candidate_mask):
        duration_ms = ((end_frame - start_frame) + 1) * frame_ms
        if duration_ms < 100 or duration_ms > 800:
            continue
        preceding_start = max(start_frame - 3, 0)
        preceding_db = max(full_db[preceding_start:start_frame] or [-100.0])
        if preceding_db > -30.0:
            continue
        sample_start = start_frame * frame_size
        sample_end = min((end_frame + 1) * frame_size, samples.size)
        segment = samples[sample_start:sample_end]
        flatness = _spectral_flatness(breath_band[sample_start:sample_end])
        if flatness < 0.05:
            continue
        peak_db = _dbfs_from_amplitude(float(np.max(np.abs(segment)))) if segment.size else -100.0
        events.append(
            {
                "start_ms": float(start_frame * frame_ms),
                "end_ms": float((end_frame + 1) * frame_ms),
                "duration_ms": float(duration_ms),
                "peak_dbfs": round(float(peak_db), 3),
                "flatness": round(float(flatness), 3),
            }
        )
    return events


def _median(values: list[float] | np.ndarray) -> float | None:
    """Return a stable median without relying on numpy's median implementation."""

    sequence = [float(value) for value in values]
    if not sequence:
        return None

    ordered = sorted(sequence)
    midpoint = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def _estimate_pitch(audio_segment: AudioSegment, sample_rate: int) -> float | None:
    """Estimate a median F0 using frame-wise zero-crossing intervals."""

    samples = _mono_samples(audio_segment)
    if samples.size == 0:
        return None

    frame_size = max(int(sample_rate * 0.1), 1)
    hop_size = max(int(sample_rate * 0.05), 1)
    pitches: list[float] = []

    for start in range(0, samples.size - frame_size + 1, hop_size):
        frame = samples[start:start + frame_size]
        if _linear_rms(frame) <= 0.01:
            continue

        centered = frame - float(np.mean(frame))
        crossings = np.where((centered[:-1] <= 0) & (centered[1:] > 0))[0]
        if crossings.size < 2:
            continue

        intervals = np.diff(crossings)
        if intervals.size == 0:
            continue

        period = _median(intervals)
        if period is None:
            continue
        if period <= 0:
            continue

        pitch = sample_rate / period
        if 48.0 <= pitch <= 480.0:
            pitches.append(pitch)

    if not pitches:
        return None
    return _median(pitches)


def _parse_chunk_boundaries(boundary_payload: str | None) -> list[float]:
    """Parse persisted chunk boundaries from JSON."""

    if boundary_payload is None or not boundary_payload.strip():
        return []

    try:
        parsed = json.loads(boundary_payload)
    except json.JSONDecodeError:
        logger.warning("Unable to parse chunk boundaries JSON: %s", boundary_payload)
        return []

    if not isinstance(parsed, list):
        return []

    boundaries = [max(float(value), 0.0) for value in parsed if isinstance(value, (int, float))]
    if not boundaries:
        return []
    if boundaries[0] > 0:
        boundaries.insert(0, 0.0)
    return sorted(set(boundaries))


def _chunk_regions(audio: AudioSegment, chunk_boundaries: list[float]) -> list[tuple[int, int]]:
    """Return `(start_ms, end_ms)` regions for each chunk."""

    if not chunk_boundaries:
        return [(0, len(audio))]

    boundaries_ms = [max(int(boundary * 1000), 0) for boundary in chunk_boundaries]
    if boundaries_ms[0] != 0:
        boundaries_ms.insert(0, 0)

    regions: list[tuple[int, int]] = []
    for index, start_ms in enumerate(boundaries_ms):
        end_ms = boundaries_ms[index + 1] if index + 1 < len(boundaries_ms) else len(audio)
        if end_ms > start_ms:
            regions.append((start_ms, end_ms))

    return regions or [(0, len(audio))]


def _status_from_severity(worst_status: str) -> int:
    """Return sortable severity for QA statuses."""

    return STATUS_SEVERITY.get(worst_status, 0)


def _synthesize_chapter_report(
    qa_result: QAResult,
    *,
    chapter_number: int,
    chapter_title: str,
    duration_seconds: float,
) -> dict[str, Any]:
    """Backfill a chapter report for older stored QA payloads."""

    passed = sum(check.status == QAAutomaticStatus.PASS.value for check in qa_result.checks)
    warnings = sum(check.status == QAAutomaticStatus.WARNING.value for check in qa_result.checks)
    failures = sum(check.status == QAAutomaticStatus.FAIL.value for check in qa_result.checks)
    pacing_stats: dict[str, Any] = {}
    silence_stats: dict[str, Any] = {}
    stitch_quality: dict[str, Any] = {}

    for check in qa_result.checks:
        if not check.details:
            continue
        if check.name == "pacing_detailed":
            pacing_stats = dict(check.details.get("pacing_stats") or {})
        elif check.name == "contextual_silence":
            silence_stats = dict(check.details.get("silence_stats") or {})
        elif check.name == "stitch_quality":
            stitch_quality = dict(check.details.get("stitch_quality") or {})

    report = ChapterQAReport(
        chapter_number=chapter_number,
        chapter_title=chapter_title,
        duration_seconds=duration_seconds,
        total_checks=len(qa_result.checks),
        passed=passed,
        warnings=warnings,
        failures=failures,
        results=qa_result.checks,
        pacing_stats=pacing_stats,
        silence_stats=silence_stats,
        stitch_quality=stitch_quality,
    )
    return report.to_dict()


def _write_chapter_report_sidecar(audio_path: Path | None, qa_result: QAResult) -> None:
    """Write the structured QA result next to the chapter WAV when possible."""

    if audio_path is None:
        return

    report_path = audio_path.with_suffix(".qa.json")
    payload = qa_result.chapter_report or qa_result.model_dump(mode="json")
    report_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def check_duration(
    audio_path: str | Path,
    word_count: int | None,
    *,
    actual_duration: float | None = None,
) -> QACheckResult:
    """Validate chapter duration against the word-count heuristic."""

    if word_count is None or word_count <= 0:
        return QACheckResult(
            name="duration_check",
            status=QAAutomaticStatus.WARNING.value,
            message="Word count unavailable; duration could not be validated.",
            value=None,
        )

    resolved_duration = actual_duration if actual_duration is not None else _load_audio_analysis(audio_path).actual_duration
    expected_duration = word_count * QA_THRESHOLDS["words_per_second"]
    tolerance = QA_THRESHOLDS["duration_tolerance_percent"] / 100
    min_duration = expected_duration * (1 - tolerance)
    max_duration = expected_duration * (1 + tolerance)

    if min_duration <= resolved_duration <= max_duration:
        return QACheckResult(
            name="duration_check",
            status=QAAutomaticStatus.PASS.value,
            message=(
                f"Duration {resolved_duration:.1f}s within expected range "
                f"{expected_duration:.1f}s (+/-{QA_THRESHOLDS['duration_tolerance_percent']}%)."
            ),
            value=round(resolved_duration, 3),
        )

    return QACheckResult(
        name="duration_check",
        status=QAAutomaticStatus.WARNING.value,
        message=(
            f"Duration {resolved_duration:.1f}s outside expected range "
            f"{expected_duration:.1f}s (+/-{QA_THRESHOLDS['duration_tolerance_percent']}%)."
        ),
        value=round(resolved_duration, 3),
    )


def check_clipping(
    audio_path: str | Path,
    *,
    peak_amplitude: float | None = None,
) -> QACheckResult:
    """Detect clipping by checking normalized peak amplitude."""

    resolved_peak = peak_amplitude if peak_amplitude is not None else _load_audio_analysis(audio_path).peak_amplitude
    threshold = float(QA_THRESHOLDS["clipping_threshold"])

    if resolved_peak < threshold:
        return QACheckResult(
            name="clipping_detection",
            status=QAAutomaticStatus.PASS.value,
            message=f"No clipping detected (peak: {resolved_peak:.3f}).",
            value=round(resolved_peak, 6),
        )

    return QACheckResult(
        name="clipping_detection",
        status=QAAutomaticStatus.FAIL.value,
        message=(
            f"Clipping detected (peak: {resolved_peak:.3f}, "
            f"threshold: {threshold:.2f})."
        ),
        value=round(resolved_peak, 6),
    )


def check_silence_gaps(audio_path: str | Path) -> QACheckResult:
    """Detect long mid-chapter silences that likely indicate bad output."""

    analysis = _load_audio_analysis(audio_path)
    if not analysis.mid_chapter_silences_ms:
        return QACheckResult(
            name="silence_gaps",
            status=QAAutomaticStatus.PASS.value,
            message="No long silence gaps detected.",
            value=0,
        )

    max_silence_seconds = max(analysis.mid_chapter_silences_ms) / 1000.0
    if max_silence_seconds <= QA_THRESHOLDS["silence_max_acceptable_seconds"]:
        return QACheckResult(
            name="silence_gaps",
            status=QAAutomaticStatus.WARNING.value,
            message=f"Long silence detected: {max_silence_seconds:.1f}s.",
            value=round(max_silence_seconds, 3),
        )

    return QACheckResult(
        name="silence_gaps",
        status=QAAutomaticStatus.FAIL.value,
        message=f"Long silence detected: {max_silence_seconds:.1f}s.",
        value=round(max_silence_seconds, 3),
    )


def check_volume_consistency(audio_path: str | Path) -> QACheckResult:
    """Measure chunk-to-chunk RMS variation to detect unstable volume."""

    analysis = _load_audio_analysis(audio_path)
    if len(analysis.chunk_rms_dbfs) <= 1:
        return QACheckResult(
            name="volume_consistency",
            status=QAAutomaticStatus.PASS.value,
            message="Audio too short or quiet to analyze chunk volume reliably.",
            value=0,
        )

    max_deviation = max(analysis.chunk_rms_dbfs) - min(analysis.chunk_rms_dbfs)
    if max_deviation <= QA_THRESHOLDS["volume_deviation_threshold_db"]:
        return QACheckResult(
            name="volume_consistency",
            status=QAAutomaticStatus.PASS.value,
            message=f"Volume consistent (max deviation: {max_deviation:.1f}dB).",
            value=round(max_deviation, 3),
        )

    return QACheckResult(
        name="volume_consistency",
        status=QAAutomaticStatus.WARNING.value,
        message=f"Volume varies significantly ({max_deviation:.1f}dB deviation).",
        value=round(max_deviation, 3),
    )


def _timeline_regions(total_duration_ms: int, chunk_boundaries: list[float]) -> list[tuple[int, int]]:
    """Return stitched chunk regions from chunk boundary timestamps."""

    if total_duration_ms <= 0:
        return [(0, 0)]
    if not chunk_boundaries:
        return [(0, total_duration_ms)]

    boundaries_ms = [max(int(boundary * 1000), 0) for boundary in chunk_boundaries]
    if not boundaries_ms or boundaries_ms[0] != 0:
        boundaries_ms.insert(0, 0)

    regions: list[tuple[int, int]] = []
    for index, start_ms in enumerate(boundaries_ms):
        end_ms = boundaries_ms[index + 1] if index + 1 < len(boundaries_ms) else total_duration_ms
        if end_ms > start_ms:
            regions.append((start_ms, min(end_ms, total_duration_ms)))

    return regions or [(0, total_duration_ms)]


def _chunk_text_ranges(text_content: str, chunk_boundaries: list[float], total_duration_ms: int) -> list[tuple[int, int]]:
    """Allocate approximate text ranges to chunk regions using stitched durations."""

    if not text_content:
        return [(0, 0)]

    regions = _timeline_regions(total_duration_ms, chunk_boundaries)
    total_chars = len(text_content)
    total_region_ms = sum(max(end - start, 1) for start, end in regions)
    allocated: list[tuple[int, int]] = []
    cursor = 0
    elapsed_ms = 0

    for index, (start_ms, end_ms) in enumerate(regions):
        region_ms = max(end_ms - start_ms, 1)
        elapsed_ms += region_ms
        if index == len(regions) - 1:
            next_cursor = total_chars
        else:
            next_cursor = min(total_chars, round((elapsed_ms / max(total_region_ms, 1)) * total_chars))
        allocated.append((cursor, next_cursor))
        cursor = next_cursor

    if allocated and allocated[-1][1] < total_chars:
        allocated[-1] = (allocated[-1][0], total_chars)
    return allocated or [(0, total_chars)]


def _timestamp_to_text_index(
    text_content: str,
    chunk_boundaries: list[float],
    timestamp_ms: float,
    total_duration_ms: int,
) -> int:
    """Map an audio timestamp onto an approximate character index in the source text."""

    if not text_content:
        return 0
    if total_duration_ms <= 0:
        return 0

    regions = _timeline_regions(total_duration_ms, chunk_boundaries)
    text_ranges = _chunk_text_ranges(text_content, chunk_boundaries, total_duration_ms)
    clamped_timestamp = min(max(int(timestamp_ms), 0), total_duration_ms)

    for (start_ms, end_ms), (char_start, char_end) in zip(regions, text_ranges, strict=False):
        if clamped_timestamp > end_ms and end_ms != total_duration_ms:
            continue

        region_duration = max(end_ms - start_ms, 1)
        ratio = min(max((clamped_timestamp - start_ms) / region_duration, 0.0), 1.0)
        char_span = max(char_end - char_start, 0)
        return min(char_start + int(round(char_span * ratio)), len(text_content))

    return len(text_content)


def _nearest_paragraph_boundary_distance(text_content: str, index: int) -> int | None:
    """Return the distance to the nearest paragraph break around a text index."""

    if not text_content or "\n\n" not in text_content:
        return None

    boundaries: list[int] = []
    search_from = 0
    while True:
        position = text_content.find("\n\n", search_from)
        if position < 0:
            break
        boundaries.append(position)
        search_from = position + 2

    if not boundaries:
        return None
    return min(abs(boundary - index) for boundary in boundaries)


def _classify_silence_context(
    text_content: str,
    chunk_boundaries: list[float],
    *,
    silence_start_ms: int,
    silence_end_ms: int,
    total_duration_ms: int,
) -> str:
    """Classify a silence region based on its approximate text location."""

    if silence_start_ms <= 500:
        return "chapter_start"
    if silence_end_ms >= max(total_duration_ms - 500, 0):
        return "chapter_end"

    midpoint = silence_start_ms + ((silence_end_ms - silence_start_ms) / 2)
    text_index = _timestamp_to_text_index(text_content, chunk_boundaries, midpoint, total_duration_ms)
    local_before = text_content[:text_index].rstrip()
    local_after = text_content[text_index:].lstrip()

    paragraph_distance = _nearest_paragraph_boundary_distance(text_content, text_index)
    if paragraph_distance is not None and paragraph_distance <= 20:
        return "paragraph_boundary"

    if local_before:
        trimmed_before = local_before.rstrip("\"')]}")
        if trimmed_before.endswith((".", "!", "?")):
            quote_transition = local_after.startswith(("\"", "'", "-", "\u2014"))
            if quote_transition:
                return "dialogue_transition"
            return "sentence_boundary"

    if local_after.startswith(("\"", "'", "-", "\u2014")):
        return "dialogue_transition"

    return "mid_sentence"


def _frequency_projection_energy(samples: np.ndarray, sample_rate: int, frequency_hz: float) -> float:
    """Return the magnitude of a target frequency using a direct sinusoid projection."""

    if samples.size == 0:
        return 0.0

    times = np.arange(samples.size, dtype=np.float32) / float(sample_rate)
    angle = 2 * np.pi * frequency_hz * times
    cosine = np.cos(angle).astype(np.float32)
    sine = np.sin(angle).astype(np.float32)
    real = float(np.dot(samples.astype(np.float32), cosine))
    imaginary = float(np.dot(samples.astype(np.float32), sine))
    return float(np.sqrt(real**2 + imaginary**2) / max(samples.size, 1))


def _analysis_windows(samples: np.ndarray, sample_rate: int, *, window_ms: int = 1500, max_windows: int = 4) -> list[np.ndarray]:
    """Sample representative windows from a chapter without analyzing the full file at once."""

    if samples.size == 0:
        return [samples]

    window_size = max(int(sample_rate * (window_ms / 1000.0)), 1)
    if samples.size <= window_size:
        return [samples]

    last_start = max(samples.size - window_size, 0)
    if max_windows <= 1 or last_start == 0:
        return [samples[:window_size]]

    starts = np.linspace(0, last_start, num=max_windows, dtype=int)
    return [samples[start:start + window_size] for start in starts]


def _detect_hum(samples: np.ndarray, sample_rate: int) -> tuple[bool, float | None, float]:
    """Detect narrowband hum at common mains frequencies and harmonics."""

    hum_frequencies = (50.0, 60.0, 100.0, 120.0, 150.0, 180.0)
    windows = _analysis_windows(samples, sample_rate)
    strongest_target: float | None = None
    strongest_ratio = 0.0

    for target in hum_frequencies:
        target_energies: list[float] = []
        surround_energies: list[float] = []
        for window in windows:
            target_energies.append(_frequency_projection_energy(window, sample_rate, target))
            for surround in (target - 10, target - 6, target + 6, target + 10):
                if surround > 0:
                    surround_energies.append(_frequency_projection_energy(window, sample_rate, surround))

        target_energy = float(np.mean(target_energies)) if target_energies else 0.0
        surround_energy = float(np.mean(surround_energies)) if surround_energies else 0.0
        ratio = target_energy / max(surround_energy, 1e-6)
        if ratio > strongest_ratio:
            strongest_ratio = ratio
            strongest_target = target

    return (strongest_ratio >= 5.0, strongest_target if strongest_ratio >= 5.0 else None, strongest_ratio)


def check_voice_consistency(audio_path: str | Path, chunk_boundaries: list[float]) -> QACheckResult:
    """Compare per-chunk pitch, spectral centroid, and energy against chapter medians."""

    analysis = _load_audio_analysis(audio_path)
    regions = _chunk_regions(analysis.audio, chunk_boundaries)
    if len(regions) <= 1:
        return QACheckResult(
            name="voice_consistency",
            status=QAAutomaticStatus.PASS.value,
            message="Single chunk chapter; no cross-chunk voice drift detected.",
            value=0,
            details={"chunk_count": len(regions)},
        )

    metrics: list[dict[str, float | None]] = []
    for start_ms, end_ms in regions:
        segment = analysis.audio[start_ms:end_ms]
        samples = _mono_samples(segment)
        metrics.append(
            {
                "pitch_hz": _estimate_pitch(segment, segment.frame_rate),
                "spectral_centroid_hz": _spectral_centroid(samples, segment.frame_rate),
                "rms": _linear_rms(samples),
            }
        )

    def _metric_median(metric_name: str) -> float | None:
        values = [float(metric[metric_name]) for metric in metrics if metric.get(metric_name) is not None]
        return _median(values)

    medians = {
        "pitch_hz": _metric_median("pitch_hz"),
        "spectral_centroid_hz": _metric_median("spectral_centroid_hz"),
        "rms": _metric_median("rms"),
    }

    deviating_chunks: list[dict[str, Any]] = []
    worst_deviation = 0.0
    overall_status = QAAutomaticStatus.PASS.value

    for chunk_index, metric in enumerate(metrics):
        chunk_deviations: dict[str, float] = {}
        chunk_status = QAAutomaticStatus.PASS.value

        for metric_name, warning_threshold in (
            ("pitch_hz", 0.15),
            ("spectral_centroid_hz", 0.20),
            ("rms", 0.15),
        ):
            value = metric.get(metric_name)
            median = medians.get(metric_name)
            if value is None or median is None or median <= 1e-6:
                continue

            deviation = abs(float(value) - median) / median
            if deviation > worst_deviation:
                worst_deviation = deviation
            if deviation > 0.25:
                chunk_status = QAAutomaticStatus.FAIL.value
            elif deviation > warning_threshold and chunk_status != QAAutomaticStatus.FAIL.value:
                chunk_status = QAAutomaticStatus.WARNING.value
            if deviation > warning_threshold:
                chunk_deviations[metric_name] = round(deviation, 3)

        if chunk_deviations:
            deviating_chunks.append(
                {
                    "chunk_index": chunk_index,
                    "status": chunk_status,
                    "deviations": chunk_deviations,
                }
            )
            if _status_from_severity(chunk_status) > _status_from_severity(overall_status):
                overall_status = chunk_status

    if not deviating_chunks:
        return QACheckResult(
            name="voice_consistency",
            status=QAAutomaticStatus.PASS.value,
            message="Voice characteristics remain stable across stitched chunks.",
            value=0,
            details={"chunk_metrics": metrics, "medians": medians, "deviating_chunks": []},
        )

    if overall_status == QAAutomaticStatus.FAIL.value:
        message = f"Voice drift exceeds fail thresholds in {len(deviating_chunks)} chunk(s)."
    else:
        message = f"Voice drift detected in {len(deviating_chunks)} chunk(s)."

    return QACheckResult(
        name="voice_consistency",
        status=overall_status,
        message=message,
        value=round(worst_deviation, 3),
        details={"chunk_metrics": metrics, "medians": medians, "deviating_chunks": deviating_chunks},
    )


def check_contextual_silence(
    audio_path: str | Path,
    text_content: str,
    chunk_boundaries: list[float],
) -> QACheckResult:
    """Validate detected silences against the surrounding narration context."""

    analysis = _load_audio_analysis(audio_path)
    silences = silence.detect_silence(
        analysis.audio,
        min_silence_len=200,
        silence_thresh=QA_THRESHOLDS["silence_threshold_dbfs"],
    )
    if not silences:
        return QACheckResult(
            name="contextual_silence",
            status=QAAutomaticStatus.PASS.value,
            message="No material silence gaps detected.",
            value=0,
            details={"silence_stats": {"count": 0, "min_ms": 0, "max_ms": 0, "avg_ms": 0}, "violations": []},
        )

    durations = [end - start for start, end in silences]
    violations: list[dict[str, Any]] = []
    overall_status = QAAutomaticStatus.PASS.value

    for start_ms, end_ms in silences:
        duration_ms = end_ms - start_ms
        context = _classify_silence_context(
            text_content,
            chunk_boundaries,
            silence_start_ms=start_ms,
            silence_end_ms=end_ms,
            total_duration_ms=len(analysis.audio),
        )
        rules = CONTEXT_SILENCE_RULES.get(context, CONTEXT_SILENCE_RULES["mid_sentence"])
        if duration_ms > 5000:
            status = QAAutomaticStatus.FAIL.value
        elif duration_ms < rules["min_ms"] or duration_ms > rules["max_ms"]:
            status = QAAutomaticStatus.WARNING.value
        else:
            status = QAAutomaticStatus.PASS.value

        if status != QAAutomaticStatus.PASS.value:
            violations.append(
                {
                    "timestamp_ms": start_ms,
                    "duration_ms": duration_ms,
                    "context": context,
                    "expected_range_ms": [rules["min_ms"], rules["max_ms"]],
                    "status": status,
                }
            )
            if _status_from_severity(status) > _status_from_severity(overall_status):
                overall_status = status

    silence_stats = {
        "count": len(durations),
        "min_ms": min(durations),
        "max_ms": max(durations),
        "avg_ms": round(sum(durations) / len(durations), 2),
    }
    if not violations:
        message = "Detected silences match their surrounding text context."
        value = 0
    else:
        message = f"{len(violations)} contextual silence issue(s) detected."
        value = round(max(violation["duration_ms"] for violation in violations) / 1000.0, 3)

    return QACheckResult(
        name="contextual_silence",
        status=overall_status,
        message=message,
        value=value,
        details={"silence_stats": silence_stats, "violations": violations},
    )


def check_stitch_quality(audio_path: str | Path, chunk_boundaries: list[float]) -> QACheckResult:
    """Combine click detection with tonal and energy discontinuity checks."""

    if len(chunk_boundaries) <= 1:
        return QACheckResult(
            name="stitch_quality",
            status=QAAutomaticStatus.PASS.value,
            message="Single chunk chapter; no stitch boundaries to evaluate.",
            value=0,
            details={
                "issues": [],
                "stitch_quality": {
                    "total_stitches": 0,
                    "clean": 0,
                    "warnings": 0,
                    "failures": 0,
                },
            },
        )

    analysis = _load_audio_analysis(audio_path)
    total_stitches = max(len(chunk_boundaries) - 1, 0)
    click_result = check_stitch_clicks(
        analysis.audio,
        chapter_duration_seconds=analysis.actual_duration,
        stitch_boundaries=chunk_boundaries[1:],
        total_stitches=total_stitches,
    )
    issues: list[dict[str, Any]] = []
    warning_count = 0
    failure_count = 0

    if click_result.status != QAAutomaticStatus.PASS.value:
        click_details = click_result.details or {}
        click_warning_regions = int(click_details.get("warning_regions", 0))
        click_failure_regions = int(click_details.get("hard_clicks", 0))
        if click_result.status == QAAutomaticStatus.FAIL.value:
            warning_count += click_warning_regions
            failure_count += click_failure_regions
        else:
            warning_count += click_warning_regions + click_failure_regions
        issues.append(
            {
                "type": "clicks",
                "status": click_result.status,
                "message": click_result.message,
                "details": click_details,
            }
        )

    for stitch_index, boundary_seconds in enumerate(chunk_boundaries[1:], start=1):
        boundary_ms = int(boundary_seconds * 1000)
        before_tonal = analysis.audio[max(boundary_ms - 50, 0):boundary_ms]
        after_tonal = analysis.audio[boundary_ms:min(boundary_ms + 50, len(analysis.audio))]
        before_energy = analysis.audio[max(boundary_ms - 100, 0):boundary_ms]
        after_energy = analysis.audio[boundary_ms:min(boundary_ms + 100, len(analysis.audio))]

        tonal_before = _spectral_centroid(_mono_samples(before_tonal), analysis.audio.frame_rate)
        tonal_after = _spectral_centroid(_mono_samples(after_tonal), analysis.audio.frame_rate)
        if tonal_before is not None and tonal_after is not None and tonal_before > 1e-6:
            tonal_shift = abs(tonal_after - tonal_before) / tonal_before
            if tonal_shift > 0.30:
                warning_count += 1
                issues.append(
                    {
                        "type": "tonal_discontinuity",
                        "stitch_index": stitch_index,
                        "boundary_ms": boundary_ms,
                        "shift": round(tonal_shift, 3),
                    }
                )

        rms_before = _linear_rms(_mono_samples(before_energy))
        rms_after = _linear_rms(_mono_samples(after_energy))
        rms_before_db = _dbfs_from_amplitude(rms_before)
        rms_after_db = _dbfs_from_amplitude(rms_after)
        jump_db = abs(rms_after_db - rms_before_db)
        if jump_db > 6.0:
            warning_count += 1
            issues.append(
                {
                    "type": "energy_jump",
                    "stitch_index": stitch_index,
                    "boundary_ms": boundary_ms,
                    "jump_db": round(jump_db, 3),
                }
            )

    if failure_count > 0:
        status = QAAutomaticStatus.FAIL.value
    elif warning_count > 0:
        status = QAAutomaticStatus.WARNING.value
    else:
        status = QAAutomaticStatus.PASS.value

    stitch_quality = {
        "total_stitches": total_stitches,
        "clean": max(total_stitches - warning_count - failure_count, 0),
        "warnings": warning_count,
        "failures": failure_count,
    }

    if status == QAAutomaticStatus.PASS.value:
        message = "No stitch quality issues detected."
        value = 0
    else:
        total_issues = warning_count + failure_count
        message = f"{total_issues} stitch issue(s) detected."
        value = float(total_issues)

    return QACheckResult(
        name="stitch_quality",
        status=status,
        message=message,
        value=value,
        details={"issues": issues, "stitch_quality": stitch_quality},
    )


def check_pacing_detailed(audio_path: str | Path, text_content: str) -> QACheckResult:
    """Measure pacing consistency in 10-second windows using active speech time."""

    analysis = _load_audio_analysis(audio_path)
    total_words = len((text_content or "").split())
    if total_words < 20 or len(analysis.audio) < 20_000:
        return QACheckResult(
            name="pacing_detailed",
            status=QAAutomaticStatus.PASS.value,
            message="Not enough chapter material to analyze pacing consistency reliably.",
            value=0,
            details={"pacing_stats": {}, "outlier_windows": []},
        )

    window_ms = 10_000
    windows: list[dict[str, Any]] = []
    total_duration_ms = max(len(analysis.audio), 1)
    for start_ms in range(0, len(analysis.audio), window_ms):
        window = analysis.audio[start_ms:start_ms + window_ms]
        if len(window) == 0:
            continue
        active_ms = _active_speech_ms(window)
        proportional_words = total_words * (len(window) / total_duration_ms)
        if active_ms <= 0:
            wpm = 0.0
        else:
            wpm = proportional_words / (active_ms / 60_000)
        windows.append({"start_ms": start_ms, "duration_ms": len(window), "active_ms": active_ms, "wpm": wpm})

    if not windows:
        return QACheckResult(
            name="pacing_detailed",
            status=QAAutomaticStatus.WARNING.value,
            message="Unable to estimate pacing because no speech windows were detected.",
            value=None,
            details={"pacing_stats": {}, "outlier_windows": []},
        )

    wpm_values = np.array([window["wpm"] for window in windows], dtype=np.float32)
    mean_wpm = float(np.mean(wpm_values))
    std_wpm = float(np.std(wpm_values))
    max_deviation = 0.0
    outlier_windows: list[dict[str, Any]] = []
    status = QAAutomaticStatus.PASS.value

    for window in windows:
        deviation = abs(window["wpm"] - mean_wpm) / max(mean_wpm, 1e-6)
        window["deviation"] = round(deviation, 3)
        if deviation > max_deviation:
            max_deviation = deviation
        if deviation > 0.40:
            outlier_windows.append({"start_ms": window["start_ms"], "wpm": round(window["wpm"], 2), "deviation": round(deviation, 3), "status": QAAutomaticStatus.FAIL.value})
            status = QAAutomaticStatus.FAIL.value
        elif deviation > 0.25:
            outlier_windows.append({"start_ms": window["start_ms"], "wpm": round(window["wpm"], 2), "deviation": round(deviation, 3), "status": QAAutomaticStatus.WARNING.value})
            if status != QAAutomaticStatus.FAIL.value:
                status = QAAutomaticStatus.WARNING.value

    std_ratio = std_wpm / max(mean_wpm, 1e-6)
    if std_ratio > 0.20 and status != QAAutomaticStatus.FAIL.value:
        status = QAAutomaticStatus.WARNING.value

    pacing_stats = {
        "mean_wpm": round(mean_wpm, 2),
        "std_wpm": round(std_wpm, 2),
        "min_wpm": round(float(np.min(wpm_values)), 2),
        "max_wpm": round(float(np.max(wpm_values)), 2),
    }

    if status == QAAutomaticStatus.PASS.value:
        message = "Pacing is consistent across 10-second chapter windows."
        value = 0
    elif status == QAAutomaticStatus.FAIL.value:
        message = f"Major pacing inconsistency detected in {len(outlier_windows)} window(s)."
        value = round(max_deviation, 3)
    else:
        message = f"Pacing drift detected in {len(outlier_windows)} window(s)."
        value = round(max(max_deviation, std_ratio), 3)

    return QACheckResult(
        name="pacing_detailed",
        status=status,
        message=message,
        value=value,
        details={"pacing_stats": pacing_stats, "outlier_windows": outlier_windows, "windows": windows},
    )


def check_spectral_quality(audio_path: str | Path) -> QACheckResult:
    """Detect hum, high-frequency ringing, and elevated silence noise floors."""

    analysis = _load_audio_analysis(audio_path)
    issues: list[dict[str, Any]] = []

    hum_detected, hum_frequency, hum_ratio = _detect_hum(analysis.normalized_samples, analysis.audio.frame_rate)
    if hum_detected:
        issues.append(
            {
                "type": "hum",
                "frequency_hz": hum_frequency,
                "concentration_ratio": round(hum_ratio, 3),
            }
        )

    frequencies, spectrum = _average_frame_spectrum(
        analysis.normalized_samples,
        analysis.audio.frame_rate,
        frame_size=2048,
        hop_size=1024,
    )
    total_energy = float(np.sum(spectrum))
    high_freq_ratio = 0.0
    if total_energy > 1e-8 and spectrum.size > 0:
        high_freq_ratio = float(np.sum(spectrum[frequencies >= 8000]) / total_energy)
        if high_freq_ratio > 0.15:
            issues.append(
                {
                    "type": "high_frequency_artifacts",
                    "energy_ratio": round(high_freq_ratio, 3),
                }
            )

    silence_regions = silence.detect_silence(
        analysis.audio,
        min_silence_len=200,
        silence_thresh=QA_THRESHOLDS["silence_threshold_dbfs"],
    )
    noise_floors: list[float] = []
    for start_ms, end_ms in silence_regions:
        segment_samples = _mono_samples(analysis.audio[start_ms:end_ms])
        noise_floor = _dbfs_from_amplitude(_linear_rms(segment_samples))
        noise_floors.append(noise_floor)
    worst_noise_floor = max(noise_floors) if noise_floors else -100.0
    if worst_noise_floor > -45.0:
        issues.append(
            {
                "type": "noise_floor",
                "dbfs": round(worst_noise_floor, 3),
            }
        )

    if not issues:
        return QACheckResult(
            name="spectral_quality",
            status=QAAutomaticStatus.PASS.value,
            message="No hum, ringing, or elevated silence noise floor detected.",
            value=0,
            details={"hum_frequency_hz": None, "high_freq_energy_ratio": round(high_freq_ratio, 3), "max_noise_floor_dbfs": round(worst_noise_floor, 3), "issues": []},
        )

    return QACheckResult(
        name="spectral_quality",
        status=QAAutomaticStatus.WARNING.value,
        message=f"{len(issues)} spectral quality issue(s) detected.",
        value=float(len(issues)),
        details={"hum_frequency_hz": hum_frequency, "high_freq_energy_ratio": round(high_freq_ratio, 3), "max_noise_floor_dbfs": round(worst_noise_floor, 3), "issues": issues},
    )


def check_stitch_clicks(
    audio: AudioSegment,
    crossfade_ms: int = 30,
    *,
    chapter_duration_seconds: float | None = None,
    stitch_boundaries: list[float] | None = None,
    total_stitches: int | None = None,
) -> QACheckResult:
    """Detect likely click or pop artifacts introduced near stitch boundaries."""

    if len(audio) < 250:
        return QACheckResult(
            name="stitch_clicks",
            status=QAAutomaticStatus.PASS.value,
            message="Audio too short to analyze stitch boundaries reliably.",
            value=0,
        )

    click_events = _detect_stitch_click_events(audio)
    if not click_events:
        return QACheckResult(
            name="stitch_clicks",
            status=QAAutomaticStatus.PASS.value,
            message="No likely stitch clicks detected.",
            value=0,
        )

    boundary_details = _match_clicks_to_boundaries(
        click_events,
        stitch_boundaries=stitch_boundaries or [],
        match_window_ms=max(crossfade_ms, 15),
        chapter_duration_seconds=chapter_duration_seconds,
    )
    classified_regions = boundary_details or _classify_click_regions(
        click_events,
        chapter_duration_seconds=chapter_duration_seconds,
    )

    hard_clicks = sum(1 for region in classified_regions if region["category"] == "hard")
    micro_clicks = sum(1 for region in classified_regions if region["category"] == "micro")
    soft_warnings = sum(1 for region in classified_regions if region["category"] == "soft")
    warning_regions = micro_clicks + soft_warnings
    affected_regions = hard_clicks + warning_regions
    if affected_regions == 0:
        return QACheckResult(
            name="stitch_clicks",
            status=QAAutomaticStatus.PASS.value,
            message="No likely stitch clicks detected.",
            value=0,
        )

    effective_total_stitches = total_stitches
    click_ratio = None
    if effective_total_stitches is not None and effective_total_stitches > 0:
        click_ratio = hard_clicks / effective_total_stitches

    if click_ratio is not None and click_ratio > 0.25:
        status = QAAutomaticStatus.FAIL.value
        message = (
            f"Likely stitch clicks detected at {hard_clicks} of {effective_total_stitches} "
            f"stitch point(s) ({click_ratio:.0%})."
        )
    elif click_ratio is None and hard_clicks >= 3:
        status = QAAutomaticStatus.FAIL.value
        message = f"Repeated click artifacts detected at {hard_clicks} boundary region(s)."
    else:
        status = QAAutomaticStatus.WARNING.value
        message = f"Possible click artifacts detected at {affected_regions} boundary region(s)."

    threshold_db = 15.0 if chapter_duration_seconds is not None and chapter_duration_seconds < 120.0 else 12.0
    return QACheckResult(
        name="stitch_clicks",
        status=status,
        message=message,
        value=float(affected_regions),
        details={
            "threshold_db": threshold_db,
            "hard_clicks": hard_clicks,
            "warning_regions": warning_regions,
            "micro_clicks": micro_clicks,
            "click_ratio": round(click_ratio, 4) if click_ratio is not None else None,
            "regions": classified_regions,
        },
    )


def _detect_stitch_click_events(audio: AudioSegment) -> list[dict[str, float]]:
    """Find short transient events by comparing sub-millisecond peaks against local context."""

    samples = _mono_samples(audio)
    if samples.size == 0:
        return []

    window_ms = 0.5
    surrounding_ms = 100.0
    window_size = max(int(audio.frame_rate * (window_ms / 1000.0)), 1)
    surrounding_windows = max(int(round(surrounding_ms / window_ms)), 1)
    window_count = int(np.ceil(samples.size / window_size))
    padded_length = (window_count * window_size) - samples.size
    padded = np.pad(np.abs(samples), (0, padded_length))
    peaks = padded.reshape(window_count, window_size).max(axis=1)
    peaks_dbfs = np.array([_dbfs_from_amplitude(float(peak)) for peak in peaks], dtype=np.float32)

    if peaks_dbfs.size <= (surrounding_windows * 2):
        return []

    kernel = np.ones((surrounding_windows * 2) + 1, dtype=np.float32)
    surrounding_sums = np.convolve(peaks_dbfs, kernel, mode="same") - peaks_dbfs
    surrounding_counts = np.convolve(np.ones_like(peaks_dbfs), kernel, mode="same") - 1.0
    surrounding_average = surrounding_sums / np.maximum(surrounding_counts, 1.0)

    valid = np.zeros(peaks_dbfs.shape[0], dtype=bool)
    valid[surrounding_windows:peaks_dbfs.shape[0] - surrounding_windows] = True
    candidate_indices = np.flatnonzero(valid & ((peaks_dbfs - surrounding_average) >= 12.0))
    if candidate_indices.size == 0:
        return []

    events: list[dict[str, float]] = []
    start_index = int(candidate_indices[0])
    previous_index = int(candidate_indices[0])

    for raw_index in candidate_indices[1:]:
        index = int(raw_index)
        if index - previous_index > 1:
            events.append(
                _build_click_event(
                    start_index,
                    previous_index,
                    peaks_dbfs=peaks_dbfs,
                    surrounding_average=surrounding_average,
                    window_ms=window_ms,
                )
            )
            start_index = index
        previous_index = index

    events.append(
        _build_click_event(
            start_index,
            previous_index,
            peaks_dbfs=peaks_dbfs,
            surrounding_average=surrounding_average,
            window_ms=window_ms,
        )
    )
    return events


def _build_click_event(
    start_index: int,
    end_index: int,
    *,
    peaks_dbfs: np.ndarray,
    surrounding_average: np.ndarray,
    window_ms: float,
) -> dict[str, float]:
    """Summarize one contiguous transient event."""

    event_slice = slice(start_index, end_index + 1)
    peak_diff = float(np.max(peaks_dbfs[event_slice] - surrounding_average[event_slice]))
    start_ms = start_index * window_ms
    end_ms = (end_index + 1) * window_ms
    center_ms = (start_ms + end_ms) / 2.0
    return {
        "start_ms": round(start_ms, 3),
        "end_ms": round(end_ms, 3),
        "center_ms": round(center_ms, 3),
        "duration_ms": round((end_index - start_index + 1) * window_ms, 3),
        "peak_diff_db": round(peak_diff, 3),
    }


def _match_clicks_to_boundaries(
    click_events: list[dict[str, float]],
    *,
    stitch_boundaries: list[float],
    match_window_ms: int,
    chapter_duration_seconds: float | None,
) -> list[dict[str, Any]]:
    """Collapse detected click events down to one severity per stitch boundary."""

    if not stitch_boundaries:
        return []

    matched_regions: list[dict[str, Any]] = []
    severity_order = {"micro": 0, "soft": 1, "hard": 2}

    for stitch_index, boundary_seconds in enumerate(stitch_boundaries, start=1):
        boundary_ms = boundary_seconds * 1000.0
        boundary_matches = [
            event
            for event in click_events
            if abs(float(event["center_ms"]) - boundary_ms) <= float(match_window_ms)
        ]
        if not boundary_matches:
            continue

        classified = _classify_click_regions(
            boundary_matches,
            chapter_duration_seconds=chapter_duration_seconds,
        )
        if not classified:
            continue
        worst_region = max(classified, key=lambda region: severity_order[str(region["category"])])
        matched_regions.append(
            {
                "stitch_index": stitch_index,
                "boundary_ms": round(boundary_ms, 3),
                **worst_region,
            }
        )

    return matched_regions


def _classify_click_regions(
    click_events: list[dict[str, float]],
    *,
    chapter_duration_seconds: float | None,
) -> list[dict[str, Any]]:
    """Assign click severity using short-chapter and micro-click rules."""

    hard_threshold = 15.0 if chapter_duration_seconds is not None and chapter_duration_seconds < 120.0 else 12.0
    regions: list[dict[str, Any]] = []

    for event in click_events:
        peak_diff = float(event["peak_diff_db"])
        duration_ms = float(event["duration_ms"])
        if 12.0 <= peak_diff < 15.0 and duration_ms <= 1.0:
            category = "micro"
        elif peak_diff >= hard_threshold:
            category = "hard"
        elif peak_diff >= 12.0:
            category = "soft"
        else:
            continue

        regions.append({**event, "category": category})

    return regions


def check_pacing_consistency(audio: AudioSegment, text: str) -> QACheckResult:
    """Check for large within-chapter pacing swings using speech-density windows."""

    total_words = len((text or "").split())
    if total_words < 20 or len(audio) < 20_000:
        return QACheckResult(
            name="pacing_consistency",
            status=QAAutomaticStatus.PASS.value,
            message="Not enough chapter material to analyze pacing consistency reliably.",
            value=0,
        )

    total_active_ms = _active_speech_ms(audio)
    if total_active_ms <= 0:
        return QACheckResult(
            name="pacing_consistency",
            status=QAAutomaticStatus.WARNING.value,
            message="Unable to estimate pacing because no active speech was detected.",
            value=None,
        )

    chapter_average_wpm = total_words / (total_active_ms / 60_000)
    window_ms = 10_000
    inconsistent_windows: list[float] = []

    for start in range(0, len(audio), window_ms):
        window = audio[start:start + window_ms]
        if len(window) == 0:
            continue

        active_ms = _active_speech_ms(window)
        if active_ms <= 0:
            inconsistent_windows.append(1.0)
            continue

        proportional_words = total_words * (len(window) / max(len(audio), 1))
        window_wpm = proportional_words / (active_ms / 60_000)
        deviation = abs(window_wpm - chapter_average_wpm) / max(chapter_average_wpm, 1e-6)
        if deviation > 0.4:
            inconsistent_windows.append(deviation)

    if not inconsistent_windows:
        return QACheckResult(
            name="pacing_consistency",
            status=QAAutomaticStatus.PASS.value,
            message="Pacing is consistent across the chapter.",
            value=0,
        )

    max_deviation = max(inconsistent_windows)
    if len(inconsistent_windows) <= 2:
        return QACheckResult(
            name="pacing_consistency",
            status=QAAutomaticStatus.WARNING.value,
            message=(
                f"Pacing drift detected in {len(inconsistent_windows)} window(s); "
                f"max deviation {max_deviation * 100:.0f}% from chapter average."
            ),
            value=round(max_deviation, 3),
        )

    return QACheckResult(
        name="pacing_consistency",
        status=QAAutomaticStatus.FAIL.value,
        message=(
            f"Major pacing inconsistency detected in {len(inconsistent_windows)} window(s); "
            f"max deviation {max_deviation * 100:.0f}% from chapter average."
        ),
        value=round(max_deviation, 3),
    )


def check_plosive_artifacts(audio_path: str | Path) -> QACheckResult:
    """Detect residual low-frequency plosive pops after mastering or generation."""

    analysis = _load_audio_analysis(audio_path)
    events = _detect_plosive_events(analysis.audio)
    if not events:
        return QACheckResult(
            name="plosive_artifacts",
            status=QAAutomaticStatus.PASS.value,
            message="No problematic plosive bursts detected.",
            value=0,
            details={"plosives_per_minute": 0.0, "events": []},
        )

    minutes = max(analysis.actual_duration / 60.0, 1 / 60.0)
    per_minute = len(events) / minutes
    if per_minute > 8.0:
        status = QAAutomaticStatus.FAIL.value
        message = f"Heavy plosive artifact rate detected ({per_minute:.1f}/min)."
    elif per_minute >= 3.0:
        status = QAAutomaticStatus.WARNING.value
        message = f"Plosive artifacts detected at {per_minute:.1f}/min."
    else:
        status = QAAutomaticStatus.PASS.value
        message = "Plosive artifact rate is within narration tolerance."

    return QACheckResult(
        name="plosive_artifacts",
        status=status,
        message=message,
        value=round(per_minute, 3),
        details={"plosives_per_minute": round(per_minute, 3), "events": events},
    )


def check_breath_levels(audio_path: str | Path) -> QACheckResult:
    """Detect breath sounds and flag inhalations that are too exposed for publishing."""

    analysis = _load_audio_analysis(audio_path)
    events = _detect_breath_events(analysis.audio)
    if not events:
        return QACheckResult(
            name="breath_levels",
            status=QAAutomaticStatus.PASS.value,
            message="No breath events exceeded the QA detector thresholds.",
            value=0,
            details={"breaths_per_minute": 0.0, "max_peak_dbfs": None, "events": []},
        )

    minutes = max(analysis.actual_duration / 60.0, 1 / 60.0)
    per_minute = len(events) / minutes
    max_peak = max(float(event["peak_dbfs"]) for event in events)

    if max_peak > -20.0:
        status = QAAutomaticStatus.FAIL.value
        message = f"A breath peaks at {max_peak:.1f} dBFS and is too loud for release."
    elif max_peak > -25.0:
        status = QAAutomaticStatus.WARNING.value
        message = f"One or more breaths peak at {max_peak:.1f} dBFS and should be softened."
    elif analysis.actual_duration >= 60.0 and not (4.0 <= per_minute <= 12.0):
        status = QAAutomaticStatus.WARNING.value
        message = f"Breath cadence is unusual for narration ({per_minute:.1f}/min)."
    else:
        status = QAAutomaticStatus.PASS.value
        message = "Breath levels are within the publishing target range."

    return QACheckResult(
        name="breath_levels",
        status=status,
        message=message,
        value=round(max_peak, 3),
        details={
            "breaths_per_minute": round(per_minute, 3),
            "max_peak_dbfs": round(max_peak, 3),
            "events": events,
        },
    )


def check_room_tone_padding(audio_path: str | Path) -> QACheckResult:
    """Fail chapters that begin or end with exposed speech instead of padded ambience."""

    analysis = _load_audio_analysis(audio_path)
    head = analysis.audio[:500]
    tail = analysis.audio[-1000:] if len(analysis.audio) >= 1000 else analysis.audio
    head_db = float(head.dBFS) if len(head) > 0 and head.dBFS != float("-inf") else -100.0
    tail_db = float(tail.dBFS) if len(tail) > 0 and tail.dBFS != float("-inf") else -100.0

    if head_db > -50.0 or tail_db > -50.0:
        return QACheckResult(
            name="room_tone_padding",
            status=QAAutomaticStatus.FAIL.value,
            message="Chapter starts or ends with exposed speech instead of padded room tone.",
            value=max(round(head_db, 3), round(tail_db, 3)),
            details={"head_dbfs": round(head_db, 3), "tail_dbfs": round(tail_db, 3)},
        )

    return QACheckResult(
        name="room_tone_padding",
        status=QAAutomaticStatus.PASS.value,
        message="Chapter edges stay below the room-tone speech threshold.",
        value=round(max(head_db, tail_db), 3),
        details={"head_dbfs": round(head_db, 3), "tail_dbfs": round(tail_db, 3)},
    )


def check_lufs_compliance(
    audio_path: str | Path,
    *,
    timeout_seconds: int = QA_LUFS_TIMEOUT_SECONDS,
) -> QACheckResult:
    """Measure integrated LUFS and compare it against audiobook loudness targets."""

    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path is None:
        return QACheckResult(
            name="lufs_compliance",
            status=QAAutomaticStatus.WARNING.value,
            message="ffmpeg is unavailable, so LUFS compliance could not be measured.",
            value=None,
        )

    command = [
        ffmpeg_path,
        "-hide_banner",
        "-i",
        str(audio_path),
        "-af",
        "loudnorm=I=-19:TP=-1.5:LRA=11:print_format=json",
        "-f",
        "null",
        "-",
    ]
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return QACheckResult(
            name="lufs_compliance",
            status=QAAutomaticStatus.WARNING.value,
            message=f"LUFS compliance timed out after {timeout_seconds}s.",
            value=None,
        )
    except subprocess.CalledProcessError as exc:
        return QACheckResult(
            name="lufs_compliance",
            status=QAAutomaticStatus.WARNING.value,
            message=f"Unable to measure LUFS compliance: {exc.stderr.strip() or exc.stdout.strip() or exc}",
            value=None,
        )

    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    match = re.search(r"(\{\s*\"input_i\".*?\})", output, re.DOTALL)
    if match is None:
        return QACheckResult(
            name="lufs_compliance",
            status=QAAutomaticStatus.WARNING.value,
            message="ffmpeg did not return loudnorm JSON output.",
            value=None,
        )

    try:
        metrics = json.loads(match.group(1))
        lufs = float(metrics["input_i"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return QACheckResult(
            name="lufs_compliance",
            status=QAAutomaticStatus.WARNING.value,
            message=f"Unable to parse LUFS measurement: {exc}",
            value=None,
        )

    if -23.0 <= lufs <= -18.0:
        status = QAAutomaticStatus.PASS.value
        message = f"Integrated loudness {lufs:.1f} LUFS is within ACX range."
    elif (-25.0 <= lufs < -23.0) or (-18.0 < lufs <= -16.0):
        status = QAAutomaticStatus.WARNING.value
        message = f"Integrated loudness {lufs:.1f} LUFS is near but outside ACX range."
    else:
        status = QAAutomaticStatus.FAIL.value
        message = f"Integrated loudness {lufs:.1f} LUFS is outside ACX range."

    return QACheckResult(
        name="lufs_compliance",
        status=status,
        message=message,
        value=round(lufs, 3),
    )


def _analysis_error_results(message: str) -> list[QACheckResult]:
    """Return a full set of failed analysis checks when audio cannot be decoded."""

    return [
        QACheckResult(name="duration_check", status=QAAutomaticStatus.FAIL.value, message=message, value=None),
        QACheckResult(name="clipping_detection", status=QAAutomaticStatus.FAIL.value, message=message, value=None),
        QACheckResult(name="contextual_silence", status=QAAutomaticStatus.FAIL.value, message=message, value=None),
        QACheckResult(name="volume_consistency", status=QAAutomaticStatus.FAIL.value, message=message, value=None),
        QACheckResult(name="voice_consistency", status=QAAutomaticStatus.FAIL.value, message=message, value=None),
        QACheckResult(name="stitch_quality", status=QAAutomaticStatus.FAIL.value, message=message, value=None),
        QACheckResult(name="pacing_detailed", status=QAAutomaticStatus.FAIL.value, message=message, value=None),
        QACheckResult(name="spectral_quality", status=QAAutomaticStatus.FAIL.value, message=message, value=None),
        QACheckResult(name="plosive_artifacts", status=QAAutomaticStatus.FAIL.value, message=message, value=None),
        QACheckResult(name="breath_levels", status=QAAutomaticStatus.FAIL.value, message=message, value=None),
        QACheckResult(name="room_tone_padding", status=QAAutomaticStatus.FAIL.value, message=message, value=None),
        QACheckResult(name="lufs_compliance", status=QAAutomaticStatus.FAIL.value, message=message, value=None),
    ]


def _overall_status(checks: list[QACheckResult]) -> str:
    """Return the worst status across all QA checks."""

    worst = max(checks, key=lambda check: STATUS_SEVERITY.get(check.status, 0))
    return worst.status


def _run_qa_checks_sync(
    *,
    book_id: int,
    chapter_n: int,
    audio_path: Path | None,
    word_count: int | None,
    text_content: str,
    chapter_title: str,
    chunk_boundaries: str | None,
) -> QAResult:
    """Synchronously execute the full QA sequence for one chapter."""

    file_result = _file_exists_result(audio_path)
    checks = [file_result]
    chapter_duration = 0.0

    if file_result.status == QAAutomaticStatus.FAIL.value or audio_path is None:
        checks.extend(_analysis_error_results("Audio file could not be analyzed because it is missing or empty."))
        overall_status = _overall_status(checks)
        qa_result = QAResult(
            chapter_n=chapter_n,
            book_id=book_id,
            timestamp=utc_now(),
            checks=checks,
            overall_status=overall_status,
            chapter_report=_synthesize_chapter_report(
                QAResult(
                    chapter_n=chapter_n,
                    book_id=book_id,
                    timestamp=utc_now(),
                    checks=checks,
                    overall_status=overall_status,
                ),
                chapter_number=chapter_n,
                chapter_title=chapter_title,
                duration_seconds=chapter_duration,
            ),
        )
        return qa_result

    try:
        analysis = _load_audio_analysis(audio_path)
        chapter_duration = analysis.actual_duration
        parsed_chunk_boundaries = _parse_chunk_boundaries(chunk_boundaries)
        checks.extend(
            [
                check_duration(audio_path, word_count, actual_duration=analysis.actual_duration),
                check_clipping(audio_path, peak_amplitude=analysis.peak_amplitude),
                check_contextual_silence(audio_path, text_content, parsed_chunk_boundaries),
                check_volume_consistency(audio_path),
                check_voice_consistency(audio_path, parsed_chunk_boundaries),
                check_stitch_quality(audio_path, parsed_chunk_boundaries),
                check_pacing_detailed(audio_path, text_content),
                check_spectral_quality(audio_path),
                check_plosive_artifacts(audio_path),
                check_breath_levels(audio_path),
                check_room_tone_padding(audio_path),
                check_lufs_compliance(audio_path),
            ]
        )
    except Exception as exc:
        logger.warning("Unable to decode audio for QA: %s", exc)
        checks.extend(_analysis_error_results(f"Unable to analyze audio: {exc}"))

    overall_status = _overall_status(checks)
    for check in checks:
        logger.info(
            "QA %s book=%s chapter=%s status=%s message=%s",
            check.name,
            book_id,
            chapter_n,
            check.status,
            check.message,
        )

    qa_result = QAResult(
        chapter_n=chapter_n,
        book_id=book_id,
        timestamp=utc_now(),
        checks=checks,
        overall_status=overall_status,
        chapter_report=_synthesize_chapter_report(
            QAResult(
                chapter_n=chapter_n,
                book_id=book_id,
                timestamp=utc_now(),
                checks=checks,
                overall_status=overall_status,
            ),
            chapter_number=chapter_n,
            chapter_title=chapter_title,
            duration_seconds=chapter_duration,
        ),
    )
    _write_chapter_report_sidecar(audio_path, qa_result)
    return qa_result


def _run_fast_qa_checks_sync(
    *,
    book_id: int,
    chapter_n: int,
    audio_path: Path | None,
    word_count: int | None,
    chapter_title: str,
    notes: str = "",
) -> QAResult:
    """Run the bounded fast QA fallback for long or timed-out chapters."""

    file_result = _file_exists_result(audio_path)
    checks = [file_result]
    chapter_duration = 0.0

    if file_result.status == QAAutomaticStatus.FAIL.value or audio_path is None:
        checks.extend(_analysis_error_results("Audio file could not be analyzed because it is missing or empty."))
    else:
        try:
            chapter_duration = _wav_duration_seconds(audio_path)
            peak_amplitude = _stream_peak_amplitude(audio_path)
            checks.extend(
                [
                    check_duration(audio_path, word_count, actual_duration=chapter_duration),
                    check_clipping(audio_path, peak_amplitude=peak_amplitude),
                    check_lufs_compliance(audio_path),
                ]
            )
        except Exception as exc:
            logger.warning("Unable to run fast QA for chapter %s in book %s: %s", chapter_n, book_id, exc)
            checks.extend(
                [
                    QACheckResult(name="duration_check", status=QAAutomaticStatus.FAIL.value, message=str(exc), value=None),
                    QACheckResult(name="clipping_detection", status=QAAutomaticStatus.FAIL.value, message=str(exc), value=None),
                    QACheckResult(name="lufs_compliance", status=QAAutomaticStatus.WARNING.value, message=str(exc), value=None),
                ]
            )

    overall_status = _overall_status(checks)
    qa_result = QAResult(
        chapter_n=chapter_n,
        book_id=book_id,
        timestamp=utc_now(),
        checks=checks,
        overall_status=overall_status,
        notes=notes,
        chapter_report=_synthesize_chapter_report(
            QAResult(
                chapter_n=chapter_n,
                book_id=book_id,
                timestamp=utc_now(),
                checks=checks,
                overall_status=overall_status,
                notes=notes,
            ),
            chapter_number=chapter_n,
            chapter_title=chapter_title,
            duration_seconds=chapter_duration,
        ),
    )
    _write_chapter_report_sidecar(audio_path, qa_result)
    return qa_result


async def _run_qa_checks_with_fallback(
    *,
    book_id: int,
    chapter_n: int,
    audio_path: Path | None,
    word_count: int | None,
    text_content: str,
    chapter_title: str,
    chunk_boundaries: str | None,
) -> QAResult:
    """Run full QA when feasible and fall back to fast QA for long-running chapters."""

    if audio_path is not None and audio_path.exists():
        try:
            duration_seconds = _wav_duration_seconds(audio_path)
        except Exception:
            duration_seconds = 0.0
        if duration_seconds >= QA_FAST_PATH_DURATION_SECONDS:
            logger.info(
                "QA fast-path for book=%s chapter=%s because duration %.1fs exceeds %.1fs",
                book_id,
                chapter_n,
                duration_seconds,
                QA_FAST_PATH_DURATION_SECONDS,
            )
            return await asyncio.to_thread(
                _run_fast_qa_checks_sync,
                book_id=book_id,
                chapter_n=chapter_n,
                audio_path=audio_path,
                word_count=word_count,
                chapter_title=chapter_title,
                notes=f"Fast QA fallback used for large chapter ({duration_seconds:.1f}s).",
            )

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                _run_qa_checks_sync,
                book_id=book_id,
                chapter_n=chapter_n,
                audio_path=audio_path,
                word_count=word_count,
                text_content=text_content,
                chapter_title=chapter_title,
                chunk_boundaries=chunk_boundaries,
            ),
            timeout=QA_CHAPTER_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "QA timed out for book=%s chapter=%s after %ss; using fast fallback",
            book_id,
            chapter_n,
            QA_CHAPTER_TIMEOUT_SECONDS,
        )
        return await asyncio.to_thread(
            _run_fast_qa_checks_sync,
            book_id=book_id,
            chapter_n=chapter_n,
            audio_path=audio_path,
            word_count=word_count,
            chapter_title=chapter_title,
            notes=f"Fast QA fallback used after {QA_CHAPTER_TIMEOUT_SECONDS}s timeout.",
        )


async def run_qa_checks(
    book_id: int,
    chapter_n: int,
    db_session: Session | None = None,
) -> QAResult:
    """
    Run all automated QA checks for a generated chapter.

    The optional session exists so generation jobs can reuse their active DB
    transaction while API callers can fall back to an internal session.
    """

    owns_session = db_session is None
    session = db_session or SessionLocal()
    try:
        chapter = (
            session.query(Chapter)
            .filter(Chapter.book_id == book_id, Chapter.number == chapter_n)
            .first()
        )
        if chapter is None:
            raise ValueError(f"Chapter {chapter_n} not found in book {book_id}")

        audio_path = _resolve_audio_path(chapter.audio_path)
        return await _run_qa_checks_with_fallback(
            book_id=book_id,
            chapter_n=chapter_n,
            audio_path=audio_path,
            word_count=chapter.word_count,
            text_content=chapter.text_content or "",
            chapter_title=chapter.title or f"Chapter {chapter_n}",
            chunk_boundaries=chapter.chunk_boundaries,
        )
    finally:
        if owns_session:
            session.close()


async def run_qa_checks_for_chapter(chapter: Chapter) -> QAResult:
    """Run QA checks using an already-loaded chapter ORM instance."""

    return await _run_qa_checks_with_fallback(
        book_id=chapter.book_id,
        chapter_n=chapter.number,
        audio_path=_resolve_audio_path(chapter.audio_path),
        word_count=chapter.word_count,
        text_content=chapter.text_content or "",
        chapter_title=chapter.title or f"Chapter {chapter.number}",
        chunk_boundaries=chapter.chunk_boundaries,
    )


def build_qa_record_response(record: ChapterQARecord, chapter: Chapter | None = None) -> dict[str, Any]:
    """Return a JSON-serializable QA payload for API responses."""

    qa_result = QAResult.model_validate(json.loads(record.qa_details))
    chapter_report = qa_result.chapter_report or _synthesize_chapter_report(
        qa_result,
        chapter_number=record.chapter_n,
        chapter_title=(chapter.title if chapter is not None and chapter.title else f"Chapter {record.chapter_n}"),
        duration_seconds=float(chapter.duration_seconds or 0.0) if chapter is not None and chapter.duration_seconds is not None else 0.0,
    )
    return {
        "chapter_n": record.chapter_n,
        "book_id": record.book_id,
        "overall_status": record.overall_status.value,
        "automatic_checks": [check.model_dump(mode="json") for check in qa_result.checks],
        "checked_at": record.checked_at,
        "manual_status": record.manual_status.value if record.manual_status is not None else None,
        "manual_notes": record.manual_notes,
        "manual_reviewed_by": record.manual_reviewed_by,
        "manual_reviewed_at": record.manual_reviewed_at,
        "chapter_report": chapter_report,
        "qa_grade": chapter_report.get("overall_grade"),
        "ready_for_export": chapter_report.get("ready_for_export"),
    }


def get_or_create_qa_record(db_session: Session, chapter: Chapter) -> ChapterQARecord:
    """Load or create the persisted QA record row for a chapter."""

    record = (
        db_session.query(ChapterQARecord)
        .filter(ChapterQARecord.book_id == chapter.book_id, ChapterQARecord.chapter_n == chapter.number)
        .first()
    )
    if record is not None:
        return record

    record = ChapterQARecord(
        book_id=chapter.book_id,
        chapter_n=chapter.number,
        overall_status=QAAutomaticStatus.FAIL,
        qa_details=QAResult(
            chapter_n=chapter.number,
            book_id=chapter.book_id,
            timestamp=utc_now(),
            checks=[],
            overall_status=QAAutomaticStatus.FAIL.value,
        ).model_dump_json(),
        checked_at=utc_now(),
    )
    db_session.add(record)
    db_session.flush()
    return record


def _chapter_summary_status(
    overall_status: QAAutomaticStatus,
    manual_status: QAManualStatus | None,
) -> QAStatus:
    """Map detailed QA data back onto the chapter summary status column."""

    if manual_status == QAManualStatus.APPROVED:
        return QAStatus.APPROVED
    if manual_status == QAManualStatus.FLAGGED:
        return QAStatus.NEEDS_REVIEW
    if overall_status == QAAutomaticStatus.PASS:
        return QAStatus.APPROVED
    return QAStatus.NEEDS_REVIEW


def persist_qa_result(db_session: Session, chapter: Chapter, qa_result: QAResult) -> ChapterQARecord:
    """Upsert the QA result row and mirror summary data onto the chapter."""

    record = get_or_create_qa_record(db_session, chapter)
    record.overall_status = QAAutomaticStatus(qa_result.overall_status)
    record.qa_details = qa_result.model_dump_json()
    record.checked_at = qa_result.timestamp
    chapter.qa_status = _chapter_summary_status(record.overall_status, record.manual_status)
    chapter.qa_notes = record.manual_notes
    db_session.flush()
    return record


def apply_manual_review(
    db_session: Session,
    chapter: Chapter,
    manual_status: QAManualStatus,
    reviewed_by: str,
    notes: str | None = None,
) -> ChapterQARecord:
    """Persist a manual QA decision for an analyzed chapter."""

    record = get_or_create_qa_record(db_session, chapter)
    record.manual_status = manual_status
    record.manual_notes = notes.strip() if notes else None
    record.manual_reviewed_by = reviewed_by.strip()
    record.manual_reviewed_at = utc_now()
    chapter.qa_status = _chapter_summary_status(record.overall_status, record.manual_status)
    chapter.qa_notes = record.manual_notes
    db_session.flush()
    return record
