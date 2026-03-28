# PROMPT-47: Whole-Document Fallback Parsing + Massive Author Expansion

## Context

We have 872 books in the library. 841 parse successfully but 171 of those show "Unknown Author" because their titles aren't mapped. 31 books fail to parse entirely because they lack chapter headings (dialogues, legal documents, hermetic texts, short stories). We need two fixes:

1. **Whole-document fallback**: When `_extract_chapters()` returns no chapters, instead of raising ValueError, treat all body paragraphs as a single chapter titled "Full Text"
2. **Massive KNOWN_TITLES expansion**: Map ~150 additional titles to correct authors so re-parsing fixes the Unknown Author problem

## Task 1: Whole-Document Fallback in `parse()` method

In `src/parser/docx_parser.py`, modify the `parse()` method. When `_extract_chapters()` returns an empty list, instead of raising ValueError:

1. Collect all non-empty paragraphs from the document, skipping known front matter (copyright, TOC headings) and back matter
2. Join them into a single chapter with:
   - `number=1`
   - `title="Full Text"`
   - `type="chapter"`
   - The concatenated paragraph text as `raw_text`
3. Log a warning: `"No chapter headings found in {filename}; using whole-document fallback ({word_count} words)"`
4. Only raise ValueError if BOTH `_extract_chapters()` returns empty AND the fallback also produces no text (truly empty document)

Implementation hint — add a new method `_fallback_single_chapter(self, doc: DocxDocument, path: Path) -> list[Chapter]` that:
- Iterates all paragraphs
- Skips paragraphs matching `_is_skip_section()` or `_is_toc_heading()` or `_is_back_matter_section()`
- Skips paragraphs where `_looks_like_credit_or_note()` returns True
- Skips the first few paragraphs that look like title/author (short paragraphs at the start before body text begins — use a heuristic: skip paragraphs until you find one with >30 words, then include everything from there)
- Concatenates remaining non-empty paragraph text with `\n\n`
- Returns a single-element list with one Chapter, or empty list if no text found

## Task 2: Expand KNOWN_TITLES dictionary

Replace the existing `KNOWN_TITLES` dict in `DocxParser` with this massively expanded version. Keep all existing entries and add all the new ones below:

