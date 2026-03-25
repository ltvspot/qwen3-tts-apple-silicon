"""Audiobook opening and closing credit generation."""

from __future__ import annotations

import logging

from src.config import get_application_settings

logger = logging.getLogger(__name__)


class CreditsGenerator:
    """Generate opening and closing credits for audiobooks."""

    @staticmethod
    def generate_opening_credits(
        title: str,
        subtitle: str | None,
        author: str,
        narrator: str | None = None,
    ) -> str:
        """Return the opening credits text for a book."""

        resolved_narrator = narrator or get_application_settings().narrator_name
        parts = [f"This is {title}."]
        if subtitle:
            parts.append(f"{subtitle}.")
        parts.append(f"Written by {author}.")
        parts.append(f"Narrated by {resolved_narrator}.")
        return " ".join(parts)

    @staticmethod
    def generate_closing_credits(
        title: str,
        subtitle: str | None,
        author: str,
        narrator: str | None = None,
    ) -> str:
        """Return the closing credits text for a book."""

        resolved_narrator = narrator or get_application_settings().narrator_name
        parts = [f"This was {title}."]
        if subtitle:
            parts.append(f"{subtitle}.")
        parts.append(f"Written by {author}.")
        parts.append(f"Narrated by {resolved_narrator}.")
        return " ".join(parts)
