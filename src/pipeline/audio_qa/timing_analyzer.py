"""Timing and pacing analysis with librosa-backed helpers and graceful fallbacks."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import numpy as np

from src.pipeline.audio_qa.models import AudioQAIssue, DependencyNotice, TimingAnalysis
from src.pipeline.audio_qa.transcription_checker import normalize_transcript_text

logger = logging.getLogger(__name__)


class TimingAndPacingAnalyzer:
    """Inspect chapter pacing, pause distribution, and duration fit."""

    WORDS_PER_MINUTE_TARGET = 155.0
    WORDS_PER_MINUTE_WARNING_RANGE = (120.0, 185.0)
    WORDS_PER_MINUTE_FAIL_RANGE = (100.0, 210.0)
    DURATION_WARNING_DELTA = 0.25
    DURATION_FAIL_DELTA = 0.45
    MID_PAUSE_WARNING_SECONDS = 1.2
    MID_PAUSE_FAIL_SECONDS = 2.5
    EDGE_PAUSE_WARNING_SECONDS = 1.5
    EDGE_PAUSE_FAIL_SECONDS = 3.0

    def analyze(self, audio_path: str | Path, reference_text: str) -> TimingAnalysis:
        """Analyze pacing, pause structure, and duration fit."""

        try:
            backend = self._load_backend()
            samples, sample_rate = self._load_audio(backend, audio_path)
        except RuntimeError as exc:
            return TimingAnalysis(
                dependency=DependencyNotice(dependency="librosa", available=False, message=str(exc)),
                status="dependency_unavailable",
                issues=[
                    AudioQAIssue(
                        code="missing_librosa",
                        category="timing",
                        severity="warning",
                        message=str(exc),
                    )
                ],
            )

        actual_duration = self._duration_seconds(samples, sample_rate)
        estimated_duration = self._estimate_duration(reference_text)
        normalized_reference = normalize_transcript_text(reference_text)
        word_count = len(normalized_reference.split())
        speech_rate_wpm = (word_count / actual_duration) * 60.0 if actual_duration > 0 else None
        pause_regions = self._detect_pauses(backend, samples, sample_rate, actual_duration)
        pause_ratio = (
            sum((pause.end_time_seconds or 0.0) - (pause.start_time_seconds or 0.0) for pause in pause_regions)
            / actual_duration
            if actual_duration > 0
            else None
        )

        issues: list[AudioQAIssue] = []
        issues.extend(pause_regions)
        issues.extend(self._duration_issues(estimated_duration, actual_duration))
        issues.extend(self._pace_issues(speech_rate_wpm))
        if pause_ratio is not None and pause_ratio > 0.35:
            severity = "warning" if pause_ratio <= 0.5 else "error"
            issues.append(
                AudioQAIssue(
                    code="excessive_pause_ratio",
                    category="timing",
                    severity=severity,
                    message=f"Silence occupies {pause_ratio:.1%} of the chapter audio.",
                    details={"pause_ratio": round(pause_ratio, 4)},
                )
            )

        score = self._score(estimated_duration, actual_duration, speech_rate_wpm, issues)
        status = self._status(score, issues)

        return TimingAnalysis(
            estimated_duration_seconds=estimated_duration,
            actual_duration_seconds=round(actual_duration, 4),
            speech_rate_wpm=round(speech_rate_wpm, 2) if speech_rate_wpm is not None else None,
            pause_ratio=round(pause_ratio, 4) if pause_ratio is not None else None,
            pauses=pause_regions,
            score=score,
            status=status,
            issues=issues,
        )

    def _load_backend(self) -> Any:
        """Import librosa lazily for easier fallback testing."""

        try:
            import librosa  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "librosa is not installed; run `pip install librosa` to enable timing QA."
            ) from exc

        return librosa

    def _load_audio(self, backend: Any, audio_path: str | Path) -> tuple[np.ndarray, int]:
        """Load audio through librosa."""

        samples, sample_rate = backend.load(str(audio_path), sr=None, mono=True)
        return np.asarray(samples, dtype=np.float32), int(sample_rate)

    def _duration_seconds(self, samples: np.ndarray, sample_rate: int) -> float:
        """Return audio duration in seconds."""

        if sample_rate <= 0:
            return 0.0
        return float(len(samples)) / float(sample_rate)

    def _estimate_duration(self, reference_text: str) -> float:
        """Estimate expected narration duration from the source text."""

        normalized = normalize_transcript_text(reference_text)
        words = [word for word in normalized.split() if word.strip()]
        if not words:
            return 0.0

        sentence_count = max(1, len([part for part in re.split(r"[.!?]+", reference_text) if part.strip()]))
        speech_seconds = (len(words) / self.WORDS_PER_MINUTE_TARGET) * 60.0
        sentence_pause_padding = sentence_count * 0.35
        return round(speech_seconds + sentence_pause_padding, 4)

    def _detect_pauses(
        self,
        backend: Any,
        samples: np.ndarray,
        sample_rate: int,
        actual_duration: float,
    ) -> list[AudioQAIssue]:
        """Convert non-silent intervals into timestamped silence issues."""

        if samples.size == 0 or sample_rate <= 0:
            return []

        intervals = backend.effects.split(samples, top_db=35, frame_length=2048, hop_length=512)
        nonsilent = np.asarray(intervals, dtype=np.int64)
        if nonsilent.size == 0:
            return [
                AudioQAIssue(
                    code="fully_silent_audio",
                    category="timing",
                    severity="error",
                    message="Audio appears to be fully silent.",
                    start_time_seconds=0.0,
                    end_time_seconds=round(actual_duration, 4),
                )
            ]

        silence_ranges: list[tuple[int, int]] = []
        cursor = 0
        for start, end in nonsilent:
            if start > cursor:
                silence_ranges.append((cursor, int(start)))
            cursor = int(end)
        if cursor < len(samples):
            silence_ranges.append((cursor, len(samples)))

        pause_issues: list[AudioQAIssue] = []
        for start, end in silence_ranges:
            duration_seconds = max(0.0, (end - start) / float(sample_rate))
            if duration_seconds <= 0.3:
                continue

            is_edge_pause = start <= 0 or end >= len(samples)
            warning_threshold = self.EDGE_PAUSE_WARNING_SECONDS if is_edge_pause else self.MID_PAUSE_WARNING_SECONDS
            fail_threshold = self.EDGE_PAUSE_FAIL_SECONDS if is_edge_pause else self.MID_PAUSE_FAIL_SECONDS
            if duration_seconds <= warning_threshold:
                continue

            severity = "warning" if duration_seconds <= fail_threshold else "error"
            label = "edge_pause" if is_edge_pause else "mid_pause"
            pause_issues.append(
                AudioQAIssue(
                    code=label,
                    category="timing",
                    severity=severity,
                    message=f"Detected a {duration_seconds:.2f}s {'leading/trailing' if is_edge_pause else 'mid-chapter'} pause.",
                    start_time_seconds=round(start / float(sample_rate), 4),
                    end_time_seconds=round(end / float(sample_rate), 4),
                    details={"duration_seconds": round(duration_seconds, 4)},
                )
            )

        return pause_issues

    def _duration_issues(self, estimated_duration: float, actual_duration: float) -> list[AudioQAIssue]:
        """Return duration-fit issues relative to the chapter text."""

        if estimated_duration <= 0 or actual_duration <= 0:
            return []

        delta_ratio = abs(actual_duration - estimated_duration) / estimated_duration
        if delta_ratio <= self.DURATION_WARNING_DELTA:
            return []

        severity = "warning" if delta_ratio <= self.DURATION_FAIL_DELTA else "error"
        return [
            AudioQAIssue(
                code="duration_mismatch",
                category="timing",
                severity=severity,
                message=(
                    f"Audio duration ({actual_duration:.2f}s) differs from the text estimate "
                    f"({estimated_duration:.2f}s) by {delta_ratio:.1%}."
                ),
                details={
                    "estimated_duration_seconds": round(estimated_duration, 4),
                    "actual_duration_seconds": round(actual_duration, 4),
                    "delta_ratio": round(delta_ratio, 4),
                },
            )
        ]

    def _pace_issues(self, speech_rate_wpm: float | None) -> list[AudioQAIssue]:
        """Return pace issues for overly fast or slow delivery."""

        if speech_rate_wpm is None:
            return []

        if self.WORDS_PER_MINUTE_WARNING_RANGE[0] <= speech_rate_wpm <= self.WORDS_PER_MINUTE_WARNING_RANGE[1]:
            return []

        severity = (
            "warning"
            if self.WORDS_PER_MINUTE_FAIL_RANGE[0] <= speech_rate_wpm <= self.WORDS_PER_MINUTE_FAIL_RANGE[1]
            else "error"
        )
        direction = "slow" if speech_rate_wpm < self.WORDS_PER_MINUTE_WARNING_RANGE[0] else "fast"
        return [
            AudioQAIssue(
                code="speech_rate_out_of_range",
                category="timing",
                severity=severity,
                message=f"Estimated narration pace is {speech_rate_wpm:.1f} WPM, which is too {direction}.",
                details={"speech_rate_wpm": round(speech_rate_wpm, 4)},
            )
        ]

    def _score(
        self,
        estimated_duration: float,
        actual_duration: float,
        speech_rate_wpm: float | None,
        issues: list[AudioQAIssue],
    ) -> float:
        """Compute a 0-100 timing score from measurable drift and issue severity."""

        score = 100.0
        if estimated_duration > 0 and actual_duration > 0:
            delta_ratio = abs(actual_duration - estimated_duration) / estimated_duration
            score -= min(35.0, delta_ratio * 70.0)

        if speech_rate_wpm is not None:
            if speech_rate_wpm < self.WORDS_PER_MINUTE_WARNING_RANGE[0]:
                score -= min(25.0, (self.WORDS_PER_MINUTE_WARNING_RANGE[0] - speech_rate_wpm) * 0.3)
            elif speech_rate_wpm > self.WORDS_PER_MINUTE_WARNING_RANGE[1]:
                score -= min(25.0, (speech_rate_wpm - self.WORDS_PER_MINUTE_WARNING_RANGE[1]) * 0.3)

        for issue in issues:
            if issue.severity == "warning":
                score -= 6.0
            elif issue.severity == "error":
                score -= 15.0

        return round(max(0.0, min(100.0, score)), 2)

    def _status(self, score: float, issues: list[AudioQAIssue]) -> str:
        """Return the top-level timing stage status."""

        if any(issue.severity == "error" for issue in issues) or score < 70.0:
            return "fail"
        if any(issue.severity == "warning" for issue in issues) or score < 85.0:
            return "warning"
        return "pass"
