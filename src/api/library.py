"""Library scanning utilities for indexed manuscript discovery."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from src.config import get_application_settings, settings
from src.database import Book, BookStatus

logger = logging.getLogger(__name__)


class LibraryScanner:
    """Scan the manuscript library and persist discovered book folders."""

    UNKNOWN_AUTHOR = "Unknown Author"

    def __init__(self, manuscripts_path: str | Path | None = None) -> None:
        """Initialize the scanner with the configured manuscript root."""

        root_path = manuscripts_path if manuscripts_path is not None else settings.FORMATTED_MANUSCRIPTS_PATH
        self.manuscripts_path = Path(root_path)

    def scan(self, db_session: Session) -> dict[str, Any]:
        """
        Scan the manuscript library and add any new folders to the database.

        Args:
            db_session: Active SQLAlchemy session.

        Returns:
            Scan summary containing counts and any recoverable scan errors.
        """

        result: dict[str, Any] = {
            "total_found": 0,
            "total_indexed": 0,
            "new_books": 0,
            "errors": [],
        }

        if not self.manuscripts_path.exists():
            message = f"Manuscripts path does not exist: {self.manuscripts_path}"
            logger.warning(message)
            result["errors"].append(message)
            return result

        folders = sorted(path for path in self.manuscripts_path.iterdir() if path.is_dir())
        result["total_found"] = len(folders)

        for folder in folders:
            metadata = self._parse_folder_name(folder.name)
            if metadata is None:
                message = f"Unable to parse folder metadata: {folder.name}"
                logger.warning(message)
                result["errors"].append(message)
                continue

            existing = db_session.query(Book).filter(Book.folder_path == folder.name).first()
            if existing is not None:
                result["total_indexed"] += 1
                continue

            title, author = self._initial_book_fields(metadata["title"])
            book = Book(
                title=title,
                subtitle=None,
                author=author,
                narrator=get_application_settings().narrator_name,
                folder_path=folder.name,
                status=BookStatus.NOT_STARTED,
                page_count=metadata.get("page_count"),
                trim_size=metadata.get("trim_size"),
            )
            db_session.add(book)
            result["total_indexed"] += 1
            result["new_books"] += 1

            if self._find_docx_file(folder) is None:
                logger.info("Indexed folder without DOCX support yet: %s", folder.name)

        return result

    def _parse_folder_name(self, folder_name: str) -> dict[str, Any] | None:
        """
        Parse a manuscript folder name into book metadata.

        This accepts the prompt's ideal format plus the real-world variants already
        present in the repository, including stray punctuation, missing page counts,
        and EPUB-only folders.
        """

        normalized = self._normalize_folder_name(folder_name)
        if normalized.lower().startswith("drive-download-"):
            return None

        match = re.match(r"^(?P<library_id>\d+)[\s\-]+(?P<remainder>.+)$", normalized)
        if match is None:
            return None

        remainder = match.group("remainder")
        page_match = re.match(r"^(?P<body>.+)-(?P<page_count>\d+)(?:\s+\d+)?$", remainder)
        page_count = int(page_match.group("page_count")) if page_match else None
        body = page_match.group("body") if page_match else remainder

        title_part, trim_size = self._extract_title_and_trim(body)
        title = self._humanize_slug(title_part)
        if not title:
            return None

        return {
            "id": match.group("library_id"),
            "title": title,
            "trim_size": trim_size,
            "page_count": page_count,
        }

    def _find_docx_file(self, folder_path: Path) -> Path | None:
        """Return the preferred DOCX manuscript inside a folder, if any."""

        docx_files = [path for path in folder_path.glob("*.docx") if not path.name.startswith("~$")]
        if not docx_files:
            return None

        def sort_key(path: Path) -> tuple[int, int, str]:
            lower_name = path.name.lower()
            if "clean" in lower_name:
                priority = 0
            elif "nopgbrfont" in lower_name:
                priority = 1
            elif "nobreaks" in lower_name:
                priority = 2
            else:
                priority = 3
            return (priority, len(lower_name), lower_name)

        return sorted(docx_files, key=sort_key)[0]

    def _normalize_folder_name(self, folder_name: str) -> str:
        """Normalize punctuation quirks before parsing folder metadata."""

        normalized = folder_name.strip()
        normalized = normalized.replace("–", "-").replace("—", "-")
        normalized = normalized.replace("’", "'").replace("‘", "'")
        normalized = re.sub(r"^(\d+)[.\-]+", r"\1-", normalized)
        normalized = re.sub(r"(?<=\dx\d)\+(?=\d+(?:\s+\d+)?$)", "-", normalized)
        normalized = re.sub(r"-{2,}(?=\d+(?:\s+\d+)?$)", "-", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized

    def _extract_title_and_trim(self, body: str) -> tuple[str, str | None]:
        """Split a normalized body into a human title fragment and trim size."""

        format_match = re.match(r"^(?P<title>.+?)-(?:epub|ebook|pdf)$", body, re.IGNORECASE)
        if format_match:
            return format_match.group("title"), None

        for pattern in (
            re.compile(r"^(?P<title>.+?)-(?P<trim>[0-9.]+x[0-9.]+)$", re.IGNORECASE),
            re.compile(r"^(?P<title>.+?)(?P<trim>[0-9.]+x[0-9.]+)$", re.IGNORECASE),
        ):
            match = pattern.match(body)
            if match is not None:
                return match.group("title"), match.group("trim")

        partial_trim_match = re.match(r"^(?P<title>.+?)-[0-9.]+x$", body, re.IGNORECASE)
        if partial_trim_match:
            return partial_trim_match.group("title"), None

        return body, None

    def _humanize_slug(self, value: str) -> str:
        """Convert a manuscript slug-like fragment into display text."""

        humanized = value.replace("_", " ").replace("-", " ")
        humanized = re.sub(r"\s+", " ", humanized)
        return humanized.strip(" .-_")

    def _initial_book_fields(self, raw_title: str) -> tuple[str, str]:
        """Derive best-effort title and author values before full parsing."""

        by_split = re.split(r"\s+by\s+", raw_title, flags=re.IGNORECASE, maxsplit=1)
        if len(by_split) == 2:
            title = by_split[0].strip() or raw_title
            author = by_split[1].strip() or self.UNKNOWN_AUTHOR
            return title, author
        return raw_title, self.UNKNOWN_AUTHOR
