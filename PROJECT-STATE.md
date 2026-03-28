# Alexandria Audiobook Narrator — Project State

**Owner:** Tim (tim@ltvspot.com)  
**Last Updated:** 2026-03-28
**Status:** Prompts 01-48 COMPLETE. Parser hardened — whole-document fallback, ~200 title-author mappings, zero Unknown Authors, Introduction sub-headings preserved. 868/872 books parsed (99.5%), 777 books with proper Introduction sections, 466 backend tests.

---

## Current State

Alexandria is now a production-hardened local FastAPI + React dashboard for turning the manuscript catalog into audiobook outputs with Qwen3-TTS on Apple Silicon.

The current codebase includes:

- Library ingestion and parsing for DOCX, EPUB, and PDF manuscripts
- Per-chapter generation with chunk validation, pause padding, model-specific mitigations, and progress heartbeats
- Queue orchestration with duplicate-job prevention, crash recovery, checkpointing, batch scheduling, and graceful shutdown
- Gate 1, Gate 2, and Gate 3 quality checks with chapter QA, book QA, mastering, loudness compliance, and export gating
- Batch generation hardening, monitoring, and catalog/export progress endpoints
- Production Overseer reporting with quality trends, manuscript validation, flagged items, and export readiness
- Frontend flows for Library, Book Detail, Voice Lab, Queue, QA, Catalog Dashboard, Settings, and Overseer

## Verification Snapshot

Latest verified results on this machine:

- Backend: `./.venv/bin/pytest -q` → `466 passed`
- Frontend tests: `cd frontend && CI=true npm test -- --watchAll=false` → `17 suites, 74 tests passed`
- Frontend production build: `cd frontend && npm run build` → passed
- Smoke: Temporary local app booted cleanly, `/api/health` returned 200, Library page loaded with no browser console errors
- **Export E2E**: Book 6 "Self-Reliance" exported successfully — MP3 (90.6 MB) + M4B (40.7 MB) with download links in webapp

## First Successful Audiobook Export

- **Book**: Self-Reliance by Ralph Waldo Emerson (modern translation)
- **Chapters**: 3 (Opening Credits 13s, Introduction/Preface 65m 26s, Closing Credits 13s)
- **Narrator**: Kent Zimering (Qwen3-TTS)
- **Export**: MP3 (90.6 MB) + M4B with chapter markers (40.7 MB)
- **Pipeline**: Manuscript → Parse → Generate → Master (ffmpeg fast chain) → QA (Gate 2 + Gate 3) → Concatenate → Normalize → Encode → Verify
- **Export duration**: ~15 minutes end-to-end
- **Webapp**: Download links available at `http://localhost:8080/book/6`

## Prompt Coverage

- Prompts 01-16: Core platform, parsing, generation, QA, export, settings, voice lab, and baseline hardening
- Prompts 17-20: Production hardening, scale features, critical bug fixes, frontend serving, and startup/runtime integration
- Prompts 21-30: Advanced hardening, heartbeats, Gate 1/2/3 QA, mastering, crash recovery, batch hardening, model-specific mitigations, and production overseer
- Prompt 31: Book mastering pipeline with ffmpeg fast chain, per-chapter mastering, loudness normalization, and peak limiting
- Prompt 32: Non-blocking export endpoint — background thread architecture, export status polling, progress tracking (commit `109613d`, 314 tests)
- Prompt 33: QA performance & progress tracking — fast-path QA for long chapters, per-chapter progress, DB session isolation, timeout fixes (commit `a95c4b6`, +890 -273, 317 tests)
- Prompt 34: Fix export mastering blockers & ACX compliance — peak target -3.5 dBFS, noise gate in fast chain, export_mode for Gate 3, relaxed credit transitions, WAV file size skip, DB enum repair (commit `0d03027`, +306 -29, 323 tests)
- Prompt 35: Fix export UI and recovery — backfill qa_report on recovery path, set current_stage to "Export completed", defensive frontend guard for null qa_report (commit `24f6503`, +241 -7, 324 tests, 55 frontend tests)
- Prompts 36-41: Production scale infrastructure (36 files +3446 -166)
- Prompt 42: Book Detail UX overhaul (+1632 -846, commit `ba257d1`)
- Prompt 43: UX polish — speed slider, styled modal, export wording, QA terminology, pronunciation quick-add, batch context (commit `395b50e`, +402 -93)
- Prompt 44: Eliminate all browser dialogs — shared ConfirmDialog component, Toast notifications, zero native prompts/confirms/alerts (commit `dbb1050`, +927 -118, 74 frontend tests)
- Prompt 45: Parser hardening — zero crash tolerance, author extraction fix. Empty chapters skipped (not crash), "A Modern Translation" blocked, folder-name author fallback with 50+ known authors, non-fatal _find_author(), improved diagnostics (commit `585035d`, +342 -13, 453 backend tests)
- Prompt 46: TOC parsing fix + title-based author fallback — fix _looks_like_toc_entry() for long chapter titles and toc-styled paragraphs, harden TOC exit condition, add KNOWN_TITLES dict for ~20 books, 5 new tests (commit `a536062`, +393 -10, 457 backend tests). Full library parsed: 841/872 books (96.4%), 24,392 chapters, zero "A Modern Translation" authors.
- Prompt 47: Whole-document fallback + massive author expansion — _fallback_single_chapter() for books without chapter headings, KNOWN_TITLES expanded to ~200 entries, KNOWN_AUTHORS +35 new entries, title lookup handles &/and/smart quotes/parentheticals, 5 new tests (commit `f810942`, +806 -3, 463 backend tests). Library: 868/872 parsed (99.5%), 0 Unknown Authors, 26,749 chapters. 4 remaining unparsed are corrupt DOCX files.
- Prompt 48: Preserve Introduction sections with sub-headings — _is_explicit_chapter_heading() method, _extract_chapters() treats sub-headings inside Introduction as body text (only explicit "Chapter N" ends an intro), 3 new tests (commit `e13eb0f`, +198 -0, 466 backend tests). 777 books now have proper Introduction sections; Art of War Introduction restored to 4,566 words.

## Operator Notes

- The repo should stay clean during normal operation once ignored runtime artifacts are excluded from git status.
- Git remote is `ltvspot` (not `origin`) — push to `ltvspot/master`.
- The first end-to-end audiobook has been successfully exported. Next validation: ACX upload testing, multi-book batch export, and voice variety testing.
- Server should be started with `uvicorn src.main:app --host 0.0.0.0 --port 8080` (not `python -m src.main`).
- The DB startup cleanup in `startup.py` automatically repairs any legacy `ERROR` status rows in `generation_jobs`.

## Mandatory QA Loop

After every meaningful change:

1. Run backend tests.
2. Run frontend tests.
3. Build the frontend.
4. Smoke the changed flows in a browser against the live app.
5. Report what passed, what failed, and what still needs hardening.
