# Alexandria Audiobook Narrator — Project State

**Owner:** Tim (tim@ltvspot.com)
**Last Updated:** 2026-03-25
**Status:** Prompts 01-18 COMPLETE. PROMPT-19 (Critical Bug Fixes) IN PROGRESS.

---

## Current State

All 16 core development prompts are implemented and committed. The application is a fully functional local web dashboard for transforming 873 formatted manuscripts into production audiobooks using Qwen3-TTS (MLX). PROMPT-17 (Production Hardening) is currently being implemented by Codex. PROMPT-18 (Production Scale) is written and ready to send.

### What Exists

**Backend (28 API endpoints):**
- `src/main.py` — FastAPI app with lifespan hooks, middleware, error handlers, CORS
- `src/config.py` — Pydantic settings with validation, persistence, defaults
- `src/database.py` — SQLAlchemy models: Book, Chapter, GenerationJob, VoicePreset, QAStatus, ExportJob
- `src/api/routes.py` — Library endpoints (scan, list, book detail, chapters, parse, text editing)
- `src/api/generation.py` — Generation endpoints (per-chapter, per-book, generate-all)
- `src/api/queue_routes.py` — Queue management (list, cancel, pause, resume, priority)
- `src/api/qa_routes.py` — QA endpoints (book QA status, pending reviews)
- `src/api/export_routes.py` — Export endpoints (trigger, status, download MP3/M4B)
- `src/api/settings_routes.py` — Settings API (get, update, schema, reset)
- `src/api/voice_lab.py` — Voice endpoints (list, test, clone, delete)
- `src/api/middleware.py` — Request context middleware with logging
- `src/api/error_handlers.py` — Global error handlers
- `src/api/cache.py` — Response caching

**Parsers:**
- `src/parser/docx_parser.py` — DOCX parser with Heading 1 chapter detection
- `src/parser/epub_parser.py` — EPUB parser via ebooklib
- `src/parser/pdf_parser.py` — PDF parser via pdfplumber
- `src/parser/factory.py` — Format detection and fallback chain
- `src/parser/text_cleaner.py` — Abbreviation expansion, artifact removal
- `src/parser/credits_generator.py` — Opening/closing credit text generation
- `src/parser/common.py` — Shared parser utilities

**TTS Engine:**
- `src/engines/base.py` — Abstract TTSEngine interface
- `src/engines/qwen3_tts.py` — Qwen3-TTS MLX adapter (1.7B CustomVoice model)
- `src/engines/chunker.py` — Sentence-aware text chunking
- `src/engines/voice_cloner.py` — Voice cloning from reference audio

**Pipeline:**
- `src/pipeline/generator.py` — Chapter WAV generation with chunk stitching
- `src/pipeline/queue_manager.py` — FIFO generation queue with cancel/pause/resume
- `src/pipeline/qa_checker.py` — Automated audio QA (clipping, silence, volume, duration)
- `src/pipeline/exporter.py` — MP3/M4B export with LUFS normalization, metadata, chapter markers

**Hardening:**
- `src/health_checks.py` — Startup health checks (DB, model, ffmpeg, disk)
- `src/logging_config.py` — Rotating file logging (app, errors, API, generation)
- `src/database_migrations.py` — Schema migration support

**Frontend (7 pages, 17+ components):**
- `frontend/src/pages/Library.jsx` — Book grid with search, filter, sort, stats
- `frontend/src/pages/BookDetail.jsx` — Chapter list, text preview, generation, export
- `frontend/src/pages/VoiceLab.jsx` — Voice testing, A/B compare, presets, cloning
- `frontend/src/pages/Queue.jsx` — Queue management with status filters
- `frontend/src/pages/QA.jsx` — QA review page
- `frontend/src/pages/QADashboard.jsx` — QA dashboard with chapter cards
- `frontend/src/pages/Settings.jsx` — Application settings
- Components: AudioPlayer, BookCard, ChapterList, ExportDialog, DownloadCard, VoiceCloneForm, SettingsForm, ErrorBoundary, and more

**Tests:** 123 total (86 passing, 37 errors from sandbox permission issue)

**Data:**
- `models/` — Three 1.7B MLX models (~2.9GB each): CustomVoice, VoiceDesign, Base
- `Formatted Manuscripts/` — 873 manuscript folders with DOCX/EPUB/PDF files
- `voices/` — Directory for cloned voice references
- `outputs/` — Directory for generated audio

### What Still Needs to Be Built (PROMPT-17 & 18)

- Chunk-level audio validation before stitching (PROMPT-17, in progress)
- Generation timeout per chunk (PROMPT-17, in progress)
- Improved abbreviation handling in chunker (PROMPT-17, in progress)
- 404 catch-all route (PROMPT-17, in progress)
- Model lifecycle management / cooldown (PROMPT-18, ready)
- Resource monitoring system (PROMPT-18, ready)
- Batch generation orchestration for 873 books (PROMPT-18, ready)
- Catalog progress dashboard (PROMPT-18, ready)
- Batch QA approval (PROMPT-18, ready)
- Batch export (PROMPT-18, ready)

---

## Prompt Tracker

