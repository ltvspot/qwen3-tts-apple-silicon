"""Audiobook export pipeline for MP3 and M4B outputs."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
import wave
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel
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


class ExportFormatResult(BaseModel):
    """Serialized result for one export format."""

    status: str
    file_size_bytes: int | None = None
    file_name: str | None = None
    download_url: str | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    verification: dict[str, Any] | None = None
    attempts: int = 0


class QAChapterSummary(BaseModel):
    """Per-chapter QA summary stored with an export job."""

    chapter_n: int
    chapter_title: str
    status: str
    file_size_bytes: int
    duration_seconds: float


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


@dataclass(slots=True)
class ChapterMarker:
    """Timeline marker for a chapter in an M4B container."""

    title: str
    start_ms: int
    end_ms: int


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
    loudness_adjustment_db: float = 0.0


@dataclass(slots=True)
class ConcatenationResult:
    """Intermediate export assembly output."""

    master_wav_path: Path
    chapter_markers: list[ChapterMarker]
    included_chapters: list[SelectedChapter]
    skipped_notes: list[str]
    qa_records: dict[int, ChapterQARecord]


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

    if qa_record is None or not qa_record.qa_details:
        return None

    try:
        qa_details = json.loads(qa_record.qa_details)
    except json.JSONDecodeError:
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


def _chapter_is_approved(chapter: Chapter, qa_record: ChapterQARecord | None) -> bool:
    """Return True when a chapter is eligible for approval-only exports."""

    if qa_record is not None:
        if qa_record.manual_status == QAManualStatus.FLAGGED:
            return False
        if qa_record.manual_status == QAManualStatus.APPROVED:
            return True
        report_ready = _chapter_report_ready_for_export(qa_record)
        if report_ready is not None:
            return report_ready
        return qa_record.overall_status == QAAutomaticStatus.PASS

    return chapter.qa_status == QAStatus.APPROVED


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
                export_approved=_chapter_is_approved(chapter, qa_record),
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
        master_wav_path = exports_root / "master.wav"
        chapter_markers = _concatenate_chapters_streaming(
            selected,
            master_wav_path=master_wav_path,
            chapter_silence_seconds=chapter_silence_seconds,
            opening_silence_seconds=opening_silence_seconds,
            closing_silence_seconds=closing_silence_seconds,
            progress_callback=progress_callback,
        )
        return ConcatenationResult(
            master_wav_path=master_wav_path,
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


def normalize_loudness(
    input_wav: Path,
    output_wav: Path,
    target_lufs: float = -19.0,
) -> None:
    """Normalize audio to a target LUFS value using ffmpeg loudnorm."""

    ffmpeg_path = _require_ffmpeg()
    command = [
        ffmpeg_path,
        "-y",
        "-i",
        str(input_wav),
        "-af",
        f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
        str(output_wav),
    ]
    run_ffmpeg(command)


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
) -> None:
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


def export_m4b(
    normalized_wav_path: Path,
    output_path: Path,
    *,
    book: Book,
    chapter_markers: list[ChapterMarker],
    metadata_path: Path,
) -> None:
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
        settings.EXPORT_M4B_BITRATE,
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


def _probe_media(path: Path) -> dict[str, Any]:
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
        probe = _probe_media(output_path)
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
        "qa_report": exports_root / "qa_report.json",
        "mp3": exports_root / f"{safe_title}.mp3",
        "m4b": exports_root / f"{safe_title}.m4b",
    }


def get_export_output_path(book: Book, export_format: str) -> Path:
    """Return the stable output path for a requested export format."""

    if export_format not in VALID_EXPORT_FORMATS:
        raise ValueError(f"Unsupported export format: {export_format}")
    return _build_export_paths(book)[export_format]


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
    session_factory: sessionmaker[Session] | None = None,
    progress_callback: ExportProgressCallback | None = None,
) -> ExportResult:
    """Synchronously export a completed book into MP3 and/or M4B formats."""

    formats = _normalize_export_formats(export_formats)
    session_factory = session_factory or SessionLocal

    with session_factory() as db_session:
        book = db_session.query(Book).filter(Book.id == book_id).first()
        if book is None:
            raise ValueError(f"Book {book_id} not found")

        export_paths = _build_export_paths(book)
        export_paths["exports_root"].mkdir(parents=True, exist_ok=True)
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

        _emit_progress(
            progress_callback,
            progress_percent=0.0,
            stage="Preparing export job",
        )

        mastering = BookMasteringPipeline()
        mastering_report = mastering.master_book_sync(book_id, db_session)
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

        try:
            for format_index, export_format in enumerate(formats):
                format_share = 100.0 / max(len(formats), 1)
                format_start = format_index * format_share
                stitch_end = format_start + (format_share * 0.8)
                format_end = format_start + format_share

                if concatenation is None:
                    def report_concatenation_progress(current_chapter_n: int, chapter_count: int) -> None:
                        _emit_progress(
                            progress_callback,
                            progress_percent=format_start + ((current_chapter_n / max(chapter_count, 1)) * (format_share * 0.8)),
                            stage=_format_stage_label(
                                "Stitching",
                                export_format,
                                current_chapter_n=current_chapter_n,
                                total_chapters=chapter_count,
                            ),
                            export_format=export_format,
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
                    )
                    total_chapters = len(concatenation.included_chapters)
                    normalize_loudness(
                        concatenation.master_wav_path,
                        export_paths["normalized_wav"],
                        target_lufs=settings.EXPORT_TARGET_LUFS,
                    )
                else:
                    _emit_progress(
                        progress_callback,
                        progress_percent=stitch_end,
                        stage=_format_stage_label(
                            "Stitching",
                            export_format,
                            current_chapter_n=total_chapters or None,
                            total_chapters=total_chapters or None,
                        ),
                        export_format=export_format,
                        current_chapter_n=total_chapters or None,
                        total_chapters=total_chapters or None,
                    )

                _emit_progress(
                    progress_callback,
                    progress_percent=stitch_end,
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

                try:
                    output_path = export_paths["mp3"] if export_format == "mp3" else export_paths["m4b"]
                    verification: dict[str, Any] | None = None
                    attempts = 0
                    expected_duration_seconds = (
                        concatenation.chapter_markers[-1].end_ms / 1000.0
                        if concatenation.chapter_markers
                        else sum(chapter.duration_seconds for chapter in concatenation.included_chapters)
                    )

                    for attempts in range(1, 3):
                        if export_format == "mp3":
                            export_mp3(
                                export_paths["normalized_wav"],
                                export_paths["mp3"],
                                book=book,
                                cover_art_path=cover_art_path,
                            )
                        else:
                            export_m4b(
                                export_paths["normalized_wav"],
                                export_paths["m4b"],
                                book=book,
                                chapter_markers=concatenation.chapter_markers,
                                metadata_path=export_paths["metadata"],
                            )

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
                    if verification is None or not verification.get("ok"):
                        raise RuntimeError(
                            "Export verification failed: "
                            + "; ".join((verification or {}).get("issues", ["unknown verification error"]))
                        )

                    format_results[export_format] = ExportFormatResult(
                        status="completed",
                        file_size_bytes=output_path.stat().st_size,
                        file_name=output_path.name,
                        download_url=f"/api/book/{book.id}/export/download/{export_format}",
                        completed_at=utc_now(),
                        verification=verification,
                        attempts=attempts,
                    )
                    _emit_progress(
                        progress_callback,
                        progress_percent=format_end,
                        stage=_format_stage_label("Completed", export_format),
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
                except Exception as exc:
                    logger.exception("Failed to export %s for book %s", export_format, book.id)
                    errors.append(f"{export_format}: {exc}")
                    format_results[export_format] = ExportFormatResult(
                        status="error",
                        error_message=str(exc),
                    )
                    _emit_progress(
                        progress_callback,
                        progress_percent=max(format_start, min(stitch_end, format_end)),
                        stage=_format_stage_label("Failed", export_format),
                        export_format=export_format,
                        current_chapter_n=total_chapters or None,
                        total_chapters=total_chapters or None,
                    )
        finally:
            for temporary_path in (
                export_paths["master_wav"],
                export_paths["normalized_wav"],
                export_paths["metadata"],
            ):
                if temporary_path.exists():
                    temporary_path.unlink()

        qa_report = _build_qa_report(
            book=book,
            included_chapters=concatenation.included_chapters,
            qa_records=concatenation.qa_records,
            skipped_notes=concatenation.skipped_notes,
            additional_notes=[*mastering_notes, *lufs_notes],
        )
        export_paths["qa_report"].write_text(
            json.dumps(qa_report.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

        _emit_progress(
            progress_callback,
            progress_percent=100.0 if not errors else max(0.0, 100.0 - (100.0 / max(len(formats), 1))),
            stage="Ready" if not errors else "Export completed with errors",
            current_chapter_n=total_chapters or None,
            total_chapters=total_chapters or None,
        )

        return ExportResult(
            book_id=book.id,
            export_status=BookExportStatus.COMPLETED.value if not errors else BookExportStatus.ERROR.value,
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

        selected, _, _ = _load_selected_chapters(
            db_session,
            book,
            include_only_approved=include_only_approved,
        )
        if not selected:
            raise ValueError("No chapter audio is eligible for export.")

        total_duration = sum(chapter.duration_seconds for chapter in selected)
        estimated_seconds = int(round(total_duration / 30.0)) + (8 * len(formats)) + 6
        return max(5, estimated_seconds)


def run_export_job_sync(export_job_id: int, session_factory: sessionmaker[Session] | None = None) -> None:
    """Execute an export job and persist the terminal result back to the database."""

    session_factory = session_factory or SessionLocal

    def persist_progress(
        progress_percent: float,
        stage: str | None,
        export_format: str | None,
        current_chapter_n: int | None,
        total_chapters: int | None,
    ) -> None:
        with session_factory() as progress_session:
            progress_job = progress_session.query(ExportJob).filter(ExportJob.id == export_job_id).first()
            if progress_job is None:
                return

            progress_job.progress_percent = round(max(0.0, min(progress_percent, 100.0)), 2)
            progress_job.current_stage = stage
            progress_job.current_format = export_format
            progress_job.current_chapter_n = current_chapter_n
            progress_job.total_chapters = total_chapters
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

        export_job.export_status = BookExportStatus.PROCESSING
        export_job.started_at = utc_now()
        export_job.completed_at = None
        export_job.error_message = None
        export_job.qa_report = None
        export_job.progress_percent = 0.0
        export_job.current_stage = "Preparing export job"
        export_job.current_format = None
        export_job.current_chapter_n = None
        export_job.total_chapters = None
        export_job.format_details = json.dumps(
            {
                name: result.model_dump(mode="json")
                for name, result in _empty_format_details(formats_requested).items()
            }
        )
        book.export_status = BookExportStatus.PROCESSING
        db_session.commit()

    try:
        result = export_book_sync(
            book_id,
            export_formats=formats_requested,
            include_only_approved=include_only_approved,
            session_factory=session_factory,
            progress_callback=persist_progress,
        )
    except Exception as exc:
        logger.exception("Export job %s failed", export_job_id)
        with session_factory() as db_session:
            failed_job = db_session.query(ExportJob).filter(ExportJob.id == export_job_id).first()
            failed_book = db_session.query(Book).filter(Book.id == book_id).first()

            if failed_job is not None:
                failed_job.export_status = BookExportStatus.ERROR
                failed_job.completed_at = utc_now()
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

        completed_at = utc_now()
        completed_job.export_status = BookExportStatus(result.export_status)
        completed_job.completed_at = completed_at
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
            {name: format_result.model_dump(mode="json") for name, format_result in result.formats.items()}
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
