"""Deep audio QA package for chapter-level transcription, timing, and audio checks."""

from __future__ import annotations

from src.pipeline.audio_qa.audio_quality_analyzer import AudioQualityAnalyzer
from src.pipeline.audio_qa.models import (
    AudioQAIssue,
    AudioQAScoreBreakdown,
    AudioQualityAnalysis,
    BookDeepQAReport,
    ChapterDeepQAResult,
    DependencyNotice,
    TimingAnalysis,
    TranscriptDiffEntry,
    TranscriptionAnalysis,
)
from src.pipeline.audio_qa.qa_scorer import AudioQAScorer
from src.pipeline.audio_qa.timing_analyzer import TimingAndPacingAnalyzer
from src.pipeline.audio_qa.transcription_checker import AudioQADependencyError, TranscriptionAccuracyChecker

__all__ = [
    "AudioQADependencyError",
    "AudioQAIssue",
    "AudioQAScoreBreakdown",
    "AudioQualityAnalysis",
    "AudioQualityAnalyzer",
    "AudioQAScorer",
    "BookDeepQAReport",
    "ChapterDeepQAResult",
    "DependencyNotice",
    "TimingAnalysis",
    "TimingAndPacingAnalyzer",
    "TranscriptDiffEntry",
    "TranscriptionAccuracyChecker",
    "TranscriptionAnalysis",
]
