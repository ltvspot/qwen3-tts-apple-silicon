"""Database models and session management."""

from __future__ import annotations

import functools
import logging
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Generator, TypeVar

from sqlalchemy import (
    Boolean,
    DateTime,
    event,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    inspect,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from src.config import DEFAULT_NARRATOR_NAME, settings

logger = logging.getLogger(__name__)
_CallableT = TypeVar("_CallableT", bound=Callable[..., Any])


def utc_now() -> datetime:
    """Return the current UTC timestamp."""

    return datetime.now(timezone.utc)


def ensure_aware(dt: datetime | None) -> datetime | None:
    """Return a UTC-aware datetime, assuming naive database values are stored in UTC."""

    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def retry_on_locked(max_retries: int = 3, backoff_ms: int = 500) -> Callable[[_CallableT], _CallableT]:
    """Retry SQLite operations that fail due to a transient database lock."""

    def decorator(func: _CallableT) -> _CallableT:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except OperationalError as exc:
                    if "database is locked" not in str(exc).lower() or attempt >= max_retries - 1:
                        raise
                    time.sleep((backoff_ms * (attempt + 1)) / 1000.0)

            raise RuntimeError("retry_on_locked exhausted without returning or re-raising.")

        return wrapper  # type: ignore[return-value]

    return decorator


class Base(DeclarativeBase):
    """Base class for all ORM models."""


class BookStatus(str, Enum):
    """Lifecycle states for a book."""

    NOT_STARTED = "not_started"
    PARSED = "parsed"
    GENERATING = "generating"
    GENERATED = "generated"
    QA = "qa"
    QA_APPROVED = "qa_approved"
    EXPORTED = "exported"


class ChapterType(str, Enum):
    """Narration segment types."""

    OPENING_CREDITS = "opening_credits"
    INTRODUCTION = "introduction"
    CHAPTER = "chapter"
    CLOSING_CREDITS = "closing_credits"


class ChapterStatus(str, Enum):
    """Generation states for a chapter."""

    PENDING = "pending"
    GENERATING = "generating"
    GENERATED = "generated"
    GENERATED_NO_QA = "generated_no_qa"
    FAILED = "failed"


class BookGenerationStatus(str, Enum):
    """Real-time generation panel state for a book."""

    IDLE = "idle"
    GENERATING = "generating"
    ERROR = "error"


class QAStatus(str, Enum):
    """QA review states for a chapter."""

    NOT_REVIEWED = "not_reviewed"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"


class QAAutomaticStatus(str, Enum):
    """Automatic QA outcomes produced by audio analysis."""

    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


class QAManualStatus(str, Enum):
    """Manual QA review decisions stored per chapter."""

    APPROVED = "approved"
    FLAGGED = "flagged"


class GenerationJobStatus(str, Enum):
    """Background job states."""

    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class GenerationJobType(str, Enum):
    """Supported generation job scopes."""

    SINGLE_CHAPTER = "single_chapter"
    FULL_BOOK = "full_book"
    BATCH_ALL = "batch_all"


class BookExportStatus(str, Enum):
    """Export lifecycle states for a book."""

    IDLE = "idle"
    PROCESSING = "processing"
    COMPLETED = "completed"
    ERROR = "error"


class Book(Base):
    """Audiobook project metadata."""

    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    narrator: Mapped[str] = mapped_column(String(255), nullable=False, default=DEFAULT_NARRATOR_NAME)
    folder_path: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    status: Mapped[BookStatus | None] = mapped_column(
        SqlEnum(BookStatus, native_enum=False, validate_strings=True),
        nullable=True,
        default=BookStatus.NOT_STARTED,
    )
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trim_size: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
    generation_status: Mapped[BookGenerationStatus] = mapped_column(
        SqlEnum(BookGenerationStatus, native_enum=False, validate_strings=True),
        nullable=False,
        default=BookGenerationStatus.IDLE,
    )
    generation_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    generation_eta_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_job_id: Mapped[int | None] = mapped_column(ForeignKey("generation_jobs.id"), nullable=True)
    last_export_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    export_status: Mapped[BookExportStatus] = mapped_column(
        SqlEnum(BookExportStatus, native_enum=False, validate_strings=True),
        nullable=False,
        default=BookExportStatus.IDLE,
    )
    pronunciation_watchlist: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    chapters: Mapped[list["Chapter"]] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
        order_by="Chapter.number",
    )
    generation_jobs: Mapped[list["GenerationJob"]] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
        foreign_keys="GenerationJob.book_id",
    )
    batch_book_statuses: Mapped[list["BatchBookStatus"]] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
    )
    current_job: Mapped["GenerationJob | None"] = relationship(
        foreign_keys=[current_job_id],
        post_update=True,
    )


