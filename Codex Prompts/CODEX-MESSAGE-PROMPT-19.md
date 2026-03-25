# Codex Message — PROMPT-19

**Paste this into Codex for the "Qwen3-TTS - Test" project:**

---

Read `CLAUDE.md` and `PROJECT-STATE.md` first, then read `Codex Prompts/PROMPT-19-CRITICAL-BUG-FIXES.md` for the full implementation spec.

**Summary:** Fix 6 verified production bugs (3 P1, 1 P2, 1 P3) found during comprehensive QA audit. All bugs have been confirmed against the actual code.

**The 6 bugs:**

1. **(P1) Single chapter generation promotes entire book to "generated"** — `queue_manager.py` line ~952: `_finalize_job()` unconditionally sets `book.status = BookStatus.GENERATED` even when only 1 chapter was generated. **Fix:** Check that ALL chapters are generated before promoting book status.

2. **(P1) Export accepts partial books and marks them exported** — `export_routes.py` has no readiness gate. A book with 1/20 chapters generated can be exported and marked EXPORTED. **Fix:** Add `_validate_book_export_readiness()` that verifies all chapters are generated (and optionally approved) before allowing export. Return 400 with clear error message if not ready.

3. **(P1) Cold voice loading stalls entire API** — `model_manager.py` line ~123: `engine.load()` is synchronous and blocks the async event loop for 10+ seconds during first model load, making ALL endpoints (health, settings, everything) unresponsive. **Fix:** Move `engine.load()` to `asyncio.to_thread()`. Add 2-second timeout on voice list endpoint with degraded `{"loading": true}` response if engine not ready.

4. **(P2) BookDetail page blocks on voice list** — `BookDetail.jsx` lines 313-326: three API calls awaited sequentially, page shows "Loading..." until ALL complete including slow voice fetch. **Fix:** Parallelize gen+export status with `Promise.all()`, move voice fetch out of the loading gate so page renders immediately while voices load in background.

5. **(P1) Queue API crashes with timezone comparison** — `queue_routes.py` line ~273: compares timezone-aware `utc_now()` with timezone-naive DB datetimes → `TypeError`. **Fix:** Add `ensure_aware()` helper in `database.py` that adds UTC timezone to naive datetimes. Apply it to ALL DB datetime comparisons across the codebase (grep for `utc_now()` comparisons).

6. **(P3) QA metrics count unparsed books as pending QA** — `qa_routes.py` line ~487: books with zero chapters (unparsed) increment `books_pending_qa`. Dashboard showed 872 pending before any parsing. **Fix:** Filter query to only include books with status PARSED/GENERATED/EXPORTED. Add `unparsedBooks` count to metrics response.

**Constraints:**
- All 152 existing backend tests must pass
- All 28 existing frontend tests must pass
- Add new tests for EVERY bug fix (listed in the prompt spec)
- Zero regressions

Commit as: `[PROMPT-19] CRITICAL BUG FIXES — 6 verified production bugs`
