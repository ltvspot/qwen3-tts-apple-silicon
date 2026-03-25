"""Production overseer reporting and quality visibility endpoints."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.database import BatchBookStatus, BatchRun, Book, Chapter, ChapterQARecord, ChapterStatus, get_db
from src.pipeline.book_qa import run_book_qa
from src.pipeline.manuscript_validator import ManuscriptValidator
from src.pipeline.pronunciation_watchlist import PronunciationWatchlist
from src.pipeline.qa_checker import build_qa_record_response
from src.pipeline.quality_tracker import GRADE_TO_SCORE, QualityTracker

router = APIRouter(prefix="/api/overseer", tags=["overseer"])


class OverseerCheckResponse(BaseModel):
    """One export-readiness or oversight checklist item."""

    name: str
    passed: bool
    detail: str


class ExportVerificationResponse(BaseModel):
    """Full export verification payload for one book."""

    book_id: int
    title: str
    checks: list[OverseerCheckResponse]
    ready_for_export: bool
    blockers: list[str]
    recommendations: list[str]


class FlaggedChapterResponse(BaseModel):
    """One chapter that requires deeper human review."""

    chapter_n: int
    chapter_title: str | None
    qa_grade: str | None = None
    ready_for_export: bool | None = None
    overall_status: str | None = None
    issues: list[str] = Field(default_factory=list)
    manual_notes: str | None = None
    pronunciation_flags: list[dict[str, str]] = Field(default_factory=list)


class PronunciationIssueResponse(BaseModel):
    """One watchlist hit within a book."""

    chapter_n: int
    chapter_title: str | None
    word: str
    pronunciation_guide: str
    context: str


class BookReportResponse(BaseModel):
    """Complete oversight report spanning manuscript checks and all quality gates."""

    book_id: int
    title: str
    total_chapters: int
    manuscript_validation: dict[str, Any]
    gate1_summary: dict[str, Any]
    gate2_summary: dict[str, Any]
    gate3_report: dict[str, Any] | None
    flagged_chapters: list[FlaggedChapterResponse]
    pronunciation_issues: list[PronunciationIssueResponse]
    export_verification: ExportVerificationResponse
    quality_snapshot: dict[str, Any] | None = None


class QualityTrendResponse(BaseModel):
    """Aggregated quality trend metrics for recent books."""

    books_analyzed: int
    avg_gate1_pass_rate: float
    avg_gate2_grade: float
    gate3_grade_distribution: dict[str, int]
    avg_chunks_regenerated: float
    avg_generation_rtf: float
    trend: str
    alerts: list[str]
    recent_books: list[dict[str, Any]]
    trend_points: list[dict[str, Any]]


class QualityAlertsResponse(BaseModel):
    """Active overseer alerts derived from quality trends."""

    trend: str
    alerts: list[str]


class BatchBookQualityResponse(BaseModel):
    """Per-book summary row within a batch report."""

    book_id: int
    title: str
    status: str
    chapters_total: int
    chapters_completed: int
    chapters_failed: int
    quality_grade: str | None = None
    issues_found: int | None = None
    ready_for_export: bool | None = None
    error_message: str | None = None


class BatchReportResponse(BaseModel):
    """Full overseer summary for one catalog batch."""

    batch_id: str
    status: str
    total_books: int
    books_completed: int
    books_failed: int
    books_skipped: int
    current_book_title: str | None = None
    resource_warnings: list[str] = Field(default_factory=list)
    books: list[BatchBookQualityResponse]


class FlaggedItemResponse(BaseModel):
    """Cross-book flagged item displayed on the overseer dashboard."""

    book_id: int
    book_title: str
    chapter_n: int | None = None
    chapter_title: str | None = None
    qa_grade: str | None = None
    reason: str
    actions: list[str] = Field(default_factory=lambda: ["View Details"])


class FlaggedItemsResponse(BaseModel):
    """Actionable flagged items across the catalog."""

    items: list[FlaggedItemResponse]


def _load_book_or_404(book_id: int, db: Session) -> Book:
    """Load a book or raise 404."""

    book = db.query(Book).filter(Book.id == book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")
    return book


def _load_batch_or_404(batch_id: str, db: Session) -> BatchRun:
    """Load a batch run or raise 404."""

    batch_run = db.query(BatchRun).filter(BatchRun.batch_id == batch_id).first()
    if batch_run is None:
        raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found")
    return batch_run


def _book_chapters(book_id: int, db: Session) -> list[Chapter]:
    """Return ordered chapters for one book."""

    return (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id)
        .order_by(Chapter.number.asc(), Chapter.id.asc())
        .all()
    )


def _qa_records(book_id: int, db: Session) -> dict[int, ChapterQARecord]:
    """Return QA records keyed by chapter number."""

    return {
        record.chapter_n: record
        for record in db.query(ChapterQARecord).filter(ChapterQARecord.book_id == book_id).all()
    }


def _generation_metadata(chapter: Chapter) -> dict[str, Any]:
    """Deserialize stored generation metadata when present."""

    if not chapter.generation_metadata:
        return {}
    try:
        payload = json.loads(chapter.generation_metadata)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def _chapter_grade_payload(record: ChapterQARecord, chapter: Chapter | None) -> dict[str, Any]:
    """Return the current QA payload for one chapter."""

    return build_qa_record_response(record, chapter)


def _pronunciation_hits(book: Book, chapters: list[Chapter]) -> list[PronunciationIssueResponse]:
    """Return watchlist matches across a book."""

    watchlist = PronunciationWatchlist()
    hits: list[PronunciationIssueResponse] = []
    for chapter in chapters:
        for hit in watchlist.check_text(chapter.text_content or ""):
            hits.append(
                PronunciationIssueResponse(
                    chapter_n=chapter.number,
                    chapter_title=chapter.title,
                    word=hit["word"],
                    pronunciation_guide=hit["pronunciation_guide"],
                    context=hit["context"],
                )
            )
    return hits


def _manuscript_validation(book: Book, chapters: list[Chapter]) -> dict[str, Any]:
    """Return the manuscript validation report in JSON-compatible form."""

    report = ManuscriptValidator.validate(
        book.id,
        [
            {
                "book_title": book.title,
                "number": chapter.number,
                "text": chapter.text_content or "",
            }
            for chapter in chapters
        ],
    )
    summary = {"errors": 0, "warnings": 0, "info": 0}
    for issue in report.issues:
        summary[f"{issue.severity}s" if issue.severity != "info" else "info"] += 1
    return {
        "book_id": report.book_id,
        "title": report.title,
        "total_chapters": report.total_chapters,
        "total_words": report.total_words,
        "difficulty_score": report.difficulty_score,
        "ready_for_generation": report.ready_for_generation,
        "issues": [
            {
                "severity": issue.severity,
                "chapter": issue.chapter,
                "description": issue.description,
                "suggestion": issue.suggestion,
            }
            for issue in report.issues
        ],
        "issue_summary": summary,
    }


def _gate1_summary(chapters: list[Chapter]) -> dict[str, Any]:
    """Aggregate Gate 1 persisted metrics across the book."""

    total_chunks = 0
    chunks_pass_first_attempt = 0
    chunks_regenerated = 0
    warning_chunks = 0
    failed_chunks = 0
    issue_chunks = 0
    wer_values: list[float] = []

    for chapter in chapters:
        gate1 = _generation_metadata(chapter).get("gate1", {})
        chapter_chunks = int(gate1.get("chunks_total", chapter.total_chunks or 0) or 0)
        if chapter_chunks <= 0:
            chapter_chunks = 1
        total_chunks += chapter_chunks
        chunks_pass_first_attempt += int(gate1.get("chunks_pass_first_attempt", chapter_chunks) or 0)
        chunks_regenerated += int(gate1.get("chunks_regenerated", 0) or 0)
        warning_chunks += int(gate1.get("chunks_with_warnings", 0) or 0)
        failed_chunks += int(gate1.get("chunks_failed_final", 0) or 0)
        issue_chunks += int(gate1.get("validation_issue_chunks", 0) or 0)
        avg_wer = gate1.get("avg_wer")
        if isinstance(avg_wer, (int, float)):
            wer_values.append(float(avg_wer))

    pass_rate = round((chunks_pass_first_attempt / total_chunks) * 100.0, 2) if total_chunks else 100.0
    return {
        "total_chunks": total_chunks,
        "chunks_pass_first_attempt": chunks_pass_first_attempt,
        "gate1_pass_rate": pass_rate,
        "chunks_regenerated": chunks_regenerated,
        "warning_chunks": warning_chunks,
        "failed_chunks": failed_chunks,
        "issue_chunks": issue_chunks,
        "avg_wer": round(sum(wer_values) / len(wer_values), 4) if wer_values else None,
    }


def _flagged_chapters(book: Book, chapters: list[Chapter], qa_records: dict[int, ChapterQARecord]) -> list[FlaggedChapterResponse]:
    """Return chapters with poor grades or actionable review notes."""

    pronunciation_lookup: dict[int, list[dict[str, str]]] = {}
    for hit in _pronunciation_hits(book, chapters):
        pronunciation_lookup.setdefault(hit.chapter_n, []).append(
            {
                "word": hit.word,
                "pronunciation_guide": hit.pronunciation_guide,
                "context": hit.context,
            }
        )

    flagged: list[FlaggedChapterResponse] = []
    for chapter in chapters:
        record = qa_records.get(chapter.number)
        if record is None:
            continue
        payload = _chapter_grade_payload(record, chapter)
        grade = payload.get("qa_grade")
        manual_notes = payload.get("manual_notes")
        issues = [
            f"{check['name']}: {check['message']}"
            for check in payload["automatic_checks"]
            if check["status"] != "pass"
        ]
        chapter_report = payload.get("chapter_report") or {}
        issues.extend(chapter_report.get("warnings", []) or [])
        issues.extend(chapter_report.get("failures", []) or [])
        pronunciation_flags = pronunciation_lookup.get(chapter.number, [])

        if grade not in {"C", "F"}:
            continue

        flagged.append(
            FlaggedChapterResponse(
                chapter_n=chapter.number,
                chapter_title=chapter.title,
                qa_grade=grade,
                ready_for_export=payload.get("ready_for_export"),
                overall_status=payload.get("overall_status"),
                issues=issues,
                manual_notes=manual_notes,
                pronunciation_flags=pronunciation_flags,
            )
        )

    return flagged


def _gate2_summary(chapters: list[Chapter], qa_records: dict[int, ChapterQARecord]) -> dict[str, Any]:
    """Aggregate Gate 2 chapter-level quality metrics."""

    grades: list[str] = []
    chapters_payload: list[dict[str, Any]] = []
    pending_manual = 0

    for chapter in chapters:
        record = qa_records.get(chapter.number)
        if record is None:
            continue
        payload = _chapter_grade_payload(record, chapter)
        grade = payload.get("qa_grade") or "F"
        if payload.get("manual_status") is None and payload.get("overall_status") in {"warning", "fail"}:
            pending_manual += 1
        grades.append(grade)
        chapters_payload.append(
            {
                "chapter_n": chapter.number,
                "title": chapter.title,
                "qa_grade": grade,
                "ready_for_export": payload.get("ready_for_export"),
                "manual_status": payload.get("manual_status"),
                "manual_notes": payload.get("manual_notes"),
            }
        )

    avg_grade = round(sum(GRADE_TO_SCORE.get(grade, 0.0) for grade in grades) / len(grades), 3) if grades else 0.0
    return {
        "average_grade": avg_grade,
        "chapters_grade_a": grades.count("A"),
        "chapters_grade_b": grades.count("B"),
        "chapters_grade_c": grades.count("C"),
        "chapters_grade_f": grades.count("F"),
        "chapters_pending_manual": pending_manual,
        "chapters": chapters_payload,
    }


def _safe_book_report(book_id: int, db: Session) -> dict[str, Any] | None:
    """Return the Gate 3 report when the book is ready, otherwise ``None``."""

    try:
        report = run_book_qa(book_id, db)
    except ValueError:
        return None
    return report.model_dump(mode="json")


def _chapter_markers_valid(chapters: list[Chapter]) -> tuple[bool, str]:
    """Return whether chapter markers are sequential and complete."""

    if not chapters:
        return False, "No chapters available."

    numbers = [chapter.number for chapter in chapters]
    expected = list(range(numbers[0], numbers[0] + len(numbers)))
    if numbers != expected:
        return False, "Chapter numbers are not sequential."
    return True, f"{len(numbers)} markers, all sequential"


def _export_verification(book: Book, chapters: list[Chapter], qa_records: dict[int, ChapterQARecord], book_report: dict[str, Any] | None) -> ExportVerificationResponse:
    """Build the pre-export verification checklist for one book."""

    total_chapters = len(chapters)
    generated_chapters = sum(chapter.status == ChapterStatus.GENERATED for chapter in chapters)
    generated_passed = generated_chapters == total_chapters and total_chapters > 0

    graded_payloads = [
        _chapter_grade_payload(record, next((chapter for chapter in chapters if chapter.number == number), None))
        for number, record in sorted(qa_records.items())
    ]
    chapters_b_or_better = [payload for payload in graded_payloads if payload.get("qa_grade") in {"A", "B"}]
    gate2_passed = total_chapters > 0 and len(chapters_b_or_better) == total_chapters

    gate3_grade = book_report.get("overall_grade") if book_report is not None else None
    gate3_passed = gate3_grade in {"A", "B"} and bool(book_report and book_report.get("ready_for_export"))

    mastering_complete = total_chapters > 0 and all(chapter.mastered for chapter in chapters if chapter.status == ChapterStatus.GENERATED)
    acx_check = (book_report or {}).get("cross_chapter_checks", {}).get("acx_compliance", {})
    acx_passed = acx_check.get("status") == "pass"

    metadata_complete = bool(book.title.strip() and book.author.strip() and book.narrator.strip()) and all(
        (chapter.title or "").strip() for chapter in chapters
    )
    chapter_markers_passed, chapter_markers_detail = _chapter_markers_valid(chapters)

    checks = [
        OverseerCheckResponse(
            name="all_chapters_generated",
            passed=generated_passed,
            detail=f"{generated_chapters}/{total_chapters} chapters",
        ),
        OverseerCheckResponse(
            name="gate2_minimum_grade",
            passed=gate2_passed,
            detail=(
                "All chapters grade B or better"
                if gate2_passed
                else f"{len(chapters_b_or_better)}/{total_chapters} chapters grade B or better"
            ),
        ),
        OverseerCheckResponse(
            name="gate3_passed",
            passed=gate3_passed,
            detail=f"Book grade: {gate3_grade or 'not_run'}",
        ),
        OverseerCheckResponse(
            name="mastering_complete",
            passed=mastering_complete,
            detail=(
                "Loudness normalized, edges trimmed"
                if mastering_complete
                else "Run the mastering pipeline to normalize all generated chapters."
            ),
        ),
        OverseerCheckResponse(
            name="acx_compliance",
            passed=acx_passed,
            detail=acx_check.get("message", "Book QA has not verified ACX compliance yet."),
        ),
        OverseerCheckResponse(
            name="metadata_complete",
            passed=metadata_complete,
            detail=(
                "Title, author, narrator set"
                if metadata_complete
                else "Title, author, narrator, and chapter names must be populated."
            ),
        ),
        OverseerCheckResponse(
            name="chapter_markers_valid",
            passed=chapter_markers_passed,
            detail=chapter_markers_detail,
        ),
    ]

    blockers = [check.detail for check in checks if not check.passed]
    recommendations = []
    if book_report is not None:
        recommendations.extend(book_report.get("recommendations", []))
    for payload in graded_payloads:
        if payload.get("qa_grade") == "B":
            chapter_n = payload.get("chapter_n")
            recommendations.append(
                f"Consider regenerating chapter {chapter_n} (grade B, review recommended)."
            )

    deduped_recommendations = list(dict.fromkeys(recommendations))
    return ExportVerificationResponse(
        book_id=book.id,
        title=book.title,
        checks=checks,
        ready_for_export=all(check.passed for check in checks),
        blockers=blockers,
        recommendations=deduped_recommendations,
    )


@router.get("/book/{book_id}/report", response_model=BookReportResponse)
async def get_book_report(book_id: int, db: Session = Depends(get_db)) -> BookReportResponse:
    """Return the complete Production Overseer report for one book."""

    book = _load_book_or_404(book_id, db)
    chapters = _book_chapters(book_id, db)
    qa_records = _qa_records(book_id, db)
    book_report = _safe_book_report(book_id, db)
    export_verification = _export_verification(book, chapters, qa_records, book_report)
    snapshot = None
    if book_report is not None:
        snapshot = asdict(QualityTracker.ensure_book_quality_snapshot(book_id, db))
        db.commit()

    return BookReportResponse(
        book_id=book.id,
        title=book.title,
        total_chapters=len(chapters),
        manuscript_validation=_manuscript_validation(book, chapters),
        gate1_summary=_gate1_summary(chapters),
        gate2_summary=_gate2_summary(chapters, qa_records),
        gate3_report=book_report,
        flagged_chapters=_flagged_chapters(book, chapters, qa_records),
        pronunciation_issues=_pronunciation_hits(book, chapters),
        export_verification=export_verification,
        quality_snapshot=snapshot,
    )


@router.get("/book/{book_id}/flagged-chapters", response_model=list[FlaggedChapterResponse])
async def get_book_flagged_chapters(book_id: int, db: Session = Depends(get_db)) -> list[FlaggedChapterResponse]:
    """Return chapters in a book with a Gate 2 grade of C or F."""

    book = _load_book_or_404(book_id, db)
    chapters = _book_chapters(book_id, db)
    qa_records = _qa_records(book_id, db)
    return _flagged_chapters(book, chapters, qa_records)


@router.get("/book/{book_id}/export-checklist", response_model=ExportVerificationResponse)
async def get_export_checklist(book_id: int, db: Session = Depends(get_db)) -> ExportVerificationResponse:
    """Return the export readiness checklist for one book."""

    book = _load_book_or_404(book_id, db)
    chapters = _book_chapters(book_id, db)
    qa_records = _qa_records(book_id, db)
    return _export_verification(book, chapters, qa_records, _safe_book_report(book_id, db))


@router.get("/quality-trend", response_model=QualityTrendResponse)
async def get_quality_trend(last_n: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)) -> QualityTrendResponse:
    """Return quality metrics trend data for recent books."""

    trend = QualityTracker.get_quality_trend(last_n_books=last_n, db_session=db)
    return QualityTrendResponse(**trend)


@router.get("/quality-trend/alerts", response_model=QualityAlertsResponse)
async def get_quality_trend_alerts(last_n: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)) -> QualityAlertsResponse:
    """Return the active quality degradation alerts only."""

    trend = QualityTracker.get_quality_trend(last_n_books=last_n, db_session=db)
    return QualityAlertsResponse(trend=trend["trend"], alerts=trend["alerts"])


@router.get("/batch/{batch_id}/report", response_model=BatchReportResponse)
async def get_batch_report(batch_id: str, db: Session = Depends(get_db)) -> BatchReportResponse:
    """Return a quality and execution summary for one batch run."""

    batch_run = _load_batch_or_404(batch_id, db)
    books: list[BatchBookQualityResponse] = []
    for book_status in (
        db.query(BatchBookStatus)
        .filter(BatchBookStatus.batch_id == batch_id)
        .order_by(BatchBookStatus.id.asc())
        .all()
    ):
        book = db.query(Book).filter(Book.id == book_status.book_id).first()
        snapshot = None
        try:
            snapshot = QualityTracker.ensure_book_quality_snapshot(book_status.book_id, db)
        except Exception:
            snapshot = None

        books.append(
            BatchBookQualityResponse(
                book_id=book_status.book_id,
                title=book.title if book is not None else f"Book {book_status.book_id}",
                status=book_status.status,
                chapters_total=book_status.chapters_total,
                chapters_completed=book_status.chapters_completed,
                chapters_failed=book_status.chapters_failed,
                quality_grade=snapshot.gate3_overall_grade if snapshot is not None else None,
                issues_found=snapshot.issues_found if snapshot is not None else None,
                ready_for_export=(snapshot.gate3_overall_grade in {"A", "B"}) if snapshot is not None else None,
                error_message=book_status.error_message,
            )
        )

    resource_warnings = [warning.strip() for warning in (batch_run.resource_warnings or "").split(";") if warning.strip()]
    db.commit()
    return BatchReportResponse(
        batch_id=batch_run.batch_id,
        status=batch_run.status,
        total_books=batch_run.total_books,
        books_completed=batch_run.books_completed,
        books_failed=batch_run.books_failed,
        books_skipped=batch_run.books_skipped,
        current_book_title=batch_run.current_book_title,
        resource_warnings=resource_warnings,
        books=books,
    )


@router.get("/pronunciation-issues", response_model=list[PronunciationIssueResponse])
async def get_pronunciation_issues(book_id: int = Query(..., ge=1), db: Session = Depends(get_db)) -> list[PronunciationIssueResponse]:
    """Return pronunciation watchlist issues for one book."""

    book = _load_book_or_404(book_id, db)
    chapters = _book_chapters(book_id, db)
    return _pronunciation_hits(book, chapters)


@router.get("/book/{book_id}/export-verification", response_model=ExportVerificationResponse)
async def get_export_verification(book_id: int, db: Session = Depends(get_db)) -> ExportVerificationResponse:
    """Return the full export verification checklist for one book."""

    book = _load_book_or_404(book_id, db)
    chapters = _book_chapters(book_id, db)
    qa_records = _qa_records(book_id, db)
    return _export_verification(book, chapters, qa_records, _safe_book_report(book_id, db))


@router.get("/flagged-items", response_model=FlaggedItemsResponse)
async def get_flagged_items(limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db)) -> FlaggedItemsResponse:
    """Return actionable flagged items across the catalog."""

    items: list[FlaggedItemResponse] = []
    chapters = (
        db.query(Chapter)
        .filter(Chapter.audio_path.is_not(None))
        .order_by(Chapter.updated_at.desc(), Chapter.id.desc())
        .all()
    )
    chapter_lookup = {(chapter.book_id, chapter.number): chapter for chapter in chapters}
    books = {book.id: book for book in db.query(Book).filter(Book.id.in_({chapter.book_id for chapter in chapters})).all()}
    records = db.query(ChapterQARecord).order_by(ChapterQARecord.checked_at.desc(), ChapterQARecord.id.desc()).all()

    for record in records:
        chapter = chapter_lookup.get((record.book_id, record.chapter_n))
        book = books.get(record.book_id)
        if chapter is None or book is None:
            continue
        payload = _chapter_grade_payload(record, chapter)
        grade = payload.get("qa_grade")
        if grade in {"C", "F"}:
            items.append(
                FlaggedItemResponse(
                    book_id=book.id,
                    book_title=book.title,
                    chapter_n=chapter.number,
                    chapter_title=chapter.title,
                    qa_grade=grade,
                    reason=f"Gate 2 grade {grade}",
                    actions=["Regenerate", "Approve Override", "View Details"],
                )
            )
        if payload.get("manual_notes"):
            items.append(
                FlaggedItemResponse(
                    book_id=book.id,
                    book_title=book.title,
                    chapter_n=chapter.number,
                    chapter_title=chapter.title,
                    qa_grade=grade,
                    reason="Manual review notes present",
                    actions=["View Details", "Regenerate"],
                )
            )

    watchlist = PronunciationWatchlist()
    for chapter in chapters:
        book = books.get(chapter.book_id)
        if book is None:
            continue
        hits = watchlist.check_text(chapter.text_content or "")
        for hit in hits:
            items.append(
                FlaggedItemResponse(
                    book_id=book.id,
                    book_title=book.title,
                    chapter_n=chapter.number,
                    chapter_title=chapter.title,
                    reason=f"Pronunciation warning: {hit['word']}",
                    actions=["View Details", "Regenerate"],
                )
            )

    return FlaggedItemsResponse(items=items[:limit])
