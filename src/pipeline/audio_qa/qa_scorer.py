"""Weighted scoring helpers for deep audio QA."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from src.config import settings
from src.database import AudioQAResult, Book, Chapter, ChapterStatus, utc_now
from src.pipeline.audio_qa.models import (
    AudioQAIssue,
    AudioQAScoreBreakdown,
    AudioQualityAnalysis,
    BookDeepQAReport,
    ChapterDeepQAResult,
    TimingAnalysis,
    TranscriptionAnalysis,
)
from src.pipeline.audio_qa.audio_quality_analyzer import AudioQualityAnalyzer
from src.pipeline.audio_qa.timing_analyzer import TimingAndPacingAnalyzer
from src.pipeline.audio_qa.transcription_checker import TranscriptionAccuracyChecker


class AudioQAScorer:
    """Compute a single grade from transcription, timing, and quality stages."""

    WEIGHTS = {
        "transcription": 0.45,
        "timing": 0.20,
        "quality": 0.35,
    }

    def score(
        self,
        transcription: TranscriptionAnalysis,
        timing: TimingAnalysis,
        quality: AudioQualityAnalysis,
    ) -> AudioQAScoreBreakdown:
        """Return a weighted scorecard."""

        overall = (
            (transcription.score * self.WEIGHTS["transcription"])
            + (timing.score * self.WEIGHTS["timing"])
            + (quality.score * self.WEIGHTS["quality"])
        )
        stage_statuses = [transcription.status, timing.status, quality.status]
        stage_reasoning = [
            f"Transcription {transcription.status} ({transcription.score:.2f}/100)",
            f"Timing {timing.status} ({timing.score:.2f}/100)",
            f"Quality {quality.status} ({quality.score:.2f}/100)",
        ]
        if any(status in {"fail", "failed", "dependency_unavailable"} for status in stage_statuses):
            overall = min(overall, 69.0)
            overall_status = "fail"
        elif any(status == "warning" for status in stage_statuses):
            overall_status = "warning"
        else:
            overall_status = "pass"

        overall = round(overall, 2)
        grade = "A" if overall >= 90 else "B" if overall >= 80 else "C" if overall >= 70 else "F"
        status = overall_status
        if overall < 70:
            status = "fail"
        elif overall < 85 and status == "pass":
            status = "warning"
        return AudioQAScoreBreakdown(
            transcription=transcription.score,
            timing=timing.score,
            quality=quality.score,
            overall=overall,
            grade=grade,
            status=status,
            reasoning=stage_reasoning + [
                f"Transcription weight {self.WEIGHTS['transcription']:.2f}",
                f"Timing weight {self.WEIGHTS['timing']:.2f}",
                f"Quality weight {self.WEIGHTS['quality']:.2f}",
            ],
        )

    def finalize(
        self,
        *,
        book_id: int,
        chapter_id: int | None,
        chapter_n: int,
        chapter_title: str | None,
        audio_path: str | None,
        transcription: TranscriptionAnalysis,
        timing: TimingAnalysis,
        quality: AudioQualityAnalysis,
        checked_at=None,
    ) -> ChapterDeepQAResult:
        """Build a chapter-level report with merged issues and scorecard."""

        scoring = self.score(transcription, timing, quality)
        issues = list(transcription.issues) + list(timing.issues) + list(quality.issues)
        ready_for_export = (
            scoring.status == "pass"
            and not any(issue.severity == "error" for issue in issues)
            and all(stage.status != "dependency_unavailable" for stage in (transcription, timing, quality))
        )
        summary = self._build_summary(scoring, issues)
        return ChapterDeepQAResult(
            book_id=book_id,
            chapter_id=chapter_id,
            chapter_n=chapter_n,
            chapter_title=chapter_title,
            audio_path=audio_path,
            checked_at=checked_at or utc_now(),
            transcription=transcription,
            timing=timing,
            quality=quality,
            scoring=scoring,
            issues=issues,
            ready_for_export=ready_for_export,
            summary=summary,
        )

    def _build_summary(self, scoring: AudioQAScoreBreakdown, issues: list[AudioQAIssue]) -> str:
        """Generate a concise summary string for the UI and API."""

        if not issues:
            return f"Deep audio QA passed with grade {scoring.grade} ({scoring.overall:.1f}/100)."
        return (
            f"Deep audio QA finished with grade {scoring.grade} ({scoring.overall:.1f}/100) "
            f"and {len(issues)} issue(s) that need review."
        )


def resolve_audio_path(audio_path: str | None) -> Path | None:
    """Resolve stored chapter audio paths against the outputs directory."""

    if not audio_path:
        return None
    candidate = Path(audio_path)
    if candidate.is_absolute():
        return candidate
    return (Path(settings.OUTPUTS_PATH) / candidate).resolve()


def _failure_result(chapter: Chapter, message: str, *, code: str = "audio_qa_failed") -> ChapterDeepQAResult:
    """Return a fully-formed failing deep-QA payload for one chapter."""

    issue = AudioQAIssue(
        code=code,
        category="pipeline",
        severity="error",
        message=message,
    )
    transcription = TranscriptionAnalysis(status="failed", issues=[issue])
    timing = TimingAnalysis(status="failed", issues=[issue])
    quality = AudioQualityAnalysis(status="failed", issues=[issue])
    scorer = AudioQAScorer()
    return scorer.finalize(
        book_id=chapter.book_id,
        chapter_id=chapter.id,
        chapter_n=chapter.number,
        chapter_title=chapter.title,
        audio_path=chapter.audio_path,
        transcription=transcription,
        timing=timing,
        quality=quality,
        checked_at=utc_now(),
    )


def persist_chapter_audio_qa_result(
    db: Session,
    chapter: Chapter,
    result: ChapterDeepQAResult,
) -> AudioQAResult:
    """Insert or update the stored deep-QA record for one chapter."""

    record = (
        db.query(AudioQAResult)
        .filter(AudioQAResult.book_id == chapter.book_id, AudioQAResult.chapter_n == chapter.number)
        .first()
    )
    if record is None:
        record = AudioQAResult(
            book_id=chapter.book_id,
            chapter_id=chapter.id,
            chapter_n=chapter.number,
        )
        db.add(record)

    record.chapter_id = chapter.id
    record.transcription_score = float(result.scoring.transcription)
    record.timing_score = float(result.scoring.timing)
    record.quality_score = float(result.scoring.quality)
    record.overall_score = float(result.scoring.overall)
    record.overall_grade = result.scoring.grade
    record.overall_status = result.scoring.status
    record.report_json = result.model_dump_json()
    record.issues_count = len(result.issues)
    record.checked_at = result.checked_at or utc_now()
    return record


def run_chapter_audio_qa(
    chapter: Chapter,
    db: Session,
    *,
    transcription_checker: TranscriptionAccuracyChecker | None = None,
    timing_analyzer: TimingAndPacingAnalyzer | None = None,
    quality_analyzer: AudioQualityAnalyzer | None = None,
    scorer: AudioQAScorer | None = None,
) -> ChapterDeepQAResult:
    """Run the deep audio QA pipeline for a single generated chapter."""

    del db

    scorer = scorer or AudioQAScorer()
    transcription_checker = transcription_checker or TranscriptionAccuracyChecker()
    timing_analyzer = timing_analyzer or TimingAndPacingAnalyzer()
    quality_analyzer = quality_analyzer or AudioQualityAnalyzer()

    if chapter.status != ChapterStatus.GENERATED:
        raise ValueError(f"Chapter {chapter.number} must be generated before deep QA can run.")

    audio_path = resolve_audio_path(chapter.audio_path)
    if audio_path is None or not audio_path.exists():
        raise ValueError(f"Audio file for chapter {chapter.number} is missing.")

    transcription = transcription_checker.analyze(audio_path, chapter.text_content or "")
    timing = timing_analyzer.analyze(audio_path, chapter.text_content or "")
    quality = quality_analyzer.analyze(audio_path)
    return scorer.finalize(
        book_id=chapter.book_id,
        chapter_id=chapter.id,
        chapter_n=chapter.number,
        chapter_title=chapter.title,
        audio_path=str(audio_path),
        transcription=transcription,
        timing=timing,
        quality=quality,
        checked_at=utc_now(),
    )


def aggregate_book_audio_qa_report(
    book_id: int,
    chapter_results: list[ChapterDeepQAResult],
) -> BookDeepQAReport:
    """Aggregate chapter deep-QA reports into one book-level summary."""

    if not chapter_results:
        raise ValueError(f"No deep audio QA results are available for book {book_id}.")

    average_score = sum(result.scoring.overall for result in chapter_results) / len(chapter_results)
    average_transcription_score = sum(result.scoring.transcription for result in chapter_results) / len(chapter_results)
    average_timing_score = sum(result.scoring.timing for result in chapter_results) / len(chapter_results)
    average_quality_score = sum(result.scoring.quality for result in chapter_results) / len(chapter_results)
    grade_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for result in chapter_results:
        grade_counts[result.scoring.grade] = grade_counts.get(result.scoring.grade, 0) + 1
        status_counts[result.scoring.status] = status_counts.get(result.scoring.status, 0) + 1

    return BookDeepQAReport(
        book_id=book_id,
        generated_at=utc_now(),
        chapters=chapter_results,
        chapter_count=len(chapter_results),
        average_score=round(average_score, 2),
        average_transcription_score=round(average_transcription_score, 2),
        average_timing_score=round(average_timing_score, 2),
        average_quality_score=round(average_quality_score, 2),
        ready_for_export=all(result.ready_for_export for result in chapter_results),
        issue_count=sum(len(result.issues) for result in chapter_results),
        grade_counts=grade_counts,
        status_counts=status_counts,
    )


def load_book_audio_qa_report(book_id: int, db: Session) -> BookDeepQAReport:
    """Load stored chapter deep-QA results and aggregate them for one book."""

    rows = (
        db.query(AudioQAResult)
        .filter(AudioQAResult.book_id == book_id)
        .order_by(AudioQAResult.chapter_n.asc(), AudioQAResult.checked_at.desc())
        .all()
    )
    if not rows:
        raise ValueError(f"No deep audio QA results are available for book {book_id}.")

    chapter_results = [ChapterDeepQAResult.model_validate_json(row.report_json) for row in rows]
    return aggregate_book_audio_qa_report(book_id, chapter_results)


def run_book_audio_qa(
    book: Book,
    db: Session,
    *,
    transcription_checker: TranscriptionAccuracyChecker | None = None,
    timing_analyzer: TimingAndPacingAnalyzer | None = None,
    quality_analyzer: AudioQualityAnalyzer | None = None,
    scorer: AudioQAScorer | None = None,
) -> BookDeepQAReport:
    """Run deep audio QA across every generated chapter in a book and persist the results."""

    chapters = (
        db.query(Chapter)
        .filter(Chapter.book_id == book.id, Chapter.status == ChapterStatus.GENERATED)
        .order_by(Chapter.number.asc())
        .all()
    )
    if not chapters:
        raise ValueError(f"Book {book.id} has no generated chapters to analyze.")

    chapter_results: list[ChapterDeepQAResult] = []
    for chapter in chapters:
        try:
            result = run_chapter_audio_qa(
                chapter,
                db,
                transcription_checker=transcription_checker,
                timing_analyzer=timing_analyzer,
                quality_analyzer=quality_analyzer,
                scorer=scorer,
            )
        except Exception as exc:
            result = _failure_result(chapter, str(exc))

        persist_chapter_audio_qa_result(db, chapter, result)
        chapter_results.append(result)

    db.flush()
    return aggregate_book_audio_qa_report(book.id, chapter_results)
