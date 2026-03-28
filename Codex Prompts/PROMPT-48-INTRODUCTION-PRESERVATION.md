# PROMPT-48: Preserve Introduction Sections — Don't Split on Sub-Headings

## Bug

When a manuscript has an Introduction section with sub-headings (e.g., `Heading 3: "I. Brief Biography of Sun Tzu"`), the parser treats those sub-headings as chapter boundaries. This splits the Introduction into multiple chapters and loses the Introduction heading entirely.

**Example — The Art of War (Book 1):**

DOCX structure:
```
[62] (Heading 1) Introduction
[63] (Heading 3) I. Brief Biography of Sun Tzu
[74] (Heading 3) II. Historical Context
[84] (Heading 3) III. Philosophical Background
[100] (Heading 3) IV. Key Themes and Structure of The Art of War
[113] (Heading 3) V. Influence and Legacy of The Art of War
[132] (Heading 1) Chapter 1 - Laying Plans
```

**Current (wrong) parse result:**
- Ch 0: Opening Credits
- Ch 1: Brief Biography of Sun Tzu (type=chapter, 612 words)
- Ch 2: Historical Context (type=chapter, 794 words)
- Ch 3: Philosophical Background (type=chapter, 1305 words)
- Ch 4: Key Themes and Structure (type=chapter, 840 words)
- Ch 5: Influence and Legacy (type=chapter, 984 words)
- Ch 6: Laying Plans (type=chapter) ← should be Ch 1

**Expected parse result:**
- Ch 0: Opening Credits
- Introduction (type=introduction, ~4500 words — all sub-sections combined)
- Ch 1: Laying Plans (type=chapter)
- Ch 2: Waging War, etc.

## Root Cause

In `_extract_chapters()` at line ~690-710 of `src/parser/docx_parser.py`:

When `current_heading["type"] == "introduction"` and a new heading is detected that matches as type `"chapter"` (via the Roman-numeral pattern `self.chapter_patterns[1]`), the parser finalizes the Introduction (which has no body yet → gets discarded by `_build_chapter`) and starts a new chapter.

The sub-heading "I. Brief Biography of Sun Tzu" with `Heading 3` style matches `chapter_patterns[1]` because:
- Pattern: `r"^(?P<number>[ivxlcdm]+|\d+)\s*[:.\-]\s*(?P<title>.+)$"`
- "I" matches as Roman numeral `[ivxlcdm]+`
- ". Brief Biography..." matches as title after the separator

## Fix

In `_extract_chapters()`, when we are currently inside an Introduction section (`current_heading is not None and current_heading["type"] == "introduction"`), only allow an **explicit** "Chapter N" heading to end the Introduction. All other headings (sub-headings, Roman numeral sections, etc.) should be treated as body text within the Introduction.

Specifically, modify the section at line ~690 where `is_heading and parsed_heading is not None`:

```python
if is_heading and parsed_heading is not None:
    if parsed_heading["type"] == "introduction":
        current_body.append(text)
        continue

    # NEW: When inside an Introduction, only explicit "Chapter N" headings
    # should end the introduction. Sub-headings (Heading 2/3/4, Roman
    # numeral sections) should be treated as Introduction body text.
    if (current_heading is not None
        and current_heading["type"] == "introduction"
        and not self._is_explicit_chapter_heading(text)):
        # This is a sub-heading within the Introduction — treat as body text
        current_body.append(text)
        continue

    # ... rest of existing chapter boundary logic ...
```

Add a new method `_is_explicit_chapter_heading(self, text: str) -> bool` that returns True ONLY when the text starts with "Chapter" (i.e., matches `self.chapter_patterns[0]` which requires the "Chapter" keyword). This distinguishes real chapter boundaries from sub-headings that happen to have a styled heading + number pattern.

```python
def _is_explicit_chapter_heading(self, text: str) -> bool:
    """Return whether text is an explicit 'Chapter N' heading (not just a numbered sub-heading)."""
    normalized = self._normalize_text(text)
    return bool(self.chapter_patterns[0].match(normalized))
```

## Tests

Add to `tests/test_docx_parser.py`:

1. **test_introduction_with_subheadings**: Create a DOCX with:
   - Heading 1: "Introduction"
   - Heading 3: "I. Biography" (with body text)
   - Heading 3: "II. Historical Context" (with body text)
   - Heading 1: "Chapter 1 - The Beginning" (with body text)
   - Heading 1: "Chapter 2 - The Middle" (with body text)

   Verify parse returns:
   - 1 introduction chapter (type="introduction") containing text from both sub-sections
   - 2 regular chapters (Chapter 1, Chapter 2)
   - The Introduction's raw_text includes content from all sub-headings

2. **test_introduction_without_subheadings**: Create a DOCX with a simple Introduction (no sub-headings, just body text) followed by chapters. Verify the Introduction is still parsed correctly as before (regression test).

3. **test_explicit_chapter_heading_detection**: Test the new `_is_explicit_chapter_heading` method:
   - "Chapter 1 - Laying Plans" → True
   - "Chapter IV" → True
   - "I. Brief Biography" → False
   - "Historical Context" → False
   - "1: Some Title" → False

## Constraints

- Do NOT change the Chapter or BookMetadata dataclasses
- All existing 463 tests must continue to pass
- Run `pytest -q` and confirm all tests pass (target: 466+)
- The fix must ONLY affect behavior when currently inside an introduction section. Normal chapter-to-chapter boundaries must work exactly as before.
