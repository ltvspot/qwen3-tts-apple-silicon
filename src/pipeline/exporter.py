"""Audiobook export pipeline for MP3 and M4B outputs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import shutil
import subprocess
import threading
import uuid
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from pydub import AudioSegment
from sqlalchemy.orm import Session, sessionmaker

from src.config import settings
from src.database import (
    Book,
    BookExportStatus,
    BookStatus,
    Chapter,
    ChapterQARecord,
    ChapterStatus,
    ChapterType,
    ExportJob,
    QAAutomaticStatus,
    QAManualStatus,
    QAStatus,
    SessionLocal,
    retry_on_locked,
    utc_now,
)
from src.pipeline.book_mastering import BookMasteringPipeline
from src.pipeline.book_qa import ACX_REQUIREMENTS, measure_integrated_lufs
from src.pipeline.qa_checker import check_lufs_compliance
from src.utils.subprocess_utils import run_ffmpeg

logger = logging.getLogger(__name__)

DEFAULT_EXPORT_FORMATS = ("mp3", "m4b")
VALID_EXPORT_FORMATS = frozenset(DEFAULT_EXPORT_FORMATS)
ExportProgressCallback = Callable[[float, str | None, str | None, int | None, int | None], None]
EXPORT_VERIFY_TIMEOUT_SECONDS = 60
EXPORT_UNKNOWN_DURATION_TIMEOUT_SECONDS = 300.0
EXPORT_STALE_TIMEOUT = timedelta(minutes=15)
FORMAT_DETAILS_ARTIFACTS_KEY = "_artifacts"
FORMAT_DETAILS_REQUEST_OPTIONS_KEY = "request_options"
EXPORT_LUFS_MAX_ATTEMPTS = 3
EXPORT_STATE_FILE_NAME = "export_state.json"
SOFT_FAIL_CHECKS = frozenset({"pacing_detailed", "spectral_quality", "volume_consistency", "duration_check"})
HARD_FAIL_CHECKS = frozenset({"clipping_detection", "file_exists", "lufs_compliance"})
_active_export_temp_files: dict[int, set[Path]] = {}
_active_export_temp_files_lock = threading.RLock()
_atomic_json_write_lock = threading.RLock()


class ExportFormatResult(BaseModel):
    """Serialized result for one export format."""

    status: str
    file_size_bytes: int | None = None
    sha256: str | None = None
    file_name: str | None = None
    download_url: str | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    verification: dict[str, Any] | None = None
    attempts: int = 0
    measured_lufs: float | None = None
    lufs_warning: str | None = None
    noise_floor_dbfs: float | None = None
    noise_floor_compliant: bool | None = None
    noise_floor_warning: str | None = None


class QAChapterSummary(BaseModel):
    """Per-chapter QA summary stored with an export job."""

    chapter_n: int
    chapter_title: str
    status: str
    file_size_bytes: int
    duration_seconds: float
    qa_soft_pass: bool = False
    qa_warnings: list[str] = Field(default_factory=list)


class QAReport(BaseModel):
    """Persisted QA export summary."""

    book_id: int
    book_title: str
    export_date: datetime
    chapters_included: int
    chapters_approved: int
    chapters_flagged: int
    chapters_warnings: int
    export_approved: bool
    notes: str
    chapter_summary: list[QAChapterSummary]


class ExportResult(BaseModel):
    """Aggregate result of an export run."""

    book_id: int
    export_status: str
    formats: dict[str, ExportFormatResult]
    qa_report: QAReport


class ExportBlockedError(RuntimeError):
    """Raised when mastering finds blocking issues that must be fixed before export."""


class ExportCancelledError(RuntimeError):
    """Raised when an operator force-cancels an in-flight export job."""


@dataclass(slots=True)
class ChapterMarker:
    """Timeline marker for a chapter in an M4B container."""

    title: str
    start_ms: int
    end_ms: int


@dataclass(slots=True)
class ChapterApprovalDecision:
    """Resolved QA export decision for one chapter."""

    approved: bool
    soft_pass: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SelectedChapter:
    """Resolved chapter selected for export."""

    chapter_n: int
    chapter_title: str
    chapter_type: ChapterType
    audio_path: Path
    file_size_bytes: int
    duration_seconds: float
    qa_status: str
    export_approved: bool
    qa_soft_pass: bool = False
    qa_warnings: list[str] = field(default_factory=list)
    loudness_adjustment_db: float = 0.0


@dataclass(slots=True)
class ConcatenationResult:
    """Intermediate export assembly output."""

    master_wav_path: Path
    chapter_markers: list[ChapterMarker]
    included_chapters: list[SelectedChapter]
    skipped_notes: list[str]
    qa_records: dict[int, ChapterQARecord]


@dataclass(slots=True)
class EncodedExportArtifact:
    """Encoded export file metadata captured before verification."""

    file_size_bytes: int
    sha256: str


@dataclass(slots=True)
class LoudnessNormalizationResult:
    """Measured loudness details captured after final normalization."""

    measured_lufs: float | None
    lufs_warning: str | None
    attempts: int


def _slugify(value: str, *, fallback: str, max_length: int = 50) -> str:
    """Return a stable filesystem slug for output folders."""

    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not normalized:
        normalized = fallback
    return normalized[:max_length].strip("-") or fallback


def _safe_filename(value: str, *, fallback: str) -> str:
    """Return a filename-safe string while preserving readable spaces."""

    cleaned = re.sub(r'[<>:"/\\\\|?*]+', " ", value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or fallback


def _normalize_export_formats(export_formats: list[str] | None) -> list[str]:
    """Validate, de-duplicate, and preserve the requested export format order."""

    requested = export_formats or list(DEFAULT_EXPORT_FORMATS)
    normalized: list[str] = []
    for export_format in requested:
        candidate = export_format.strip().lower()
        if candidate not in VALID_EXPORT_FORMATS:
            raise ValueError(f"Unsupported export format: {export_format}")
        if candidate not in normalized:
            normalized.append(candidate)

    if not normalized:
        raise ValueError("At least one export format is required.")

    return normalized


def _outputs_root() -> Path:
    """Return the configured output root as an absolute path."""

    return Path(settings.OUTPUTS_PATH).resolve()


def _sample_rate() -> int:
    """Return the configured export sample rate."""

    return int(settings.EXPORT_SAMPLE_RATE)


def _book_root(book: Book) -> Path:
    """Return the output folder for one book."""

    return _outputs_root() / f"{book.id}-{_slugify(book.title, fallback=f'book-{book.id}')}"


def _exports_root(book: Book) -> Path:
    """Return the exports folder for one book."""

    return _book_root(book) / "exports"


def _placeholder_cover_path(exports_root: Path) -> Path:
    """Return the generated placeholder cover art path."""

    return exports_root / ".placeholder-cover.jpg"


def _ensure_placeholder_cover(exports_root: Path) -> Path:
    """Write a tiny placeholder JPEG once and return its path."""

    cover_path = _placeholder_cover_path(exports_root)
    if not cover_path.exists():
        ffmpeg_path = _require_ffmpeg()
        run_ffmpeg(
            [
                ffmpeg_path,
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x1f2937:s=200x200",
                "-frames:v",
                "1",
                str(cover_path),
            ],
        )
    return cover_path


def create_silence(duration_seconds: float, sample_rate: int | None = None) -> AudioSegment:
    """Generate a mono silence segment at the requested sample rate."""

    resolved_sample_rate = _sample_rate() if sample_rate is None else sample_rate
    return AudioSegment.silent(duration=int(duration_seconds * 1000), frame_rate=resolved_sample_rate).set_channels(1)


def _require_ffmpeg() -> str:
    """Return the ffmpeg binary path or raise a clear error."""

    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path is None:
        raise RuntimeError("ffmpeg is required for exports. Install it with `brew install ffmpeg`.")
    return ffmpeg_path


def _file_sha256(path: Path, *, chunk_size: int = 64 * 1024) -> str:
    """Return the SHA256 for one file without loading it into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_checksum(file_path: Path, expected_sha256: str) -> bool:
    """Return whether one file still matches its persisted SHA256 checksum."""

    if not file_path.exists():
        logger.error("Checksum verification skipped because %s is missing.", file_path)
        return False

    actual_sha256 = _file_sha256(file_path)
    if actual_sha256 == expected_sha256:
        return True

    logger.error(
        "Checksum mismatch for %s. Expected %s, got %s. File may be corrupted.",
        file_path,
        expected_sha256,
        actual_sha256,
    )
    return False


def _serialize_state_value(value: Any) -> Any:
    """Return one JSON-serializable export-state value."""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, Path):
        return str(value)
    return value


def _write_json_atomic(path: Path, payload: dict[str, Any], *, temp_suffix: str = ".tmp") -> None:
    """Atomically persist one JSON payload with a caller-selected temp suffix."""

    with _atomic_json_write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(temp_suffix)
        temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp_path.replace(path)


def _write_export_state_atomic(state_path: Path, state: dict[str, Any]) -> None:
    """Atomically persist the on-disk export state snapshot."""

    _write_json_atomic(state_path, state, temp_suffix=".tmp")


