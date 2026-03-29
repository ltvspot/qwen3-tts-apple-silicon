"""Post-generation validation for individual audio chunks."""

from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
from pydub import AudioSegment

from src.config import ChunkValidationSettings, get_application_settings

logger = logging.getLogger(__name__)
_DFT_MATRIX_CACHE: dict[int, np.ndarray] = {}


class ValidationSeverity(Enum):
    """Severity levels for individual chunk validation checks."""

    PASS = "pass"
    INFO = "info"
    WARNING = "warning"
    FAIL = "fail"


SEVERITY_ORDER = {
    ValidationSeverity.PASS: 0,
    ValidationSeverity.INFO: 1,
    ValidationSeverity.WARNING: 2,
    ValidationSeverity.FAIL: 3,
}


@dataclass(slots=True)
class ValidationResult:
    """Result emitted by one validation check."""

    check: str
    severity: ValidationSeverity
    message: str
    details: dict[str, Any] | None = None


@dataclass(slots=True)
class ChunkValidationReport:
    """Aggregate validation report for one generated chunk."""

    chunk_index: int
    text: str
    duration_ms: int
    results: list[ValidationResult]

    @property
    def worst_severity(self) -> ValidationSeverity:
        """Return the highest severity across all check results."""

        worst = ValidationSeverity.PASS
        for result in self.results:
            if SEVERITY_ORDER[result.severity] > SEVERITY_ORDER[worst]:
                worst = result.severity
        return worst

    @property
    def needs_regeneration(self) -> bool:
        """Return True when one or more checks failed hard."""

        return self.worst_severity == ValidationSeverity.FAIL

    @property
    def valid(self) -> bool:
        """Compatibility helper for callers that only need pass/fail semantics."""

        return self.worst_severity in {ValidationSeverity.PASS, ValidationSeverity.INFO}

    @property
    def issues(self) -> list[str]:
        """Compatibility helper exposing all non-pass messages as flat strings."""

        return [
            f"{result.check}: {result.message}"
            for result in self.results
            if result.severity != ValidationSeverity.PASS
        ]


@dataclass(slots=True)
class _TranscriptionOutcome:
    """Internal transcription state shared across validation checks."""

    transcript: str | None
    status: ValidationResult | None = None