```python
KNOWN_TITLES: dict[str, str] = {
    # === Existing entries (keep these) ===
    "arthashastra": "Kautilya",
    "instructions to his generals": "Frederick the Great",
    "history of the peloponnesian war": "Thucydides",
    "fear and trembling": "Søren Kierkegaard",
    "on the nature of things": "Lucretius",
    "the chaldean oracles": "Anonymous",
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
    "aristotles complete works on the mind dreams and the nature of thought": "Aristotle",
    "aristotle's metaphysical and scientific masterpieces": "Aristotle",
    "aristotle's insights into memory, sleep, and the mysteries of the human mind": "Aristotle",
    "the virgin of the world": "Hermes Trismegistus",
    "the life and teachings of thoth hermes trismegistus": "Hermes Trismegistus",
    # === NEW: Voltaire ===
    "micromegas: a philosophical story": "Voltaire",
    "micromegas": "Voltaire",
    "candide": "Voltaire",
    "zadig": "Voltaire",
    # === NEW: Charles Dickens collaborative works ===
    "the wreck of the golden mary": "Charles Dickens",
    "the perils of certain english prisoners": "Charles Dickens",
    "the haunted house": "Charles Dickens",
    "the lazy tour of two idle apprentices": "Charles Dickens",
    "a house to let": "Charles Dickens",
    "no thoroughfare": "Charles Dickens",
    "the battle of life": "Charles Dickens",
    # === NEW: Brontë sisters ===
    "jane eyre": "Charlotte Brontë",
    "villette": "Charlotte Brontë",
    "shirley": "Charlotte Brontë",
    "the professor": "Charlotte Brontë",
    "wuthering heights": "Emily Brontë",
    "the tenant of wildfell hall": "Anne Brontë",
    "agnes grey": "Anne Brontë",
    # === NEW: Classical / Ancient ===
    "the iliad: the fall of troy": "Homer",
    "the iliad": "Homer",
    "the odyssey": "Homer",
    "anabasis": "Xenophon",
    "memorabilia": "Xenophon",
    "the last days of socrates": "Plato",
    "euthyphro": "Plato",
    "apology": "Plato",
    "crito": "Plato",
    "phaedo": "Plato",
    "the three theban plays": "Sophocles",
    "oedipus the king": "Sophocles",
    "oedipus at colonus": "Sophocles",
    "antigone": "Sophocles",
    "meno": "Plato",
    "symposium": "Plato",
    "the republic": "Plato",
    "true history": "Lucian",
    "parallel lives": "Plutarch",
    "the golden ass": "Apuleius",
    "the satyricon": "Petronius",
    "clouds": "Aristophanes",
    "oeconomicus": "Xenophon",
    "apology of socrates": "Xenophon",
    "plato apology": "Plato",
    "charmides": "Plato",
    "plato lesser hippias": "Plato",
    "plato minos": "Plato",
    "plato clitophon": "Plato",
    "plato epinomis": "Plato",
    "plato cratylus": "Plato",
    "plato alcibiades ii": "Plato",
    "plato theages": "Plato",
    "socrates axiochus": "Plato",
    "socrates eryxias": "Plato",
    "socrates demodocus": "Plato",
    # === NEW: Eastern / Religious / Spiritual ===
    "the bhagavad gita": "Vyasa",
    "the dhammapada": "Buddha",
    "the tao of chuang tzu (zhuangzi)": "Zhuangzi",
    "the tao of chuang tzu": "Zhuangzi",
    "the yoga sutras of patanjali": "Patanjali",
    "the diamond sutra": "Anonymous",
    "the ramayana": "Valmiki",
    "confessions": "Saint Augustine",
    "the spiritual exercises": "Ignatius of Loyola",
    "the book of tea": "Kakuzo Okakura",
    "the pillow book": "Sei Shōnagon",
    "meditations of descartes": "René Descartes",
    # === NEW: Anonymous ancient texts ===
    "el cantar de mio cid": "Anonymous",
    "the song of roland": "Anonymous",
    "the egyptian book of the dead": "Anonymous",
    "the tibetan book of the dead (bardo thodol)": "Padmasambhava",
    "the tibetan book of the dead": "Padmasambhava",
    "the popol vuh": "Anonymous",
    "the epic of gilgamesh": "Anonymous",
    "the descent of ishtar": "Anonymous",
    "nergal and ereshkigal": "Anonymous",
    "the lament for ur": "Anonymous",
    "the marriage of martu": "Anonymous",
    "temple hymn to nanna (sin)": "Enheduanna",
    "temple hymn to nanna": "Enheduanna",
    "the debate between sheep and grain": "Anonymous",
    "erra and ishum": "Kabti-ilani-Marduk",
    # === NEW: Apocryphal / Gnostic / Religious texts ===
    "the first book of enoch": "Anonymous",
    "the first book of maccabees": "Anonymous",
    "the second book of esdras": "Anonymous",
    "the second book of the maccabees": "Anonymous",
    "the third book of maccabees": "Anonymous",
    "the fourth book of maccabees": "Anonymous",
    "the book of jubilees": "Anonymous",
    "the testament of the twelve patriarchs": "Anonymous",
    "the book of baruch": "Baruch ben Neriah",
    "the third book of baruch": "Anonymous",
    "the apocalypse of peter": "Anonymous",
    "the book of the secrets of enoch": "Anonymous",
    "the hebrew book of enoch": "Anonymous",
    "the acts of paul and thecla": "Anonymous",
    "the book of the watchers": "Anonymous",
    "pistis sophia": "Anonymous",
    "the gospel of philip": "Anonymous",
    "the gospel of thomas": "Anonymous",
    "the gospel of judas": "Anonymous",
    "the book of thomas the contender": "Anonymous",
    "the apocryphon of john (the secret book of john)": "Anonymous",
    "the apocryphon of john": "Anonymous",
    "the apocalypse of abraham": "Anonymous",
    "the sophia of jesus christ": "Anonymous",
    "the words of gad the seer": "Anonymous",
    "the gospel of the egyptians": "Anonymous",
    "thunder, perfect mind": "Anonymous",
    "the dialogue of the savior": "Anonymous",
    "the odes of solomon": "Anonymous",
    "the psalms of solomon": "Anonymous",
    "the epistle of barnabas": "Anonymous",
    "the book of creation (sefer yetzirah)": "Anonymous",
    "the book of creation": "Anonymous",
    "the gospel of the hebrews": "Anonymous",
    "the gospel of the nazarenes": "Anonymous",
    "the epistle to the laodiceans": "Anonymous",
    "the book of enoch & the fallen angels": "Anonymous",
    "the book of enoch and the fallen angels": "Anonymous",
    "the books of baruch & the exiles": "Anonymous",
    "the books of baruch and the exiles": "Anonymous",
    "the nag hammadi scriptures": "Anonymous",
    "the shepherd of hermas": "Hermas",
    "ecclesiasticus (the wisdom of jesus the son of sirach)": "Ben Sira",
    "ecclesiasticus": "Ben Sira",
    "the second treatise of the great seth": "Anonymous",
    "the acts of peter and the twelve apostles": "Anonymous",
    "the dead sea scrolls bible": "Anonymous",
    # === NEW: Cervantes ===
    "don quixote": "Miguel de Cervantes",
    # === NEW: Political / Historical ===
    "the communist manifesto": "Karl Marx",
    "on war": "Carl von Clausewitz",
    "the federalist papers": "Alexander Hamilton, James Madison & John Jay",
    "the us constitution": "United States Founding Fathers",
    "the declaration of independence": "Thomas Jefferson",
    "the bill of rights n constitutional amendments": "United States Congress",
    "the bill of rights and constitutional amendments": "United States Congress",
    "the constitution of the us n declaration of independence": "United States Founding Fathers",
    "the constitution of the us and declaration of independence": "United States Founding Fathers",
    "constitution of the united states pocket edition": "United States Founding Fathers",
    "constitution of the united states large print": "United States Founding Fathers",
    "the simple sabotage field manual": "Office of Strategic Services",
    "democracy in america": "Alexis de Tocqueville",
    "the complete essays": "Michel de Montaigne",
    # === NEW: Boethius ===
    "theological tractates": "Boethius",
    "on the trinity (de trinitate)": "Boethius",
    "on the trinity": "Boethius",
    # === NEW: Fairy tales / Fiction ===
    "grimms' fairy tales (complete)": "Brothers Grimm",
    "grimms' fairy tales": "Brothers Grimm",
    "unveiling a parallel": "Alice Ilgenfritz Jones",
    "varney the vampire": "James Malcolm Rymer",
    "the horla": "Guy de Maupassant",
    "the mummy's foot": "Théophile Gautier",
    "the mummys foot": "Théophile Gautier",
    "rip van winkle": "Washington Irving",
    "the unparalleled adventure of one hans pfaall": "Edgar Allan Poe",
    # === NEW: Ethiopian Bible ===
    "the complete ethiopian bible (volume i)": "Anonymous",
    "the complete ethiopian bible (volume ii)": "Anonymous",
    "the complete ethiopian bible": "Anonymous",
    # === NEW: Multi-volume collections ===
    "the ultimate horror collection of 101+ macabre masterpieces (volume i)": "Various Authors",
    "the ultimate horror collection of 101+ macabre masterpieces (volume ii)": "Various Authors",
    "the ultimate horror collection of 101+ macabre masterpieces (volume iii)": "Various Authors",
    "the ultimate horror collection of 101+ macabre masterpieces (volume iv)": "Various Authors",
    "the ultimate horror collection of 101+ macabre masterpieces (volume vi)": "Various Authors",
    "the ultimate horror collection of 101+ macabre masterpieces (volume vii)": "Various Authors",
    "the ultimate horror collection of 101+ macabre masterpieces (volume viii)": "Various Authors",
    "the ultimate horror collection of 101+ macabre masterpieces (volume ix)": "Various Authors",
    "the ultimate horror collection of 101+ macabre masterpieces (volume x)": "Various Authors",
    "the ultimate horror collection of 101+ macabre masterpieces (volume xi)": "Various Authors",
    "the ultimate horror collection of 101+ macabre masterpieces (volume xii)": "Various Authors",
    "the ultimate horror collection of 101+ macabre masterpieces (volume xiii)": "Various Authors",
    "the ultimate horror collection of 101+ macabre masterpieces (volume xiv)": "Various Authors",
    "the complete strategy collection (volume i)": "Various Authors",
    "the complete strategy collection (volume ii)": "Various Authors",
    "the complete strategy collection (volume iii)": "Various Authors",
    "the complete philosophy collection (vol. i)": "Various Authors",
    "the complete philosophy collection (vol. iii)": "Various Authors",
    "the complete philosophy collection (vol. iv)": "Various Authors",
    "the complete philosophy collection (vol. v)": "Various Authors",
    "the complete philosophy collection (vol. vi)": "Various Authors",
    "the complete philosophy collection (vol. viii)": "Various Authors",
    "the complete leadership collection": "Various Authors",
    "the complete leadership collection (volume i)": "Various Authors",
    "the complete leadership collection (volume ii)": "Various Authors",
    "the complete hermeticism philosophy collection": "Various Authors",
    "the ancient wisdom collection": "Various Authors",
    "the ultimate esoteric wisdom collection": "Various Authors",
    "the classical wisdom collection": "Various Authors",
    "the complete strategy & war collection": "Various Authors",
    "the complete strategy and war collection": "Various Authors",
    "the complete warrior's mindset collection": "Various Authors",
    "the complete warriors mindset collection": "Various Authors",
    "mystical hermetic & christian dialogues": "Various Authors",
    "mystical hermetic and christian dialogues": "Various Authors",
}
```