def _load_json_payload(path: Path) -> dict[str, Any] | None:
    """Load one JSON object from disk when it is valid."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _resolve_chapter_audio_path(chapter: Chapter) -> Path | None:
    """Return the absolute WAV path for a chapter when available."""

    if not chapter.audio_path:
        return None

    audio_path = Path(chapter.audio_path)
    if audio_path.is_absolute():
        return audio_path
    return (_outputs_root() / audio_path).resolve()


def _chapter_display_title(chapter: Chapter) -> str:
    """Return a human-facing chapter label for export metadata."""

    if chapter.title:
        return chapter.title
    return f"Chapter {chapter.number}"


def _chapter_effective_qa_status(chapter: Chapter, qa_record: ChapterQARecord | None) -> str:
    """Resolve the chapter QA summary used in reports and filtering."""

    if qa_record is not None:
        if qa_record.manual_status == QAManualStatus.APPROVED:
            return "approved"
        if qa_record.manual_status == QAManualStatus.FLAGGED:
            return "flagged"
        return qa_record.overall_status.value

    if chapter.qa_status == QAStatus.APPROVED:
        return "approved"
    if chapter.qa_status == QAStatus.NEEDS_REVIEW:
        return QAAutomaticStatus.WARNING.value
    return QAStatus.NOT_REVIEWED.value


def _chapter_report_ready_for_export(qa_record: ChapterQARecord | None) -> bool | None:
    """Return the stored chapter export-readiness flag when present."""

    qa_details = _load_qa_details_payload(qa_record)
    if not qa_details:
        return None

    chapter_report = qa_details.get("chapter_report")
    if not isinstance(chapter_report, dict):
        return None

    ready_for_export = chapter_report.get("ready_for_export")
    if isinstance(ready_for_export, bool):
        return ready_for_export

    overall_grade = chapter_report.get("overall_grade")
    if overall_grade in {"A", "B"}:
        return True
    if overall_grade in {"C", "F"}:
        return False
    return None


def _load_qa_details_payload(qa_record: ChapterQARecord | None) -> dict[str, Any]:
    """Return the parsed QA details payload when available."""

    if qa_record is None or not qa_record.qa_details:
        return {}

    try:
        payload = json.loads(qa_record.qa_details)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _failed_qa_checks(qa_record: ChapterQARecord | None) -> list[tuple[str, str]]:
    """Return the failed QA checks captured for one chapter."""

    qa_details = _load_qa_details_payload(qa_record)
    raw_checks = qa_details.get("checks")
    if not isinstance(raw_checks, list):
        return []

    failures: list[tuple[str, str]] = []
    for raw_check in raw_checks:
        if not isinstance(raw_check, dict):
            continue
        if raw_check.get("status") != QAAutomaticStatus.FAIL.value:
            continue
        name = raw_check.get("name")
        if not isinstance(name, str) or not name:
            continue
        message = raw_check.get("message")
        if not isinstance(message, str) or not message.strip():
            message = "Automatic QA failure."
        failures.append((name, message))
    return failures


def _chapter_approval_decision(chapter: Chapter, qa_record: ChapterQARecord | None) -> ChapterApprovalDecision:
    """Return whether a chapter is eligible for approval-only exports."""

    if qa_record is None:
        return ChapterApprovalDecision(approved=True)

    if qa_record.manual_status == QAManualStatus.FLAGGED:
        return ChapterApprovalDecision(approved=False)
    if qa_record.manual_status == QAManualStatus.APPROVED:
        return ChapterApprovalDecision(approved=True)
    if qa_record.overall_status == QAAutomaticStatus.PASS:
        return ChapterApprovalDecision(approved=True)

    failed_checks = _failed_qa_checks(qa_record)
    if failed_checks:
        hard_failures = [name for name, _message in failed_checks if name in HARD_FAIL_CHECKS]
        if hard_failures:
            return ChapterApprovalDecision(approved=False)

        if all(name in SOFT_FAIL_CHECKS for name, _message in failed_checks):
            return ChapterApprovalDecision(
                approved=True,
                soft_pass=True,
                warnings=[f"{name}: {message}" for name, message in failed_checks],
            )

        return ChapterApprovalDecision(approved=False)

    report_ready = _chapter_report_ready_for_export(qa_record)
    if report_ready is not None:
        return ChapterApprovalDecision(approved=report_ready)

    if qa_record.overall_status == QAAutomaticStatus.WARNING:
        return ChapterApprovalDecision(approved=True)

    return ChapterApprovalDecision(approved=chapter.qa_status == QAStatus.APPROVED)


def _chapter_is_approved(chapter: Chapter, qa_record: ChapterQARecord | None) -> bool:
    """Return True when a chapter is eligible for approval-only exports."""

    return _chapter_approval_decision(chapter, qa_record).approved


def _should_include_chapter(
    chapter: Chapter,
    qa_record: ChapterQARecord | None,
    *,
    include_only_approved: bool,
) -> tuple[bool, str | None]:
    """Return whether a chapter should be included and why it may be skipped."""

    if chapter.status != ChapterStatus.GENERATED:
        return (False, f"Skipped chapter {chapter.number}: audio not generated.")

    audio_path = _resolve_chapter_audio_path(chapter)
    if audio_path is None or not audio_path.exists():
        return (False, f"Skipped chapter {chapter.number}: audio file missing.")

    if qa_record is not None and qa_record.manual_status == QAManualStatus.FLAGGED:
        return (False, f"Skipped chapter {chapter.number}: manually flagged during QA.")

    if include_only_approved and not _chapter_is_approved(chapter, qa_record):
        return (False, f"Skipped chapter {chapter.number}: not QA approved.")

    return (True, None)


def _silence_between(
    current: ChapterType,
    following: ChapterType | None,
    *,
    chapter_silence_seconds: float,
    opening_silence_seconds: float,
    closing_silence_seconds: float,
) -> float:
    """Return the silence duration inserted after the current segment."""

    if following is None:
        return 0.0
    if current == ChapterType.OPENING_CREDITS:
        return opening_silence_seconds
    if following == ChapterType.CLOSING_CREDITS:
        return closing_silence_seconds
    return chapter_silence_seconds


def _load_selected_chapters(
    db_session: Session,
    book: Book,
    *,
    include_only_approved: bool,
) -> tuple[list[SelectedChapter], list[str], dict[int, ChapterQARecord]]:
    """Load, filter, and validate the chapter WAVs eligible for export."""

    chapters = (
        db_session.query(Chapter)
        .filter(Chapter.book_id == book.id)
        .order_by(Chapter.number, Chapter.id)
        .all()
    )
    qa_records = {
        record.chapter_n: record
        for record in db_session.query(ChapterQARecord).filter(ChapterQARecord.book_id == book.id).all()
    }

    selected: list[SelectedChapter] = []
    skipped_notes: list[str] = []

    for chapter in chapters:
        qa_record = qa_records.get(chapter.number)
        approval_decision = _chapter_approval_decision(chapter, qa_record)
        include_chapter, skipped_note = _should_include_chapter(
            chapter,
            qa_record,
            include_only_approved=include_only_approved,
        )
        if not include_chapter:
            if skipped_note is not None:
                skipped_notes.append(skipped_note)
            continue

        audio_path = _resolve_chapter_audio_path(chapter)
        if audio_path is None:
            continue

        try:
            audio_segment = (
                AudioSegment.from_wav(audio_path)
                .set_frame_rate(_sample_rate())
                .set_channels(1)
                .set_sample_width(2)
            )
            measured_lufs = measure_integrated_lufs(audio_path)
            loudness_adjustment_db = 0.0
            if measured_lufs is not None:
                loudness_adjustment_db = round(-20.0 - measured_lufs, 3)
                if abs(loudness_adjustment_db) > 0.1:
                    audio_segment = audio_segment + loudness_adjustment_db
        except Exception as exc:
            skipped_notes.append(f"Skipped chapter {chapter.number}: invalid WAV ({exc}).")
            logger.warning("Skipping invalid export WAV for book=%s chapter=%s: %s", book.id, chapter.number, exc)
            continue

        selected.append(
            SelectedChapter(
                chapter_n=chapter.number,
                chapter_title=_chapter_display_title(chapter),
                chapter_type=chapter.type,
                audio_path=audio_path,
                file_size_bytes=audio_path.stat().st_size,
                duration_seconds=round(len(audio_segment) / 1000.0, 3),
                qa_status=_chapter_effective_qa_status(chapter, qa_record),
                export_approved=approval_decision.approved,
                qa_soft_pass=approval_decision.soft_pass,
                qa_warnings=list(approval_decision.warnings),
                loudness_adjustment_db=loudness_adjustment_db,
            )
        )

    return (selected, skipped_notes, qa_records)


def _load_streamable_audio(chapter: SelectedChapter) -> AudioSegment:
    """Load one chapter WAV into a streamable mono 16-bit segment."""

    audio_segment = (
        AudioSegment.from_wav(chapter.audio_path)
        .set_frame_rate(_sample_rate())
        .set_channels(1)
        .set_sample_width(2)
    )
    if abs(chapter.loudness_adjustment_db) > 0.1:
        audio_segment = audio_segment + chapter.loudness_adjustment_db
    return audio_segment


def _concatenate_chapters_streaming(
    selected: list[SelectedChapter],
    *,
    master_wav_path: Path,
    chapter_silence_seconds: float,
    opening_silence_seconds: float,
    closing_silence_seconds: float,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[ChapterMarker]:
    """Concatenate chapters while keeping only one chapter in memory at a time."""

    chapter_markers: list[ChapterMarker] = []
    current_ms = 0

    with wave.open(str(master_wav_path), "wb") as output_wav:
        output_wav.setnchannels(1)
        output_wav.setsampwidth(2)
        output_wav.setframerate(_sample_rate())

        for index, selected_chapter in enumerate(selected):
            audio_segment = _load_streamable_audio(selected_chapter)
            start_ms = current_ms
            output_wav.writeframes(audio_segment.raw_data)
            current_ms += len(audio_segment)

            next_type = selected[index + 1].chapter_type if index + 1 < len(selected) else None
            silence_seconds = _silence_between(
                selected_chapter.chapter_type,
                next_type,
                chapter_silence_seconds=chapter_silence_seconds,
                opening_silence_seconds=opening_silence_seconds,
                closing_silence_seconds=closing_silence_seconds,
            )
            if silence_seconds > 0:
                silence_segment = create_silence(silence_seconds).set_sample_width(2)
                output_wav.writeframes(silence_segment.raw_data)
                current_ms += len(silence_segment)

            chapter_markers.append(
                ChapterMarker(
                    title=selected_chapter.chapter_title,
                    start_ms=start_ms,
                    end_ms=current_ms,
                )
            )
            if progress_callback is not None:
                progress_callback(index + 1, len(selected))

            del audio_segment

    return chapter_markers


def concatenate_chapters_sync(
    book_id: int,
    *,
    include_only_approved: bool = True,
    chapter_silence_seconds: float = 2.0,
    opening_silence_seconds: float = 3.0,
    closing_silence_seconds: float = 3.0,
    session_factory: sessionmaker[Session] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    master_wav_path: Path | None = None,
) -> ConcatenationResult:
    """Concatenate exported chapter WAV files and return the master WAV path."""

    session_factory = session_factory or SessionLocal
    with session_factory() as db_session:
        book = db_session.query(Book).filter(Book.id == book_id).first()
        if book is None:
            raise ValueError(f"Book {book_id} not found")

        selected, skipped_notes, qa_records = _load_selected_chapters(
            db_session,
            book,
            include_only_approved=include_only_approved,
        )
        if not selected:
            raise ValueError("No chapter audio is eligible for export.")

        exports_root = _exports_root(book)
        exports_root.mkdir(parents=True, exist_ok=True)
        target_master_wav_path = master_wav_path or (exports_root / "master.wav")
        chapter_markers = _concatenate_chapters_streaming(
            selected,
            master_wav_path=target_master_wav_path,
            chapter_silence_seconds=chapter_silence_seconds,
            opening_silence_seconds=opening_silence_seconds,
            closing_silence_seconds=closing_silence_seconds,
            progress_callback=progress_callback,
        )
        return ConcatenationResult(
            master_wav_path=target_master_wav_path,
            chapter_markers=chapter_markers,
            included_chapters=selected,
            skipped_notes=skipped_notes,
            qa_records=qa_records,
        )


async def concatenate_chapters(
    book_id: int,
    *,
    include_only_approved: bool = True,
    chapter_silence_seconds: float = 2.0,
    opening_silence_seconds: float = 3.0,
    closing_silence_seconds: float = 3.0,
    session_factory: sessionmaker[Session] | None = None,
) -> Path:
    """Async wrapper that concatenates chapter WAV files into a master WAV."""

    result = await asyncio.to_thread(
        concatenate_chapters_sync,
        book_id,
        include_only_approved=include_only_approved,
        chapter_silence_seconds=chapter_silence_seconds,
        opening_silence_seconds=opening_silence_seconds,
        closing_silence_seconds=closing_silence_seconds,
        session_factory=session_factory,
    )
    return result.master_wav_path


def _parse_loudnorm_metrics(output: str) -> dict[str, Any] | None:
    """Return the parsed loudnorm JSON block from one ffmpeg run."""

    match = re.search(r"(\{\s*\"input_i\".*?\})", output, re.DOTALL)
    if match is None:
        return None

    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _ffmpeg_timeout(duration_seconds: float, *, minimum: float = 60.0, multiplier: float = 0.5) -> float:
    """Return a timeout in seconds that scales with audio duration."""

    return max(minimum, duration_seconds * multiplier)


def _resolve_ffmpeg_timeout(duration_seconds: float | None) -> float:
    """Return the timeout used for duration-aware ffmpeg measurements."""

    if duration_seconds is None:
        return EXPORT_UNKNOWN_DURATION_TIMEOUT_SECONDS
    return _ffmpeg_timeout(duration_seconds, minimum=float(EXPORT_VERIFY_TIMEOUT_SECONDS))


def _format_timeout_seconds(timeout_seconds: float) -> str:
    """Return a compact timeout string for user-facing warnings."""

    return f"{int(timeout_seconds)}s" if float(timeout_seconds).is_integer() else f"{timeout_seconds:.1f}s"


def _escape_filter_path(path: Path) -> str:
    """Escape one filesystem path for ffmpeg filter arguments."""

    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _measure_loudness(
    audio_path: Path,
    *,
    target_lufs: float,
    duration_seconds: float | None = None,
) -> float | None:
    """Measure integrated LUFS for one audio file using ffmpeg loudnorm."""

    ffmpeg_path = _require_ffmpeg()
    timeout_seconds = _resolve_ffmpeg_timeout(duration_seconds)
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-i",
        str(audio_path),
        "-af",
        (
            f"loudnorm=I={target_lufs}:"
            f"TP={BookMasteringPipeline.PEAK_TARGET_DBFS}:LRA=11:print_format=json"
        ),
        "-f",
        "null",
        "-",
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "LUFS measurement timed out for %s after %s.",
            audio_path,
            _format_timeout_seconds(timeout_seconds),
        )
        return None
    payload = _parse_loudnorm_metrics("\n".join(part for part in (completed.stdout, completed.stderr) if part))
    if payload is None:
        return None
    try:
        return round(float(payload["input_i"]), 3)
    except (KeyError, TypeError, ValueError):
        return None


def _measure_noise_floor(audio_path: Path, duration_seconds: float | None = None) -> float | None:
    """Return the average RMS level for the quietest 10 percent of one audio file."""

    ffprobe_path = shutil.which("ffprobe")
    if ffprobe_path is None:
        audio = AudioSegment.from_file(audio_path)
        return round(float(audio.dBFS), 3)

    command = [
        ffprobe_path,
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"amovie='{_escape_filter_path(audio_path)}',astats=metadata=1:reset=1:length=0.1",
        "-show_entries",
        "frame_tags=lavfi.astats.1.RMS_level",
        "-of",
        "json",
    ]
    timeout_seconds = _resolve_ffmpeg_timeout(duration_seconds)
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "Noise floor measurement timed out for %s after %s.",
            audio_path,
            _format_timeout_seconds(timeout_seconds),
        )
        return None

    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Unable to parse astats output for {audio_path}: {exc}") from exc

    values: list[float] = []
    for frame in payload.get("frames", []):
        if not isinstance(frame, dict):
            continue
        tags = frame.get("tags")
        if not isinstance(tags, dict):
            continue
        raw_value = tags.get("lavfi.astats.1.RMS_level")
        if raw_value in {None, "nan", "NAN"}:
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            values.append(value)

    if not values:
        return -100.0

    quietest_count = max(1, math.ceil(len(values) * 0.1))
    quietest_values = sorted(values)[:quietest_count]
    return round(sum(quietest_values) / len(quietest_values), 3)


def normalize_loudness(
    input_wav: Path,
    output_wav: Path,
    target_lufs: float = -19.0,
    duration_seconds: float | None = None,
) -> LoudnessNormalizationResult:
    """Normalize audio and keep retrying until the measured LUFS is ACX compliant."""

    ffmpeg_path = _require_ffmpeg()
    measured_lufs: float | None = None
    current_target = target_lufs
    measurement_timeout = _resolve_ffmpeg_timeout(duration_seconds)

    for attempt in range(1, EXPORT_LUFS_MAX_ATTEMPTS + 1):
        command = [
            ffmpeg_path,
            "-y",
            "-i",
            str(input_wav),
            "-af",
            f"loudnorm=I={current_target}:TP={BookMasteringPipeline.PEAK_TARGET_DBFS}:LRA=11",
            str(output_wav),
        ]
        run_ffmpeg(command)
        measured_lufs = _measure_loudness(
            output_wav,
            target_lufs=current_target,
            duration_seconds=duration_seconds,
        )
        if measured_lufs is not None and -23.0 <= measured_lufs <= -18.0:
            return LoudnessNormalizationResult(
                measured_lufs=measured_lufs,
                lufs_warning=None,
                attempts=attempt,
            )

        if attempt >= EXPORT_LUFS_MAX_ATTEMPTS:
            break

        if measured_lufs is None:
            warning = (
                "LUFS measurement timed out after "
                f"{_format_timeout_seconds(measurement_timeout)}. Manual verification recommended."
            )
            logger.warning("%s", warning)
            return LoudnessNormalizationResult(
                measured_lufs=None,
                lufs_warning=warning,
                attempts=attempt,
            )
        elif measured_lufs < -23.0:
            current_target = -21.0
        else:
            current_target = -20.0

    warning = (
        "Normalized loudness remained outside the ACX range after "
        f"{EXPORT_LUFS_MAX_ATTEMPTS} attempts (measured={measured_lufs})."
    )
    logger.warning("%s", warning)
    return LoudnessNormalizationResult(
        measured_lufs=measured_lufs,
        lufs_warning=warning,
        attempts=EXPORT_LUFS_MAX_ATTEMPTS,
    )


def _escape_ffmetadata(value: str) -> str:
    """Escape ffmetadata control characters in a metadata value."""

    escaped = value.replace("\\", "\\\\")
    escaped = escaped.replace("\n", "\\\n")
    for character in ("=", ";", "#"):
        escaped = escaped.replace(character, f"\\{character}")
    return escaped


def _write_ffmetadata(
    metadata_path: Path,
    *,
    book: Book,
    chapter_markers: list[ChapterMarker],
) -> None:
    """Write ffmetadata chapter markers for M4B exports."""

    lines = [
        ";FFMETADATA1",
        f"title={_escape_ffmetadata(book.title)}",
        f"artist={_escape_ffmetadata(book.author)}",
        f"album={_escape_ffmetadata(book.title)}",
        f"comment={_escape_ffmetadata(f'Narrated by {book.narrator}')}",
    ]

    for marker in chapter_markers:
        lines.extend(
            [
                "[CHAPTER]",
                "TIMEBASE=1/1000",
                f"START={marker.start_ms}",
                f"END={max(marker.end_ms - 1, marker.start_ms)}",
                f"title={_escape_ffmetadata(marker.title)}",
            ]
        )

    metadata_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_mp3(
    normalized_wav_path: Path,
    output_path: Path,
    *,
    book: Book,
    cover_art_path: Path | None,
) -> EncodedExportArtifact:
    """Encode a normalized master WAV into audiobook MP3 format."""

    ffmpeg_path = _require_ffmpeg()
    command = [
        ffmpeg_path,
        "-y",
        "-i",
        str(normalized_wav_path),
        "-map",
        "0:a",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        settings.EXPORT_MP3_BITRATE,
        "-ar",
        str(_sample_rate()),
        "-ac",
        "1",
        "-id3v2_version",
        "3",
        "-metadata",
        f"title={book.title}",
        "-metadata",
        f"artist={book.author}",
        "-metadata",
        f"album={book.title}",
        "-metadata",
        f"comment=Narrated by {book.narrator}",
        str(output_path),
    ]
    if cover_art_path is not None:
        command[4:4] = [
            "-i",
            str(cover_art_path),
            "-map",
            "1:v",
            "-codec:v",
            "mjpeg",
            "-disposition:v",
            "attached_pic",
        ]
        command[-1:-1] = [
            "-metadata:s:v",
            "title=Cover Art",
            "-metadata:s:v",
            "comment=Cover (front)",
        ]
    run_ffmpeg(command)
    return EncodedExportArtifact(
        file_size_bytes=output_path.stat().st_size,
        sha256=_file_sha256(output_path),
    )


def export_m4b(
    normalized_wav_path: Path,
    output_path: Path,
    *,
    book: Book,
    chapter_markers: list[ChapterMarker],
    metadata_path: Path,
    bitrate: str | None = None,
) -> EncodedExportArtifact:
    """Encode a normalized master WAV into M4B with chapter markers."""

    ffmpeg_path = _require_ffmpeg()
    _write_ffmetadata(metadata_path, book=book, chapter_markers=chapter_markers)
    command = [
        ffmpeg_path,
        "-y",
        "-i",
        str(normalized_wav_path),
        "-i",
        str(metadata_path),
        "-map",
        "0:a",
        "-map_metadata",
        "1",
        "-codec:a",
        "aac",
        "-b:a",
        bitrate or settings.EXPORT_M4B_BITRATE,
        "-ar",
        str(_sample_rate()),
        "-ac",
        "1",
        "-movflags",
        "+faststart",
        "-f",
        "ipod",
        str(output_path),
    ]
    run_ffmpeg(command)
    return EncodedExportArtifact(
        file_size_bytes=output_path.stat().st_size,
        sha256=_file_sha256(output_path),
    )


def _ffprobe_metadata(path: Path) -> dict[str, Any]:
    """Return ffprobe metadata when available, falling back to pydub decode checks."""

    ffprobe_path = shutil.which("ffprobe")
    if ffprobe_path is None:
        audio = AudioSegment.from_file(path)
        return {
            "format": {"duration": len(audio) / 1000.0},
            "streams": [{"codec_type": "audio"}],
            "chapters": [],
        }

    completed = subprocess.run(
        [
            ffprobe_path,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=EXPORT_VERIFY_TIMEOUT_SECONDS,
    )
    return json.loads(completed.stdout or "{}")


def _verify_export_output(
    output_path: Path,
    *,
    expected_duration_seconds: float,
    export_format: str,
    expected_markers: list[ChapterMarker] | None = None,
) -> dict[str, Any]:
    """Validate that an exported asset is decodable and matches expected structure."""

    issues: list[str] = []
    if not output_path.exists():
        issues.append("export file is missing")
        return {
            "ok": False,
            "issues": issues,
        }

    file_size_bytes = output_path.stat().st_size
    if file_size_bytes <= 0:
        issues.append("file size is 0 bytes")
    if file_size_bytes > int(ACX_REQUIREMENTS["max_file_size_mb"] * 1024 * 1024):
        issues.append("file exceeds the ACX upload size limit")

    try:
        probe = _ffprobe_metadata(output_path)
    except Exception as exc:
        issues.append(f"ffprobe/decode failed: {exc}")
        return {
            "ok": False,
            "fileSizeBytes": file_size_bytes,
            "issues": issues,
        }

    actual_duration = float(probe.get("format", {}).get("duration", 0.0) or 0.0)
    if abs(actual_duration - expected_duration_seconds) > 1.0:
        issues.append(
            f"duration mismatch: expected {expected_duration_seconds:.2f}s, got {actual_duration:.2f}s"
        )

    marker_titles: list[str] = []
    if export_format == "m4b":
        chapters = probe.get("chapters", [])
        marker_titles = [
            str((chapter.get("tags") or {}).get("title") or chapter.get("title") or "").strip()
            for chapter in chapters
        ]
        expected_titles = [marker.title for marker in expected_markers or []]
        if len(marker_titles) != len(expected_titles):
            issues.append(f"chapter marker count mismatch: expected {len(expected_titles)}, got {len(marker_titles)}")
        elif marker_titles != expected_titles:
            issues.append("chapter marker titles do not match the database ordering")

    return {
        "ok": not issues,
        "fileSizeBytes": file_size_bytes,
        "durationSeconds": round(actual_duration, 3),
        "expectedDurationSeconds": round(expected_duration_seconds, 3),
        "chapterMarkers": marker_titles,
        "issues": issues,
    }


def _build_export_paths(book: Book) -> dict[str, Path]:
    """Return all stable export paths for a book."""

    exports_root = _exports_root(book)
    safe_title = _safe_filename(book.title, fallback=f"book-{book.id}")
    return {
        "exports_root": exports_root,
        "master_wav": exports_root / "master.wav",
        "normalized_wav": exports_root / "master.normalized.wav",
        "metadata": exports_root / "chapters.ffmetadata",
        "state": exports_root / EXPORT_STATE_FILE_NAME,
        "qa_report": exports_root / "qa_report.json",
        "mp3": exports_root / f"{safe_title}.mp3",
        "m4b": exports_root / f"{safe_title}.m4b",
    }


def _build_temporary_export_paths(book: Book, *, temp_suffix: str) -> dict[str, Path]:
    """Return unique temporary paths for one in-flight export run."""

    exports_root = _exports_root(book)
    return {
        "master_wav": exports_root / f"master{temp_suffix}.wav",
        "normalized_wav": exports_root / f"master.normalized{temp_suffix}.wav",
        "metadata": exports_root / f"chapters{temp_suffix}.ffmetadata",
    }


def _register_export_temp_files(export_job_id: int | None, *paths: Path) -> None:
    """Track temporary export files so route shutdown can clean them up."""

    if export_job_id is None:
        return

    with _active_export_temp_files_lock:
        bucket = _active_export_temp_files.setdefault(export_job_id, set())
        bucket.update(paths)


def _discard_export_temp_files(export_job_id: int | None) -> None:
    """Forget tracked temporary files for one export job."""

    if export_job_id is None:
        return

    with _active_export_temp_files_lock:
        _active_export_temp_files.pop(export_job_id, None)


def cleanup_export_temp_files(export_job_id: int) -> None:
    """Delete any tracked temporary files for one export job."""

    with _active_export_temp_files_lock:
        paths = list(_active_export_temp_files.get(export_job_id, set()))

    for path in paths:
        path.unlink(missing_ok=True)


def get_export_output_path(book: Book, export_format: str) -> Path:
    """Return the stable output path for a requested export format."""

    if export_format not in VALID_EXPORT_FORMATS:
        raise ValueError(f"Unsupported export format: {export_format}")
    return _build_export_paths(book)[export_format]


def _load_export_state_payload(book: Book) -> dict[str, Any]:
    """Load the persisted export-state snapshot, preferring a valid recovery temp file."""

    state_path = _build_export_paths(book)["state"]
    temp_path = state_path.with_suffix(".tmp")

    temp_payload = _load_json_payload(temp_path) if temp_path.exists() else None
    main_payload = _load_json_payload(state_path) if state_path.exists() else None

    if temp_payload is not None:
        _write_export_state_atomic(state_path, temp_payload)
        temp_path.unlink(missing_ok=True)
        return temp_payload

    if temp_path.exists():
        temp_path.unlink(missing_ok=True)

    return main_payload or {}


def _resolved_format_details_payload(
    book: Book,
    stored_format_details: str | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the best available format-details payload for recovery and downloads."""

    state_payload = _load_export_state_payload(book)
    state_format_details = state_payload.get("format_details")
    if isinstance(state_format_details, dict):
        return state_format_details
    return _load_format_details_payload(stored_format_details)