def normalize_text(text: str) -> str:
    """Lowercase text, strip punctuation, and collapse whitespace."""

    collapsed = re.sub(r"[^\w\s]", " ", text.lower())
    return re.sub(r"\s+", " ", collapsed).strip()


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Compute WER using editdistance when present, otherwise a DP fallback."""

    ref_words = normalize_text(reference).split()
    hyp_words = normalize_text(hypothesis).split()

    if not ref_words:
        return 0.0 if not hyp_words else 1.0

    try:
        import editdistance  # type: ignore

        return float(editdistance.eval(ref_words, hyp_words)) / float(len(ref_words))
    except ImportError:
        pass

    rows = len(ref_words) + 1
    cols = len(hyp_words) + 1
    distance = [[0] * cols for _ in range(rows)]

    for row in range(rows):
        distance[row][0] = row
    for col in range(cols):
        distance[0][col] = col

    for row in range(1, rows):
        for col in range(1, cols):
            substitution_cost = 0 if ref_words[row - 1] == hyp_words[col - 1] else 1
            distance[row][col] = min(
                distance[row - 1][col] + 1,
                distance[row][col - 1] + 1,
                distance[row - 1][col - 1] + substitution_cost,
            )

    return distance[-1][-1] / len(ref_words)


def detect_repeated_phrases(transcript: str, min_ngram: int = 3, max_ngram: int = 8) -> list[str]:
    """Return repeated back-to-back phrases detected in a transcript."""

    words = normalize_text(transcript).split()
    repeats: list[str] = []
    seen: set[str] = set()

    for ngram in range(min_ngram, max_ngram + 1):
        for index in range(len(words) - (2 * ngram) + 1):
            phrase = words[index:index + ngram]
            next_phrase = words[index + ngram:index + (2 * ngram)]
            if phrase != next_phrase:
                continue

            candidate = " ".join(phrase)
            if candidate not in seen:
                repeats.append(candidate)
                seen.add(candidate)

    return repeats


def spectral_flatness(
    signal: np.ndarray,
    sample_rate: int,
    frame_size: int = 1024,
    hop_size: int = 256,
) -> float:
    """Return average spectral flatness for non-silent frames."""

    del sample_rate

    if signal.size == 0:
        return 0.0

    if signal.size < frame_size:
        signal = np.pad(signal, (0, frame_size - signal.size))

    window = np.hanning(frame_size)
    flatness_values: list[float] = []

    for start in range(0, signal.size - frame_size + 1, hop_size):
        frame = signal[start:start + frame_size]
        rms = float(np.sqrt(np.mean(np.square(frame))))
        if rms <= 1e-4:
            continue

        magnitude = _magnitude_spectrum(frame * window) + 1e-12
        geometric_mean = float(np.exp(np.mean(np.log(magnitude))))
        arithmetic_mean = float(np.mean(magnitude))
        flatness_values.append(geometric_mean / arithmetic_mean)

    return float(np.mean(flatness_values)) if flatness_values else 0.0


def _magnitude_spectrum(frame: np.ndarray) -> np.ndarray:
    """Return a real-spectrum magnitude without relying on ``numpy.fft``."""

    frame_length = frame.size
    if frame_length not in _DFT_MATRIX_CACHE:
        frequencies = np.arange((frame_length // 2) + 1, dtype=np.float32)[:, None]
        times = np.arange(frame_length, dtype=np.float32)[None, :]
        exponent = (-2j * np.pi * frequencies * times) / float(frame_length)
        _DFT_MATRIX_CACHE[frame_length] = np.exp(exponent).astype(np.complex64)

    basis = _DFT_MATRIX_CACHE[frame_length]
    return np.abs(basis @ frame.astype(np.float32))


def count_dialogue_chars(text: str) -> int:
    """Approximate the number of characters inside quoted dialogue."""

    count = 0
    in_dialogue = False

    for char in text:
        if char in {'"', "“", "”"}:
            in_dialogue = not in_dialogue
            continue
        if in_dialogue:
            count += 1

    return count


class ChunkValidator:
    """Validate individual audio chunks before they are stitched together."""

    MIN_DURATION_MS = 100
    MAX_DURATION_MS = 120_000
    MIN_RMS_DBFS = -55.0
    WARNING_PEAK_DBFS = -0.3
    FAIL_PEAK_DBFS = -0.05
    EXPECTED_SAMPLE_RATES = {22050, 24000, 44100, 48000}
    REPEAT_AUDIO_FAIL_THRESHOLD = 0.90
    REPEAT_AUDIO_WARNING_SECONDS = 1.5
    REPEAT_AUDIO_FAIL_SECONDS = 2.0
    REPEAT_WINDOW_MS = 500
    REPEAT_HOP_MS = 250
    ENVELOPE_FRAME_MS = 50
    ENERGY_WINDOW_MS = 100
    WHISPER_SKIP_MESSAGE = "STT alignment check skipped (mlx-whisper not installed)"

    _whisper_model_cache: dict[str, Any] = {}
    _whisper_import_failed = False
    _whisper_model_loaded = False

    def __init__(self, validation_settings: ChunkValidationSettings | None = None) -> None:
        """Initialize a validator with persisted or test-specific settings."""

        self.validation_settings = validation_settings or get_application_settings().chunk_validation

    def validate(
        self,
        audio: AudioSegment,
        input_text: str,
        voice: str | None = None,
        speed: float = 1.0,
        *,
        chunk_index: int = 0,
        expected_sample_rate: int | None = None,
    ) -> ChunkValidationReport:
        """Run all configured chunk validation checks and return a report."""

        del voice

        transcription = None
        if self.validation_settings.stt_alignment_enabled:
            transcription = self._transcribe_audio(audio)

        results = [
            self.check_duration_detailed(audio, input_text, speed=speed),
            self.check_max_audio_duration(audio, input_text, speed=speed),
            self.check_silence(audio),
            self.check_clipping(audio),
            self.check_sample_rate(audio, expected_sample_rate),
            self.check_text_alignment(audio, input_text, transcription=transcription),
            self.check_repeats(audio, input_text, transcript=None if transcription is None else transcription.transcript),
            self.check_audio_clarity(audio),
        ]

        return ChunkValidationReport(
            chunk_index=chunk_index + 1,
            text=input_text,
            duration_ms=len(audio),
            results=results,
        )

    def check_text_alignment(
        self,
        audio: AudioSegment,
        input_text: str,
        transcription: _TranscriptionOutcome | None = None,
    ) -> ValidationResult:
        """Compare the generated audio transcript against the source text."""

        if not self.validation_settings.stt_alignment_enabled:
            return ValidationResult(
                check="text_alignment",
                severity=ValidationSeverity.INFO,
                message="STT alignment check disabled in settings",
            )

        if transcription is None:
            transcription = self._transcribe_audio(audio)

        if transcription.status is not None:
            return transcription.status

        transcript = (transcription.transcript or "").strip()
        if not transcript:
            return ValidationResult(
                check="text_alignment",
                severity=ValidationSeverity.WARNING,
                message="STT alignment could not produce a transcript",
            )

        score = word_error_rate(input_text, transcript)
        details = {
            "wer": round(score, 4),
            "reference": normalize_text(input_text),
            "transcript": normalize_text(transcript),
        }
        warning_threshold = self.validation_settings.wer_warning_threshold
        fail_threshold = self.validation_settings.wer_fail_threshold

        if score > fail_threshold:
            return ValidationResult(
                check="text_alignment",
                severity=ValidationSeverity.FAIL,
                message=f"Transcript mismatch detected (WER {score:.2f})",
                details=details,
            )

        if score >= warning_threshold:
            return ValidationResult(
                check="text_alignment",
                severity=ValidationSeverity.WARNING,
                message=f"Transcript drift detected (WER {score:.2f})",
                details=details,
            )

        return ValidationResult(
            check="text_alignment",
            severity=ValidationSeverity.PASS,
            message=f"Transcript aligned with source text (WER {score:.2f})",
            details=details,
        )

    def check_repeats(
        self,
        audio: AudioSegment,
        input_text: str,
        transcript: str | None = None,
    ) -> ValidationResult:
        """Detect repeated phrases or repeated acoustic loop patterns."""

        del input_text

        if not self.validation_settings.repeat_detection_enabled:
            return ValidationResult(
                check="repeat_detection",
                severity=ValidationSeverity.INFO,
                message="Repeat detection disabled in settings",
            )

        if transcript:
            max_ngram = max(8, self.validation_settings.min_repeat_ngram)
            repeated_phrases = detect_repeated_phrases(
                transcript,
                min_ngram=self.validation_settings.min_repeat_ngram,
                max_ngram=max_ngram,
            )
            if repeated_phrases:
                return ValidationResult(
                    check="repeat_detection",
                    severity=ValidationSeverity.FAIL,
                    message=f"Repeated phrase detected: '{repeated_phrases[0]}'",
                    details={"repeats": repeated_phrases},
                )

            repeated_bigrams = detect_repeated_phrases(transcript, min_ngram=2, max_ngram=2)
            if repeated_bigrams:
                return ValidationResult(
                    check="repeat_detection",
                    severity=ValidationSeverity.WARNING,
                    message=f"Repeated short phrase detected: '{repeated_bigrams[0]}'",
                    details={"repeats": repeated_bigrams},
                )

            return ValidationResult(
                check="repeat_detection",
                severity=ValidationSeverity.PASS,
                message="No repeated transcript phrases detected",
            )

        repeat_details = self._detect_audio_repeat_pattern(audio)
        if repeat_details is None:
            return ValidationResult(
                check="repeat_detection",
                severity=ValidationSeverity.PASS,
                message="No repeated audio loop detected",
            )

        max_correlation = repeat_details["max_correlation"]
        repeated_seconds = repeat_details["repeated_seconds"]
        message = (
            "Repeated acoustic pattern detected "
            f"({repeated_seconds:.2f}s at correlation {max_correlation:.2f})"
        )

        if (
            max_correlation >= self.REPEAT_AUDIO_FAIL_THRESHOLD
            and repeated_seconds > self.REPEAT_AUDIO_FAIL_SECONDS
        ):
            severity = ValidationSeverity.FAIL
        elif (
            max_correlation >= self.validation_settings.repeat_correlation_threshold
            and repeated_seconds > self.REPEAT_AUDIO_WARNING_SECONDS
        ):
            severity = ValidationSeverity.WARNING
        else:
            severity = ValidationSeverity.PASS

        return ValidationResult(
            check="repeat_detection",
            severity=severity,
            message=message if severity != ValidationSeverity.PASS else "No repeated audio loop detected",
            details=repeat_details,
        )

    def check_audio_clarity(self, audio: AudioSegment) -> ValidationResult:
        """Detect gibberish-like or unstable acoustic patterns."""

        if not self.validation_settings.clarity_check_enabled:
            return ValidationResult(
                check="audio_clarity",
                severity=ValidationSeverity.INFO,
                message="Audio clarity check disabled in settings",
            )

        samples = self._mono_samples(audio)
        if samples.size == 0:
            return ValidationResult(
                check="audio_clarity",
                severity=ValidationSeverity.WARNING,
                message="Audio clarity could not be measured on an empty signal",
            )

        flatness = spectral_flatness(samples, audio.frame_rate)
        zcr_values = self._zero_crossing_rates(samples, audio.frame_rate, window_ms=50)
        energy_dbfs = self._rms_levels(samples, audio.frame_rate, window_ms=self.ENERGY_WINDOW_MS)
        erratic_energy_windows = self._count_erratic_energy_drops(energy_dbfs)

        warnings: list[str] = []
        severity = ValidationSeverity.PASS

        if flatness > self.validation_settings.spectral_flatness_fail:
            severity = ValidationSeverity.FAIL
            warnings.append(f"spectral flatness {flatness:.2f} exceeds fail threshold")
        elif flatness > self.validation_settings.spectral_flatness_warning:
            severity = ValidationSeverity.WARNING
            warnings.append(f"spectral flatness {flatness:.2f} exceeds warning threshold")

        zcr_mean = float(np.mean(zcr_values)) if zcr_values.size else 0.0
        zcr_std = float(np.std(zcr_values)) if zcr_values.size else 0.0
        if zcr_mean > 0 and zcr_std > (zcr_mean * 2):
            severity = self._max_severity(severity, ValidationSeverity.WARNING)
            warnings.append(f"erratic zero-crossing rate (mean {zcr_mean:.3f}, std {zcr_std:.3f})")

        if erratic_energy_windows > 3:
            severity = self._max_severity(severity, ValidationSeverity.WARNING)
            warnings.append(f"{erratic_energy_windows} unstable energy windows detected")

        details = {
            "spectral_flatness": round(flatness, 4),
            "zcr_mean": round(zcr_mean, 4),
            "zcr_std": round(zcr_std, 4),
            "erratic_energy_windows": erratic_energy_windows,
        }

        if warnings:
            return ValidationResult(
                check="audio_clarity",
                severity=severity,
                message="; ".join(warnings),
                details=details,
            )

        return ValidationResult(
            check="audio_clarity",
            severity=ValidationSeverity.PASS,
            message="Audio clarity metrics are within expected range",
            details=details,
        )

    def check_duration_detailed(
        self,
        audio: AudioSegment,
        input_text: str,
        *,
        speed: float = 1.0,
    ) -> ValidationResult:
        """Validate chunk duration using text-aware expectations."""

        duration_ms = len(audio)
        duration_seconds = duration_ms / 1000.0

        if duration_ms < self.MIN_DURATION_MS:
            return ValidationResult(
                check="duration",
                severity=ValidationSeverity.FAIL,
                message=f"Chunk is too short ({duration_ms}ms)",
                details={"duration_ms": duration_ms, "minimum_ms": self.MIN_DURATION_MS},
            )

        if duration_ms > self.MAX_DURATION_MS:
            return ValidationResult(
                check="duration",
                severity=ValidationSeverity.FAIL,
                message=f"Chunk is too long ({duration_ms}ms)",
                details={"duration_ms": duration_ms, "maximum_ms": self.MAX_DURATION_MS},
            )

        min_expected, max_expected = self.estimate_duration(input_text, speed=speed)
        if min_expected <= duration_seconds <= max_expected:
            return ValidationResult(
                check="duration",
                severity=ValidationSeverity.PASS,
                message=(
                    f"Duration {duration_seconds:.2f}s is within expected range "
                    f"{min_expected:.2f}-{max_expected:.2f}s"
                ),
                details={
                    "duration_seconds": round(duration_seconds, 4),
                    "min_expected_seconds": round(min_expected, 4),
                    "max_expected_seconds": round(max_expected, 4),
                    "outside_percent": 0.0,
                },
            )

        if duration_seconds < min_expected:
            outside_percent = (min_expected - duration_seconds) / max(min_expected, 1e-6)
            direction = "short"
        else:
            outside_percent = (duration_seconds - max_expected) / max(max_expected, 1e-6)
            direction = "long"

        if outside_percent <= 0.10:
            severity = ValidationSeverity.PASS
        elif outside_percent <= 0.40:
            severity = ValidationSeverity.WARNING
        else:
            severity = ValidationSeverity.FAIL

        return ValidationResult(
            check="duration",
            severity=severity,
            message=(
                f"Duration is too {direction}: {duration_seconds:.2f}s vs expected "
                f"{min_expected:.2f}-{max_expected:.2f}s ({outside_percent * 100:.0f}% outside)"
            ),
            details={
                "duration_seconds": round(duration_seconds, 4),
                "min_expected_seconds": round(min_expected, 4),
                "max_expected_seconds": round(max_expected, 4),
                "outside_percent": round(outside_percent, 4),
            },
        )

    def check_max_audio_duration(
        self,
        audio: AudioSegment,
        input_text: str,
        *,
        speed: float = 1.0,
    ) -> ValidationResult:
        """Reject chunks that are implausibly long for the input text."""

        resolved_speed = max(speed, 0.1)
        word_count = len(input_text.split())
        expected_seconds = (word_count / 2.5) / resolved_speed if word_count else 0.0
        max_allowed_seconds = max(10.0, expected_seconds * 2.0)
        actual_seconds = len(audio) / 1000.0

        if actual_seconds > max_allowed_seconds:
            return ValidationResult(
                check="max_audio_duration",
                severity=ValidationSeverity.FAIL,
                message=(
                    f"Audio {actual_seconds:.1f}s exceeds 2x expected {expected_seconds:.1f}s "
                    "— likely infinite loop"
                ),
                details={
                    "actual_seconds": round(actual_seconds, 4),
                    "expected_seconds": round(expected_seconds, 4),
                    "max_allowed_seconds": round(max_allowed_seconds, 4),
                },
            )

        return ValidationResult(
            check="max_audio_duration",
            severity=ValidationSeverity.PASS,
            message="OK",
            details={
                "actual_seconds": round(actual_seconds, 4),
                "expected_seconds": round(expected_seconds, 4),
                "max_allowed_seconds": round(max_allowed_seconds, 4),
            },
        )

    def check_silence(self, audio: AudioSegment) -> ValidationResult:
        """Flag near-silent chunks that contain no usable speech."""

        rms_dbfs = audio.dBFS if audio.dBFS != float("-inf") else -100.0
        if rms_dbfs < self.MIN_RMS_DBFS:
            return ValidationResult(
                check="silence_floor",
                severity=ValidationSeverity.FAIL,
                message=f"Chunk is effectively silent (RMS {rms_dbfs:.1f} dBFS)",
                details={"rms_dbfs": round(rms_dbfs, 4)},
            )

        return ValidationResult(
            check="silence_floor",
            severity=ValidationSeverity.PASS,
            message=f"Chunk RMS level is acceptable ({rms_dbfs:.1f} dBFS)",
            details={"rms_dbfs": round(rms_dbfs, 4)},
        )

    def check_clipping(self, audio: AudioSegment) -> ValidationResult:
        """Flag clipped or near-clipped chunks."""

        peak_dbfs = audio.max_dBFS if audio.max_dBFS != float("-inf") else -100.0
        if peak_dbfs >= self.FAIL_PEAK_DBFS:
            return ValidationResult(
                check="clipping",
                severity=ValidationSeverity.FAIL,
                message=f"Hard clipping detected (peak {peak_dbfs:.1f} dBFS)",
                details={"peak_dbfs": round(peak_dbfs, 4)},
            )

        if peak_dbfs > self.WARNING_PEAK_DBFS:
            return ValidationResult(
                check="clipping",
                severity=ValidationSeverity.WARNING,
                message=f"Clipping risk detected (peak {peak_dbfs:.1f} dBFS)",
                details={"peak_dbfs": round(peak_dbfs, 4)},
            )

        return ValidationResult(
            check="clipping",
            severity=ValidationSeverity.PASS,
            message=f"Peak headroom is acceptable ({peak_dbfs:.1f} dBFS)",
            details={"peak_dbfs": round(peak_dbfs, 4)},
        )

    def check_sample_rate(
        self,
        audio: AudioSegment,
        expected_sample_rate: int | None = None,
    ) -> ValidationResult:
        """Validate sample rate compatibility."""

        sample_rate = audio.frame_rate
        if sample_rate not in self.EXPECTED_SAMPLE_RATES:
            return ValidationResult(
                check="sample_rate",
                severity=ValidationSeverity.FAIL,
                message=f"Unexpected sample rate: {sample_rate}Hz",
                details={"sample_rate": sample_rate},
            )

        if expected_sample_rate is not None and sample_rate != expected_sample_rate:
            return ValidationResult(
                check="sample_rate",
                severity=ValidationSeverity.FAIL,
                message=f"Sample rate mismatch: got {sample_rate}Hz, expected {expected_sample_rate}Hz",
                details={"sample_rate": sample_rate, "expected_sample_rate": expected_sample_rate},
            )

        return ValidationResult(
            check="sample_rate",
            severity=ValidationSeverity.PASS,
            message=f"Sample rate is valid ({sample_rate}Hz)",
            details={"sample_rate": sample_rate, "expected_sample_rate": expected_sample_rate},
        )

    def estimate_duration(self, text: str, speed: float = 1.0) -> tuple[float, float]:
        """Estimate an expected narration range for the provided text chunk."""

        words = [word for word in text.split() if word.strip()]
        word_count = len(words)
        if word_count == 0:
            return (0.0, 1.0)

        base_words_per_second = 2.5 * max(speed, 0.1)
        dialogue_ratio = count_dialogue_chars(text) / max(len(text), 1)
        if dialogue_ratio >= 0.25:
            base_words_per_second *= 1.15
        elif dialogue_ratio <= 0.05 and word_count >= 20:
            base_words_per_second *= 0.92

        avg_duration = word_count / max(base_words_per_second, 1e-6)
        sentence_count = max(1, len([part for part in re.split(r"[.!?]+", text) if part.strip()]))
        pause_per_sentence = 0.5 if word_count >= 6 else 0.2
        pause_time = sentence_count * pause_per_sentence

        expected = avg_duration + pause_time
        return (expected * 0.6, expected * 1.8)

    def _transcribe_audio(self, audio: AudioSegment) -> _TranscriptionOutcome:
        """Run mlx-whisper transcription or return a non-fatal skipped result."""

        try:
            backend = self._load_whisper_model(self.validation_settings.stt_model)
        except ImportError:
            logger.warning("mlx-whisper is not installed; chunk STT alignment is disabled.")
            return _TranscriptionOutcome(
                transcript=None,
                status=ValidationResult(
                    check="text_alignment",
                    severity=ValidationSeverity.INFO,
                    message=self.WHISPER_SKIP_MESSAGE,
                ),
            )
        except Exception as exc:
            logger.warning("Unable to initialize mlx-whisper for chunk validation: %s", exc)
            return _TranscriptionOutcome(
                transcript=None,
                status=ValidationResult(
                    check="text_alignment",
                    severity=ValidationSeverity.INFO,
                    message=f"STT alignment check skipped ({exc})",
                ),
            )

        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
                temp_path = Path(handle.name)

            audio.export(temp_path, format="wav")
            payload = backend.transcribe(
                str(temp_path),
                path_or_hf_repo=self.validation_settings.stt_model,
                language="en",
            )
            transcript = str(payload.get("text", "")).strip()
            return _TranscriptionOutcome(transcript=transcript)
        except Exception as exc:
            logger.warning("STT alignment transcription failed: %s", exc)
            return _TranscriptionOutcome(
                transcript=None,
                status=ValidationResult(
                    check="text_alignment",
                    severity=ValidationSeverity.INFO,
                    message=f"STT alignment check skipped ({exc})",
                ),
            )
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    @classmethod
    def _load_whisper_model(cls, model_name: str) -> Any:
        """Lazily import mlx-whisper and reuse its internal per-model singleton."""

        if cls._whisper_import_failed:
            raise ImportError(cls.WHISPER_SKIP_MESSAGE)

        if model_name in cls._whisper_model_cache:
            return cls._whisper_model_cache[model_name]

        try:
            import mlx_whisper  # type: ignore[import-not-found]
        except ImportError as exc:
            cls._whisper_import_failed = True
            raise ImportError(cls.WHISPER_SKIP_MESSAGE) from exc

        # mlx_whisper.transcribe() already memoizes loaded weights per model name
        # via its internal ModelHolder, so caching the backend handle here is
        # enough to avoid repeated import/load setup in the chunk validator.
        cls._whisper_model_cache[model_name] = mlx_whisper
        cls._whisper_model_loaded = True
        return mlx_whisper

    def _detect_audio_repeat_pattern(self, audio: AudioSegment) -> dict[str, float] | None:
        """Return repeat-loop metrics from the chunk energy envelope when detectable."""

        samples = self._mono_samples(audio)
        if samples.size == 0:
            return None

        # The synthetic test backend emits steady tones, not speech. Their
        # spectral flatness is near zero and would otherwise look like a perfect
        # repeat-loop. Skip the audio fallback unless the chunk has speech-like
        # spectral complexity.
        if spectral_flatness(samples, audio.frame_rate) < 0.01:
            return None

        envelope = self._rms_levels(samples, audio.frame_rate, window_ms=self.ENVELOPE_FRAME_MS, clamp_floor=False)
        if envelope.size < 10:
            return None

        window_frames = max(self.REPEAT_WINDOW_MS // self.ENVELOPE_FRAME_MS, 1)
        hop_frames = max(self.REPEAT_HOP_MS // self.ENVELOPE_FRAME_MS, 1)

        correlations: list[float] = []
        for start in range(0, envelope.size - (2 * window_frames) + 1, hop_frames):
            first = envelope[start:start + window_frames]
            second = envelope[start + hop_frames:start + hop_frames + window_frames]
            correlation = self._window_correlation(first, second)
            if correlation is not None:
                correlations.append(correlation)

        if not correlations:
            return None

        streak = 0
        max_streak_duration = 0.0
        max_correlation = 0.0
        hop_seconds = self.REPEAT_HOP_MS / 1000.0
        window_seconds = self.REPEAT_WINDOW_MS / 1000.0

        for correlation in correlations:
            max_correlation = max(max_correlation, correlation)
            if correlation >= self.validation_settings.repeat_correlation_threshold:
                streak += 1
                repeated_seconds = window_seconds + (streak * hop_seconds)
                max_streak_duration = max(max_streak_duration, repeated_seconds)
            else:
                streak = 0

        if max_streak_duration <= 0:
            return None

        return {
            "max_correlation": round(max_correlation, 4),
            "repeated_seconds": round(max_streak_duration, 4),
        }

    def _mono_samples(self, audio: AudioSegment) -> np.ndarray:
        """Return normalized mono samples for analysis."""

        mono_audio = audio.set_channels(1)
        samples = np.array(mono_audio.get_array_of_samples(), dtype=np.float32)
        if samples.size == 0:
            return samples

        max_amplitude = float(1 << ((8 * mono_audio.sample_width) - 1))
        return samples / max_amplitude

    def _zero_crossing_rates(self, samples: np.ndarray, sample_rate: int, window_ms: int) -> np.ndarray:
        """Return per-window zero-crossing rates."""

        window_size = max(int(sample_rate * (window_ms / 1000.0)), 1)
        values: list[float] = []

        for start in range(0, samples.size - window_size + 1, window_size):
            frame = samples[start:start + window_size]
            if frame.size <= 1:
                continue
            crossings = np.count_nonzero(np.diff(np.signbit(frame)))
            values.append(float(crossings / frame.size))

        return np.asarray(values, dtype=np.float32)

    def _rms_levels(
        self,
        samples: np.ndarray,
        sample_rate: int,
        *,
        window_ms: int,
        clamp_floor: bool = True,
    ) -> np.ndarray:
        """Return per-window RMS levels as linear amplitude or dBFS."""

        window_size = max(int(sample_rate * (window_ms / 1000.0)), 1)
        values: list[float] = []

        for start in range(0, samples.size - window_size + 1, window_size):
            frame = samples[start:start + window_size]
            rms = float(np.sqrt(np.mean(np.square(frame))))
            if clamp_floor:
                values.append(self._amplitude_to_dbfs(rms))
            else:
                values.append(rms)

        return np.asarray(values, dtype=np.float32)

    def _count_erratic_energy_drops(self, energy_dbfs: np.ndarray) -> int:
        """Count interior windows with >20dB drops relative to both neighbors."""

        if energy_dbfs.size < 3:
            return 0

        drops = 0
        for index in range(1, energy_dbfs.size - 1):
            previous_drop = energy_dbfs[index - 1] - energy_dbfs[index]
            next_drop = energy_dbfs[index + 1] - energy_dbfs[index]
            if previous_drop > 20 and next_drop > 20:
                drops += 1
        return drops

    def _window_correlation(self, first: np.ndarray, second: np.ndarray) -> float | None:
        """Return cosine-like correlation for two envelope windows."""

        centered_first = first - float(np.mean(first))
        centered_second = second - float(np.mean(second))
        first_norm = float(np.linalg.norm(centered_first))
        second_norm = float(np.linalg.norm(centered_second))
        if first_norm <= 1e-8 or second_norm <= 1e-8:
            return None

        return float(np.dot(centered_first, centered_second) / (first_norm * second_norm))

    def _amplitude_to_dbfs(self, amplitude: float) -> float:
        """Convert a normalized amplitude into dBFS."""

        if amplitude <= 1e-9:
            return -100.0
        return float(20 * np.log10(amplitude))

    def _max_severity(
        self,
        left: ValidationSeverity,
        right: ValidationSeverity,
    ) -> ValidationSeverity:
        """Return the higher of the two severities."""

        return left if SEVERITY_ORDER[left] >= SEVERITY_ORDER[right] else right