## Task 3: Expand KNOWN_AUTHORS dictionary

Add these new entries to the `KNOWN_AUTHORS` dict (keep all existing entries):

```python
# Add to KNOWN_AUTHORS:
"charlotte brontë": "Charlotte Brontë",
"charlotte bronte": "Charlotte Brontë",
"emily brontë": "Emily Brontë",
"emily bronte": "Emily Brontë",
"anne brontë": "Anne Brontë",
"anne bronte": "Anne Brontë",
"xenophon": "Xenophon",
"sophocles": "Sophocles",
"plutarch": "Plutarch",
"apuleius": "Apuleius",
"petronius": "Petronius",
"lucian": "Lucian",
"aristophanes": "Aristophanes",
"cervantes": "Miguel de Cervantes",
"miguel de cervantes": "Miguel de Cervantes",
"alexis de tocqueville": "Alexis de Tocqueville",
"tocqueville": "Alexis de Tocqueville",
"karl marx": "Karl Marx",
"carl von clausewitz": "Carl von Clausewitz",
"clausewitz": "Carl von Clausewitz",
"boethius": "Boethius",
"guy de maupassant": "Guy de Maupassant",
"maupassant": "Guy de Maupassant",
"théophile gautier": "Théophile Gautier",
"theophile gautier": "Théophile Gautier",
"washington irving": "Washington Irving",
"brothers grimm": "Brothers Grimm",
"valmiki": "Valmiki",
"vyasa": "Vyasa",
"patanjali": "Patanjali",
"zhuangzi": "Zhuangzi",
"augustine": "Saint Augustine",
"saint augustine": "Saint Augustine",
"ignatius of loyola": "Ignatius of Loyola",
"rené descartes": "René Descartes",
"rene descartes": "René Descartes",
"descartes": "René Descartes",
```

