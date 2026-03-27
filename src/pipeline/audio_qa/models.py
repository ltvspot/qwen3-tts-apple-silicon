"""Shared Pydantic models for deep audio QA results."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DependencyNotice(BaseModel):
    """Describe one optional dependency state for QA execution."""

    dependency: str
    available: bool
    message: str | None = None


class AudioQAIssue(BaseModel):
    """One issue detected by a deep audio QA stage."""

    code: str
    category: str
    severity: str
    message: str
    start_time_seconds: float | None = None
    end_time_seconds: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class TranscriptDiffEntry(BaseModel):
    """One token-level diff item between expected text and transcription."""

    operation: str
    expected: str | None = None
    actual: str | None = None
    start_time_seconds: float | None = None
    end_time_seconds: float | None = None


class TranscriptionAnalysis(BaseModel):
    """Result of the transcription accuracy stage."""

    dependency: DependencyNotice = Field(
        default_factory=lambda: DependencyNotice(dependency="mlx-whisper", available=True),
    )
    provider: str = "mlx-whisper"
    model_name: str | None = None
    transcript: str | None = None
    normalized_reference: str = ""
    normalized_transcript: str = ""
    reference_word_count: int = 0
    transcript_word_count: int = 0
    word_error_rate: float | None = None
    score: float = 0.0
    status: str = "not_run"
    diff: list[TranscriptDiffEntry] = Field(default_factory=list)
    issues: list[AudioQAIssue] = Field(default_factory=list)


class TimingAnalysis(BaseModel):
    """Result of the pacing and pause analysis stage."""

    dependency: DependencyNotice = Field(
        default_factory=lambda: DependencyNotice(dependency="librosa", available=True),
    )
    estimated_duration_seconds: float | None = None
    actual_duration_seconds: float | None = None
    speech_rate_wpm: float | None = None
    pause_ratio: float | None = None
    pauses: list[AudioQAIssue] = Field(default_factory=list)
    score: float = 0.0
    status: str = "not_run"
    issues: list[AudioQAIssue] = Field(default_factory=list)


class AudioQualityAnalysis(BaseModel):
    """Result of loudness, SNR, and artifact analysis."""

    dependency: DependencyNotice = Field(
        default_factory=lambda: DependencyNotice(dependency="pyloudnorm", available=True),
    )
    integrated_lufs: float | None = None
    loudness_range_lu: float | None = None
    peak_dbfs: float | None = None
    snr_db: float | None = None
    clipping_ratio: float | None = None
    artifact_events: list[AudioQAIssue] = Field(default_factory=list)
    score: float = 0.0
    status: str = "not_run"
    issues: list[AudioQAIssue] = Field(default_factory=list)


class AudioQAScoreBreakdown(BaseModel):
    """Weighted scorecard across all QA stages."""

    transcription: float = 0.0
    timing: float = 0.0
    quality: float = 0.0
    overall: float = 0.0
    grade: str = "F"
    status: str = "fail"
    reasoning: list[str] = Field(default_factory=list)


class ChapterDeepQAResult(BaseModel):
    """Full deep-QA report for a single chapter."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    book_id: int
    chapter_id: int | None = None
    chapter_n: int
    chapter_title: str | None = None
    audio_path: str | None = None
    checked_at: datetime | None = None
    transcription: TranscriptionAnalysis = Field(default_factory=TranscriptionAnalysis)
    timing: TimingAnalysis = Field(default_factory=TimingAnalysis)
    quality: AudioQualityAnalysis = Field(default_factory=AudioQualityAnalysis)
    scoring: AudioQAScoreBreakdown = Field(default_factory=AudioQAScoreBreakdown)
    issues: list[AudioQAIssue] = Field(default_factory=list)
    ready_for_export: bool = False
    summary: str = ""


class BookDeepQAReport(BaseModel):
    """Aggregate deep-QA report across all chapters in a book."""

    book_id: int
    generated_at: datetime
    chapters: list[ChapterDeepQAResult] = Field(default_factory=list)
    chapter_count: int = 0
    average_score: float = 0.0
    average_transcription_score: float = 0.0
    average_timing_score: float = 0.0
    average_quality_score: float = 0.0
    ready_for_export: bool = False
    issue_count: int = 0
    grade_counts: dict[str, int] = Field(default_factory=dict)
    status_counts: dict[str, int] = Field(default_factory=dict)
