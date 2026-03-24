"""Database models and session management."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Generator

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from src.config import settings

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    """Return the current UTC timestamp."""

    return datetime.now(timezone.utc)


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
    FAILED = "failed"


class QAStatus(str, Enum):
    """QA review states for a chapter."""

    NOT_REVIEWED = "not_reviewed"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"


class GenerationJobStatus(str, Enum):
    """Background job states."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Book(Base):
    """Audiobook project metadata."""

    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    narrator: Mapped[str] = mapped_column(String(255), nullable=False, default=settings.NARRATOR_NAME)
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

    chapters: Mapped[list["Chapter"]] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
        order_by="Chapter.number",
    )
    generation_jobs: Mapped[list["GenerationJob"]] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
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
    )
    audio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    qa_status: Mapped[QAStatus | None] = mapped_column(
        SqlEnum(QAStatus, native_enum=False, validate_strings=True),
        nullable=True,
        default=QAStatus.NOT_REVIEWED,
    )
    qa_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    book: Mapped["Book"] = relationship(back_populates="chapters")
    generation_jobs: Mapped[list["GenerationJob"]] = relationship(back_populates="chapter")


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
    status: Mapped[GenerationJobStatus] = mapped_column(
        SqlEnum(GenerationJobStatus, native_enum=False, validate_strings=True),
        nullable=False,
    )
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    book: Mapped["Book"] = relationship(back_populates="generation_jobs")
    chapter: Mapped[Chapter | None] = relationship(back_populates="generation_jobs")


def _sqlite_connect_args(database_url: str) -> dict[str, bool]:
    """Return SQLite connection arguments when needed."""

    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


engine = create_engine(settings.DATABASE_URL, connect_args=_sqlite_connect_args(settings.DATABASE_URL))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    """Create all database tables if they do not already exist."""

    logger.info("Initializing database schema.")
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Yield a SQLAlchemy session for dependency injection."""

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