## Task 4: Fix the `parse_with_folder_hint` title lookup

The current title lookup in `parse_with_folder_hint` does:
```python
title_lower = self._normalize_text(metadata.title).casefold().replace("'", "'")
```

But many titles contain special characters like `&`, parentheses `()`, and smart quotes. The lookup should also try:
- The exact normalized+casefolded title
- A version with `&` replaced by `and`
- A version with `n` replaced by `and` (since folder names use `n` for `and`)
- A version with content in parentheses stripped

Add fallback lookup logic:
```python
if metadata.author == "Unknown Author" and metadata.title:
    title_lower = self._normalize_text(metadata.title).casefold().replace("\u2019", "'")
    known_author = self.KNOWN_TITLES.get(title_lower)
    if not known_author or known_author == "Unknown Author":
        # Try with & → and
        alt = title_lower.replace(" & ", " and ")
        known_author = self.KNOWN_TITLES.get(alt) or known_author
    if not known_author or known_author == "Unknown Author":
        # Try with smart quotes normalized
        alt = title_lower.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
        known_author = self.KNOWN_TITLES.get(alt) or known_author
    if known_author and known_author not in ("Unknown Author",):
        logger.info("Using title-based author lookup: %s", known_author)
        metadata = replace(metadata, author=known_author)
```

## Task 5: Tests

Add tests to `tests/test_docx_parser.py`:

1. **test_fallback_single_chapter**: Create a DOCX with no chapter headings but paragraphs of body text. Verify parse returns 1 chapter titled "Full Text" with the body text.

2. **test_fallback_empty_document**: Create a DOCX with only copyright/front-matter text. Verify parse raises ValueError.

3. **test_known_titles_expansion**: Verify that several new KNOWN_TITLES entries exist and return the correct author. Test at least: "jane eyre" → "Charlotte Brontë", "don quixote" → "Miguel de Cervantes", "the communist manifesto" → "Karl Marx", "the epic of gilgamesh" → "Anonymous".

4. **test_known_authors_expansion**: Verify new KNOWN_AUTHORS entries. Test at least: "xenophon" → "Xenophon", "charlotte brontë" → "Charlotte Brontë".

5. **test_title_lookup_with_ampersand**: Test that a book titled "The Complete Strategy & War Collection" resolves to "Various Authors" via the `&`→`and` fallback.

## Constraints

- Do NOT change the Chapter or BookMetadata dataclasses
- Do NOT change the factory.py integration
- All existing tests must continue to pass
- Run `pytest -q` and confirm all tests pass (should be 457 + 5 new = 462+)
