# PROMPT-46: TOC Parsing Fix + Title-to-Author Mapping

## Context

After bulk-parsing all 872 manuscripts, approximately 10 books fail with "No narratable chapters detected" because the parser incorrectly exits TOC (Table of Contents) mode too early, then treats TOC entries as actual chapter headings. Since TOC entries have no body text between them, every "chapter" gets skipped as empty, resulting in zero parseable chapters.

Additionally, 22 books end up with "Unknown Author" because their folder names don't contain the author name, but the author can be inferred from the book title.

## Bug Analysis: TOC Parsing

**Root cause in `_extract_chapters()` (line ~302-310):**

```python
if collecting_toc:
    if self._looks_like_chapter_style(style):
        collecting_toc = False
    elif self._looks_like_toc_entry(text, style):
        self.last_toc_entries.append(text)
        continue
    else:
        collecting_toc = False
        continue
```

**What happens with long TOC entries:**
1. Parser encounters "Table of Contents" → sets `collecting_toc = True`
2. TOC entries like "Chapter I: In Which the North Polar Practical Association..." have `toc 1` style
3. `_looks_like_chapter_style("toc 1")` returns False (doesn't start with "heading")
4. `_looks_like_toc_entry(text, style)` returns False because the entry has >16 words
5. The `else` branch sets `collecting_toc = False` and continues — **exiting TOC mode prematurely**
6. All subsequent TOC entries are now treated as real chapter headings
7. Since TOC entries have no body paragraphs between them, all chapters are empty → all skipped

**Affected books (confirmed):** 117, 142, 159, 160, 162, 172 (all have long chapter titles in TOC)

### Task 1: Fix `_looks_like_toc_entry()` to handle long TOC entries

**File:** `src/parser/docx_parser.py`
**Method:** `_looks_like_toc_entry()` (line ~624)

The current check `len(text.split()) <= 16` is too restrictive. Many real TOC entries have long chapter titles (20+ words).

**Fix:** Also recognize entries as TOC if they have a `toc` style or contain tab-separated page numbers:

```python
def _looks_like_toc_entry(self, text: str, style: str | None) -> bool:
    """Return whether a paragraph looks like a short TOC entry."""
    if self._looks_like_chapter_style(style):
        return False
    if self._looks_like_credit_or_note(text):
        return False
    # Recognize TOC-styled paragraphs regardless of length
    if style and "toc" in style.lower():
        return True
    # Recognize entries with tab-separated page numbers (common TOC format)
    if re.search(r"\t\d+\s*$", text):
        return True
    return len(text.split()) <= 16
```

### Task 2: Harden the TOC exit condition

**File:** `src/parser/docx_parser.py`
**Method:** `_extract_chapters()` (line ~302-310)

The `else` branch that exits TOC mode should be more conservative. Instead of exiting on the first non-TOC-looking paragraph, continue collecting while paragraphs have `toc` styles:

```python
if collecting_toc:
    if self._looks_like_chapter_style(style):
        collecting_toc = False
        # Fall through to normal heading detection
    elif self._looks_like_toc_entry(text, style):
        self.last_toc_entries.append(text)
        continue
    elif style and "toc" in style.lower():
        # Still in TOC section even if not a standard entry
        self.last_toc_entries.append(text)
        continue
    else:
        collecting_toc = False
        continue
```

### Task 3: Add KNOWN_TITLES mapping for Unknown Author resolution

**File:** `src/parser/docx_parser.py`

22 books have "Unknown Author" because their folder names contain only the title, not the author. Add a title-to-author mapping that `parse_with_folder_hint()` can use as a second fallback.

**Add class-level constant:**

```python
KNOWN_TITLES: dict[str, str] = {
    "arthashastra": "Kautilya",
    "instructions to his generals": "Frederick the Great",
    "history of the peloponnesian war": "Thucydides",
    "fear and trembling": "Søren Kierkegaard",
    "on the nature of things": "Lucretius",
    "the chaldean oracles": "Unknown Author",  # compilation, no single author
    "the book concerning the tincture of the philosophers": "Paracelsus",
    "on sense and the sensible": "Aristotle",
    "on life and death": "Aristotle",
    "on memory and reminiscence": "Aristotle",
    "on sleep and sleeplessness": "Aristotle",
    "on dreams": "Aristotle",
    "metaphysics": "Aristotle",
    "on longevity and shortness of life": "Aristotle",
    "rhetoric": "Aristotle",
    "the emerald tablet of thoth": "Hermes Trismegistus",
    "in the year 2889": "Jules Verne",
    "corpus hermeticum": "Hermes Trismegistus",
    "the emerald tablet": "Hermes Trismegistus",
    "the hermetic and alchemical writings of paracelsus": "Paracelsus",
    "coelum philosophorum": "Paracelsus",
    "the master key system": "Charles F. Haanel",
    "on sense and the sensible": "Aristotle",
    "aristotles complete works on the mind dreams and the nature of thought": "Aristotle",
    "aristotle's metaphysical and scientific masterpieces": "Aristotle",
    "aristotle's insights into memory, sleep, and the mysteries of the human mind": "Aristotle",
    "the virgin of the world": "Hermes Trismegistus",
    "the life and teachings of thoth hermes trismegistus": "Hermes Trismegistus",
}
```

**Update `parse_with_folder_hint()` to use title-based lookup as second fallback:**

```python
def parse_with_folder_hint(self, docx_path: str | Path, folder_name: str | None = None) -> tuple[BookMetadata, list[Chapter]]:
    """Parse a DOCX file, using the folder name as an author hint if needed."""
    metadata, chapters = self.parse(docx_path)

    if metadata.author == "Unknown Author":
        # Fallback 1: folder name author extraction
        if folder_name:
            folder_author = self._extract_author_from_folder(folder_name)
            if folder_author:
                logger.info("Using folder-name author hint: %s", folder_author)
                metadata = dataclasses.replace(metadata, author=folder_author)

        # Fallback 2: title-to-author lookup
        if metadata.author == "Unknown Author" and metadata.title:
            title_lower = metadata.title.strip().lower()
            if title_lower in self.KNOWN_TITLES:
                known_author = self.KNOWN_TITLES[title_lower]
                if known_author != "Unknown Author":
                    logger.info("Using title-based author lookup: %s", known_author)
                    metadata = dataclasses.replace(metadata, author=known_author)

    return metadata, chapters
```

## Testing

Run the full backend test suite after implementation:
```
cd /path/to/project && ./.venv/bin/pytest -q
```

All existing tests (453) must continue to pass.

**Add at minimum these new tests in `tests/test_docx_parser.py`:**

1. **Test: Long TOC entries are recognized as TOC**
   - Create a mock DOCX with TOC entries having >16 words and `toc 1` style
   - Verify they are skipped as TOC, not treated as chapter headings
   - Verify the actual chapter headings later in the document ARE detected

2. **Test: TOC entries with tab-separated page numbers**
   - Create a mock DOCX with TOC entries containing tab+page number
   - Verify `_looks_like_toc_entry()` returns True

3. **Test: Title-based author lookup works**
   - Parse with folder_hint where title matches KNOWN_TITLES
   - Verify author is resolved from the title mapping

4. **Test: Title lookup doesn't override folder-name author**
   - Ensure folder-name author takes priority over title lookup

## Files to Modify

- `src/parser/docx_parser.py` — TOC fix (Tasks 1-2), KNOWN_TITLES (Task 3)
- `tests/test_docx_parser.py` — New tests

## Files NOT to Modify

- No frontend files
- No factory.py changes (already wired from PROMPT-45)
- No route changes

## Constraints

- Do NOT change any frontend code
- Keep ALL existing tests passing (453 backend tests)
- The `parse()` method signature must remain backward-compatible
- `KNOWN_TITLES` should be a class-level constant
- No external libraries