def get_expected_export_sha256(
    book: Book,
    export_format: str,
    *,
    stored_format_details: str | dict[str, Any] | None = None,
) -> str | None:
    """Return the persisted checksum for one export format when available."""

    payload = _resolved_format_details_payload(book, stored_format_details=stored_format_details)
    raw_result = payload.get(export_format)
    if not isinstance(raw_result, dict):
        return None
    sha256 = raw_result.get("sha256")
    return sha256 if isinstance(sha256, str) and sha256 else None


def _job_formats_requested(export_job: ExportJob | None) -> list[str]:
    """Return the normalized requested formats for one persisted export job."""

    if export_job is None:
        return list(DEFAULT_EXPORT_FORMATS)

    try:
        return _normalize_export_formats(json.loads(export_job.formats_requested))
    except (TypeError, ValueError, json.JSONDecodeError):
        return list(DEFAULT_EXPORT_FORMATS)


def _load_format_details_payload(raw_payload: str | dict[str, Any] | None) -> dict[str, Any]:
    """Deserialize stored format details into a mutable mapping."""

    if raw_payload is None:
        return {}
    if isinstance(raw_payload, dict):
        return dict(raw_payload)

    try:
        decoded = json.loads(raw_payload)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _format_details_artifacts(raw_payload: str | dict[str, Any] | None) -> dict[str, Any]:
    """Return the internal artifact metadata stored alongside format details."""

    artifacts = _load_format_details_payload(raw_payload).get(FORMAT_DETAILS_ARTIFACTS_KEY)
    return dict(artifacts) if isinstance(artifacts, dict) else {}


