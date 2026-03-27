"""Audio quality analysis for loudness, clipping, SNR, and artifact detection."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

import numpy as np

from src.pipeline.audio_qa.models import AudioQAIssue, AudioQualityAnalysis, DependencyNotice

logger = logging.getLogger(__name__)


class AudioQualityAnalyzer:
    """Evaluate mastered audio quality for one generated chapter."""

    LUFS_TARGET_RANGE = (-23.0, -18.0)
    LUFS_FAIL_MARGIN = 3.0
    PEAK_WARNING_DBFS = -3.0
    PEAK_FAIL_DBFS = -1.0
    SNR_WARNING_DB = 20.0
    SNR_FAIL_DB = 12.0
    CLIPPING_WARNING_RATIO = 0.0001
    CLIPPING_FAIL_RATIO = 0.001
    DROPOUT_RMS_THRESHOLD = 1e-4
    DROPOUT_MIN_SECONDS = 0.10

    def analyze(self, audio_path: str | Path) -> AudioQualityAnalysis:
        """Run loudness, clipping, SNR, and artifact checks."""

        try:
            samples, sample_rate = self._load_audio(audio_path)
        except RuntimeError as exc:
            return AudioQualityAnalysis(
                dependency=DependencyNotice(dependency="soundfile", available=False, message=str(exc)),
                status="dependency_unavailable",
                issues=[
                    AudioQAIssue(
                        code="missing_audio_quality_dependency",
                        category="quality",
                        severity="warning",
                        message=str(exc),
                    )
                ],
            )

        mono_samples = self._to_mono(samples)
        if mono_samples.size == 0:
            return AudioQualityAnalysis(
                status="fail",
                issues=[
                    AudioQAIssue(
                        code="empty_audio",
                        category="quality",
                        severity="error",
                        message="Audio file contains no samples.",
                    )
                ],
            )

        integrated_lufs, loudness_range_lu, dependency_notice = self._measure_loudness(audio_path, mono_samples, sample_rate)
        peak_dbfs = self._peak_dbfs(mono_samples)
        snr_db = self._estimate_snr(mono_samples, sample_rate)
        clipping_ratio, clipping_events = self._detect_clipping(mono_samples, sample_rate)
        artifact_events = clipping_events + self._detect_dropouts(mono_samples, sample_rate)
        issues = artifact_events + self._metric_issues(integrated_lufs, peak_dbfs, snr_db, clipping_ratio)
        score = self._score(integrated_lufs, peak_dbfs, snr_db, clipping_ratio, issues)
        status = self._status(score, issues)

        return AudioQualityAnalysis(
            dependency=dependency_notice,
            integrated_lufs=round(integrated_lufs, 3) if integrated_lufs is not None else None,
            loudness_range_lu=round(loudness_range_lu, 3) if loudness_range_lu is not None else None,
            peak_dbfs=round(peak_dbfs, 3) if peak_dbfs is not None else None,
            snr_db=round(snr_db, 3) if snr_db is not None else None,
            clipping_ratio=round(clipping_ratio, 6),
            artifact_events=artifact_events,
            score=score,
            status=status,
            issues=issues,
        )

    def _load_audio(self, audio_path: str | Path) -> tuple[Any, int]:
        """Load decoded audio samples for analysis."""

        try:
            import soundfile as sf  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "soundfile is not installed; run `pip install soundfile` to enable audio quality QA."
            ) from exc

        samples, sample_rate = sf.read(str(audio_path), always_2d=False)
        return (samples, int(sample_rate))

    def _to_mono(self, samples: Any) -> np.ndarray:
        """Convert soundfile output into a mono float array."""

        array = np.asarray(samples, dtype=np.float32)
        if array.ndim == 2:
            return np.mean(array, axis=1)
        return array

    def _measure_loudness(
        self,
        audio_path: str | Path,
        mono_samples: np.ndarray,
        sample_rate: int,
    ) -> tuple[float | None, float | None, DependencyNotice]:
        """Measure integrated loudness, preferring pyloudnorm with a fallback path."""

        try:
            import pyloudnorm as pyln  # type: ignore

            meter = pyln.Meter(sample_rate)
            float64_samples = mono_samples.astype(np.float64, copy=False)
            integrated_lufs = float(meter.integrated_loudness(float64_samples))
            loudness_range = None
            loudness_range_method = getattr(meter, "loudness_range", None)
            if callable(loudness_range_method):
                loudness_range = float(loudness_range_method(float64_samples))
            return (
                integrated_lufs,
                loudness_range,
                DependencyNotice(dependency="pyloudnorm", available=True),
            )
        except Exception as exc:
            logger.warning("pyloudnorm measurement unavailable for %s, falling back to ffmpeg: %s", audio_path, exc)

        fallback_lufs = self._measure_loudness_with_ffmpeg(audio_path)
        return (
            fallback_lufs,
            None,
            DependencyNotice(
                dependency="pyloudnorm",
                available=False,
                message="pyloudnorm unavailable at runtime; using ffmpeg loudnorm fallback.",
            ),
        )

    def _measure_loudness_with_ffmpeg(self, audio_path: str | Path) -> float | None:
        """Fallback LUFS measurement using ffmpeg loudnorm."""

        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path is None:
            return None

        command = [
            ffmpeg_path,
            "-hide_banner",
            "-i",
            str(audio_path),
            "-af",
            "loudnorm=I=-19:TP=-1.5:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ]
        try:
            completed = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None

        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        match = re.search(r"(\{\s*\"input_i\".*?\})", output, re.DOTALL)
        if match is None:
            return None

        try:
            metrics = json.loads(match.group(1))
            return float(metrics["input_i"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _peak_dbfs(self, mono_samples: np.ndarray) -> float | None:
        """Return true peak approximation in dBFS."""

        peak = float(np.max(np.abs(mono_samples))) if mono_samples.size else 0.0
        if peak <= 0:
            return None
        return 20.0 * np.log10(peak)

    def _estimate_snr(self, mono_samples: np.ndarray, sample_rate: int) -> float | None:
        """Estimate SNR from frame RMS percentiles."""

        if mono_samples.size == 0 or sample_rate <= 0:
            return None

        frame_length = max(1, int(sample_rate * 0.05))
        if mono_samples.size < frame_length:
            frame = mono_samples
            rms_values = np.array([np.sqrt(np.mean(np.square(frame)))], dtype=np.float32)
        else:
            rms_values = []
            for start in range(0, len(mono_samples) - frame_length + 1, frame_length):
                frame = mono_samples[start:start + frame_length]
                rms_values.append(float(np.sqrt(np.mean(np.square(frame)))))
            rms_values = np.asarray(rms_values, dtype=np.float32)

        positive = rms_values[rms_values > 1e-6]
        if positive.size == 0:
            return None

        noise_floor = float(np.percentile(positive, 10))
        signal_floor = float(np.percentile(positive, 90))
        if noise_floor <= 0:
            return None
        return 20.0 * np.log10(signal_floor / noise_floor)

    def _detect_clipping(self, mono_samples: np.ndarray, sample_rate: int) -> tuple[float, list[AudioQAIssue]]:
        """Detect clipped sample runs and return their timestamps."""

        if mono_samples.size == 0 or sample_rate <= 0:
            return (0.0, [])

        clipped_mask = np.abs(mono_samples) >= 0.999
        clipping_ratio = float(np.mean(clipped_mask))
        if not np.any(clipped_mask):
            return (clipping_ratio, [])

        events: list[AudioQAIssue] = []
        indices = np.flatnonzero(clipped_mask)
        run_start = int(indices[0])
        previous = int(indices[0])
        for index in indices[1:]:
            index = int(index)
            if index == previous + 1:
                previous = index
                continue
            events.append(self._clipping_issue(run_start, previous + 1, sample_rate))
            run_start = index
            previous = index
        events.append(self._clipping_issue(run_start, previous + 1, sample_rate))
        return (clipping_ratio, events)

    def _clipping_issue(self, start_index: int, end_index: int, sample_rate: int) -> AudioQAIssue:
        """Build one clipping artifact issue."""

        duration_seconds = max(0.0, (end_index - start_index) / float(sample_rate))
        severity = "warning" if duration_seconds <= 0.02 else "error"
        return AudioQAIssue(
            code="clipping_event",
            category="quality",
            severity=severity,
            message=f"Detected clipped samples from {start_index / sample_rate:.3f}s to {end_index / sample_rate:.3f}s.",
            start_time_seconds=round(start_index / float(sample_rate), 4),
            end_time_seconds=round(end_index / float(sample_rate), 4),
            details={"duration_seconds": round(duration_seconds, 6)},
        )

    def _detect_dropouts(self, mono_samples: np.ndarray, sample_rate: int) -> list[AudioQAIssue]:
        """Detect abrupt low-energy dropouts inside otherwise non-silent audio."""

        if mono_samples.size == 0 or sample_rate <= 0:
            return []

        frame_length = max(1, int(sample_rate * 0.05))
        hop = frame_length
        rms_values: list[float] = []
        for start in range(0, len(mono_samples) - frame_length + 1, hop):
            frame = mono_samples[start:start + frame_length]
            rms_values.append(float(np.sqrt(np.mean(np.square(frame)))))

        if not rms_values:
            return []

        frames = np.asarray(rms_values, dtype=np.float32)
        active_threshold = max(self.DROPOUT_RMS_THRESHOLD * 10.0, float(np.percentile(frames, 70)) * 0.25)
        dropout_mask = frames <= self.DROPOUT_RMS_THRESHOLD
        if not np.any(dropout_mask):
            return []

        issues: list[AudioQAIssue] = []
        start_frame: int | None = None
        for index, is_dropout in enumerate(dropout_mask):
            if is_dropout and start_frame is None:
                start_frame = index
            elif not is_dropout and start_frame is not None:
                issues.extend(self._finalize_dropout(start_frame, index, frames, active_threshold, frame_length, sample_rate))
                start_frame = None
        if start_frame is not None:
            issues.extend(self._finalize_dropout(start_frame, len(frames), frames, active_threshold, frame_length, sample_rate))
        return issues

    def _finalize_dropout(
        self,
        start_frame: int,
        end_frame: int,
        frames: np.ndarray,
        active_threshold: float,
        frame_length: int,
        sample_rate: int,
    ) -> list[AudioQAIssue]:
        """Emit a dropout issue when silence is sandwiched by active audio."""

        start_sample = start_frame * frame_length
        end_sample = end_frame * frame_length
        duration_seconds = (end_sample - start_sample) / float(sample_rate)
        if duration_seconds < self.DROPOUT_MIN_SECONDS:
            return []

        left_active = start_frame > 0 and frames[start_frame - 1] >= active_threshold
        right_active = end_frame < len(frames) and frames[min(end_frame, len(frames) - 1)] >= active_threshold
        if not (left_active and right_active):
            return []

        severity = "warning" if duration_seconds <= 0.25 else "error"
        return [
            AudioQAIssue(
                code="audio_dropout",
                category="quality",
                severity=severity,
                message=f"Detected an abrupt low-energy dropout lasting {duration_seconds:.2f}s.",
                start_time_seconds=round(start_sample / float(sample_rate), 4),
                end_time_seconds=round(end_sample / float(sample_rate), 4),
                details={"duration_seconds": round(duration_seconds, 4)},
            )
        ]

    def _metric_issues(
        self,
        integrated_lufs: float | None,
        peak_dbfs: float | None,
        snr_db: float | None,
        clipping_ratio: float,
    ) -> list[AudioQAIssue]:
        """Return threshold-based metric issues."""

        issues: list[AudioQAIssue] = []
        if integrated_lufs is not None:
            lufs_low, lufs_high = self.LUFS_TARGET_RANGE
            if not (lufs_low <= integrated_lufs <= lufs_high):
                deviation = min(abs(integrated_lufs - lufs_low), abs(integrated_lufs - lufs_high))
                severity = "warning" if deviation <= self.LUFS_FAIL_MARGIN else "error"
                issues.append(
                    AudioQAIssue(
                        code="loudness_out_of_range",
                        category="quality",
                        severity=severity,
                        message=f"Integrated loudness {integrated_lufs:.2f} LUFS is outside the audiobook target range.",
                        details={"integrated_lufs": round(integrated_lufs, 4)},
                    )
                )

        if peak_dbfs is not None and peak_dbfs > self.PEAK_WARNING_DBFS:
            severity = "warning" if peak_dbfs <= self.PEAK_FAIL_DBFS else "error"
            issues.append(
                AudioQAIssue(
                    code="peak_too_hot",
                    category="quality",
                    severity=severity,
                    message=f"Peak level {peak_dbfs:.2f} dBFS is too hot for export.",
                    details={"peak_dbfs": round(peak_dbfs, 4)},
                )
            )

        if snr_db is not None and snr_db < self.SNR_WARNING_DB:
            severity = "warning" if snr_db >= self.SNR_FAIL_DB else "error"
            issues.append(
                AudioQAIssue(
                    code="low_snr",
                    category="quality",
                    severity=severity,
                    message=f"Estimated SNR {snr_db:.2f} dB is below the target floor.",
                    details={"snr_db": round(snr_db, 4)},
                )
            )

        if clipping_ratio > self.CLIPPING_WARNING_RATIO:
            severity = "warning" if clipping_ratio <= self.CLIPPING_FAIL_RATIO else "error"
            issues.append(
                AudioQAIssue(
                    code="clipping_ratio_high",
                    category="quality",
                    severity=severity,
                    message=f"Clipping ratio {clipping_ratio:.4%} exceeds the safe threshold.",
                    details={"clipping_ratio": round(clipping_ratio, 8)},
                )
            )

        return issues

    def _score(
        self,
        integrated_lufs: float | None,
        peak_dbfs: float | None,
        snr_db: float | None,
        clipping_ratio: float,
        issues: list[AudioQAIssue],
    ) -> float:
        """Compute a 0-100 quality score."""

        score = 100.0
        if integrated_lufs is not None:
            lufs_low, lufs_high = self.LUFS_TARGET_RANGE
            if integrated_lufs < lufs_low:
                score -= min(25.0, (lufs_low - integrated_lufs) * 5.0)
            elif integrated_lufs > lufs_high:
                score -= min(25.0, (integrated_lufs - lufs_high) * 5.0)

        if peak_dbfs is not None and peak_dbfs > self.PEAK_WARNING_DBFS:
            score -= min(20.0, (peak_dbfs - self.PEAK_WARNING_DBFS) * 8.0)

        if snr_db is not None and snr_db < self.SNR_WARNING_DB:
            score -= min(25.0, (self.SNR_WARNING_DB - snr_db) * 1.5)

        if clipping_ratio > self.CLIPPING_WARNING_RATIO:
            score -= 10.0 if clipping_ratio <= self.CLIPPING_FAIL_RATIO else 25.0

        for issue in issues:
            if issue.severity == "warning":
                score -= 5.0
            elif issue.severity == "error":
                score -= 12.0

        return round(max(0.0, min(100.0, score)), 2)

    def _status(self, score: float, issues: list[AudioQAIssue]) -> str:
        """Return the quality-stage status."""

        if any(issue.severity == "error" for issue in issues) or score < 70.0:
            return "fail"
        if any(issue.severity == "warning" for issue in issues) or score < 85.0:
            return "warning"
        return "pass"
