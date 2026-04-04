#!/usr/bin/env python3
"""Migration: fix mid-sentence paragraph breaks in existing chapter text."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.parser.text_cleaner import merge_broken_paragraphs


def fix_text(text: str) -> str:
    """Apply paragraph merge to a text_content string."""

    paragraphs = [paragraph.strip() for paragraph in text.split("\n\n") if paragraph.strip()]
    merged = merge_broken_paragraphs(paragraphs)
    return "\n\n".join(merged)


def main() -> None:
    """Run the paragraph-break migration against the chapter table."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    parser.add_argument("--book-ids", nargs="*", type=int, help="Limit to specific book IDs")
    args = parser.parse_args()

    conn = sqlite3.connect(Path("alexandria.db"))
    cur = conn.cursor()

    if args.book_ids:
        placeholders = ",".join("?" * len(args.book_ids))
        cur.execute(
            f"SELECT id, book_id, number, title, text_content FROM chapters "
            f"WHERE book_id IN ({placeholders}) AND text_content IS NOT NULL",
            args.book_ids,
        )
    else:
        cur.execute(
            "SELECT id, book_id, number, title, text_content FROM chapters "
            "WHERE text_content IS NOT NULL"
        )

    rows = cur.fetchall()
    changed = 0
    total = len(rows)

    for ch_id, book_id, number, title, text in rows:
        fixed = fix_text(text)
        if fixed != text:
            changed += 1
            if args.dry_run:
                old_paragraphs = text.split("\n\n")
                new_paragraphs = fixed.split("\n\n")
                if len(old_paragraphs) != len(new_paragraphs):
                    print(
                        f"Book {book_id} Ch#{number} ({title!r}): "
                        f"{len(old_paragraphs)} -> {len(new_paragraphs)} paragraphs"
                    )
                continue

            conn.execute(
                "UPDATE chapters SET text_content = ? WHERE id = ?",
                (fixed, ch_id),
            )

    if args.dry_run:
        print(f"Dry run: would update {changed}/{total} chapters.")
    else:
        conn.commit()
        print(f"Updated {changed}/{total} chapters.")

    conn.close()


if __name__ == "__main__":
    main()
