"""Transcription accuracy analysis using mlx-whisper with graceful fallbacks."""

from __future__ import annotations

import difflib
import logging
import re
from pathlib import Path
from typing import Any

from src.pipeline.audio_qa.models import AudioQAIssue, DependencyNotice, TranscriptDiffEntry, TranscriptionAnalysis

logger = logging.getLogger(__name__)


class AudioQADependencyError(RuntimeError):
    """Raised when an optional deep-QA dependency is unavailable."""


def normalize_transcript_text(text: str) -> str:
    """Normalize text for transcription comparison."""

    lowered = text.casefold()
    lowered = re.sub(r"[\u2018\u2019]", "'", lowered)
    lowered = re.sub(r"[^\w\s']+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def compute_word_error_rate(reference: str, hypothesis: str) -> float:
    """Compute WER using editdistance when present, otherwise a DP fallback."""

    ref_words = normalize_transcript_text(reference).split()
    hyp_words = normalize_transcript_text(hypothesis).split()

    if not ref_words:
        return 0.0 if not hyp_words else 1.0

    try:
        import editdistance  # type: ignore

        return float(editdistance.eval(ref_words, hyp_words)) / float(len(ref_words))
    except ImportError:
        rows = len(ref_words) + 1
        cols = len(hyp_words) + 1
        distance = [[0] * cols for _ in range(rows)]
        for row in range(rows):
            distance[row][0] = row
        for col in range(cols):
            distance[0][col] = col
        for row in range(1, rows):
            for col in range(1, cols):
                substitution = 0 if ref_words[row - 1] == hyp_words[col - 1] else 1
                distance[row][col] = min(
                    distance[row - 1][col] + 1,
                    distance[row][col - 1] + 1,
                    distance[row - 1][col - 1] + substitution,
                )
        return float(distance[-1][-1]) / float(len(ref_words))


class TranscriptionAccuracyChecker:
    """Transcribe chapter audio and compare it to the source text."""

    PASS_WER_THRESHOLD = 0.05
    WARNING_WER_THRESHOLD = 0.10
    SEGMENT_WARNING_THRESHOLD = 0.25
    SEGMENT_ERROR_THRESHOLD = 0.45

    def __init__(self, model_name: str = "mlx-community/whisper-large-v3-turbo") -> None:
        self.model_name = model_name
        self._backend: Any | None = None

    def analyze(self, audio_path: str | Path, reference_text: str) -> TranscriptionAnalysis:
        """Transcribe audio, compute WER, and return a frontend-ready diff payload."""

        reference = normalize_transcript_text(reference_text)
        payload = TranscriptionAnalysis(
            model_name=self.model_name,
            normalized_reference=reference,
            reference_word_count=len(reference.split()),
            status="not_run",
        )

        if not reference:
            payload.status = "skipped"
            payload.issues.append(
                AudioQAIssue(
                    code="empty_reference",
                    category="transcription",
                    severity="warning",
                    message="Reference text is empty; transcription accuracy cannot be scored.",
                )
            )
            return payload

        audio_file = Path(audio_path)
        if not audio_file.exists():
            payload.status = "failed"
            payload.issues.append(
                AudioQAIssue(
                    code="missing_audio_file",
                    category="transcription",
                    severity="error",
                    message=f"Audio file not found: {audio_file}",
                )
            )
            return payload

        try:
            backend = self._load_backend()
        except AudioQADependencyError as exc:
            payload.status = "dependency_unavailable"
            payload.dependency = DependencyNotice(
                dependency="mlx-whisper",
                available=False,
                message=str(exc),
            )
            payload.issues.append(
                AudioQAIssue(
                    code="missing_mlx_whisper",
                    category="transcription",
                    severity="warning",
                    message=str(exc),
                )
            )
            return payload

        try:
            transcription_payload = self._transcribe(backend, audio_file)
        except Exception as exc:
            logger.warning("mlx-whisper transcription failed for %s: %s", audio_file, exc)
            payload.status = "failed"
            payload.issues.append(
                AudioQAIssue(
                    code="transcription_failed",
                    category="transcription",
                    severity="error",
                    message=f"mlx-whisper transcription failed: {exc}",
                )
            )
            return payload

        transcript, segments = self._extract_transcript(transcription_payload)
        normalized_transcript = normalize_transcript_text(transcript)
        payload.transcript = transcript
        payload.normalized_transcript = normalized_transcript
        payload.transcript_word_count = len(normalized_transcript.split())
        payload.word_error_rate = compute_word_error_rate(reference, normalized_transcript)
        payload.diff = self._diff(reference, normalized_transcript)
        payload.issues.extend(self._build_segment_issues(reference, segments))
        payload.status = self._status_from_wer(payload.word_error_rate)
        payload.score = self._score_from_wer(payload.word_error_rate)
        if payload.status != "pass":
            payload.issues.append(
                AudioQAIssue(
                    code="chapter_alignment",
                    category="transcription",
                    severity="warning" if payload.status == "warning" else "error",
                    message=(
                        f"Chapter transcription WER is {payload.word_error_rate:.3f}; "
                        "review mismatched words before export."
                    ),
                    details={
                        "word_error_rate": payload.word_error_rate,
                        "diff_count": len(payload.diff),
                    },
                )
            )
        return payload

    def _load_backend(self) -> Any:
        """Load the mlx-whisper backend lazily."""

        if self._backend is not None:
            return self._backend

        try:
            import mlx_whisper  # type: ignore
        except ImportError as exc:
            raise AudioQADependencyError(
                "mlx-whisper is not installed; run `pip install mlx-whisper` to enable transcription QA."
            ) from exc

        self._backend = mlx_whisper
        return self._backend

    def _transcribe(self, backend: Any, audio_path: str | Path) -> Any:
        """Run transcription through mlx-whisper."""

        return backend.transcribe(str(audio_path), path_or_hf_repo=self.model_name)

    def _diff(self, reference: str, hypothesis: str) -> list[TranscriptDiffEntry]:
        """Return a compact word-level diff payload for the frontend."""

        reference_words = reference.split()
        hypothesis_words = hypothesis.split()
        matcher = difflib.SequenceMatcher(a=reference_words, b=hypothesis_words)
        entries: list[TranscriptDiffEntry] = []
        for opcode, ref_start, ref_end, hyp_start, hyp_end in matcher.get_opcodes():
            if opcode == "equal":
                continue
            entries.append(
                TranscriptDiffEntry(
                    operation=opcode,
                    expected=" ".join(reference_words[ref_start:ref_end]) or None,
                    actual=" ".join(hypothesis_words[hyp_start:hyp_end]) or None,
                )
            )
        return entries

    def _extract_transcript(self, payload: Any) -> tuple[str, list[dict[str, Any]]]:
        """Normalize mlx-whisper output into transcript text plus segment metadata."""

        if isinstance(payload, dict):
            transcript = str(payload.get("text", "")).strip()
            segments = payload.get("segments")
            if isinstance(segments, list):
                return transcript, [segment for segment in segments if isinstance(segment, dict)]
            return transcript, []
        return str(payload).strip(), []

    def _status_from_wer(self, word_error_rate: float) -> str:
        """Convert WER into a stable status."""

        if word_error_rate <= self.PASS_WER_THRESHOLD:
            return "pass"
        if word_error_rate <= self.WARNING_WER_THRESHOLD:
            return "warning"
        return "fail"

    def _score_from_wer(self, word_error_rate: float) -> float:
        """Map WER into a 0-100 score."""

        bounded = max(0.0, min(1.0, word_error_rate))
        return round((1.0 - bounded) * 100.0, 2)

    def _build_segment_issues(self, reference: str, segments: list[dict[str, Any]]) -> list[AudioQAIssue]:
        """Emit timestamped mismatch issues for low-similarity transcription segments."""

        if not segments:
            return []

        reference_words = reference.split()
        segment_word_counts = [len(normalize_transcript_text(str(segment.get("text", ""))).split()) for segment in segments]
        transcript_total_words = sum(count for count in segment_word_counts if count > 0)
        if transcript_total_words <= 0:
            return []

        issues: list[AudioQAIssue] = []
        consumed_words = 0
        total_reference_words = len(reference_words)

        for index, segment in enumerate(segments):
            segment_text = normalize_transcript_text(str(segment.get("text", "")))
            segment_words = segment_text.split()
            if not segment_words:
                continue

            relative_start = consumed_words / transcript_total_words
            approx_reference_index = int(round(relative_start * total_reference_words))
            window_size = len(segment_words)
            start_index = max(0, min(total_reference_words, approx_reference_index))
            end_index = min(total_reference_words, start_index + window_size)
            expected_excerpt = " ".join(reference_words[start_index:end_index])
            local_wer = compute_word_error_rate(expected_excerpt, segment_text) if expected_excerpt else 1.0

            if local_wer > self.SEGMENT_WARNING_THRESHOLD:
                severity = "warning" if local_wer <= self.SEGMENT_ERROR_THRESHOLD else "error"
                issues.append(
                    AudioQAIssue(
                        code="segment_mismatch",
                        category="transcription",
                        severity=severity,
                        message=(
                            f"Transcript segment around {float(segment.get('start', 0.0)):.2f}s "
                            f"has local WER {local_wer:.3f}."
                        ),
                        start_time_seconds=float(segment.get("start", 0.0)),
                        end_time_seconds=float(segment.get("end", segment.get("start", 0.0))),
                        details={
                            "segment_index": index,
                            "expected_excerpt": expected_excerpt,
                            "actual_excerpt": segment_text,
                            "word_error_rate": local_wer,
                        },
                    )
                )

            consumed_words += len(segment_words)

        return issues
