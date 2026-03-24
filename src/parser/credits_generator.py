"""Audiobook opening and closing credit generation."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class CreditsGenerator:
    """Generate opening and closing credits for audiobooks."""

    NARRATOR = "Kent Zimering"

    @staticmethod
    def generate_opening_credits(
        title: str,
        subtitle: str | None,
        author: str,
        narrator: str = NARRATOR,
    ) -> str:
        """Return the opening credits text for a book."""

        parts = [f"This is {title}."]
        if subtitle:
            parts.append(f"{subtitle}.")
        parts.append(f"Written by {author}.")
        parts.append(f"Narrated by {narrator}.")
        return " ".join(parts)

    @staticmethod
    def generate_closing_credits(
        title: str,
        subtitle: str | None,
        author: str,
        narrator: str = NARRATOR,
    ) -> str:
        """Return the closing credits text for a book."""

        parts = [f"This was {title}."]
        if subtitle:
            parts.append(f"{subtitle}.")
        parts.append(f"Written by {author}.")
        parts.append(f"Narrated by {narrator}.")
        return " ".join(parts)
