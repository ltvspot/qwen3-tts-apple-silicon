"""Track words known to cause pronunciation issues with Qwen3-TTS."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

WATCHLIST_PATH = Path("data/pronunciation_watchlist.json")
INSTRUCTION_PREFIX = "[[alexandria-instruct:"
INSTRUCTION_SUFFIX = "]]"

DEFAULT_WATCHLIST = {
    "hyperbole": "hy-PER-bo-lee",
    "epitome": "eh-PIT-oh-mee",
    "quinoa": "KEEN-wah",
    "albeit": "all-BEE-it",
    "segue": "SEG-way",
    "cache": "CASH",
    "Hermione": "her-MY-oh-nee",
    "Versailles": "ver-SIGH",
    "niche": "NEESH",
    "genre": "ZHAHN-ruh",
    "queue": "KYOO",
    "debris": "duh-BREE",
    "colonel": "KUR-nul",
    "worcestershire": "WUUS-tuhr-sheer",
    "acai": "ah-sigh-EE",
    "croissant": "kwah-SONT",
    "facade": "fuh-SAHD",
    "faux": "FOH",
    "rendezvous": "RAHN-day-voo",
    "coup": "KOO",
    "ballet": "bal-LAY",
    "depot": "DEE-poh",
    "lingerie": "lahn-zhuh-RAY",
    "naive": "ny-EVE",
    "naïve": "ny-EVE",
    "cliche": "klee-SHAY",
    "cliché": "klee-SHAY",
    "reservoir": "REZ-er-vwar",
    "rhetoric": "RET-er-ik",
    "plethora": "PLETH-er-uh",
    "hors d'oeuvres": "or-DERVZ",
    "bourgeois": "boor-ZHWAH",
    "entrepreneur": "ahn-truh-pruh-NUR",
    "lieutenant": "lef-TEN-unt",
    "archipelago": "ar-kuh-PEL-uh-go",
    "chameleon": "kuh-MEEL-yun",
    "paradigm": "PAIR-uh-dime",
    "phenomenon": "fih-NOM-uh-non",
    "posthumous": "POS-chuh-mus",
    "subtle": "SUT-ul",
    "debt": "DET",
    "receipt": "ri-SEET",
    "february": "FEB-yoo-air-ee",
    "wednesday": "WENZ-day",
    "library": "LIE-brair-ee",
    "mischievous": "MIS-chuh-vus",
    "nuclear": "NOO-klee-er",
    "espresso": "ess-PRESS-oh",
    "jewelry": "JOO-ul-ree",
    "arctic": "ARK-tik",
    "often": "OFF-en",
    "realtor": "REE-ul-ter",
    "miniature": "MIN-ee-uh-cher",
    "temperature": "TEM-pruh-cher",
    "comfortable": "KUMF-ter-bul",
    "debut": "day-BYOO",
    "gif": "JIF",
    "karaoke": "kah-rah-OH-kay",
    "macabre": "muh-KAHB",
    "pho": "FUH",
    "route": "ROOT",
    "solder": "SOD-er",
    "suite": "SWEET",
    "synecdoche": "sih-NEK-duh-kee",
    "timestamp": "TIME-stamp",
    "ubiquitous": "yoo-BIK-wi-tus",
    "wary": "WAIR-ee",
    "xylophone": "ZY-luh-fohn",
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

    def check_text(
        self,
        text: str,
        *,
        custom_entries: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        """Return warnings for any watchlist words found in the input text."""

        warnings: list[dict[str, str]] = []
        merged_entries = self.merge_entries(custom_entries)
        for word, guide in merged_entries.items():
            pattern = re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)
            if not pattern.search(text):
                continue

            warnings.append(
                {
                    "word": word,
                    "pronunciation_guide": guide,
                    "context": "This word is known to cause pronunciation issues with Qwen3-TTS.",
                }
            )
        return warnings

    def merge_entries(self, custom_entries: list[dict[str, str]] | None = None) -> dict[str, str]:
        """Return the merged global + per-book watchlist."""

        merged = self._watchlist.copy()
        for entry in custom_entries or []:
            word = str(entry.get("word", "")).strip()
            phonetic = str(entry.get("phonetic", entry.get("pronunciation_guide", ""))).strip()
            if word and phonetic:
                merged[word] = phonetic
        return merged

    def custom_entries_from_payload(self, payload: str | None) -> list[dict[str, str]]:
        """Parse a persisted per-book JSON payload into watchlist entries."""

        if not payload:
            return []
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError:
            return []

        if isinstance(raw, dict):
            words = raw.get("words")
            raw = words if isinstance(words, list) else []
        if not isinstance(raw, list):
            return []

        normalized: list[dict[str, str]] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            word = str(entry.get("word", "")).strip()
            phonetic = str(entry.get("phonetic", entry.get("pronunciation_guide", ""))).strip()
            if not word or not phonetic:
                continue
            normalized.append({"word": word, "phonetic": phonetic})
        return normalized

    def serialize_custom_entries(self, entries: list[dict[str, Any]]) -> str:
        """Serialize per-book watchlist entries in a stable JSON payload."""

        normalized = [
            {
                "word": str(entry.get("word", "")).strip(),
                "phonetic": str(entry.get("phonetic", entry.get("pronunciation_guide", ""))).strip(),
            }
            for entry in entries
            if str(entry.get("word", "")).strip() and str(entry.get("phonetic", entry.get("pronunciation_guide", ""))).strip()
        ]
        normalized.sort(key=lambda item: item["word"].lower())
        return json.dumps({"words": normalized}, ensure_ascii=True)

    def inject_phonetic_hints(
        self,
        text: str,
        *,
        custom_entries: list[dict[str, str]] | None = None,
    ) -> str:
        """Embed non-spoken pronunciation instructions for the engine adapter."""

        matches = self.check_text(text, custom_entries=custom_entries)
        if not matches:
            return text

        instructions = " ".join(
            f"Pronounce '{match['word']}' as '{match['pronunciation_guide']}'."
            for match in matches
        )
        return f"{INSTRUCTION_PREFIX}{instructions}{INSTRUCTION_SUFFIX}\n{text}"

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
                "context": "This word is known to cause pronunciation issues with Qwen3-TTS.",
            }
            for word, guide in sorted(self._watchlist.items(), key=lambda item: item[0].lower())
        ]