| # | Name | Status | Commit | Notes |
|---|------|--------|--------|-------|
| 01 | Project Scaffolding | COMPLETE | 9e246e2 | FastAPI + React skeleton, DB schema |
| 02 | DOCX Manuscript Parser | COMPLETE | af260f0 | Chapter detection, text cleaning, credits |
| 03 | Parser API Integration | COMPLETE | 3c2d691 | Library scanner, parse endpoints, DB storage |
| 04 | Library UI | COMPLETE | 75ebb6b | Book grid, search, filter, sorting, stats |
| 05 | Book Detail UI | COMPLETE | ee59aeb | Chapter list, text preview, narration settings |
| 06 | TTS Engine + Qwen3 Adapter | COMPLETE | 162bf01 | Abstract interface, MLX adapter, chunker |
| 07 | Voice Lab UI | COMPLETE | 9a582c6 | Voice testing, A/B compare, presets |
| 08 | Generation Pipeline | COMPLETE | 55b9354 | Background tasks, chunking, WAV output |
| 09 | Generation UI + Player | COMPLETE | 8034087 | Progress tracking, audio player |
| 10 | Production Queue | COMPLETE | 0d920be | Queue management, FIFO, cancel, ETA |
| 11 | Automated QA System | COMPLETE | 8c9b2d4 | Audio QA checks, dashboard, review workflow |
| 12 | Export Pipeline | COMPLETE | 8efb2fb | MP3/M4B, LUFS normalization, metadata, chapter markers |
| 13 | Settings + Configuration | COMPLETE | dfb9402 | Persistent settings, validation, settings UI |
| 14 | Voice Cloning Integration | COMPLETE | 0724f49 | Reference audio upload, clone management |
| 15 | EPUB + PDF Parsers | COMPLETE | 2c66d5b | EPUB via ebooklib, PDF via pdfplumber, factory |
| 16 | Polish + Hardening | COMPLETE | be46afb | Health checks, logging, error handlers, middleware |
| 17 | Production Hardening | COMPLETE | 50a2f20 | Bug fixes, chunk validation, timeouts, abbreviations |
| 18 | Production Scale | COMPLETE | 6a18543 | Batch orchestration, monitoring, catalog dashboard |
| 19 | Critical Bug Fixes | IN PROGRESS | — | 6 verified production bugs from QA audit |

---

## Known Issues (from COMPREHENSIVE-AUDIT-V2.md)

### Fixed
- BUG-02: Audio normalization peak limiter added (prevents clipping)
- BUG-03: QA checker crash on corrupted audio handled gracefully
- BUG-04: Commit-before-QA partially fixed with error handling

### Being Fixed (PROMPT-17)
- Chunk-level audio validation missing
- No generation timeout (model can hang indefinitely)
- Sentence chunker breaks on abbreviations (Dr., Mr., 3.14)
- No 404 catch-all route in frontend
- Health check file cleanup permission error

### To Be Fixed (PROMPT-18)
- No model cooldown/restart after extended generation
- No resource monitoring (disk, memory, CPU)
- No batch generation orchestration
- No catalog-level progress dashboard
- No batch QA approval
- No batch export
- Race condition in queue_manager.jobs dict (no locking)
- Missing database indexes on Chapter.status and GenerationJob.status

---

## Workflow Rules

- **Claude (Cowork)** = CEO / Project Manager — writes prompts, reviews output, manages state
- **Codex** = Senior Developer — implements code, runs tests, commits
- **Tim** = Founder / Product Owner — provides requirements, final approval

Claude NEVER writes production code directly. All code changes go through Codex prompts.

---

## ⚠️ MANDATORY QA FEEDBACK LOOP — READ THIS FIRST ⚠️

**This is a NON-NEGOTIABLE requirement from Tim. It applies to EVERY session, EVERY prompt, EVERY time.**

After EVERY Codex prompt completes, Claude (Cowork) MUST:

1. **Programmatic Testing** — Start server, test ALL API endpoints, run full pytest suite, verify imports
2. **Code Audit** — Read every new/changed file, verify business rules, check for regressions
3. **Visual/UX Testing** — Load frontend in browser, navigate all pages, interact with new features, test user flows
4. **Report** — Summarize what was built, what passed, what failed. Only proceed when QA is clean.

Tim's exact words: *"It does not even seem like you're actually testing the code, output, functionality both programmatically and visually + the UX/UI, etc. like a human? Please hardcode this in your systems everywhere so this never happens again!"*

**Skipping this loop is a FAILURE. No exceptions. No shortcuts.**

---

## Production Audit Documents

- `COMPREHENSIVE-AUDIT-V2.md` — Full codebase audit after all 16 prompts (March 25, 2026)
- `PRODUCTION-AUDIT.md` — Initial audit after prompt 11 (March 24, 2026)
- `TEST-RESULTS.md` — Automated test results

## API Routes (28 total)

```
GET  /api/health
GET  /api/library
POST /api/library/scan
GET  /api/book/{book_id}
GET  /api/book/{book_id}/chapters
GET  /api/book/{book_id}/parsed
POST /api/book/{book_id}/parse
GET  /api/book/{book_id}/status
PUT  /api/book/{book_id}/chapter/{chapter_number}/text
POST /api/book/{book_id}/chapter/{chapter_number}/generate
GET  /api/book/{book_id}/chapter/{chapter_number}/status
GET  /api/book/{book_id}/chapter/{chapter_number}/audio
POST /api/book/{book_id}/generate
POST /api/book/{book_id}/generate-all
POST /api/book/{book_id}/export
GET  /api/book/{book_id}/export/status
GET  /api/book/{book_id}/export/download/{format}
GET  /api/job/{job_id}
DELETE /api/job/{job_id}
GET  /api/queue
GET  /api/queue/{job_id}
POST /api/queue/batch-all
POST /api/queue/{job_id}/cancel
POST /api/queue/{job_id}/pause
POST /api/queue/{job_id}/resume
PUT  /api/queue/{job_id}/priority
GET  /api/voice-lab/voices
POST /api/voice-lab/test
GET  /api/qa/book/{book_id}
GET  /api/qa/pending
GET  /api/settings
PUT  /api/settings
GET  /audio/voices/{filename}
```
