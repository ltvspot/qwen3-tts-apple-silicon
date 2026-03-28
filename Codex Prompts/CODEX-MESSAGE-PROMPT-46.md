Read CLAUDE.md and PROJECT-STATE.md for project conventions.

Then read and implement all tasks in Codex Prompts/PROMPT-46-TOC-PARSING-FIX.md

This prompt modifies 2 files:

Modified files:
- src/parser/docx_parser.py — 3 changes: fix _looks_like_toc_entry() to handle long TOC entries and toc-styled paragraphs, harden TOC exit condition in _extract_chapters(), add KNOWN_TITLES dict and title-based author fallback in parse_with_folder_hint()
- tests/test_docx_parser.py — Add 4+ new tests for TOC fix and title-based author lookup

Key goal: Fix TOC parsing so books with long chapter titles in their Table of Contents parse correctly instead of producing zero chapters. Also resolve Unknown Author for ~20 books via title-to-author mapping.

Run the existing test suite after implementation:
```
cd /path/to/project && ./.venv/bin/pytest -q
```