def _format_details_request_options(raw_payload: str | dict[str, Any] | None) -> dict[str, Any]:
    """Return request-scoped export options stored alongside format details."""

    artifacts = _format_details_artifacts(raw_payload)
    options = artifacts.get(FORMAT_DETAILS_REQUEST_OPTIONS_KEY)
    return dict(options) if isinstance(options, dict) else {}


def _serialize_format_details_payload(
    format_results: dict[str, ExportFormatResult],
    *,
    artifacts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the persisted format-details payload, preserving auxiliary artifacts."""

    payload: dict[str, Any] = {
        name: format_result.model_dump(mode="json")
        for name, format_result in format_results.items()
    }
    if artifacts:
        payload[FORMAT_DETAILS_ARTIFACTS_KEY] = artifacts
    return payload


def _persist_export_checkpoint(
    db_session_factory: sessionmaker[Session],
    export_job_id: int,
    updates: dict[str, Any],
) -> None:
    """Persist one export checkpoint using an isolated DB session."""

    @retry_on_locked(max_retries=5, backoff_ms=250)
    def _persist() -> None:
        with db_session_factory() as checkpoint_session:
            export_job = checkpoint_session.query(ExportJob).filter(ExportJob.id == export_job_id).first()
            if export_job is None or export_job.export_status != BookExportStatus.PROCESSING:
                raise ExportCancelledError("Export job was cancelled.")

            serialized_updates = dict(updates)
            serialized_updates.setdefault("updated_at", utc_now())
            for field_name, field_value in serialized_updates.items():
                if field_name == "format_details" and field_value is not None:
                    if isinstance(field_value, str):
                        value_to_set = field_value
                    else:
                        value_to_set = json.dumps(field_value)
                elif field_name == "qa_report" and isinstance(field_value, QAReport):
                    value_to_set = field_value.model_dump_json()
                elif field_name == "qa_report" and isinstance(field_value, dict):
                    value_to_set = json.dumps(field_value)
                else:
                    value_to_set = field_value
                setattr(export_job, field_name, value_to_set)

            checkpoint_session.commit()

    _persist()


def _recoverable_completed_at(
    output_path: Path,
    *,
    stored_result: ExportFormatResult | None,
) -> datetime:
    """Prefer persisted completion timestamps and fall back to filesystem metadata."""

    if stored_result is not None and stored_result.completed_at is not None:
        return stored_result.completed_at
    return datetime.fromtimestamp(output_path.stat().st_mtime, tz=timezone.utc)


def _recovery_probe_output(output_path: Path) -> str | None:
    """Return an integrity error for one recovered export output when probing fails."""

    try:
        probe = _ffprobe_metadata(output_path)
    except Exception as exc:
        return f"ffprobe/decode failed during recovery: {exc}"

    file_size_bytes = output_path.stat().st_size
    if file_size_bytes <= 0:
        return "file size is 0 bytes"

    actual_duration = float(probe.get("format", {}).get("duration", 0.0) or 0.0)
    if actual_duration <= 0.0:
        return "decoded duration is 0 seconds"

    return None


def _existing_export_artifacts(
    book: Book,
    *,
    formats_requested: list[str] | None = None,
    stored_format_details: str | dict[str, Any] | None = None,
) -> tuple[dict[str, ExportFormatResult], datetime | None, bool]:
    """Return on-disk export artifacts already present for one book."""

    requested = _normalize_export_formats(formats_requested)
    latest_completed_at: datetime | None = None
    found_any = False
    results = _empty_format_details(requested)
    stored_payload = _resolved_format_details_payload(book, stored_format_details=stored_format_details)

    for export_format in requested:
        output_path = get_export_output_path(book, export_format)
        if not output_path.exists():
            continue

        stored_result: ExportFormatResult | None = None
        raw_stored_result = stored_payload.get(export_format)
        if isinstance(raw_stored_result, dict):
            try:
                stored_result = ExportFormatResult.model_validate(raw_stored_result)
            except ValidationError as exc:
                logger.warning(
                    "Ignoring invalid stored format metadata for book %s format %s during recovery: %s",
                    book.id,
                    export_format,
                    exc,
                )

        stat_result = output_path.stat()
        integrity_error = _recovery_probe_output(output_path)
        actual_sha256: str | None = None
        expected_sha256 = stored_result.sha256 if stored_result is not None else None
        if expected_sha256:
            actual_sha256 = _file_sha256(output_path)
            if not _verify_checksum(output_path, expected_sha256):
                output_path.unlink(missing_ok=True)
                integrity_error = (
                    f"sha256 mismatch during recovery: expected {expected_sha256}, got {actual_sha256}"
                )

        if integrity_error:
            results[export_format] = ExportFormatResult(
                status="error",
                file_size_bytes=stat_result.st_size,
                sha256=actual_sha256 or expected_sha256,
                file_name=output_path.name,
                error_message=integrity_error,
            )
            logger.warning(
                "Refusing to recover export artifact for book %s format %s: %s",
                book.id,
                export_format,
                integrity_error,
            )
            continue

        found_any = True
        completed_at = _recoverable_completed_at(output_path, stored_result=stored_result)
        if latest_completed_at is None or completed_at > latest_completed_at:
            latest_completed_at = completed_at
        results[export_format] = ExportFormatResult(
            status="completed",
            file_size_bytes=stat_result.st_size,
            sha256=actual_sha256 or expected_sha256,
            file_name=output_path.name,
            download_url=f"/api/book/{book.id}/export/download/{export_format}",
            completed_at=completed_at,
        )

    return (results, latest_completed_at, found_any)


def _as_utc_datetime(value: datetime | None) -> datetime | None:
    """Normalize persisted datetimes so SQLite naive values compare safely."""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def reconcile_export_job_state(
    db_session: Session,
    book: Book,
    export_job: ExportJob,
    *,
    now: datetime | None = None,
) -> str | None:
    """Reconcile a persisted export job against stale timers and disk artifacts."""

    now = _as_utc_datetime(now or utc_now()) or utc_now()
    requested_formats = _job_formats_requested(export_job)
    format_details, completed_at, found_any = _existing_export_artifacts(
        book,
        formats_requested=requested_formats,
        stored_format_details=export_job.format_details,
    )

    should_recover_completed_metadata = (
        export_job.export_status in {BookExportStatus.PROCESSING, BookExportStatus.ERROR}
        or not export_job.qa_report
        or export_job.current_stage == "Recovered from existing export files"
    )
    if found_any and should_recover_completed_metadata:
        recovered_qa_report = _load_recovered_qa_report(
            db_session,
            book=book,
            include_only_approved=export_job.include_only_approved,
            export_date=completed_at or now,
            stored_format_details=export_job.format_details,
        )
        if recovered_qa_report is not None:
            recovery_completed_at = completed_at or now
            export_updates = {
                "export_status": BookExportStatus.COMPLETED,
                "progress_percent": 100.0,
                "current_stage": "Export completed",
                "current_format": None,
                "completed_at": recovery_completed_at,
                "updated_at": now,
                "error_message": None,
                "current_chapter_n": recovered_qa_report.chapters_included,
                "total_chapters": recovered_qa_report.chapters_included,
                "format_details": json.dumps(
                    _serialize_format_details_payload(
                        format_details,
                        artifacts=_format_details_artifacts(
                            _resolved_format_details_payload(book, stored_format_details=export_job.format_details)
                        ),
                    )
                ),
                "qa_report": recovered_qa_report.model_dump_json(),
            }
            book_updates = {
                "export_status": BookExportStatus.COMPLETED,
                "last_export_date": recovery_completed_at,
                "status": BookStatus.EXPORTED,
            }
            logger.info(
                "Recovering export job %s for book %s with export updates=%s and book updates=%s",
                export_job.id,
                book.id,
                export_updates,
                book_updates,
            )
            try:
                for field_name, field_value in export_updates.items():
                    setattr(export_job, field_name, field_value)
                db_session.flush()
                for field_name, field_value in book_updates.items():
                    setattr(book, field_name, field_value)
                db_session.commit()
            except Exception:
                db_session.rollback()
                logger.exception(
                    "Failed to atomically recover export job %s for book %s",
                    export_job.id,
                    book.id,
                )
                raise
            return "recovered"

    last_activity = _as_utc_datetime(export_job.updated_at or export_job.started_at or export_job.created_at)
    if export_job.export_status == BookExportStatus.PROCESSING and last_activity < (now - EXPORT_STALE_TIMEOUT):
        timeout_updates = {
            "export_status": BookExportStatus.ERROR,
            "current_stage": "Export timed out",
            "completed_at": now,
            "updated_at": now,
            "error_message": "Export timed out after 15 minutes without a progress update.",
        }
        book_updates = {
            "export_status": BookExportStatus.ERROR,
        }
        logger.info(
            "Timing out stale export job %s for book %s with export updates=%s and book updates=%s",
            export_job.id,
            book.id,
            timeout_updates,
            book_updates,
        )
        try:
            for field_name, field_value in timeout_updates.items():
                setattr(export_job, field_name, field_value)
            db_session.flush()
            for field_name, field_value in book_updates.items():
                setattr(book, field_name, field_value)
            db_session.commit()
        except Exception:
            db_session.rollback()
            logger.exception(
                "Failed to atomically time out export job %s for book %s",
                export_job.id,
                book.id,
            )
            raise
        return "timed_out"

    return None


def reconcile_book_export_artifacts(db_session: Session, book: Book) -> bool:
    """Create or repair export metadata for books that already have export files on disk."""

    export_job = db_session.query(ExportJob).filter(ExportJob.book_id == book.id).first()
    if export_job is not None:
        return reconcile_export_job_state(db_session, book, export_job) == "recovered"

    format_details, completed_at, found_any = _existing_export_artifacts(
        book,
        formats_requested=list(DEFAULT_EXPORT_FORMATS),
    )
    if not found_any:
        return False

    created_at = completed_at or utc_now()
    completed_formats = [name for name, result in format_details.items() if result.status == "completed"]
    recovered_qa_report = _load_recovered_qa_report(
        db_session,
        book=book,
        include_only_approved=True,
        export_date=created_at,
        stored_format_details=_resolved_format_details_payload(book),
    )
    export_job = ExportJob(
        book_id=book.id,
        job_token=f"recovered_export_{book.id}_{created_at.strftime('%Y%m%d_%H%M%S')}",
        export_status=BookExportStatus.COMPLETED,
        formats_requested=json.dumps(completed_formats or list(DEFAULT_EXPORT_FORMATS)),
        format_details=json.dumps(
            _serialize_format_details_payload(
                format_details,
                artifacts=_format_details_artifacts(_resolved_format_details_payload(book)),
            )
        ),
        progress_percent=100.0,
        current_stage="Export completed",
        current_format=None,
        current_chapter_n=recovered_qa_report.chapters_included,
        total_chapters=recovered_qa_report.chapters_included,
        include_only_approved=True,
        created_at=created_at,
        started_at=created_at,
        completed_at=created_at,
        updated_at=utc_now(),
        error_message=None,
        qa_report=recovered_qa_report.model_dump_json(),
    )
    db_session.add(export_job)
    book.export_status = BookExportStatus.COMPLETED
    book.last_export_date = created_at
    book.status = BookStatus.EXPORTED
    db_session.commit()
    return True


def _build_recovered_qa_report(
    db_session: Session,
    *,
    book: Book,
    include_only_approved: bool,
    export_date: datetime,
) -> QAReport:
    """Rebuild enough export QA metadata when only the output files remain."""

    chapters = (
        db_session.query(Chapter)
        .filter(Chapter.book_id == book.id)
        .order_by(Chapter.number, Chapter.id)
        .all()
    )
    qa_records = {
        record.chapter_n: record
        for record in db_session.query(ChapterQARecord).filter(ChapterQARecord.book_id == book.id).all()
    }

    selected: list[SelectedChapter] = []
    skipped_notes: list[str] = []
    for chapter in chapters:
        qa_record = qa_records.get(chapter.number)
        approval_decision = _chapter_approval_decision(chapter, qa_record)
        if chapter.status != ChapterStatus.GENERATED or not chapter.audio_path:
            if chapter.status != ChapterStatus.GENERATED:
                skipped_notes.append(f"Skipped chapter {chapter.number}: audio not generated.")
            continue
        if qa_record is not None and qa_record.manual_status == QAManualStatus.FLAGGED:
            skipped_notes.append(f"Skipped chapter {chapter.number}: manually flagged during QA.")
            continue
        if include_only_approved and not approval_decision.approved:
            skipped_notes.append(f"Skipped chapter {chapter.number}: not QA approved.")
            continue

        selected.append(
            SelectedChapter(
                chapter_n=chapter.number,
                chapter_title=_chapter_display_title(chapter),
                chapter_type=chapter.type,
                audio_path=Path(chapter.audio_path),
                file_size_bytes=chapter.audio_file_size_bytes or 0,
                duration_seconds=chapter.duration_seconds or 0.0,
                qa_status=_chapter_effective_qa_status(chapter, qa_record),
                export_approved=approval_decision.approved,
                qa_soft_pass=approval_decision.soft_pass,
                qa_warnings=list(approval_decision.warnings),
            )
        )

    recovered_report = _build_qa_report(
        book=book,
        included_chapters=selected,
        qa_records=qa_records,
        skipped_notes=skipped_notes,
        additional_notes=["Recovered export metadata from existing export files."],
    )
    return recovered_report.model_copy(update={"export_date": export_date})


def _load_recovered_qa_report(
    db_session: Session,
    *,
    book: Book,
    include_only_approved: bool,
    export_date: datetime,
    stored_format_details: str | dict[str, Any] | None = None,
) -> QAReport | None:
    """Load an existing QA report from disk when possible, or rebuild a minimal replacement."""

    qa_report_path = _build_export_paths(book)["qa_report"]
    resolved_details = _resolved_format_details_payload(book, stored_format_details=stored_format_details)
    qa_report_artifact = _format_details_artifacts(resolved_details).get("qa_report")
    expected_qa_report_sha256 = (
        qa_report_artifact.get("sha256")
        if isinstance(qa_report_artifact, dict)
        else None
    )
    if expected_qa_report_sha256:
        if not qa_report_path.exists():
            logger.warning(
                "Refusing to recover QA report for book %s because the hashed artifact is missing",
                book.id,
            )
            return None

        if not _verify_checksum(qa_report_path, expected_qa_report_sha256):
            qa_report_path.unlink(missing_ok=True)
            logger.warning("Rebuilding QA report for book %s after checksum verification failed.", book.id)
            return _build_recovered_qa_report(
                db_session,
                book=book,
                include_only_approved=include_only_approved,
                export_date=export_date,
            )

    if qa_report_path.exists():
        try:
            return QAReport.model_validate_json(qa_report_path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, json.JSONDecodeError) as exc:
            if expected_qa_report_sha256:
                qa_report_path.unlink(missing_ok=True)
                logger.warning(
                    "Rebuilding QA report for book %s because the hashed artifact could not be parsed: %s",
                    book.id,
                    exc,
                )
                return _build_recovered_qa_report(
                    db_session,
                    book=book,
                    include_only_approved=include_only_approved,
                    export_date=export_date,
                )
            logger.warning("Failed to load recovered QA report for book %s: %s", book.id, exc)

    return _build_recovered_qa_report(
        db_session,
        book=book,
        include_only_approved=include_only_approved,
        export_date=export_date,
    )


def _build_qa_report(
    *,
    book: Book,
    included_chapters: list[SelectedChapter],
    qa_records: dict[int, ChapterQARecord],
    skipped_notes: list[str],
    additional_notes: list[str] | None = None,
) -> QAReport:
    """Return the QA report written during export."""

    chapter_summary = [
        QAChapterSummary(
            chapter_n=chapter.chapter_n,
            chapter_title=chapter.chapter_title,
            status=chapter.qa_status,
            file_size_bytes=chapter.file_size_bytes,
            duration_seconds=round(chapter.duration_seconds, 3),
            qa_soft_pass=chapter.qa_soft_pass,
            qa_warnings=list(chapter.qa_warnings),
        )
        for chapter in included_chapters
    ]
    chapters_flagged = sum(
        1 for record in qa_records.values() if record.manual_status == QAManualStatus.FLAGGED
    )
    chapters_warnings = sum(
        1
        for record in qa_records.values()
        if record.manual_status != QAManualStatus.FLAGGED
        and record.overall_status in {QAAutomaticStatus.WARNING, QAAutomaticStatus.FAIL}
    )
    chapters_approved = sum(1 for chapter in included_chapters if chapter.export_approved)

    notes = []
    if skipped_notes:
        notes.append(" ".join(skipped_notes))
    if chapters_flagged:
        notes.append(f"{chapters_flagged} chapters were flagged and excluded from export.")
    if chapters_warnings:
        notes.append(f"{chapters_warnings} chapters have QA warnings.")
    if additional_notes:
        notes.extend(additional_notes)
    if not notes:
        notes.append("All selected chapters exported without QA exclusions.")

    export_approved = all(chapter.export_approved for chapter in included_chapters)
    return QAReport(
        book_id=book.id,
        book_title=book.title,
        export_date=utc_now(),
        chapters_included=len(included_chapters),
        chapters_approved=chapters_approved,
        chapters_flagged=chapters_flagged,
        chapters_warnings=chapters_warnings,
        export_approved=export_approved,
        notes=" ".join(notes),
        chapter_summary=chapter_summary,
    )


def _empty_format_details(formats: list[str]) -> dict[str, ExportFormatResult]:
    """Return the initial per-format status map for a queued export."""

    return {export_format: ExportFormatResult(status="pending") for export_format in formats}


def _format_stage_label(
    action: str,
    export_format: str | None,
    *,
    current_chapter_n: int | None = None,
    total_chapters: int | None = None,
) -> str:
    """Return a human-readable export stage string."""

    if export_format is None:
        return action

    label = export_format.upper()
    if current_chapter_n is not None and total_chapters is not None:
        return f"{action} {label} (chapter {current_chapter_n}/{total_chapters})"
    return f"{action} {label}"


def _emit_progress(
    progress_callback: ExportProgressCallback | None,
    *,
    progress_percent: float,
    stage: str | None,
    export_format: str | None = None,
    current_chapter_n: int | None = None,
    total_chapters: int | None = None,
) -> None:
    """Invoke the export progress callback when present."""

    if progress_callback is None:
        return

    progress_callback(
        round(max(0.0, min(progress_percent, 100.0)), 2),
        stage,
        export_format,
        current_chapter_n,
        total_chapters,
    )


def export_book_sync(
    book_id: int,
    *,
    export_formats: list[str] | None = None,
    include_only_approved: bool = True,
    m4b_bitrate: str | None = None,
    session_factory: sessionmaker[Session] | None = None,
    progress_callback: ExportProgressCallback | None = None,
    should_abort: Callable[[], None] | None = None,
    export_job_id: int | None = None,
) -> ExportResult:
    """Synchronously export a completed book into MP3 and/or M4B formats."""

    formats = _normalize_export_formats(export_formats)
    session_factory = session_factory or SessionLocal

    def ensure_active() -> None:
        if should_abort is not None:
            should_abort()

    with session_factory() as db_session:
        book = db_session.query(Book).filter(Book.id == book_id).first()
        if book is None:
            raise ValueError(f"Book {book_id} not found")
        db_session.expunge(book)

    export_paths = _build_export_paths(book)
    temp_suffix = f".{uuid.uuid4().hex[:8]}.tmp"
    temp_paths = _build_temporary_export_paths(
        book,
        temp_suffix=temp_suffix,
    )
    export_paths["exports_root"].mkdir(parents=True, exist_ok=True)
    _register_export_temp_files(export_job_id, *temp_paths.values())
    cover_art_path = (
        _ensure_placeholder_cover(export_paths["exports_root"])
        if settings.EXPORT_INCLUDE_ALBUM_ART
        else None
    )

    format_results = _empty_format_details(formats)
    errors: list[str] = []
    lufs_notes: list[str] = []
    mastering_notes: list[str] = []
    concatenation: ConcatenationResult | None = None
    total_chapters = 0
    checkpoint_artifacts: dict[str, Any] = {}
    normalization_result = LoudnessNormalizationResult(measured_lufs=None, lufs_warning=None, attempts=0)
    state_payload = _load_export_state_payload(book)
    checkpoint_artifacts.update(_format_details_artifacts(state_payload.get("format_details")))
    request_options = _format_details_request_options(state_payload.get("format_details"))
    if m4b_bitrate:
        request_options["m4b_bitrate"] = m4b_bitrate
    resolved_m4b_bitrate = str(request_options.get("m4b_bitrate") or settings.EXPORT_M4B_BITRATE)
    if request_options:
        checkpoint_artifacts[FORMAT_DETAILS_REQUEST_OPTIONS_KEY] = request_options
    state_payload.update(
        {
            "book_id": book.id,
            "book_title": book.title,
            "export_job_id": export_job_id,
            "formats_requested": formats,
            "include_only_approved": include_only_approved,
            "export_status": BookExportStatus.PROCESSING.value,
            "current_stage": "Preparing export job",
            "current_format": None,
            "progress_percent": 0.0,
            "format_details": _serialize_format_details_payload(format_results, artifacts=checkpoint_artifacts),
            "updated_at": utc_now().isoformat(),
        }
    )
    _write_export_state_atomic(export_paths["state"], state_payload)

    def persist_checkpoint(**updates: Any) -> None:
        serialized_updates = dict(updates)
        serialized_updates.setdefault("updated_at", utc_now())
        for field_name, field_value in serialized_updates.items():
            state_payload[field_name] = _serialize_state_value(field_value)
        _write_export_state_atomic(export_paths["state"], state_payload)
        if export_job_id is None:
            return
        _persist_export_checkpoint(
            session_factory,
            export_job_id,
            serialized_updates,
        )

    _emit_progress(
        progress_callback,
        progress_percent=0.0,
        stage="Preparing export job",
    )
    ensure_active()
    _emit_progress(
        progress_callback,
        progress_percent=5.0,
        stage="Preparing export",
    )

    def report_mastering_progress(phase: str, current_index: int, chapter_count: int, chapter: Chapter) -> None:
        ensure_active()
        chapter_total = max(chapter_count, 1)
        chapter_label = current_index if current_index > 0 else 0
        if phase == "mastering":
            progress_percent = 5.0 + ((chapter_label / chapter_total) * 15.0)
            stage = _format_stage_label(
                "Mastering chapters",
                None,
                current_chapter_n=chapter_label,
                total_chapters=chapter_count,
            )
        else:
            progress_percent = 20.0 + ((chapter_label / chapter_total) * 10.0)
            stage = _format_stage_label(
                "Running QA analysis",
                None,
                current_chapter_n=chapter_label,
                total_chapters=chapter_count,
            )
        _emit_progress(
            progress_callback,
            progress_percent=progress_percent,
            stage=stage,
            current_chapter_n=chapter.number,
            total_chapters=chapter_count,
        )

    mastering = BookMasteringPipeline()
    with session_factory() as mastering_session:
        _emit_progress(
            progress_callback,
            progress_percent=5.0,
            stage="Mastering chapters",
        )
        mastering_report = mastering.master_book_sync(
            book_id,
            mastering_session,
            prefer_fast_chain=True,
            export_mode=True,
            progress_callback=report_mastering_progress,
            session_factory=session_factory,
        )
    if mastering_report.loudness_adjustments:
        mastering_notes.append(
            f"Mastering leveled {len(mastering_report.loudness_adjustments)} chapters toward -20 LUFS."
        )
    if mastering_report.edge_normalized_chapters:
        mastering_notes.append(
            f"Mastering normalized chapter edge silence for {len(mastering_report.edge_normalized_chapters)} chapters."
        )
    if mastering_report.peak_limited_chapters:
        mastering_notes.append(
            f"Mastering peak-limited {len(mastering_report.peak_limited_chapters)} chapters."
        )
    mastering_notes.extend(mastering_report.notes)
    if mastering_report.has_blockers:
        raise ExportBlockedError(
            "Mastering found blocking issues: " + "; ".join(mastering_report.blockers)
        )
    persist_checkpoint(
        current_stage="Mastering complete",
        progress_percent=30.0,
        current_format=None,
    )
    _emit_progress(
        progress_callback,
        progress_percent=30.0,
        stage="Mastering complete",
    )

    try:
        def report_concatenation_progress(current_chapter_n: int, chapter_count: int) -> None:
            ensure_active()
            _emit_progress(
                progress_callback,
                progress_percent=30.0 + ((current_chapter_n / max(chapter_count, 1)) * 20.0),
                stage=_format_stage_label(
                    "Concatenating chapters",
                    None,
                    current_chapter_n=current_chapter_n,
                    total_chapters=chapter_count,
                ),
                current_chapter_n=current_chapter_n,
                total_chapters=chapter_count,
            )

        concatenation = concatenate_chapters_sync(
            book_id,
            include_only_approved=include_only_approved,
            chapter_silence_seconds=settings.EXPORT_CHAPTER_SILENCE_SECONDS,
            opening_silence_seconds=settings.EXPORT_OPENING_SILENCE_SECONDS,
            closing_silence_seconds=settings.EXPORT_CLOSING_SILENCE_SECONDS,
            session_factory=session_factory,
            progress_callback=report_concatenation_progress,
            master_wav_path=temp_paths["master_wav"],
        )
        ensure_active()
        total_chapters = len(concatenation.included_chapters)
        checkpoint_artifacts["master_wav_hash"] = _file_sha256(concatenation.master_wav_path)
        expected_duration_seconds = (
            concatenation.chapter_markers[-1].end_ms / 1000.0
            if concatenation.chapter_markers
            else sum(chapter.duration_seconds for chapter in concatenation.included_chapters)
        )
        normalization_result = normalize_loudness(
            concatenation.master_wav_path,
            temp_paths["normalized_wav"],
            target_lufs=settings.EXPORT_TARGET_LUFS,
            duration_seconds=expected_duration_seconds,
        )
        if not isinstance(normalization_result, LoudnessNormalizationResult):
            normalization_result = LoudnessNormalizationResult(
                measured_lufs=None,
                lufs_warning=None,
                attempts=0,
            )
        if normalization_result.lufs_warning:
            lufs_notes.append(normalization_result.lufs_warning)
        persist_checkpoint(
            current_stage="Concatenation complete",
            progress_percent=50.0,
            current_format=None,
            current_chapter_n=total_chapters or None,
            total_chapters=total_chapters or None,
            format_details=_serialize_format_details_payload(
                format_results,
                artifacts=checkpoint_artifacts,
            ),
        )
        _emit_progress(
            progress_callback,
            progress_percent=50.0,
            stage="Concatenation complete",
            current_chapter_n=total_chapters or None,
            total_chapters=total_chapters or None,
        )

        encoded_outputs: dict[str, Path] = {}
        encoded_artifacts: dict[str, EncodedExportArtifact] = {}
        for format_index, export_format in enumerate(formats):
            ensure_active()
            encode_start = 50.0 + ((format_index / max(len(formats), 1)) * 30.0)
            encode_end = 50.0 + (((format_index + 1) / max(len(formats), 1)) * 30.0)
            output_path = export_paths["mp3"] if export_format == "mp3" else export_paths["m4b"]
            _emit_progress(
                progress_callback,
                progress_percent=encode_start,
                stage=_format_stage_label(
                    "Encoding",
                    export_format,
                    current_chapter_n=total_chapters or None,
                    total_chapters=total_chapters or None,
                ),
                export_format=export_format,
                current_chapter_n=total_chapters or None,
                total_chapters=total_chapters or None,
            )
            format_results[export_format] = ExportFormatResult(
                status="processing",
                file_name=output_path.name,
                file_size_bytes=output_path.stat().st_size if output_path.exists() else None,
                measured_lufs=normalization_result.measured_lufs,
                lufs_warning=normalization_result.lufs_warning,
            )

            try:
                if export_format == "mp3":
                    encoded_artifact = export_mp3(
                        temp_paths["normalized_wav"],
                        export_paths["mp3"],
                        book=book,
                        cover_art_path=cover_art_path,
                    )
                else:
                    encoded_artifact = export_m4b(
                        temp_paths["normalized_wav"],
                        export_paths["m4b"],
                        book=book,
                        chapter_markers=concatenation.chapter_markers,
                        metadata_path=temp_paths["metadata"],
                        bitrate=resolved_m4b_bitrate,
                    )
                encoded_artifacts[export_format] = encoded_artifact
                encoded_outputs[export_format] = output_path
                format_results[export_format] = ExportFormatResult(
                    status="encoded",
                    file_size_bytes=encoded_artifact.file_size_bytes,
                    sha256=encoded_artifact.sha256,
                    file_name=output_path.name,
                    attempts=0,
                    measured_lufs=normalization_result.measured_lufs,
                    lufs_warning=normalization_result.lufs_warning,
                )
                persist_checkpoint(
                    current_stage=_format_stage_label("Encoded", export_format),
                    progress_percent=encode_end,
                    current_format=export_format,
                    current_chapter_n=total_chapters or None,
                    total_chapters=total_chapters or None,
                    format_details=_serialize_format_details_payload(
                        format_results,
                        artifacts=checkpoint_artifacts,
                    ),
                )
                _emit_progress(
                    progress_callback,
                    progress_percent=encode_end,
                    stage=_format_stage_label("Encoded", export_format),
                    export_format=export_format,
                    current_chapter_n=total_chapters or None,
                    total_chapters=total_chapters or None,
                )
            except Exception as exc:
                logger.exception("Failed to export %s for book %s", export_format, book.id)
                errors.append(f"{export_format}: {exc}")
                format_results[export_format] = ExportFormatResult(
                    status="error",
                    error_message=str(exc),
                    file_name=output_path.name,
                    file_size_bytes=output_path.stat().st_size if output_path.exists() else None,
                    measured_lufs=normalization_result.measured_lufs,
                    lufs_warning=normalization_result.lufs_warning,
                )
                persist_checkpoint(
                    current_stage=_format_stage_label("Encoding failed", export_format),
                    progress_percent=encode_end,
                    current_format=export_format,
                    current_chapter_n=total_chapters or None,
                    total_chapters=total_chapters or None,
                    format_details=_serialize_format_details_payload(
                        format_results,
                        artifacts=checkpoint_artifacts,
                    ),
                )

        verifiable_formats = [export_format for export_format in formats if export_format in encoded_outputs]
        for verify_index, export_format in enumerate(verifiable_formats):
            ensure_active()
            output_path = encoded_outputs[export_format]
            verify_start = 80.0 + ((verify_index / max(len(verifiable_formats), 1)) * 15.0)
            verify_end = 80.0 + (((verify_index + 1) / max(len(verifiable_formats), 1)) * 15.0)
            _emit_progress(
                progress_callback,
                progress_percent=verify_start,
                stage=_format_stage_label("Verifying output", export_format),
                export_format=export_format,
                current_chapter_n=total_chapters or None,
                total_chapters=total_chapters or None,
            )

            verification: dict[str, Any] | None = None
            attempts = 0
            for attempts in range(1, 3):
                verification = _verify_export_output(
                    output_path,
                    expected_duration_seconds=expected_duration_seconds,
                    export_format=export_format,
                    expected_markers=concatenation.chapter_markers if export_format == "m4b" else None,
                )
                if verification.get("ok"):
                    break

                logger.warning(
                    "Export verification failed for %s on attempt %s: %s",
                    output_path,
                    attempts,
                    "; ".join(verification.get("issues", [])),
                )
                if attempts >= 2:
                    break

                ensure_active()
                if export_format == "mp3":
                    encoded_artifacts[export_format] = export_mp3(
                        temp_paths["normalized_wav"],
                        export_paths["mp3"],
                        book=book,
                        cover_art_path=cover_art_path,
                    )
                else:
                    encoded_artifacts[export_format] = export_m4b(
                        temp_paths["normalized_wav"],
                        export_paths["m4b"],
                        book=book,
                        chapter_markers=concatenation.chapter_markers,
                        metadata_path=temp_paths["metadata"],
                        bitrate=resolved_m4b_bitrate,
                    )

            if verification is None or not verification.get("ok"):
                error_message = (
                    "Export verification failed: "
                    + "; ".join((verification or {}).get("issues", ["unknown verification error"]))
                )
                logger.error("Keeping %s on disk despite verification failure: %s", output_path, error_message)
                errors.append(f"{export_format}: {error_message}")
                format_results[export_format] = ExportFormatResult(
                    status="error",
                    file_size_bytes=output_path.stat().st_size if output_path.exists() else None,
                    sha256=encoded_artifacts.get(export_format).sha256 if export_format in encoded_artifacts else None,
                    file_name=output_path.name,
                    error_message=error_message,
                    verification=verification,
                    attempts=attempts,
                    measured_lufs=normalization_result.measured_lufs,
                    lufs_warning=normalization_result.lufs_warning,
                )
                persist_checkpoint(
                    current_stage=_format_stage_label("Verification failed", export_format),
                    progress_percent=verify_end,
                    current_format=export_format,
                    current_chapter_n=total_chapters or None,
                    total_chapters=total_chapters or None,
                    format_details=_serialize_format_details_payload(
                        format_results,
                        artifacts=checkpoint_artifacts,
                    ),
                )
                _emit_progress(
                    progress_callback,
                    progress_percent=verify_end,
                    stage=_format_stage_label("Verification failed", export_format),
                    export_format=export_format,
                    current_chapter_n=total_chapters or None,
                    total_chapters=total_chapters or None,
                )
                continue

            noise_floor_dbfs = _measure_noise_floor(output_path, duration_seconds=expected_duration_seconds)
            noise_floor_warning: str | None = None
            noise_floor_compliant: bool | None = None
            if noise_floor_dbfs is None:
                noise_floor_warning = (
                    "Noise floor measurement timed out after "
                    f"{_format_timeout_seconds(_resolve_ffmpeg_timeout(expected_duration_seconds))}. "
                    "Manual verification recommended."
                )
                logger.warning("%s", noise_floor_warning)
            else:
                noise_floor_compliant = noise_floor_dbfs <= -60.0
            if noise_floor_compliant is False:
                logger.warning("Noise floor %s dBFS exceeds ACX limit of -60 dBFS", noise_floor_dbfs)

            format_results[export_format] = ExportFormatResult(
                status="completed",
                file_size_bytes=encoded_artifacts[export_format].file_size_bytes,
                sha256=encoded_artifacts[export_format].sha256,
                file_name=output_path.name,
                download_url=f"/api/book/{book.id}/export/download/{export_format}",
                completed_at=utc_now(),
                verification=verification,
                attempts=attempts,
                measured_lufs=normalization_result.measured_lufs,
                lufs_warning=normalization_result.lufs_warning,
                noise_floor_dbfs=noise_floor_dbfs,
                noise_floor_compliant=noise_floor_compliant,
                noise_floor_warning=noise_floor_warning,
            )
            persist_checkpoint(
                current_stage=_format_stage_label("Verified", export_format),
                progress_percent=verify_end,
                current_format=export_format,
                current_chapter_n=total_chapters or None,
                total_chapters=total_chapters or None,
                format_details=_serialize_format_details_payload(
                    format_results,
                    artifacts=checkpoint_artifacts,
                ),
            )
            _emit_progress(
                progress_callback,
                progress_percent=verify_end,
                stage=_format_stage_label("Verified", export_format),
                export_format=export_format,
                current_chapter_n=total_chapters or None,
                total_chapters=total_chapters or None,
            )
            loudness_result = check_lufs_compliance(output_path)
            if loudness_result.status != QAAutomaticStatus.PASS.value:
                logger.warning(
                    "Export %s for book %s loudness check returned %s: %s",
                    export_format,
                    book.id,
                    loudness_result.status,
                    loudness_result.message,
                )
                lufs_notes.append(f"{output_path.name}: {loudness_result.message}")
            if noise_floor_warning:
                lufs_notes.append(f"{output_path.name}: {noise_floor_warning}")
            elif noise_floor_compliant is False:
                lufs_notes.append(
                    f"{output_path.name}: noise floor {noise_floor_dbfs:.1f} dBFS exceeds ACX limit of -60 dBFS."
                )
    finally:
        for temporary_path in temp_paths.values():
            temporary_path.unlink(missing_ok=True)
        _discard_export_temp_files(export_job_id)

    qa_report = _build_qa_report(
        book=book,
        included_chapters=concatenation.included_chapters,
        qa_records=concatenation.qa_records,
        skipped_notes=concatenation.skipped_notes,
        additional_notes=[*mastering_notes, *lufs_notes],
    )
    _emit_progress(
        progress_callback,
        progress_percent=95.0,
        stage="Finalizing",
        current_chapter_n=total_chapters or None,
        total_chapters=total_chapters or None,
    )
    _write_json_atomic(export_paths["qa_report"], qa_report.model_dump(mode="json"), temp_suffix=temp_suffix)
    checkpoint_artifacts["qa_report"] = {
        "file_name": export_paths["qa_report"].name,
        "file_size_bytes": export_paths["qa_report"].stat().st_size,
        "sha256": _file_sha256(export_paths["qa_report"]),
    }
    persist_checkpoint(
        current_stage="Finalizing",
        progress_percent=95.0,
        current_format=None,
        current_chapter_n=total_chapters or None,
        total_chapters=total_chapters or None,
        format_details=_serialize_format_details_payload(
            format_results,
            artifacts=checkpoint_artifacts,
        ),
        qa_report=qa_report,
    )

    final_stage = "Ready" if not errors else "Export completed with errors"
    final_status = BookExportStatus.COMPLETED.value if not errors else BookExportStatus.ERROR.value
    persist_checkpoint(
        current_stage=final_stage,
        progress_percent=100.0 if not errors else max(95.0, 100.0 - (100.0 / max(len(formats), 1))),
        current_format=None,
        current_chapter_n=total_chapters or None,
        total_chapters=total_chapters or None,
        format_details=_serialize_format_details_payload(
            format_results,
            artifacts=checkpoint_artifacts,
        ),
        qa_report=qa_report,
        export_status=final_status,
    )
    _emit_progress(
        progress_callback,
        progress_percent=100.0 if not errors else max(95.0, 100.0 - (100.0 / max(len(formats), 1))),
        stage=final_stage,
        current_chapter_n=total_chapters or None,
        total_chapters=total_chapters or None,
    )

    return ExportResult(
        book_id=book.id,
        export_status=final_status,
        formats=format_results,
        qa_report=qa_report,
    )


async def export_book(
    book_id: int,
    export_formats: list[str] | None = None,
    include_only_approved: bool = True,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> ExportResult:
    """Async wrapper for the export pipeline."""

    return await asyncio.to_thread(
        export_book_sync,
        book_id,
        export_formats=export_formats,
        include_only_approved=include_only_approved,
        session_factory=session_factory,
    )


def estimate_export_seconds(
    book_id: int,
    *,
    export_formats: list[str] | None = None,
    include_only_approved: bool = True,
    session_factory: sessionmaker[Session] | None = None,
) -> int:
    """Estimate export duration for the selected formats."""

    formats = _normalize_export_formats(export_formats)
    session_factory = session_factory or SessionLocal

    with session_factory() as db_session:
        book = db_session.query(Book).filter(Book.id == book_id).first()
        if book is None:
            raise ValueError(f"Book {book_id} not found")

        chapters = (
            db_session.query(Chapter)
            .filter(Chapter.book_id == book.id)
            .order_by(Chapter.number, Chapter.id)
            .all()
        )
        qa_records = {
            record.chapter_n: record
            for record in db_session.query(ChapterQARecord).filter(ChapterQARecord.book_id == book.id).all()
        }
        selected_durations = [
            chapter.duration_seconds or max((chapter.word_count or 0) / 2.5, 1.0)
            for chapter in chapters
            if chapter.status == ChapterStatus.GENERATED
            and chapter.audio_path
            and (
                not include_only_approved
                or _chapter_is_approved(chapter, qa_records.get(chapter.number))
            )
        ]
        if not selected_durations:
            raise ValueError("No chapter audio is eligible for export.")

        total_duration = sum(selected_durations)
        estimated_seconds = int(round(total_duration / 30.0)) + (8 * len(formats)) + 6
        return max(5, estimated_seconds)


def run_export_job_sync(export_job_id: int, session_factory: sessionmaker[Session] | None = None) -> None:
    """Execute an export job and persist the terminal result back to the database."""

    session_factory = session_factory or SessionLocal

    @retry_on_locked(max_retries=5, backoff_ms=250)
    def ensure_job_active() -> None:
        with session_factory() as active_session:
            active_job = active_session.query(ExportJob).filter(ExportJob.id == export_job_id).first()
            if active_job is None or active_job.export_status != BookExportStatus.PROCESSING:
                raise ExportCancelledError("Export job was cancelled.")

    @retry_on_locked(max_retries=5, backoff_ms=250)
    def persist_progress(
        progress_percent: float,
        stage: str | None,
        export_format: str | None,
        current_chapter_n: int | None,
        total_chapters: int | None,
    ) -> None:
        with session_factory() as progress_session:
            progress_job = progress_session.query(ExportJob).filter(ExportJob.id == export_job_id).first()
            if progress_job is None or progress_job.export_status != BookExportStatus.PROCESSING:
                raise ExportCancelledError("Export job was cancelled.")

            progress_job.progress_percent = round(max(0.0, min(progress_percent, 100.0)), 2)
            progress_job.current_stage = stage
            progress_job.current_format = export_format
            progress_job.current_chapter_n = current_chapter_n
            progress_job.total_chapters = total_chapters
            progress_job.updated_at = utc_now()
            if export_format is not None:
                format_details = _load_format_details_payload(progress_job.format_details)
                current_details = format_details.get(export_format, {})
                if not isinstance(current_details, dict):
                    current_details = {}
                if current_details.get("status") in {None, "pending", "processing"}:
                    current_details["status"] = "processing"
                format_details[export_format] = current_details
                progress_job.format_details = json.dumps(format_details)
            progress_session.commit()

    with session_factory() as db_session:
        export_job = db_session.query(ExportJob).filter(ExportJob.id == export_job_id).first()
        if export_job is None:
            return

        book = db_session.query(Book).filter(Book.id == export_job.book_id).first()
        if book is None:
            return

        book_id = export_job.book_id
        formats_requested = json.loads(export_job.formats_requested)
        include_only_approved = export_job.include_only_approved
        request_options = _format_details_request_options(export_job.format_details)
        requested_m4b_bitrate = request_options.get("m4b_bitrate")
        existing_artifacts = _format_details_artifacts(export_job.format_details)

        export_job.export_status = BookExportStatus.PROCESSING
        export_job.started_at = utc_now()
        export_job.completed_at = None
        export_job.updated_at = utc_now()
        export_job.error_message = None
        export_job.qa_report = None
        export_job.progress_percent = 0.0
        export_job.current_stage = "Preparing export job"
        export_job.current_format = None
        export_job.current_chapter_n = None
        export_job.total_chapters = None
        export_job.format_details = json.dumps(
            _serialize_format_details_payload(
                _empty_format_details(formats_requested),
                artifacts=existing_artifacts,
            )
        )
        book.export_status = BookExportStatus.PROCESSING
        db_session.commit()

    try:
        export_kwargs: dict[str, object] = {
            "export_formats": formats_requested,
            "include_only_approved": include_only_approved,
            "session_factory": session_factory,
            "progress_callback": persist_progress,
            "should_abort": ensure_job_active,
            "export_job_id": export_job_id,
        }
        if isinstance(requested_m4b_bitrate, str):
            export_kwargs["m4b_bitrate"] = requested_m4b_bitrate

        result = export_book_sync(
            book_id,
            **export_kwargs,
        )
    except ExportCancelledError:
        logger.info("Export job %s cancelled before completion", export_job_id)
        return
    except Exception as exc:
        logger.exception("Export job %s failed", export_job_id)
        with session_factory() as db_session:
            failed_job = db_session.query(ExportJob).filter(ExportJob.id == export_job_id).first()
            failed_book = db_session.query(Book).filter(Book.id == book_id).first()

            if failed_job is not None:
                failed_job.export_status = BookExportStatus.ERROR
                failed_job.completed_at = utc_now()
                failed_job.updated_at = failed_job.completed_at
                failed_job.error_message = str(exc)
                failed_job.current_stage = "Export failed"
            if failed_book is not None:
                failed_book.export_status = BookExportStatus.ERROR
            db_session.commit()
        return

    with session_factory() as db_session:
        completed_job = db_session.query(ExportJob).filter(ExportJob.id == export_job_id).first()
        completed_book = db_session.query(Book).filter(Book.id == result.book_id).first()
        if completed_job is None or completed_book is None:
            return
        if completed_job.export_status != BookExportStatus.PROCESSING:
            logger.info("Skipping terminal update for export job %s because it is no longer active", export_job_id)
            return

        completed_at = utc_now()
        completed_job.export_status = BookExportStatus(result.export_status)
        completed_job.completed_at = completed_at
        completed_job.updated_at = completed_at
        completed_job.error_message = "; ".join(
            format_result.error_message
            for format_result in result.formats.values()
            if format_result.error_message
        ) or None
        completed_job.progress_percent = 100.0 if result.export_status == BookExportStatus.COMPLETED.value else completed_job.progress_percent
        completed_job.current_stage = "Ready" if result.export_status == BookExportStatus.COMPLETED.value else "Export completed with errors"
        completed_job.current_format = None
        completed_job.current_chapter_n = completed_job.total_chapters
        completed_job.format_details = json.dumps(
            _serialize_format_details_payload(
                result.formats,
                artifacts=_format_details_artifacts(completed_job.format_details),
            )
        )
        completed_job.qa_report = result.qa_report.model_dump_json()

        completed_book.export_status = BookExportStatus(result.export_status)
        if completed_job.export_status == BookExportStatus.COMPLETED:
            completed_book.last_export_date = completed_at
            completed_book.status = BookStatus.EXPORTED

        db_session.commit()


async def run_export_job(
    export_job_id: int,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> None:
    """Async wrapper for export job execution."""

    await asyncio.to_thread(
        run_export_job_sync,
        export_job_id,
        session_factory=session_factory,
    )
