"""Manuscript parser selection helpers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from src.parser.docx_parser import BookMetadata, Chapter, DocxParser
from src.parser.epub_parser import EPUBParser
from src.parser.pdf_parser import PDFParser

logger = logging.getLogger(__name__)


class ManuscriptParser(Protocol):
    """Protocol shared by all manuscript parser adapters."""

    def parse(self, path: str | Path) -> tuple[BookMetadata, list[Chapter]]:
        """Parse a manuscript file into metadata and narratable chapters."""


class ManuscriptParserFactory:
    """Select the best available manuscript parser for a folder."""

    @staticmethod
    def get_parser(manuscript_folder: str | Path) -> tuple[ManuscriptParser | None, Path | None]:
        """
        Detect the best supported manuscript format in a folder.

        Priority order is DOCX > EPUB > PDF.
        """

        folder = Path(manuscript_folder)
        if not folder.exists():
            logger.error("Manuscript folder not found: %s", folder)
            return None, None

        docx_path = ManuscriptParserFactory._find_docx_file(folder)
        if docx_path is not None:
            logger.info("Using DOCX parser for %s", folder.name)
            return DocxParser(), docx_path

        epub_path = ManuscriptParserFactory._find_epub_file(folder)
        if epub_path is not None:
            logger.info("Using EPUB parser for %s", folder.name)
            return EPUBParser(), epub_path

        pdf_path = ManuscriptParserFactory._find_pdf_file(folder)
        if pdf_path is not None:
            logger.info("Using PDF parser for %s", folder.name)
            return PDFParser(), pdf_path

        logger.warning("No supported manuscript format found in %s", folder)
        return None, None

    @staticmethod
    def parse_manuscript(manuscript_folder: str | Path) -> tuple[BookMetadata, list[Chapter], Path]:
        """Auto-detect the best format in a folder and parse it."""

        parser, manuscript_path = ManuscriptParserFactory.get_parser(manuscript_folder)
        if parser is None or manuscript_path is None:
            folder_name = Path(manuscript_folder).name or str(manuscript_folder)
            raise ValueError(f"No supported manuscript format in {folder_name}")
        metadata, chapters = parser.parse(manuscript_path)
        return metadata, chapters, manuscript_path

    @staticmethod
    def _find_docx_file(folder_path: Path) -> Path | None:
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

    @staticmethod
    def _find_epub_file(folder_path: Path) -> Path | None:
        """Return the preferred EPUB manuscript inside a folder, if any."""

        epub_files = sorted(folder_path.glob("*.epub"), key=lambda path: (len(path.name), path.name.lower()))
        return epub_files[0] if epub_files else None

    @staticmethod
    def _find_pdf_file(folder_path: Path) -> Path | None:
        """Return the preferred PDF manuscript inside a folder, if any."""

        pdf_files = sorted(folder_path.glob("*.pdf"), key=lambda path: (len(path.name), path.name.lower()))
        return pdf_files[0] if pdf_files else None