class Chapter(Base):
    """Parsed narration unit for a book."""

    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False, index=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    type: Mapped[ChapterType] = mapped_column(
        SqlEnum(ChapterType, native_enum=False, validate_strings=True),
        nullable=False,
    )
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[ChapterStatus] = mapped_column(
        SqlEnum(ChapterStatus, native_enum=False, validate_strings=True),
        nullable=False,
        default=ChapterStatus.PENDING,
        index=True,
    )
    audio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    qa_status: Mapped[QAStatus | None] = mapped_column(
        SqlEnum(QAStatus, native_enum=False, validate_strings=True),
        nullable=True,
        default=QAStatus.NOT_REVIEWED,
    )
    qa_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_chunk: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_chunks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_boundaries: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)
    mastered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    book: Mapped["Book"] = relationship(back_populates="chapters")
    generation_jobs: Mapped[list["GenerationJob"]] = relationship(back_populates="chapter")


class ChapterQARecord(Base):
    """Automatic and manual QA details for a generated chapter."""

    __tablename__ = "qa_status"
    __table_args__ = (
        UniqueConstraint("book_id", "chapter_n", name="uq_qa_status_book_chapter"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False, index=True)
    chapter_n: Mapped[int] = mapped_column(Integer, nullable=False)
    overall_status: Mapped[QAAutomaticStatus] = mapped_column(
        SqlEnum(QAAutomaticStatus, native_enum=False, validate_strings=True),
        nullable=False,
    )
    qa_details: Mapped[str] = mapped_column(Text, nullable=False)
    manual_status: Mapped[QAManualStatus | None] = mapped_column(
        SqlEnum(QAManualStatus, native_enum=False, validate_strings=True),
        nullable=True,
    )
    manual_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    manual_reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    manual_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    book: Mapped["Book"] = relationship()


class QualitySnapshot(Base):
    """Historical per-book quality metrics used by the production overseer."""

    __tablename__ = "quality_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False, index=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, index=True)
    gate1_pass_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    gate2_avg_grade: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    gate3_overall_grade: Mapped[str] = mapped_column(String(1), nullable=False, default="F")
    chunks_regenerated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_wer: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_lufs: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    generation_rtf: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    issues_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    book: Mapped["Book"] = relationship()


class AudioQAResult(Base):
    """Persisted deep-audio-QA report for one generated chapter."""

    __tablename__ = "audio_qa_results"
    __table_args__ = (
        UniqueConstraint("book_id", "chapter_n", name="uq_audio_qa_results_book_chapter"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False, index=True)
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id"), nullable=True, index=True)
    chapter_n: Mapped[int] = mapped_column(Integer, nullable=False)
    transcription_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    timing_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    overall_grade: Mapped[str | None] = mapped_column(String(8), nullable=True)
    overall_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    report_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    issues_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    book: Mapped["Book"] = relationship()
    chapter: Mapped["Chapter | None"] = relationship()


class ExportJob(Base):
    """Persistent export status for the most recent book export."""

    __tablename__ = "export_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False, unique=True, index=True)
    job_token: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    export_status: Mapped[BookExportStatus] = mapped_column(
        SqlEnum(BookExportStatus, native_enum=False, validate_strings=True),
        nullable=False,
        default=BookExportStatus.PROCESSING,
    )
    formats_requested: Mapped[str] = mapped_column(Text, nullable=False)
    format_details: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    progress_percent: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    current_stage: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_format: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_chapter_n: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_chapters: Mapped[int | None] = mapped_column(Integer, nullable=True)
    include_only_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    qa_report: Mapped[str | None] = mapped_column(Text, nullable=True)

    book: Mapped["Book"] = relationship()


class AppSetting(Base):
    """Persisted application settings payloads."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class ClonedVoice(Base):
    """Metadata for a persisted cloned voice reference."""

    __tablename__ = "cloned_voices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    voice_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    reference_audio_path: Mapped[str] = mapped_column(String(500), nullable=False)
    transcript_path: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class VoicePreset(Base):
    """Saved TTS preset configuration."""

    __tablename__ = "voice_presets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    engine: Mapped[str] = mapped_column(String(100), nullable=False)
    voice_name: Mapped[str] = mapped_column(String(255), nullable=False)
    emotion: Mapped[str | None] = mapped_column(String(100), nullable=True)
    speed: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class GenerationJob(Base):
    """Represents a queued or running generation task."""

    __tablename__ = "generation_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False, index=True)
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id"), nullable=True, index=True)
    job_type: Mapped[GenerationJobType] = mapped_column(
        SqlEnum(GenerationJobType, native_enum=False, validate_strings=True),
        nullable=False,
        default=GenerationJobType.FULL_BOOK,
    )
    status: Mapped[GenerationJobStatus] = mapped_column(
        SqlEnum(GenerationJobStatus, native_enum=False, validate_strings=True),
        nullable=False,
        index=True,
    )
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    current_chapter_progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    chapters_total: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    chapters_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chapters_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_chapter_n: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    eta_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_seconds_per_chapter: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_completed_chapter: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    force: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    voice_name: Mapped[str] = mapped_column(String(255), nullable=False, default="Ethan")
    emotion: Mapped[str] = mapped_column(String(100), nullable=False, default="neutral")
    speed: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    pause_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    book: Mapped["Book"] = relationship(back_populates="generation_jobs", foreign_keys=[book_id])
    chapter: Mapped[Chapter | None] = relationship(back_populates="generation_jobs")
    history_entries: Mapped[list["JobHistory"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobHistory.timestamp",
    )


class JobHistory(Base):
    """Audit trail for queue control actions."""

    __tablename__ = "job_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("generation_jobs.id"), nullable=False, index=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    job: Mapped["GenerationJob"] = relationship(back_populates="history_entries")
    book: Mapped["Book"] = relationship()


class BatchRun(Base):
    """Persistent record of one catalog batch operation."""

    __tablename__ = "batch_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    total_books: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    books_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    books_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    books_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_book_id: Mapped[int | None] = mapped_column(ForeignKey("books.id"), nullable=True)
    current_book_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resource_warnings: Mapped[str | None] = mapped_column(Text, nullable=True)
    pause_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    estimated_completion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    elapsed_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_seconds_per_book: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    model_reloads: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    book_statuses: Mapped[list["BatchBookStatus"]] = relationship(
        back_populates="batch_run",
        cascade="all, delete-orphan",
        order_by="BatchBookStatus.id",
    )


class BatchBookStatus(Base):
    """Per-book state within a batch run."""

    __tablename__ = "batch_book_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batch_runs.batch_id"), nullable=False, index=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    chapters_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chapters_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chapters_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    batch_run: Mapped["BatchRun"] = relationship(back_populates="book_statuses")
    book: Mapped["Book"] = relationship(back_populates="batch_book_statuses")


def _sqlite_connect_args(database_url: str) -> dict[str, Any]:
    """Return SQLite connection arguments when needed."""

    if database_url.startswith("sqlite"):
        return {"check_same_thread": False, "timeout": 5.0}
    return {}


def create_database_engine(database_url: str):
    """Create the shared application engine with SQLite reliability settings."""

    db_engine = create_engine(database_url, connect_args=_sqlite_connect_args(database_url))

    if database_url.startswith("sqlite"):
        @event.listens_for(db_engine, "connect")
        def set_sqlite_pragma(dbapi_conn, _connection_record) -> None:
            cursor = dbapi_conn.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.execute("PRAGMA synchronous=NORMAL")
            finally:
                cursor.close()

    return db_engine


engine = create_database_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


@retry_on_locked(max_retries=10, backoff_ms=250)
def init_db() -> None:
    """Create all database tables if they do not already exist."""

    logger.info("Initializing database schema.")
    Base.metadata.create_all(bind=engine)
    _migrate_sqlite_schema()


@retry_on_locked(max_retries=10, backoff_ms=250)
def _migrate_sqlite_schema() -> None:
    """Apply lightweight additive schema migrations for the local SQLite database."""

    if engine.dialect.name != "sqlite":
        return

    required_columns = {
        "books": {
            "generation_status": "VARCHAR(32) NOT NULL DEFAULT 'idle'",
            "generation_started_at": "DATETIME",
            "generation_eta_seconds": "INTEGER",
            "current_job_id": "INTEGER",
            "last_export_date": "DATETIME",
            "export_status": "VARCHAR(32) NOT NULL DEFAULT 'idle'",
            "pronunciation_watchlist": "TEXT",
            "description": "TEXT",
        },
        "chapters": {
            "started_at": "DATETIME",
            "completed_at": "DATETIME",
            "error_message": "TEXT",
            "audio_file_size_bytes": "INTEGER",
            "current_chunk": "INTEGER",
            "total_chunks": "INTEGER",
            "chunk_boundaries": "TEXT",
            "generation_metadata": "TEXT",
            "mastered": "BOOLEAN NOT NULL DEFAULT 0",
        },
        "generation_jobs": {
            "job_type": "VARCHAR(32) NOT NULL DEFAULT 'full_book'",
            "current_chapter_progress": "FLOAT NOT NULL DEFAULT 0.0",
            "chapters_total": "INTEGER NOT NULL DEFAULT 1",
            "chapters_completed": "INTEGER NOT NULL DEFAULT 0",
            "chapters_failed": "INTEGER NOT NULL DEFAULT 0",
            "current_chapter_n": "INTEGER",
            "priority": "INTEGER NOT NULL DEFAULT 0",
            "paused_at": "DATETIME",
            "eta_seconds": "INTEGER",
            "avg_seconds_per_chapter": "FLOAT",
            "last_completed_chapter": "INTEGER NOT NULL DEFAULT 0",
            "force": "BOOLEAN NOT NULL DEFAULT 0",
            "voice_name": "VARCHAR(255) NOT NULL DEFAULT 'Ethan'",
            "emotion": "VARCHAR(100)",
            "speed": "FLOAT NOT NULL DEFAULT 1.0",
            "pause_requested": "BOOLEAN NOT NULL DEFAULT 0",
            "cancel_requested": "BOOLEAN NOT NULL DEFAULT 0",
            "updated_at": "DATETIME",
        },
        "export_jobs": {
            "progress_percent": "FLOAT NOT NULL DEFAULT 0.0",
            "current_stage": "VARCHAR(255)",
            "current_format": "VARCHAR(32)",
            "current_chapter_n": "INTEGER",
            "total_chapters": "INTEGER",
            "updated_at": "DATETIME",
        },
    }

    with engine.begin() as connection:
        inspector = inspect(connection)
        for table_name, columns in required_columns.items():
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, ddl in columns.items():
                if column_name in existing_columns:
                    continue
                logger.info("Applying SQLite schema migration: %s.%s", table_name, column_name)
                connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"))

        connection.execute(
            text(
                "UPDATE generation_jobs "
                "SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP) "
                "WHERE updated_at IS NULL"
            )
        )
        connection.execute(
            text(
                "UPDATE export_jobs "
                "SET updated_at = COALESCE(updated_at, started_at, created_at, CURRENT_TIMESTAMP) "
                "WHERE updated_at IS NULL"
            )
        )

        for ddl in (
            "CREATE INDEX IF NOT EXISTS ix_chapters_status ON chapters (status)",
            "CREATE INDEX IF NOT EXISTS ix_generation_jobs_status ON generation_jobs (status)",
            "CREATE INDEX IF NOT EXISTS ix_batch_book_status_batch_id ON batch_book_status (batch_id)",
            "CREATE INDEX IF NOT EXISTS ix_batch_book_status_book_id ON batch_book_status (book_id)",
            "CREATE INDEX IF NOT EXISTS ix_batch_runs_batch_id ON batch_runs (batch_id)",
        ):
            connection.execute(text(ddl))


def get_db() -> Generator[Session, None, None]:
    """Yield a SQLAlchemy session for dependency injection."""

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
