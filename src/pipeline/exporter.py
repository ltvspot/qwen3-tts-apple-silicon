"""Audiobook export pipeline for MP3 and M4B outputs."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
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

logger = logging.getLogger(__name__)

DEFAULT_EXPORT_FORMATS = ("mp3", "m4b")
VALID_EXPORT_FORMATS = frozenset(DEFAULT_EXPORT_FORMATS)
EXPORT_SAMPLE_RATE = 44100


class ExportFormatResult(BaseModel):
    """Serialized result for one export format."""

    status: str
    file_size_bytes: int | None = None
    file_name: str | None = None
    download_url: str | None = None
    completed_at: datetime | None = None
    error_message: str | None = None


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
    audio_segment: AudioSegment
    file_size_bytes: int
    duration_seconds: float
    qa_status: str
    export_approved: bool


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
        subprocess.run(
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
            check=True,
            capture_output=True,
            text=True,
        )
    return cover_path


def create_silence(duration_seconds: float, sample_rate: int = EXPORT_SAMPLE_RATE) -> AudioSegment:
    """Generate a mono silence segment at the requested sample rate."""

    return AudioSegment.silent(duration=int(duration_seconds * 1000), frame_rate=sample_rate).set_channels(1)


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


def _chapter_is_approved(chapter: Chapter, qa_record: ChapterQARecord | None) -> bool:
    """Return True when a chapter is eligible for approval-only exports."""

    if qa_record is not None:
        if qa_record.manual_status == QAManualStatus.FLAGGED:
            return False
        if qa_record.manual_status == QAManualStatus.APPROVED:
            return True
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
                .set_frame_rate(EXPORT_SAMPLE_RATE)
                .set_channels(1)
            )
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
                audio_segment=audio_segment,
                file_size_bytes=audio_path.stat().st_size,
                duration_seconds=round(len(audio_segment) / 1000.0, 3),
                qa_status=_chapter_effective_qa_status(chapter, qa_record),
                export_approved=_chapter_is_approved(chapter, qa_record),
            )
        )

    return (selected, skipped_notes, qa_records)


def concatenate_chapters_sync(
    book_id: int,
    *,
    include_only_approved: bool = True,
    chapter_silence_seconds: float = 2.0,
    opening_silence_seconds: float = 3.0,
    closing_silence_seconds: float = 3.0,
    session_factory: sessionmaker[Session] | None = None,
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

        combined_audio = AudioSegment.silent(duration=0, frame_rate=EXPORT_SAMPLE_RATE).set_channels(1)
        chapter_markers: list[ChapterMarker] = []
        current_ms = 0

        for index, selected_chapter in enumerate(selected):
            start_ms = current_ms
            combined_audio += selected_chapter.audio_segment
            current_ms += len(selected_chapter.audio_segment)

            next_type = selected[index + 1].chapter_type if index + 1 < len(selected) else None
            silence_seconds = _silence_between(
                selected_chapter.chapter_type,
                next_type,
                chapter_silence_seconds=chapter_silence_seconds,
                opening_silence_seconds=opening_silence_seconds,
                closing_silence_seconds=closing_silence_seconds,
            )
            if silence_seconds > 0:
                combined_audio += create_silence(silence_seconds)
                current_ms += int(round(silence_seconds * 1000))

            chapter_markers.append(
                ChapterMarker(
                    title=selected_chapter.chapter_title,
                    start_ms=start_ms,
                    end_ms=current_ms,
                )
            )

        combined_audio.export(master_wav_path, format="wav")
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
    subprocess.run(command, check=True, capture_output=True, text=True)


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


def export_mp3(normalized_wav_path: Path, output_path: Path, *, book: Book, cover_art_path: Path) -> None:
    """Encode a normalized master WAV into audiobook MP3 format."""

    ffmpeg_path = _require_ffmpeg()
    command = [
        ffmpeg_path,
        "-y",
        "-i",
        str(normalized_wav_path),
        "-i",
        str(cover_art_path),
        "-map",
        "0:a",
        "-map",
        "1:v",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        settings.EXPORT_MP3_BITRATE,
        "-ar",
        str(EXPORT_SAMPLE_RATE),
        "-ac",
        "1",
        "-codec:v",
        "mjpeg",
        "-disposition:v",
        "attached_pic",
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
        "-metadata:s:v",
        "title=Cover Art",
        "-metadata:s:v",
        "comment=Cover (front)",
        str(output_path),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)


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
        str(EXPORT_SAMPLE_RATE),
        "-ac",
        "1",
        "-movflags",
        "+faststart",
        "-f",
        "ipod",
        str(output_path),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)


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


def export_book_sync(
    book_id: int,
    *,
    export_formats: list[str] | None = None,
    include_only_approved: bool = True,
    session_factory: sessionmaker[Session] | None = None,
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
        cover_art_path = _ensure_placeholder_cover(export_paths["exports_root"])

        concatenation = concatenate_chapters_sync(
            book_id,
            include_only_approved=include_only_approved,
            chapter_silence_seconds=settings.EXPORT_CHAPTER_SILENCE_SECONDS,
            opening_silence_seconds=settings.EXPORT_OPENING_SILENCE_SECONDS,
            closing_silence_seconds=settings.EXPORT_CLOSING_SILENCE_SECONDS,
            session_factory=session_factory,
        )
        normalize_loudness(
            concatenation.master_wav_path,
            export_paths["normalized_wav"],
            target_lufs=settings.EXPORT_TARGET_LUFS,
        )

        qa_report = _build_qa_report(
            book=book,
            included_chapters=concatenation.included_chapters,
            qa_records=concatenation.qa_records,
            skipped_notes=concatenation.skipped_notes,
        )
        export_paths["qa_report"].write_text(
            json.dumps(qa_report.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

        format_results = _empty_format_details(formats)
        errors: list[str] = []
        try:
            for export_format in formats:
                try:
                    if export_format == "mp3":
                        export_mp3(
                            export_paths["normalized_wav"],
                            export_paths["mp3"],
                            book=book,
                            cover_art_path=cover_art_path,
                        )
                        output_path = export_paths["mp3"]
                    else:
                        export_m4b(
                            export_paths["normalized_wav"],
                            export_paths["m4b"],
                            book=book,
                            chapter_markers=concatenation.chapter_markers,
                            metadata_path=export_paths["metadata"],
                        )
                        output_path = export_paths["m4b"]

                    format_results[export_format] = ExportFormatResult(
                        status="completed",
                        file_size_bytes=output_path.stat().st_size,
                        file_name=output_path.name,
                        download_url=f"/api/book/{book.id}/export/download/{export_format}",
                        completed_at=utc_now(),
                    )
                except Exception as exc:
                    logger.exception("Failed to export %s for book %s", export_format, book.id)
                    errors.append(f"{export_format}: {exc}")
                    format_results[export_format] = ExportFormatResult(
                        status="error",
                        error_message=str(exc),
                    )
        finally:
            for temporary_path in (
                export_paths["master_wav"],
                export_paths["normalized_wav"],
                export_paths["metadata"],
            ):
                if temporary_path.exists():
                    temporary_path.unlink()

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
