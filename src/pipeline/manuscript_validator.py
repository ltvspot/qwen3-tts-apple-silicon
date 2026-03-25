"""Pre-generation validation of manuscript text quality."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(slots=True)
class ManuscriptIssue:
    """One manuscript quality issue surfaced before generation."""

    severity: str
    chapter: int | None
    description: str
    suggestion: str


@dataclass(slots=True)
class ManuscriptValidationReport:
    """Aggregate manuscript quality report for one book."""

    book_id: int
    title: str
    total_chapters: int
    total_words: int
    issues: list[ManuscriptIssue] = field(default_factory=list)
    difficulty_score: float = 0.0
    ready_for_generation: bool = True


class ManuscriptValidator:
    """Run text-only checks that catch bad source material before TTS starts."""

    MOJIBAKE_PATTERNS = [r"â€™", r"â€œ", r"â€", r"Ã©", r"Ã¡", r"Â", r"\ufffd"]
    COMMON_CAPS = {
        "The", "And", "But", "For", "Not", "You", "All", "Can", "Her",
        "Was", "One", "Our", "Out", "His", "Has", "How", "Man", "New",
        "Now", "Old", "See", "Way", "Who", "Boy", "Did", "Get", "Let",
    }

    @classmethod
    def validate(cls, book_id: int, chapters: list[dict]) -> ManuscriptValidationReport:
        """Run the full pre-generation manuscript validation pass."""

        report = ManuscriptValidationReport(
            book_id=book_id,
            title=chapters[0].get("book_title", "Unknown") if chapters else "Unknown",
            total_chapters=len(chapters),
            total_words=sum(len((chapter.get("text") or "").split()) for chapter in chapters),
        )
        difficulty_factors: list[float] = []

        for chapter in chapters:
            text = chapter.get("text", "") or ""
            chapter_number = chapter.get("number", 0)
            stripped = text.strip()
            word_count = len(stripped.split())

            if len(stripped) < 50:
                report.issues.append(
                    ManuscriptIssue(
                        severity="warning",
                        chapter=chapter_number,
                        description=f"Chapter {chapter_number} is very short ({len(stripped)} chars)",
                        suggestion="Verify this chapter parsed correctly",
                    )
                )

            if word_count > 20_000:
                report.issues.append(
                    ManuscriptIssue(
                        severity="warning",
                        chapter=chapter_number,
                        description=f"Chapter {chapter_number} is very long ({word_count} words)",
                        suggestion="Generation may use significant memory. Model reloads likely.",
                    )
                )
                difficulty_factors.append(2.0)

            for pattern in cls.MOJIBAKE_PATTERNS:
                if re.search(pattern, text):
                    report.issues.append(
                        ManuscriptIssue(
                            severity="error",
                            chapter=chapter_number,
                            description=f"Chapter {chapter_number} contains encoding artifacts ({pattern})",
                            suggestion="Re-parse the manuscript with correct encoding (UTF-8)",
                        )
                    )
                    report.ready_for_generation = False

            capitalized_words = re.findall(r"\b[A-Z][a-z]{3,}\b", text)
            proper_nouns = [word for word in capitalized_words if word not in cls.COMMON_CAPS]
            noun_density = len(proper_nouns) / max(word_count, 1)
            if noun_density > 0.05:
                report.issues.append(
                    ManuscriptIssue(
                        severity="info",
                        chapter=chapter_number,
                        description=f"Chapter {chapter_number} has high proper noun density ({noun_density * 100:.1f}%)",
                        suggestion="Monitor pronunciation quality closely for this chapter",
                    )
                )
                difficulty_factors.append(1.5)

            dialogue_chars = sum(
                len(match.group(0))
                for match in re.finditer(r'["\u201c\u201d][^"\u201c\u201d]{5,}["\u201c\u201d]', text)
            )
            dialogue_ratio = dialogue_chars / max(len(text), 1)
            if dialogue_ratio > 0.6:
                report.issues.append(
                    ManuscriptIssue(
                        severity="info",
                        chapter=chapter_number,
                        description=f"Chapter {chapter_number} is dialogue-heavy ({dialogue_ratio * 100:.0f}%)",
                        suggestion="Single-voice narration may sound less natural for heavy dialogue",
                    )
                )
                difficulty_factors.append(1.0)

            non_ascii = re.findall(r"[àáâãäåèéêëìíîïòóôõöùúûüýÿñçæœ]+", text.lower())
            if len(non_ascii) > 5:
                report.issues.append(
                    ManuscriptIssue(
                        severity="warning",
                        chapter=chapter_number,
                        description=f"Chapter {chapter_number} contains non-English characters ({len(non_ascii)} instances)",
                        suggestion="These may trigger pronunciation artifacts. Review generated audio carefully.",
                    )
                )
                difficulty_factors.append(2.0)

            paragraphs = [paragraph for paragraph in text.split("\n\n") if paragraph.strip()]
            long_paragraphs = [paragraph for paragraph in paragraphs if len(paragraph) > 2000]
            if long_paragraphs:
                report.issues.append(
                    ManuscriptIssue(
                        severity="info",
                        chapter=chapter_number,
                        description=f"Chapter {chapter_number} has {len(long_paragraphs)} very long paragraphs (>2000 chars)",
                        suggestion="Chunking will split these. Check for natural break points.",
                    )
                )

            lines = [line for line in text.splitlines() if line.strip()]
            short_lines = [line for line in lines if 0 < len(line.strip()) < 60]
            if len(short_lines) > 10 and len(short_lines) / max(len(lines), 1) > 0.5:
                report.issues.append(
                    ManuscriptIssue(
                        severity="warning",
                        chapter=chapter_number,
                        description=f"Chapter {chapter_number} appears to contain poetry/verse",
                        suggestion="TTS may not handle line breaks and meter correctly. Review pacing.",
                    )
                )
                difficulty_factors.append(2.5)

        if difficulty_factors:
            report.difficulty_score = min(10.0, round((sum(difficulty_factors) / max(len(chapters), 1)) * 2, 2))
        else:
            report.difficulty_score = 1.0

        if any(issue.severity == "error" for issue in report.issues):
            report.ready_for_generation = False

        return report
