"""Manuscript parsing package."""

from .credits_generator import CreditsGenerator
from .docx_parser import BookMetadata, Chapter, DocxParser
from .epub_parser import EPUBParser
from .factory import ManuscriptParserFactory
from .pdf_parser import PDFParser
from .text_cleaner import TextCleaner

DOCXParser = DocxParser

__all__ = [
    "BookMetadata",
    "Chapter",
    "CreditsGenerator",
    "DOCXParser",
    "DocxParser",
    "EPUBParser",
    "ManuscriptParserFactory",
    "PDFParser",
    "TextCleaner",
]
