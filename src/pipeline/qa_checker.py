"""Automated audio QA checks and persistence helpers."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel
from pydub import AudioSegment, silence
from sqlalchemy.orm import Session

from src.config import settings
from src.database import Chapter, ChapterQARecord, QAAutomaticStatus, QAManualStatus, QAStatus, SessionLocal, utc_now

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
    "silence_gaps",
    "volume_consistency",
)


class QACheckResult(BaseModel):
    """One automatic QA check outcome."""

    name: str
    status: str
    message: str
    value: float | None = None


class QAResult(BaseModel):
    """Aggregate QA outcome for a generated chapter."""

    chapter_n: int
    book_id: int
    timestamp: datetime
    checks: list[QACheckResult]
    overall_status: str
    notes: str = ""

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
    actual_duration: float
    peak_amplitude: float
    chunk_rms_dbfs: list[float]
    mid_chapter_silences_ms: list[int]

    model_config = {"arbitrary_types_allowed": True}


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


def _load_audio_analysis(audio_path: str | Path) -> _AudioAnalysis:
    """Decode WAV audio and pre-compute reusable QA metrics."""

    audio = AudioSegment.from_wav(audio_path).set_channels(1)
    normalized_samples = _mono_samples(audio)

    peak_amplitude = 0.0
    if normalized_samples.size > 0:
        peak_amplitude = float(np.max(np.abs(normalized_samples)))

    return _AudioAnalysis(
        audio=audio,
        actual_duration=len(audio) / 1000.0,
        peak_amplitude=peak_amplitude,
        chunk_rms_dbfs=_chunk_rms_dbfs(
            normalized_samples,
            audio.frame_rate,
            int(QA_THRESHOLDS["chunk_duration_seconds"]),
        ),
        mid_chapter_silences_ms=_mid_chapter_silences(audio),
    )


def check_duration(audio_path: str | Path, word_count: int | None) -> QACheckResult:
    """Validate chapter duration against the word-count heuristic."""

    if word_count is None or word_count <= 0:
        return QACheckResult(
            name="duration_check",
            status=QAAutomaticStatus.WARNING.value,
            message="Word count unavailable; duration could not be validated.",
            value=None,
        )

    analysis = _load_audio_analysis(audio_path)
    expected_duration = word_count * QA_THRESHOLDS["words_per_second"]
    tolerance = QA_THRESHOLDS["duration_tolerance_percent"] / 100
    min_duration = expected_duration * (1 - tolerance)
    max_duration = expected_duration * (1 + tolerance)

    if min_duration <= analysis.actual_duration <= max_duration:
        return QACheckResult(
            name="duration_check",
            status=QAAutomaticStatus.PASS.value,
            message=(
                f"Duration {analysis.actual_duration:.1f}s within expected range "
                f"{expected_duration:.1f}s (+/-{QA_THRESHOLDS['duration_tolerance_percent']}%)."
            ),
            value=round(analysis.actual_duration, 3),
        )

    return QACheckResult(
        name="duration_check",
        status=QAAutomaticStatus.WARNING.value,
        message=(
            f"Duration {analysis.actual_duration:.1f}s outside expected range "
            f"{expected_duration:.1f}s (+/-{QA_THRESHOLDS['duration_tolerance_percent']}%)."
        ),
        value=round(analysis.actual_duration, 3),
    )


def check_clipping(audio_path: str | Path) -> QACheckResult:
    """Detect clipping by checking normalized peak amplitude."""

    analysis = _load_audio_analysis(audio_path)
    threshold = float(QA_THRESHOLDS["clipping_threshold"])

    if analysis.peak_amplitude < threshold:
        return QACheckResult(
            name="clipping_detection",
            status=QAAutomaticStatus.PASS.value,
            message=f"No clipping detected (peak: {analysis.peak_amplitude:.3f}).",
            value=round(analysis.peak_amplitude, 6),
        )

    return QACheckResult(
        name="clipping_detection",
        status=QAAutomaticStatus.FAIL.value,
        message=(
            f"Clipping detected (peak: {analysis.peak_amplitude:.3f}, "
            f"threshold: {threshold:.2f})."
        ),
        value=round(analysis.peak_amplitude, 6),
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


def _analysis_error_results(message: str) -> list[QACheckResult]:
    """Return a full set of failed analysis checks when audio cannot be decoded."""

    return [
        QACheckResult(name="duration_check", status=QAAutomaticStatus.FAIL.value, message=message, value=None),
        QACheckResult(name="clipping_detection", status=QAAutomaticStatus.FAIL.value, message=message, value=None),
        QACheckResult(name="silence_gaps", status=QAAutomaticStatus.FAIL.value, message=message, value=None),
        QACheckResult(name="volume_consistency", status=QAAutomaticStatus.FAIL.value, message=message, value=None),
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
) -> QAResult:
    """Synchronously execute the full QA sequence for one chapter."""

    file_result = _file_exists_result(audio_path)
    checks = [file_result]

    if file_result.status == QAAutomaticStatus.FAIL.value or audio_path is None:
        checks.extend(_analysis_error_results("Audio file could not be analyzed because it is missing or empty."))
        overall_status = _overall_status(checks)
        return QAResult(
            chapter_n=chapter_n,
            book_id=book_id,
            timestamp=utc_now(),
            checks=checks,
            overall_status=overall_status,
        )

    try:
        _load_audio_analysis(audio_path)
        checks.extend(
            [
                check_duration(audio_path, word_count),
                check_clipping(audio_path),
                check_silence_gaps(audio_path),
                check_volume_consistency(audio_path),
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

    return QAResult(
        chapter_n=chapter_n,
        book_id=book_id,
        timestamp=utc_now(),
        checks=checks,
        overall_status=overall_status,
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
        return await asyncio.to_thread(
            _run_qa_checks_sync,
            book_id=book_id,
            chapter_n=chapter_n,
            audio_path=audio_path,
            word_count=chapter.word_count,
        )
    finally:
        if owns_session:
            session.close()


async def run_qa_checks_for_chapter(chapter: Chapter) -> QAResult:
    """Run QA checks using an already-loaded chapter ORM instance."""

    return await asyncio.to_thread(
        _run_qa_checks_sync,
        book_id=chapter.book_id,
        chapter_n=chapter.number,
        audio_path=_resolve_audio_path(chapter.audio_path),
        word_count=chapter.word_count,
    )


def build_qa_record_response(record: ChapterQARecord) -> dict[str, Any]:
    """Return a JSON-serializable QA payload for API responses."""

    qa_result = QAResult.model_validate(json.loads(record.qa_details))
    return {
        "chapter_n": record.chapter_n,
        "book_id": record.book_id,
        "overall_status": record.overall_status.value,
        "automatic_checks": [check.model_dump() for check in qa_result.checks],
        "checked_at": record.checked_at,
        "manual_status": record.manual_status.value if record.manual_status is not None else None,
        "manual_notes": record.manual_notes,
        "manual_reviewed_by": record.manual_reviewed_by,
        "manual_reviewed_at": record.manual_reviewed_at,
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
