# Codex Message for PROMPT-47

Copy-paste this into the Codex chat:

---

Read the full spec at `Codex Prompts/PROMPT-47-FALLBACK-PARSING-AND-AUTHOR-EXPANSION.md` and implement all 5 tasks:

1. Add `_fallback_single_chapter()` method and wire it into `parse()` so books without chapter headings get a single "Full Text" chapter instead of raising ValueError
2. Replace KNOWN_TITLES dict with the massively expanded version (~200 entries) from the spec
3. Expand KNOWN_AUTHORS with ~35 new author entries from the spec
4. Fix title lookup in `parse_with_folder_hint` to handle `&`→`and` and smart quote normalization
5. Add 5 new tests

Run `pytest -q` to confirm all tests pass. Target: 462+ tests passing.
