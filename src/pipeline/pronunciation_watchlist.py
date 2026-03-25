"""Track words known to cause pronunciation issues with Qwen3-TTS."""

from __future__ import annotations

import json
import re
from pathlib import Path

WATCHLIST_PATH = Path("data/pronunciation_watchlist.json")

DEFAULT_WATCHLIST = {
    "hyperbole": "hy-PER-bo-lee",
    "epitome": "eh-PIT-oh-mee",
    "quinoa": "KEEN-wah",
    "albeit": "all-BEE-it",
    "segue": "SEG-way",
    "cache": "CASH",
    "Hermione": "her-MY-oh-nee",
    "Versailles": "ver-SIGH",
}


class PronunciationWatchlist:
    """Persist and query words known to cause pronunciation artifacts."""

    def __init__(self) -> None:
        self._watchlist = self._load()

    def _load(self) -> dict[str, str]:
        if WATCHLIST_PATH.exists():
            return json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
        return DEFAULT_WATCHLIST.copy()

    def save(self) -> None:
        WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        WATCHLIST_PATH.write_text(json.dumps(self._watchlist, indent=2), encoding="utf-8")

    def check_text(self, text: str) -> list[dict[str, str]]:
        """Return warnings for any watchlist words found in the input text."""

        warnings: list[dict[str, str]] = []
        for word, guide in self._watchlist.items():
            pattern = re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)
            if not pattern.search(text):
                continue

            warnings.append(
                {
                    "word": word,
                    "pronunciation_guide": guide,
                    "context": "This word is known to cause pronunciation issues with Qwen3-TTS",
                }
            )
        return warnings

    def add_word(self, word: str, guide: str) -> None:
        self._watchlist[word] = guide
        self.save()

    def remove_word(self, word: str) -> None:
        to_delete = next((candidate for candidate in self._watchlist if candidate.lower() == word.lower()), None)
        if to_delete is not None:
            self._watchlist.pop(to_delete, None)
            self.save()

    def entries(self) -> list[dict[str, str]]:
        """Return the full watchlist in a stable serialized shape."""

        return [
            {
                "word": word,
                "pronunciation_guide": guide,
                "context": "This word is known to cause pronunciation issues with Qwen3-TTS",
            }
            for word, guide in sorted(self._watchlist.items(), key=lambda item: item[0].lower())
        ]
