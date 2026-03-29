"""Gate 3 book-level QA checks for cross-chapter consistency and mastering readiness."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel
from pydub import AudioSegment, silence
from sqlalchemy.orm import Session

from src.config import settings
from src.database import Book, Chapter, ChapterQARecord, ChapterStatus, ChapterType, QAAutomaticStatus
from src.pipeline.qa_checker import (
    _average_frame_spectrum,
    _dbfs_from_amplitude,
    _estimate_pitch,
    _linear_rms,
    _mono_samples,
    _resolve_audio_path,
    _spectral_centroid,
)

logger = logging.getLogger(__name__)

ACX_REQUIREMENTS = {
    "sample_rate": 44100,
    "bit_depth": 16,
    "channels": 1,
    "lufs_min": -23.0,
    "lufs_max": -18.0,
    "rms_min_db": -23.0,
    "rms_max_db": -18.0,
    "peak_max_db": -3.0,
    "noise_floor_max_db": -60.0,
    "min_leading_silence_ms": 500,
    "max_leading_silence_ms": 1000,
    "min_trailing_silence_ms": 1000,
    "max_trailing_silence_ms": 5000,
    "room_tone_min_db": -80.0,
    "room_tone_max_db": -50.0,
    "min_chapter_duration_s": 1.0,
    "max_chapter_duration_s": 120 * 60.0,
    "max_file_size_mb": 170.0,
    "lra_warning_min_lu": 4.0,
    "lra_warning_max_lu": 15.0,
    "lra_fail_min_lu": 3.0,
    "lra_fail_max_lu": 18.0,
}


class BookQACheck(BaseModel):
    """One cross-chapter or mastering-level QA check result."""

    status: str
    message: str
    details: dict[str, Any] = {}
    recommendations: list[str] = []
    blockers: list[str] = []


class VoiceChartPoint(BaseModel):
    """One chapter entry in the frontend voice consistency visualization."""

    number: int
    pitch: float | None = None
    rate: float | None = None
    brightness: float | None = None
    grade: str


class VoiceConsistencyChart(BaseModel):
    """Frontend-ready per-chapter voice fingerprint series."""

    chapters: list[VoiceChartPoint]
    book_median: dict[str, float | None]
    outlier_chapters: list[int]


class BookQAReport(BaseModel):
    """Aggregate Gate 3 report for a full audiobook."""

    book_id: int
    title: str
    total_chapters: int
    chapters_grade_a: int
    chapters_grade_b: int
    chapters_grade_c: int
    chapters_grade_f: int
    overall_grade: str
    ready_for_export: bool
    cross_chapter_checks: dict[str, dict[str, Any]]
    recommendations: list[str]
    export_blockers: list[str]


def _median(values: list[float]) -> float | None:
    """Return a stable median without using ``numpy.median``."""

    if not values:
        return None

    ordered = sorted(float(value) for value in values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def _book_or_404(book_id: int, db_session: Session) -> Book:
    """Load a book row or raise ``ValueError``."""

    book = db_session.query(Book).filter(Book.id == book_id).first()
    if book is None:
        raise ValueError(f"Book {book_id} not found")
    return book


def _export_sample_rate() -> int:
    """Return the configured export sample rate used for ACX validation."""

    return int(settings.EXPORT_SAMPLE_RATE)


def _generated_chapters(book_id: int, db_session: Session) -> list[Chapter]:
    """Return generated chapters in reading order."""

    return (
        db_session.query(Chapter)
        .filter(Chapter.book_id == book_id, Chapter.status == ChapterStatus.GENERATED)
        .order_by(Chapter.number.asc(), Chapter.id.asc())
        .all()
    )


def _all_book_chapters(book_id: int, db_session: Session) -> list[Chapter]:
    """Return all chapters for validation readiness checks."""

    return (
        db_session.query(Chapter)
        .filter(Chapter.book_id == book_id)
        .order_by(Chapter.number.asc(), Chapter.id.asc())
        .all()
    )


def _load_qa_records(book_id: int, db_session: Session) -> dict[int, ChapterQARecord]:
    """Return QA records keyed by chapter number."""

    return {
        record.chapter_n: record
        for record in db_session.query(ChapterQARecord).filter(ChapterQARecord.book_id == book_id).all()
    }


def _content_chapters(chapters: list[Chapter]) -> list[Chapter]:
    """Return the chapters that should participate in cross-chapter narration checks."""

    content_types = {ChapterType.CHAPTER, ChapterType.INTRODUCTION}
    filtered = [chapter for chapter in chapters if chapter.type in content_types]
    return filtered or chapters


def _chapter_grade(chapter: Chapter, qa_record: ChapterQARecord | None) -> str:
    """Extract the stored chapter grade when available."""

    if qa_record is None or not qa_record.qa_details:
        return "F"

    try:
        qa_details = json.loads(qa_record.qa_details)
    except json.JSONDecodeError:
        return "F"

    chapter_report = qa_details.get("chapter_report")
    if isinstance(chapter_report, dict):
        grade = chapter_report.get("overall_grade")
        if grade in {"A", "B", "C", "F"}:
            return grade

    return "A" if qa_record.overall_status == QAAutomaticStatus.PASS else "B"


def _require_gate3_ready(book_id: int, db_session: Session) -> tuple[Book, list[Chapter], dict[int, ChapterQARecord]]:
    """Validate that a book is ready for whole-book QA."""

    book = _book_or_404(book_id, db_session)
    all_chapters = _all_book_chapters(book_id, db_session)
    generated_chapters = _generated_chapters(book_id, db_session)
    if not all_chapters:
        raise ValueError("Book has no chapters. Parse the manuscript first.")
    if len(generated_chapters) != len(all_chapters):
        raise ValueError("Gate 3 requires all chapters to be generated first.")

    qa_records = _load_qa_records(book_id, db_session)
    missing_qa = [chapter.number for chapter in generated_chapters if chapter.number not in qa_records]
    if missing_qa:
        raise ValueError(f"Gate 3 requires chapter QA first. Missing QA for chapters: {missing_qa}")

    return (book, generated_chapters, qa_records)


def _loudnorm_metrics(audio_path: str | Path) -> dict[str, float | None]:
    """Return integrated loudness stats, preferring pyloudnorm and falling back to ffmpeg."""

    resolved_path = Path(audio_path)
    temp_path: Path | None = None
    measurement_audio: AudioSegment | None = None
    measurement_path = resolved_path

    try:
        try:
            original_audio = AudioSegment.from_file(resolved_path).set_channels(1).set_sample_width(2)
            trimmed_audio = _trimmed_audio(original_audio)
            measurement_audio = trimmed_audio if 0 < len(trimmed_audio) <= len(original_audio) else original_audio
        except Exception:
            measurement_audio = None

        if measurement_audio is not None:
            try:
                import pyloudnorm as pyln  # type: ignore

                samples = _mono_samples(measurement_audio).astype(np.float64, copy=False)
                if samples.size > 0:
                    meter = pyln.Meter(measurement_audio.frame_rate)
                    loudness_range = None
                    loudness_range_method = getattr(meter, "loudness_range", None)
                    if callable(loudness_range_method):
                        loudness_range = float(loudness_range_method(samples))
                    return {
                        "integrated_lufs": round(float(meter.integrated_loudness(samples)), 3),
                        "loudness_range_lu": round(loudness_range, 3) if loudness_range is not None else None,
                    }
            except Exception as exc:
                logger.debug("pyloudnorm unavailable for %s: %s", resolved_path, exc)

        if measurement_audio is not None and measurement_audio.duration_seconds > 0:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
                temp_path = Path(handle.name)
            measurement_audio.export(temp_path, format="wav")
            measurement_path = temp_path

        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path is None:
            try:
                fallback_audio = measurement_audio or AudioSegment.from_file(measurement_path)
                fallback_db = float(fallback_audio.dBFS)
                return {
                    "integrated_lufs": round(fallback_db, 3) if fallback_db != float("-inf") else None,
                    "loudness_range_lu": None,
                }
            except Exception:
                return {"integrated_lufs": None, "loudness_range_lu": None}

        command = [
            ffmpeg_path,
            "-hide_banner",
            "-i",
            str(measurement_path),
            "-af",
            "loudnorm=I=-19:TP=-1.5:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ]
        try:
            completed = subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            logger.warning("Unable to measure loudness for %s: %s", resolved_path, exc)
            return {"integrated_lufs": None, "loudness_range_lu": None}

        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        match = re.search(r"(\{\s*\"input_i\".*?\})", output, re.DOTALL)
        if match is None:
            return {"integrated_lufs": None, "loudness_range_lu": None}

        try:
            metrics = json.loads(match.group(1))
            integrated = metrics.get("input_i")
            loudness_range = metrics.get("input_lra")
            return {
                "integrated_lufs": round(float(integrated), 3) if integrated is not None else None,
                "loudness_range_lu": round(float(loudness_range), 3) if loudness_range is not None else None,
            }
        except (TypeError, ValueError, json.JSONDecodeError):
            return {"integrated_lufs": None, "loudness_range_lu": None}
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def measure_integrated_lufs(audio_path: str | Path) -> float | None:
    """Return integrated LUFS using the shared loudness measurement helper."""

    return _loudnorm_metrics(audio_path).get("integrated_lufs")


def measure_loudness_range_lu(audio_path: str | Path) -> float | None:
    """Return loudness range in LU when the underlying meter can provide it."""

    return _loudnorm_metrics(audio_path).get("loudness_range_lu")


def _leading_silence_ms(audio: AudioSegment, *, silence_thresh: float = -45.0, step_ms: int = 10) -> int:
    """Return the detected leading silence in milliseconds."""

    for start_ms in range(0, len(audio), step_ms):
        chunk = audio[start_ms:start_ms + step_ms]
        if chunk.dBFS > silence_thresh:
            return start_ms
    return len(audio)


def _trailing_silence_ms(audio: AudioSegment, *, silence_thresh: float = -45.0, step_ms: int = 10) -> int:
    """Return the detected trailing silence in milliseconds."""

    reversed_audio = audio.reverse()
    return _leading_silence_ms(reversed_audio, silence_thresh=silence_thresh, step_ms=step_ms)


def _trimmed_audio(audio: AudioSegment) -> AudioSegment:
    """Trim leading and trailing silence while preserving the spoken content."""

    nonsilent = silence.detect_nonsilent(audio, min_silence_len=200, silence_thresh=-40)
    if not nonsilent:
        return audio

    start_ms = nonsilent[0][0]
    end_ms = nonsilent[-1][1]
    return audio[start_ms:end_ms]


def _trimmed_duration_seconds(audio: AudioSegment) -> float:
    """Return the speaking duration without chapter-edge silence."""

    trimmed = _trimmed_audio(audio)
    return max(len(trimmed) / 1000.0, 0.001)


def _pitch_series(audio_segment: AudioSegment) -> list[float]:
    """Return per-frame pitch estimates for fingerprint analysis."""

    samples = _mono_samples(audio_segment)
    if samples.size == 0:
        return []

    frame_size = max(int(audio_segment.frame_rate * 0.1), 1)
    hop_size = max(int(audio_segment.frame_rate * 0.05), 1)
    series: list[float] = []

    for start in range(0, samples.size - frame_size + 1, hop_size):
        frame = samples[start:start + frame_size]
        if _linear_rms(frame) <= 0.01:
            continue

        centered = frame - float(np.mean(frame))
        crossings = np.where((centered[:-1] <= 0) & (centered[1:] > 0))[0]
        if crossings.size < 2:
            continue

        intervals = np.diff(crossings)
        period = _median([float(interval) for interval in intervals.tolist() if interval > 0])
        if period is None or period <= 0:
            continue

        pitch = audio_segment.frame_rate / period
        if 48.0 <= pitch <= 480.0:
            series.append(round(float(pitch), 3))

    return series


def _spectral_bandwidth(samples: np.ndarray, sample_rate: int) -> float | None:
    """Return a simple spectral bandwidth estimate for a chapter segment."""

    frequencies, spectrum = _average_frame_spectrum(samples, sample_rate, frame_size=2048, hop_size=1024)
    if spectrum.size == 0:
        return None

    total_energy = float(np.sum(spectrum))
    if total_energy <= 1e-8:
        return None

    centroid = float(np.sum(frequencies * spectrum) / total_energy)
    variance = float(np.sum(((frequencies - centroid) ** 2) * spectrum) / total_energy)
    return float(np.sqrt(max(variance, 0.0)))


def compute_voice_fingerprint(audio_path: str | Path, *, word_count: int | None = None) -> dict[str, float | None]:
    """Extract the cross-chapter voice fingerprint from one chapter file."""

    audio = AudioSegment.from_file(audio_path).set_channels(1)
    trimmed = _trimmed_audio(audio)
    if len(trimmed) > 30_000:
        midpoint = len(trimmed) // 2
        start_ms = max(midpoint - 15_000, 0)
        sample_segment = trimmed[start_ms:start_ms + 30_000]
    else:
        sample_segment = trimmed

    samples = _mono_samples(sample_segment)
    pitch_values = _pitch_series(sample_segment)
    mean_pitch = _median(pitch_values)
    pitch_range = float(np.std(np.array(pitch_values, dtype=np.float32))) if pitch_values else None
    centroid = _spectral_centroid(samples, sample_segment.frame_rate)
    bandwidth = _spectral_bandwidth(samples, sample_segment.frame_rate)
    mean_rms_db = _dbfs_from_amplitude(_linear_rms(samples))
    speech_rate_wpm = None
    if word_count is not None and word_count > 0:
        speech_rate_wpm = round(word_count / (_trimmed_duration_seconds(audio) / 60.0), 3)

    return {
        "mean_pitch_hz": round(mean_pitch, 3) if mean_pitch is not None else None,
        "pitch_range_hz": round(pitch_range, 3) if pitch_range is not None else None,
        "spectral_centroid": round(centroid, 3) if centroid is not None else None,
        "speech_rate_wpm": round(speech_rate_wpm, 3) if speech_rate_wpm is not None else None,
        "mean_rms_db": round(mean_rms_db, 3),
        "spectral_bandwidth": round(bandwidth, 3) if bandwidth is not None else None,
    }


def _chapter_audio_path(chapter: Chapter) -> Path:
    """Resolve one chapter path or raise a clear error."""

    audio_path = _resolve_audio_path(chapter.audio_path)
    if audio_path is None or not audio_path.exists():
        raise ValueError(f"Chapter {chapter.number} audio file is missing.")
    return audio_path


def _summarize_status(statuses: list[str]) -> str:
    """Collapse check statuses to the worst severity."""

    if any(status == QAAutomaticStatus.FAIL.value for status in statuses):
        return QAAutomaticStatus.FAIL.value
    if any(status == QAAutomaticStatus.WARNING.value for status in statuses):
        return QAAutomaticStatus.WARNING.value
    return QAAutomaticStatus.PASS.value


def check_cross_chapter_loudness(book_id: int, db_session: Session) -> BookQACheck:
    """Measure cross-chapter LUFS spread across the finished book."""

    _, chapters, _ = _require_gate3_ready(book_id, db_session)
    chapters = _content_chapters(chapters)
    chapter_lufs: list[dict[str, float | int | None]] = []
    values: list[float] = []
    warnings: list[str] = []
    blockers: list[str] = []

    for chapter in chapters:
        lufs = measure_integrated_lufs(_chapter_audio_path(chapter))
        if lufs is None:
            blockers.append(f"Chapter {chapter.number} loudness could not be measured.")
            chapter_lufs.append({"chapter_n": chapter.number, "lufs": None, "deviation_lu": None})
            continue
        values.append(lufs)
        chapter_lufs.append({"chapter_n": chapter.number, "lufs": lufs, "deviation_lu": None})

    mean_lufs = round(float(np.mean(np.array(values, dtype=np.float32))), 3) if values else None
    std_lufs = round(float(np.std(np.array(values, dtype=np.float32))), 3) if values else None
    max_deviation = 0.0
    statuses: list[str] = []

    for entry in chapter_lufs:
        lufs = entry["lufs"]
        if lufs is None or mean_lufs is None:
            statuses.append(QAAutomaticStatus.FAIL.value)
            continue
        deviation = abs(float(lufs) - mean_lufs)
        entry["deviation_lu"] = round(deviation, 3)
        if deviation > max_deviation:
            max_deviation = deviation

        if deviation > 3.0:
            statuses.append(QAAutomaticStatus.FAIL.value)
            blockers.append(
                f"Chapter {entry['chapter_n']} deviates {deviation:.1f} LU from the book mean and must be re-leveled."
            )
        elif deviation > 1.5:
            statuses.append(QAAutomaticStatus.WARNING.value)
            warnings.append(
                f"Chapter {entry['chapter_n']} deviates {deviation:.1f} LU from the book mean and should be re-normalized."
            )
        else:
            statuses.append(QAAutomaticStatus.PASS.value)

    status = _summarize_status(statuses)
    message = (
        "All chapters are within the allowed loudness spread."
        if status == QAAutomaticStatus.PASS.value
        else f"Cross-chapter loudness drift detected (max deviation {max_deviation:.1f} LU)."
    )
    recommendations = warnings or ["All chapters within ACX loudness range."]
    return BookQACheck(
        status=status,
        message=message,
        details={
            "chapters": chapter_lufs,
            "mean_lufs": mean_lufs,
            "std_lufs": std_lufs,
            "min_lufs": min(values) if values else None,
            "max_lufs": max(values) if values else None,
            "max_deviation_lu": round(max_deviation, 3),
        },
        recommendations=recommendations,
        blockers=blockers,
    )


def check_cross_chapter_voice(book_id: int, db_session: Session) -> BookQACheck:
    """Detect voice drift between chapters using acoustic fingerprints."""

    _, chapters, qa_records = _require_gate3_ready(book_id, db_session)
    chapters = _content_chapters(chapters)
    fingerprints: list[dict[str, Any]] = []

    for chapter in chapters:
        fingerprint = compute_voice_fingerprint(_chapter_audio_path(chapter), word_count=chapter.word_count)
        fingerprints.append(
            {
                "chapter_n": chapter.number,
                "grade": _chapter_grade(chapter, qa_records.get(chapter.number)),
                **fingerprint,
            }
        )

    medians = {
        "pitch": _median([entry["mean_pitch_hz"] for entry in fingerprints if entry["mean_pitch_hz"] is not None]),
        "rate": _median([entry["speech_rate_wpm"] for entry in fingerprints if entry["speech_rate_wpm"] is not None]),
        "brightness": _median([entry["spectral_centroid"] for entry in fingerprints if entry["spectral_centroid"] is not None]),
    }
    outlier_chapters: list[int] = []
    recommendations: list[str] = []
    blockers: list[str] = []
    statuses: list[str] = []

    for entry in fingerprints:
        chapter_status = QAAutomaticStatus.PASS.value
        deviations: dict[str, float] = {}
        for metric_name, median_key, warning_threshold in (
            ("mean_pitch_hz", "pitch", 0.10),
            ("speech_rate_wpm", "rate", 0.12),
            ("spectral_centroid", "brightness", 0.15),
        ):
            value = entry.get(metric_name)
            median = medians[median_key]
            if value is None or median is None or median <= 1e-6:
                continue

            deviation = abs(float(value) - median) / median
            if deviation > 0.20:
                chapter_status = QAAutomaticStatus.FAIL.value
                deviations[metric_name] = round(deviation, 3)
            elif deviation > warning_threshold and chapter_status != QAAutomaticStatus.FAIL.value:
                chapter_status = QAAutomaticStatus.WARNING.value
                deviations[metric_name] = round(deviation, 3)

        entry["deviations"] = deviations
        statuses.append(chapter_status)
        if deviations:
            outlier_chapters.append(entry["chapter_n"])
            if chapter_status == QAAutomaticStatus.FAIL.value:
                blockers.append(
                    f"Chapter {entry['chapter_n']} voice fingerprint deviates more than 20% from the book median."
                )
            else:
                recommendations.append(
                    f"Chapter {entry['chapter_n']} has mild voice drift and should be spot-checked."
                )

    status = _summarize_status(statuses)
    chapters_chart = [
        VoiceChartPoint(
            number=entry["chapter_n"],
            pitch=entry.get("mean_pitch_hz"),
            rate=entry.get("speech_rate_wpm"),
            brightness=entry.get("spectral_centroid"),
            grade="F" if entry["chapter_n"] in outlier_chapters and any((entry.get("deviations") or {}).values()) and any(
                deviation > 0.20 for deviation in (entry.get("deviations") or {}).values()
            ) else ("B" if entry["chapter_n"] in outlier_chapters else "A"),
        )
        for entry in fingerprints
    ]
    message = (
        "Voice fingerprint remains consistent across the book."
        if status == QAAutomaticStatus.PASS.value
        else f"Voice drift detected in chapters {outlier_chapters}."
    )
    return BookQACheck(
        status=status,
        message=message,
        details={
            "chapters": [chapter.model_dump(mode="json") for chapter in chapters_chart],
            "book_median": medians,
            "outlier_chapters": outlier_chapters,
            "fingerprints": fingerprints,
        },
        recommendations=recommendations or ["Voice fingerprint is consistent across chapters."],
        blockers=blockers,
    )


def check_cross_chapter_pacing(book_id: int, db_session: Session) -> BookQACheck:
    """Compare chapter speech rates against the book-wide pacing median."""

    _, chapters, _ = _require_gate3_ready(book_id, db_session)
    chapters = _content_chapters(chapters)
    chapter_wpm: list[dict[str, float | int]] = []
    values: list[float] = []

    for chapter in chapters:
        audio = AudioSegment.from_file(_chapter_audio_path(chapter)).set_channels(1)
        duration_seconds = _trimmed_duration_seconds(audio)
        words = max(chapter.word_count or 0, 1)
        wpm = words / (duration_seconds / 60.0)
        values.append(float(wpm))
        chapter_wpm.append({"chapter_n": chapter.number, "wpm": round(float(wpm), 3)})

    mean_wpm = round(float(np.mean(np.array(values, dtype=np.float32))), 3)
    std_wpm = round(float(np.std(np.array(values, dtype=np.float32))), 3)
    max_deviation_pct = 0.0
    recommendations: list[str] = []
    blockers: list[str] = []
    statuses: list[str] = []

    for entry in chapter_wpm:
        deviation_pct = abs(float(entry["wpm"]) - mean_wpm) / max(mean_wpm, 1e-6)
        entry["deviation_pct"] = round(deviation_pct * 100.0, 3)
        if deviation_pct * 100.0 > max_deviation_pct:
            max_deviation_pct = deviation_pct * 100.0

        if deviation_pct > 0.20:
            statuses.append(QAAutomaticStatus.FAIL.value)
            suggested_speed = round(mean_wpm / max(float(entry["wpm"]), 1e-6), 3)
            entry["suggested_speed"] = suggested_speed
            blockers.append(
                f"Chapter {entry['chapter_n']} is {deviation_pct * 100:.1f}% off the book pacing; suggested speed {suggested_speed:.2f}."
            )
        elif deviation_pct > 0.10:
            statuses.append(QAAutomaticStatus.WARNING.value)
            suggested_speed = round(mean_wpm / max(float(entry["wpm"]), 1e-6), 3)
            entry["suggested_speed"] = suggested_speed
            recommendations.append(
                f"Chapter {entry['chapter_n']} is {deviation_pct * 100:.1f}% off the book pacing; suggested speed {suggested_speed:.2f}."
            )
        else:
            statuses.append(QAAutomaticStatus.PASS.value)

    status = _summarize_status(statuses)
    message = (
        "Cross-chapter pacing is consistent."
        if status == QAAutomaticStatus.PASS.value
        else f"Pacing outliers detected (max deviation {max_deviation_pct:.1f}%)."
    )
    return BookQACheck(
        status=status,
        message=message,
        details={
            "chapters": chapter_wpm,
            "mean_wpm": mean_wpm,
            "std_wpm": std_wpm,
            "max_deviation_pct": round(max_deviation_pct, 3),
        },
        recommendations=recommendations or ["Speech pacing stays within the expected book-wide range."],
        blockers=blockers,
    )


def check_chapter_transitions(book_id: int, db_session: Session) -> BookQACheck:
    """Validate cross-chapter transition quality at book assembly boundaries."""

    _, chapters, _ = _require_gate3_ready(book_id, db_session)
    issues: list[dict[str, Any]] = []
    recommendations: list[str] = []
    blockers: list[str] = []
    statuses: list[str] = []

    for current, following in zip(chapters, chapters[1:], strict=False):
        current_audio = AudioSegment.from_file(_chapter_audio_path(current)).set_channels(1)
        following_audio = AudioSegment.from_file(_chapter_audio_path(following)).set_channels(1)

        current_tail = current_audio[max(len(current_audio) - 3000, 0):]
        following_head = following_audio[:3000]
        current_rms = _dbfs_from_amplitude(_linear_rms(_mono_samples(current_tail)))
        following_rms = _dbfs_from_amplitude(_linear_rms(_mono_samples(following_head)))
        energy_jump_db = abs(current_rms - following_rms)

        current_centroid = _spectral_centroid(_mono_samples(current_tail), current_tail.frame_rate)
        following_centroid = _spectral_centroid(_mono_samples(following_head), following_head.frame_rate)
        centroid_delta = 0.0
        if current_centroid is not None and following_centroid is not None and current_centroid > 1e-6:
            centroid_delta = abs(following_centroid - current_centroid) / current_centroid

        trailing_silence_ms = _trailing_silence_ms(current_audio)
        leading_silence_ms = _leading_silence_ms(following_audio)
        pair_status = QAAutomaticStatus.PASS.value
        pair_issue: dict[str, Any] = {
            "from_chapter": current.number,
            "to_chapter": following.number,
            "energy_jump_db": round(energy_jump_db, 3),
            "spectral_centroid_delta_pct": round(centroid_delta * 100.0, 3),
            "trailing_silence_ms": trailing_silence_ms,
            "leading_silence_ms": leading_silence_ms,
        }

        involves_credits = current.type in {ChapterType.OPENING_CREDITS, ChapterType.CLOSING_CREDITS} or following.type in {
            ChapterType.OPENING_CREDITS,
            ChapterType.CLOSING_CREDITS,
        }
        blocker_threshold_db = 10.0 if involves_credits else 6.0
        warning_threshold_db = 6.0 if involves_credits else 3.0
        pair_issue["credits_transition"] = involves_credits

        if energy_jump_db > blocker_threshold_db:
            pair_status = QAAutomaticStatus.FAIL.value
            blockers.append(
                f"Transition {current.number}->{following.number} jumps {energy_jump_db:.1f} dB and must be re-leveled."
            )
        elif (
            energy_jump_db > warning_threshold_db
            or centroid_delta > 0.20
            or trailing_silence_ms < 500
            or leading_silence_ms < 500
        ):
            pair_status = QAAutomaticStatus.WARNING.value
            recommendations.append(
                f"Transition {current.number}->{following.number} should be reviewed for loudness or spacing."
            )

        pair_issue["status"] = pair_status
        issues.append(pair_issue)
        statuses.append(pair_status)

    status = _summarize_status(statuses or [QAAutomaticStatus.PASS.value])
    message = (
        "Chapter transitions are smooth and properly padded."
        if status == QAAutomaticStatus.PASS.value
        else "One or more chapter transitions need correction."
    )
    return BookQACheck(
        status=status,
        message=message,
        details={"issues": issues},
        recommendations=recommendations or ["All chapter transitions look smooth."],
        blockers=blockers,
    )


def _true_peak_dbfs(audio: AudioSegment, oversample_factor: int = 4) -> float:
    """Return a simple oversampled peak estimate for ACX validation."""

    samples = _mono_samples(audio)
    if samples.size == 0:
        return -100.0

    indices = np.arange(samples.size, dtype=np.float32)
    oversampled_indices = np.linspace(0, samples.size - 1, num=samples.size * oversample_factor)
    oversampled = np.interp(oversampled_indices, indices, samples)
    peak = float(np.max(np.abs(oversampled)))
    return _dbfs_from_amplitude(peak)


def _silence_noise_floor_db(audio: AudioSegment) -> float:
    """Return the loudest detected silence noise floor in dBFS."""

    silence_regions = silence.detect_silence(audio, min_silence_len=200, silence_thresh=-40)
    if not silence_regions:
        return -100.0

    floors: list[float] = []
    for start_ms, end_ms in silence_regions:
        inner_start = start_ms + 50
        inner_end = max(inner_start, end_ms - 50)
        segment = audio[inner_start:inner_end] if inner_end > inner_start else audio[start_ms:end_ms]
        floors.append(_dbfs_from_amplitude(_linear_rms(_mono_samples(segment))))
    return max(floors) if floors else -100.0


def _trimmed_rms_db(audio: AudioSegment) -> float:
    """Return RMS dBFS for the speaking portion of a chapter."""

    trimmed = _trimmed_audio(audio)
    if len(trimmed) == 0 or trimmed.dBFS == float("-inf"):
        return -100.0
    return round(float(trimmed.dBFS), 3)


def _edge_room_tone_db(audio: AudioSegment, *, duration_ms: int, trailing: bool = False) -> float:
    """Measure the RMS level of a leading or trailing room-tone window."""

    if len(audio) == 0:
        return -100.0
    segment = audio[-duration_ms:] if trailing else audio[:duration_ms]
    if len(segment) == 0 or segment.dBFS == float("-inf"):
        return -100.0
    return round(float(segment.dBFS), 3)


def check_loudness_range(book_id: int, db_session: Session) -> BookQACheck:
    """Validate narration loudness range so chapters are neither crushed nor too dynamic."""

    _, chapters, _ = _require_gate3_ready(book_id, db_session)
    chapters = _content_chapters(chapters)
    chapter_ranges: list[dict[str, float | int | None]] = []
    statuses: list[str] = []
    recommendations: list[str] = []
    blockers: list[str] = []

    for chapter in chapters:
        lra = measure_loudness_range_lu(_chapter_audio_path(chapter))
        chapter_ranges.append({"chapter_n": chapter.number, "lra_lu": lra})
        if lra is None:
            statuses.append(QAAutomaticStatus.WARNING.value)
            recommendations.append(f"Chapter {chapter.number} loudness range could not be measured.")
            continue
        if lra < ACX_REQUIREMENTS["lra_fail_min_lu"] or lra > ACX_REQUIREMENTS["lra_fail_max_lu"]:
            statuses.append(QAAutomaticStatus.FAIL.value)
            blockers.append(f"Chapter {chapter.number} LRA is {lra:.1f} LU; target is 4-15 LU.")
        elif lra < ACX_REQUIREMENTS["lra_warning_min_lu"] or lra > ACX_REQUIREMENTS["lra_warning_max_lu"]:
            statuses.append(QAAutomaticStatus.WARNING.value)
            recommendations.append(f"Chapter {chapter.number} LRA is {lra:.1f} LU and should be re-mastered.")
        else:
            statuses.append(QAAutomaticStatus.PASS.value)

    status = _summarize_status(statuses or [QAAutomaticStatus.PASS.value])
    values = [float(entry["lra_lu"]) for entry in chapter_ranges if entry["lra_lu"] is not None]
    message = (
        "All chapters fall within the narration loudness-range target."
        if status == QAAutomaticStatus.PASS.value
        else "One or more chapters fall outside the narration loudness-range target."
    )
    return BookQACheck(
        status=status,
        message=message,
        details={
            "chapters": chapter_ranges,
            "mean_lra_lu": round(float(np.mean(np.array(values, dtype=np.float32))), 3) if values else None,
            "min_lra_lu": min(values) if values else None,
            "max_lra_lu": max(values) if values else None,
        },
        recommendations=recommendations or ["Narration loudness range is in the preferred 4-15 LU band."],
        blockers=blockers,
    )


def check_acx_compliance(book_id: int, db_session: Session, *, export_mode: bool = False) -> BookQACheck:
    """Ensure every chapter meets ACX/Audible production requirements."""

    _, chapters, _ = _require_gate3_ready(book_id, db_session)
    violations: list[dict[str, Any]] = []
    blockers: list[str] = []
    recommendations: list[str] = []
    export_warning_issues = {"lufs", "rms_db", "true_peak_db", "noise_floor_db", "file_size_mb"}

    for chapter in chapters:
        audio_path = _chapter_audio_path(chapter)
        audio = AudioSegment.from_file(audio_path).set_channels(1).set_sample_width(2)
        lufs = measure_integrated_lufs(audio_path)
        rms_db = _trimmed_rms_db(audio)
        true_peak_db = _true_peak_dbfs(audio)
        noise_floor_db = _silence_noise_floor_db(audio)
        leading_silence_ms = _leading_silence_ms(audio)
        trailing_silence_ms = _trailing_silence_ms(audio)
        head_room_tone_db = _edge_room_tone_db(audio, duration_ms=500)
        tail_room_tone_db = _edge_room_tone_db(audio, duration_ms=1000, trailing=True)
        duration_seconds = chapter.duration_seconds or (len(audio) / 1000.0)
        file_size_mb = audio_path.stat().st_size / (1024 * 1024)
        bit_depth = audio.sample_width * 8

        def add_violation(issue: str, remediation: str, value: Any, *, chapter_n: int | None = None) -> None:
            severity = "warning" if export_mode and issue in export_warning_issues else "blocker"
            label = f"Chapter {chapter_n if chapter_n is not None else chapter.number} {issue}: {remediation}"
            violations.append(
                {
                    "chapter_n": chapter_n if chapter_n is not None else chapter.number,
                    "issue": issue,
                    "value": value,
                    "remediation": remediation,
                    "severity": severity,
                }
            )
            if severity == "warning":
                recommendations.append(label)
            else:
                blockers.append(label)

        if audio.frame_rate != ACX_REQUIREMENTS["sample_rate"]:
            add_violation("sample_rate", "Resample chapter masters to 44.1kHz.", audio.frame_rate)
        if bit_depth != ACX_REQUIREMENTS["bit_depth"]:
            add_violation("bit_depth", "Export chapter masters at 16-bit PCM.", bit_depth)
        if audio.channels != ACX_REQUIREMENTS["channels"]:
            add_violation("channels", "Convert chapter audio to mono.", audio.channels)
        if not (ACX_REQUIREMENTS["rms_min_db"] <= rms_db <= ACX_REQUIREMENTS["rms_max_db"]):
            add_violation("rms_db", "Adjust chapter RMS into the -23 to -18 dBFS publishing range.", rms_db)
        if lufs is None or not (ACX_REQUIREMENTS["lufs_min"] <= lufs <= ACX_REQUIREMENTS["lufs_max"]):
            add_violation("lufs", "Adjust chapter loudness into the ACX range of -23 to -18 LUFS.", lufs)
        if true_peak_db > ACX_REQUIREMENTS["peak_max_db"]:
            add_violation("true_peak_db", "Apply final peak limiting below -3 dBFS.", round(true_peak_db, 3))
        if noise_floor_db > ACX_REQUIREMENTS["noise_floor_max_db"]:
            add_violation("noise_floor_db", "Lower the silence noise floor below -60 dBFS.", round(noise_floor_db, 3))
        if not (ACX_REQUIREMENTS["room_tone_min_db"] <= head_room_tone_db <= ACX_REQUIREMENTS["room_tone_max_db"]):
            add_violation("room_tone_head_db", "Provide 0.5s+ low-level head room tone between -80 and -50 dBFS.", head_room_tone_db)
        if not (ACX_REQUIREMENTS["room_tone_min_db"] <= tail_room_tone_db <= ACX_REQUIREMENTS["room_tone_max_db"]):
            add_violation("room_tone_tail_db", "Provide 1.0s+ low-level tail room tone between -80 and -50 dBFS.", tail_room_tone_db)
        if not (ACX_REQUIREMENTS["min_leading_silence_ms"] <= leading_silence_ms <= ACX_REQUIREMENTS["max_leading_silence_ms"]):
            add_violation("leading_silence_ms", "Normalize chapter lead-in silence to 500-1000ms.", leading_silence_ms)
        if not (ACX_REQUIREMENTS["min_trailing_silence_ms"] <= trailing_silence_ms <= ACX_REQUIREMENTS["max_trailing_silence_ms"]):
            add_violation("trailing_silence_ms", "Normalize chapter tail silence to 1000-5000ms.", trailing_silence_ms)
        if duration_seconds < ACX_REQUIREMENTS["min_chapter_duration_s"]:
            add_violation("duration_seconds", "Ensure the chapter contains at least one second of audio.", chapter.duration_seconds)
        if duration_seconds > ACX_REQUIREMENTS["max_chapter_duration_s"]:
            add_violation("duration_seconds_max", "Split files so no chapter exceeds 120 minutes.", chapter.duration_seconds)
        skip_file_size_check = audio_path.suffix.lower() == ".wav" and duration_seconds > (20 * 60)
        if not skip_file_size_check and file_size_mb > ACX_REQUIREMENTS["max_file_size_mb"]:
            add_violation("file_size_mb", "Reduce chapter size below the ACX upload limit.", round(file_size_mb, 3))

    opening_credits_exists = any(
        chapter.type == ChapterType.OPENING_CREDITS
        or (chapter.number in {0, 1} and "opening" in (chapter.title or "").lower())
        for chapter in chapters
    )
    closing_credits_exists = any(chapter.type == ChapterType.CLOSING_CREDITS for chapter in chapters) or (
        bool(chapters) and "closing" in ((chapters[-1].title or "").lower())
    )
    if not opening_credits_exists:
        severity = "warning" if export_mode else "blocker"
        violations.append(
            {
                "chapter_n": None,
                "issue": "opening_credits",
                "value": None,
                "remediation": "Ensure an opening credits chapter exists before export.",
                "severity": severity,
            }
        )
        (recommendations if severity == "warning" else blockers).append(
            "Opening credits chapter is missing from the export sequence."
        )
    if not closing_credits_exists:
        severity = "warning" if export_mode else "blocker"
        violations.append(
            {
                "chapter_n": None,
                "issue": "closing_credits",
                "value": None,
                "remediation": "Ensure a closing credits chapter exists as the final chapter.",
                "severity": severity,
            }
        )
        (recommendations if severity == "warning" else blockers).append(
            "Closing credits chapter is missing from the export sequence."
        )

    if blockers:
        status = QAAutomaticStatus.FAIL.value
    elif violations:
        status = QAAutomaticStatus.WARNING.value
    else:
        status = QAAutomaticStatus.PASS.value
    message = (
        "All chapters satisfy ACX/Audible requirements."
        if not violations
        else f"ACX compliance violations detected in {len({violation['chapter_n'] for violation in violations})} chapter(s)."
    )
    return BookQACheck(
        status=status,
        message=message,
        details={
            "violations": violations,
            "opening_credits_present": opening_credits_exists,
            "closing_credits_present": closing_credits_exists,
        },
        recommendations=recommendations or (["All chapters satisfy ACX/Audible requirements."] if not violations else []),
        blockers=blockers,
    )


def get_voice_consistency_chart(book_id: int, db_session: Session) -> VoiceConsistencyChart:
    """Return the frontend voice consistency chart payload."""

    voice_check = check_cross_chapter_voice(book_id, db_session)
    return VoiceConsistencyChart.model_validate(
        {
            "chapters": voice_check.details.get("chapters", []),
            "book_median": voice_check.details.get("book_median", {}),
            "outlier_chapters": voice_check.details.get("outlier_chapters", []),
        }
    )


def run_book_qa(book_id: int, db_session: Session, *, export_mode: bool = False) -> BookQAReport:
    """Run the full Gate 3 pipeline and return the aggregate book report."""

    book, chapters, qa_records = _require_gate3_ready(book_id, db_session)
    loudness = check_cross_chapter_loudness(book_id, db_session)
    loudness_range = check_loudness_range(book_id, db_session)
    voice = check_cross_chapter_voice(book_id, db_session)
    pacing = check_cross_chapter_pacing(book_id, db_session)
    transitions = check_chapter_transitions(book_id, db_session)
    acx = check_acx_compliance(book_id, db_session, export_mode=export_mode)

    chapter_grades = [_chapter_grade(chapter, qa_records.get(chapter.number)) for chapter in chapters]
    chapters_grade_a = chapter_grades.count("A")
    chapters_grade_b = chapter_grades.count("B")
    chapters_grade_c = chapter_grades.count("C")
    chapters_grade_f = chapter_grades.count("F")

    checks = {
        "loudness_consistency": loudness,
        "loudness_range": loudness_range,
        "voice_consistency": voice,
        "pacing_consistency": pacing,
        "chapter_transitions": transitions,
        "acx_compliance": acx,
    }
    warning_checks = sum(check.status == QAAutomaticStatus.WARNING.value for check in checks.values())
    export_blockers = [blocker for check in checks.values() for blocker in check.blockers]
    recommendations = [recommendation for check in checks.values() for recommendation in check.recommendations]

    if export_blockers or chapters_grade_f > 0:
        overall_grade = "F"
    elif chapters_grade_c > 1 or warning_checks > 2:
        overall_grade = "C"
    elif chapters_grade_b > 0 or chapters_grade_c > 0 or warning_checks > 0:
        overall_grade = "B"
    else:
        overall_grade = "A"

    return BookQAReport(
        book_id=book.id,
        title=book.title,
        total_chapters=len(chapters),
        chapters_grade_a=chapters_grade_a,
        chapters_grade_b=chapters_grade_b,
        chapters_grade_c=chapters_grade_c,
        chapters_grade_f=chapters_grade_f,
        overall_grade=overall_grade,
        ready_for_export=len(export_blockers) == 0,
        cross_chapter_checks={
            "loudness_consistency": {
                "status": loudness.status,
                "mean_lufs": loudness.details.get("mean_lufs"),
                "max_deviation_lu": loudness.details.get("max_deviation_lu"),
                "message": loudness.message,
            },
            "voice_consistency": {
                "status": voice.status,
                "outlier_chapters": voice.details.get("outlier_chapters", []),
                "message": voice.message,
            },
            "loudness_range": {
                "status": loudness_range.status,
                "chapters": loudness_range.details.get("chapters", []),
                "mean_lra_lu": loudness_range.details.get("mean_lra_lu"),
                "message": loudness_range.message,
            },
            "pacing_consistency": {
                "status": pacing.status,
                "mean_wpm": pacing.details.get("mean_wpm"),
                "max_deviation_pct": pacing.details.get("max_deviation_pct"),
                "message": pacing.message,
            },
            "chapter_transitions": {
                "status": transitions.status,
                "issues": transitions.details.get("issues", []),
                "message": transitions.message,
            },
            "acx_compliance": {
                "status": acx.status,
                "violations": acx.details.get("violations", []),
                "message": acx.message,
            },
        },
        recommendations=recommendations,
        export_blockers=export_blockers,
    )
