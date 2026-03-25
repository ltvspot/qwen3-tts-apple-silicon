"""Track audio quality metrics across books for trend analysis."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

from sqlalchemy.orm import Session

from src.config import settings
from src.database import Book, Chapter, ChapterQARecord, QualitySnapshot, ensure_aware, utc_now
from src.pipeline.book_qa import measure_integrated_lufs, run_book_qa
from src.pipeline.qa_checker import build_qa_record_response

GRADE_TO_SCORE = {
    "A": 4.0,
    "B": 3.0,
    "C": 2.0,
    "F": 0.0,
}


@dataclass(slots=True)
class BookQualitySnapshot:
    """Computed quality metrics for one completed book."""

    book_id: int
    title: str
    completed_at: datetime
    total_chapters: int
    gate1_pass_rate: float
    gate2_avg_grade: float
    gate3_overall_grade: str
    chunks_regenerated: int
    avg_wer: float | None
    avg_lufs: float
    generation_rtf: float
    issues_found: int


def _safe_mean(values: list[float]) -> float:
    """Return the arithmetic mean or zero when the input is empty."""

    return float(mean(values)) if values else 0.0


def _load_generation_metadata(chapter: Chapter) -> dict[str, Any]:
    """Return persisted Gate 1 summary metadata for a chapter."""

    if not chapter.generation_metadata:
        return {}

    try:
        payload = json.loads(chapter.generation_metadata)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def _extract_wer(metadata: dict[str, Any]) -> float | None:
    """Return the average WER from chapter generation metadata when present."""

    gate1 = metadata.get("gate1")
    if not isinstance(gate1, dict):
        return None

    avg_wer = gate1.get("avg_wer")
    if avg_wer is None:
        return None
    try:
        return float(avg_wer)
    except (TypeError, ValueError):
        return None


def _resolve_audio_path(audio_path: str) -> Path:
    """Resolve a stored chapter audio path against the outputs directory."""

    candidate = Path(audio_path)
    if candidate.is_absolute():
        return candidate
    return (Path(settings.OUTPUTS_PATH) / candidate).resolve()


def _chapter_grade(record: ChapterQARecord) -> str:
    """Return the persisted Gate 2 grade for one chapter."""

    payload = build_qa_record_response(record)
    grade = payload.get("qa_grade")
    return grade if grade in GRADE_TO_SCORE else "F"


def _chapter_issue_count(record: ChapterQARecord, chapter: Chapter | None) -> int:
    """Count surfaced Gate 2 issues for one chapter."""

    payload = build_qa_record_response(record, chapter)
    automatic_issues = sum(1 for check in payload["automatic_checks"] if check["status"] != "pass")
    manual_issues = 1 if payload.get("manual_status") == "flagged" else 0
    report = payload.get("chapter_report") or {}
    report_warnings = len(report.get("warnings", []) or [])
    report_failures = len(report.get("failures", []) or [])
    return automatic_issues + manual_issues + report_warnings + report_failures


def _stable_trend(snapshots: list[BookQualitySnapshot]) -> bool:
    """Return whether recent quality metrics appear stable."""

    if len(snapshots) < 4:
        return True

    ordered = sorted(snapshots, key=lambda snapshot: ensure_aware(snapshot.completed_at) or snapshot.completed_at)
    midpoint = len(ordered) // 2
    older = ordered[:midpoint]
    newer = ordered[midpoint:]

    gate1_delta = _safe_mean([snapshot.gate1_pass_rate for snapshot in newer]) - _safe_mean(
        [snapshot.gate1_pass_rate for snapshot in older]
    )
    gate2_delta = _safe_mean([snapshot.gate2_avg_grade for snapshot in newer]) - _safe_mean(
        [snapshot.gate2_avg_grade for snapshot in older]
    )
    regeneration_delta = _safe_mean([float(snapshot.chunks_regenerated) for snapshot in newer]) - _safe_mean(
        [float(snapshot.chunks_regenerated) for snapshot in older]
    )

    return gate1_delta >= -2.0 and gate2_delta >= -0.35 and regeneration_delta <= 1.5


def _generate_trend_alerts(snapshots: list[BookQualitySnapshot]) -> list[str]:
    """Return human-readable alerts for recent quality degradation."""

    if not snapshots:
        return []

    alerts: list[str] = []
    recent = snapshots[: min(len(snapshots), 5)]
    recent_gate1 = _safe_mean([snapshot.gate1_pass_rate for snapshot in recent])
    recent_gate2 = _safe_mean([snapshot.gate2_avg_grade for snapshot in recent])
    recent_regens = _safe_mean([float(snapshot.chunks_regenerated) for snapshot in recent])

    if recent_gate1 < 95.0:
        alerts.append(f"Gate 1 first-pass rate fell to {recent_gate1:.1f}% across recent books.")
    if recent_gate2 < 3.0:
        alerts.append(f"Gate 2 average grade dropped to {recent_gate2:.2f}, below the B target.")
    if recent_regens > 2.0:
        alerts.append(f"Chunks regenerated rose to {recent_regens:.1f} per book across recent runs.")
    if any(snapshot.gate3_overall_grade in {"C", "F"} for snapshot in recent):
        alerts.append("Recent books include Gate 3 grades of C/F and should be reviewed before export.")

    return alerts


class QualityTracker:
    """Store per-book quality snapshots and summarize recent quality trends."""

    @classmethod
    def record_book_quality(cls, book_id: int, db_session: Session) -> BookQualitySnapshot:
        """Compute and persist one fresh quality snapshot for a completed book."""

        book = db_session.query(Book).filter(Book.id == book_id).first()
        if book is None:
            raise ValueError(f"Book {book_id} not found")

        chapters = (
            db_session.query(Chapter)
            .filter(Chapter.book_id == book_id)
            .order_by(Chapter.number.asc(), Chapter.id.asc())
            .all()
        )
        if not chapters:
            raise ValueError("Book has no chapters")

        qa_records = {
            record.chapter_n: record
            for record in db_session.query(ChapterQARecord).filter(ChapterQARecord.book_id == book_id).all()
        }
        book_report = run_book_qa(book_id, db_session)

        total_chunks = 0
        chunks_pass_first_attempt = 0
        chunks_regenerated = 0
        chunk_issue_count = 0
        wer_values: list[float] = []
        lufs_values: list[float] = []
        generation_seconds_values: list[float] = []
        audio_seconds_values: list[float] = []
        gate2_scores: list[float] = []
        gate2_issue_count = 0

        for chapter in chapters:
            metadata = _load_generation_metadata(chapter)
            gate1 = metadata.get("gate1") if isinstance(metadata.get("gate1"), dict) else {}

            chapter_chunks = int(gate1.get("chunks_total", chapter.total_chunks or 0) or 0)
            if chapter_chunks <= 0:
                chapter_chunks = 1
            total_chunks += chapter_chunks
            chunks_pass_first_attempt += int(gate1.get("chunks_pass_first_attempt", chapter_chunks) or 0)
            chunks_regenerated += int(gate1.get("chunks_regenerated", 0) or 0)
            chunk_issue_count += int(gate1.get("validation_issue_chunks", 0) or 0)

            avg_wer = _extract_wer(metadata)
            if avg_wer is not None:
                wer_values.append(avg_wer)

            if chapter.started_at is not None and chapter.completed_at is not None:
                elapsed = (ensure_aware(chapter.completed_at) or chapter.completed_at) - (
                    ensure_aware(chapter.started_at) or chapter.started_at
                )
                generation_seconds_values.append(max(elapsed.total_seconds(), 0.0))

            if chapter.duration_seconds is not None:
                audio_seconds_values.append(float(chapter.duration_seconds))

            if chapter.audio_path:
                try:
                    lufs = measure_integrated_lufs(_resolve_audio_path(chapter.audio_path))
                except Exception:
                    lufs = None
                if lufs is not None:
                    lufs_values.append(float(lufs))

            record = qa_records.get(chapter.number)
            if record is not None:
                gate2_scores.append(GRADE_TO_SCORE[_chapter_grade(record)])
                gate2_issue_count += _chapter_issue_count(record, chapter)

        completed_at = max(
            [
                ensure_aware(chapter.completed_at) or chapter.completed_at
                for chapter in chapters
                if chapter.completed_at is not None
            ]
            or [ensure_aware(book.last_export_date) or book.last_export_date or utc_now()]
        )
        gate1_pass_rate = (
            round((chunks_pass_first_attempt / total_chunks) * 100.0, 2)
            if total_chunks
            else 100.0
        )
        gate2_avg_grade = round(_safe_mean(gate2_scores), 3) if gate2_scores else 0.0
        avg_wer = round(_safe_mean(wer_values), 4) if wer_values else None
        avg_lufs = round(_safe_mean(lufs_values), 3) if lufs_values else 0.0
        generation_rtf = (
            round(sum(generation_seconds_values) / max(sum(audio_seconds_values), 1e-6), 3)
            if generation_seconds_values and audio_seconds_values
            else 0.0
        )
        warning_checks = sum(
            1
            for check in book_report.cross_chapter_checks.values()
            if check.get("status") in {"warning", "fail"}
        )
        issues_found = chunk_issue_count + gate2_issue_count + len(book_report.export_blockers) + warning_checks

        snapshot = BookQualitySnapshot(
            book_id=book.id,
            title=book.title,
            completed_at=completed_at,
            total_chapters=len(chapters),
            gate1_pass_rate=gate1_pass_rate,
            gate2_avg_grade=gate2_avg_grade,
            gate3_overall_grade=book_report.overall_grade,
            chunks_regenerated=chunks_regenerated,
            avg_wer=avg_wer,
            avg_lufs=avg_lufs,
            generation_rtf=generation_rtf,
            issues_found=issues_found,
        )

        db_session.add(
            QualitySnapshot(
                book_id=snapshot.book_id,
                completed_at=snapshot.completed_at,
                gate1_pass_rate=snapshot.gate1_pass_rate,
                gate2_avg_grade=snapshot.gate2_avg_grade,
                gate3_overall_grade=snapshot.gate3_overall_grade,
                chunks_regenerated=snapshot.chunks_regenerated,
                avg_wer=snapshot.avg_wer,
                avg_lufs=snapshot.avg_lufs,
                generation_rtf=snapshot.generation_rtf,
                issues_found=snapshot.issues_found,
            )
        )
        db_session.flush()
        return snapshot

    @classmethod
    def ensure_book_quality_snapshot(cls, book_id: int, db_session: Session) -> BookQualitySnapshot:
        """Return the latest snapshot for a book, recomputing it when the book changed."""

        latest_snapshot = (
            db_session.query(QualitySnapshot)
            .filter(QualitySnapshot.book_id == book_id)
            .order_by(QualitySnapshot.completed_at.desc(), QualitySnapshot.id.desc())
            .first()
        )
        latest_book_update = (
            db_session.query(Chapter.updated_at)
            .filter(Chapter.book_id == book_id)
            .order_by(Chapter.updated_at.desc(), Chapter.id.desc())
            .first()
        )
        latest_update = ensure_aware(latest_book_update[0]) if latest_book_update is not None else None

        if (
            latest_snapshot is not None
            and latest_update is not None
            and (ensure_aware(latest_snapshot.completed_at) or latest_snapshot.completed_at) >= latest_update
        ):
            book = db_session.query(Book).filter(Book.id == latest_snapshot.book_id).first()
            return BookQualitySnapshot(
                book_id=latest_snapshot.book_id,
                title=book.title if book is not None else "Unknown",
                completed_at=ensure_aware(latest_snapshot.completed_at) or latest_snapshot.completed_at,
                total_chapters=db_session.query(Chapter.id).filter(Chapter.book_id == latest_snapshot.book_id).count(),
                gate1_pass_rate=float(latest_snapshot.gate1_pass_rate),
                gate2_avg_grade=float(latest_snapshot.gate2_avg_grade),
                gate3_overall_grade=latest_snapshot.gate3_overall_grade,
                chunks_regenerated=int(latest_snapshot.chunks_regenerated),
                avg_wer=float(latest_snapshot.avg_wer) if latest_snapshot.avg_wer is not None else None,
                avg_lufs=float(latest_snapshot.avg_lufs),
                generation_rtf=float(latest_snapshot.generation_rtf),
                issues_found=int(latest_snapshot.issues_found),
            )

        return cls.record_book_quality(book_id, db_session)

    @classmethod
    def get_recent_snapshots(cls, db_session: Session, last_n_books: int = 20) -> list[BookQualitySnapshot]:
        """Return the newest persisted quality snapshots with book titles attached."""

        rows = (
            db_session.query(QualitySnapshot, Book.title)
            .join(Book, Book.id == QualitySnapshot.book_id)
            .order_by(QualitySnapshot.completed_at.desc(), QualitySnapshot.id.desc())
            .limit(last_n_books)
            .all()
        )
        snapshots: list[BookQualitySnapshot] = []
        for quality_snapshot, title in rows:
            snapshots.append(
                BookQualitySnapshot(
                    book_id=quality_snapshot.book_id,
                    title=title,
                    completed_at=ensure_aware(quality_snapshot.completed_at) or quality_snapshot.completed_at,
                    total_chapters=(
                        db_session.query(Chapter.id).filter(Chapter.book_id == quality_snapshot.book_id).count()
                    ),
                    gate1_pass_rate=float(quality_snapshot.gate1_pass_rate),
                    gate2_avg_grade=float(quality_snapshot.gate2_avg_grade),
                    gate3_overall_grade=quality_snapshot.gate3_overall_grade,
                    chunks_regenerated=int(quality_snapshot.chunks_regenerated),
                    avg_wer=(
                        float(quality_snapshot.avg_wer)
                        if quality_snapshot.avg_wer is not None
                        else None
                    ),
                    avg_lufs=float(quality_snapshot.avg_lufs),
                    generation_rtf=float(quality_snapshot.generation_rtf),
                    issues_found=int(quality_snapshot.issues_found),
                )
            )
        return snapshots

    @classmethod
    def get_quality_trend(cls, last_n_books: int = 20, db_session: Session | None = None) -> dict[str, Any]:
        """Return aggregated quality metrics and trend indicators."""

        if db_session is None:
            raise ValueError("db_session is required")

        snapshots = cls.get_recent_snapshots(db_session, last_n_books=last_n_books)
        trend_is_stable = _stable_trend(snapshots)
        alerts = _generate_trend_alerts(snapshots)

        return {
            "books_analyzed": len(snapshots),
            "avg_gate1_pass_rate": round(_safe_mean([snapshot.gate1_pass_rate for snapshot in snapshots]), 2),
            "avg_gate2_grade": round(_safe_mean([snapshot.gate2_avg_grade for snapshot in snapshots]), 3),
            "gate3_grade_distribution": {
                "A": sum(1 for snapshot in snapshots if snapshot.gate3_overall_grade == "A"),
                "B": sum(1 for snapshot in snapshots if snapshot.gate3_overall_grade == "B"),
                "C": sum(1 for snapshot in snapshots if snapshot.gate3_overall_grade == "C"),
                "F": sum(1 for snapshot in snapshots if snapshot.gate3_overall_grade == "F"),
            },
            "avg_chunks_regenerated": round(_safe_mean([float(snapshot.chunks_regenerated) for snapshot in snapshots]), 3),
            "avg_generation_rtf": round(_safe_mean([snapshot.generation_rtf for snapshot in snapshots]), 3),
            "trend": "stable" if trend_is_stable else "degrading",
            "alerts": alerts,
            "recent_books": [asdict(snapshot) for snapshot in snapshots],
            "trend_points": [
                {
                    "book_id": snapshot.book_id,
                    "title": snapshot.title,
                    "completed_at": snapshot.completed_at,
                    "gate1_pass_rate": snapshot.gate1_pass_rate,
                    "gate2_avg_grade": snapshot.gate2_avg_grade,
                    "chunks_regenerated": snapshot.chunks_regenerated,
                }
                for snapshot in reversed(snapshots)
            ],
        }
