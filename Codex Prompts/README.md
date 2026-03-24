# Alexandria Audiobook Narrator — Codex Prompts

This directory contains 8 detailed, self-contained Codex prompts for building the Alexandria Audiobook Narrator web application. Each prompt is designed to be executed in a single Codex session and includes acceptance criteria, test requirements, and exact file paths.

## Project Overview

**Alexandria Audiobook Narrator** is a local FastAPI + React web app that transforms formatted manuscripts (873 titles) into production-ready audiobooks using local TTS (Qwen3-TTS via MLX).

**Tech Stack:**
- Backend: Python 3.11+ / FastAPI on localhost:8080
- Frontend: React + Tailwind CSS on localhost:3000
- Database: SQLite via SQLAlchemy
- TTS Engine: Qwen3-TTS (MLX) for Apple Silicon
- Audio Processing: pydub + ffmpeg

---

## Prompts Overview

### PROMPT-01: Project Scaffolding & Database Schema
**File:** `PROMPT-01-PROJECT-SCAFFOLDING.md`

Create complete project structure, FastAPI skeleton, SQLite schema, and React frontend shell.

**Deliverables:**
- `src/main.py` — FastAPI app with health check, CORS, logging
- `src/database.py` — SQLAlchemy models (books, chapters, voice_presets, generation_jobs)
- `src/config.py` — Configuration management
- `frontend/package.json` — React dependencies (Tailwind, React Router)
- `frontend/src/` — App shell with page stubs
- `tests/conftest.py` — pytest fixtures

**Acceptance:** Server starts on port 8080, `GET /api/health` returns 200, `npm run build` succeeds

---

### PROMPT-02: DOCX Manuscript Parser & Text Cleaning Pipeline
**File:** `PROMPT-02-DOCX-PARSER.md`

Build DOCX parser to extract chapter structure, metadata, and credits.

**Deliverables:**
- `src/parser/docx_parser.py` — Parse DOCX files, detect chapters via style+regex
- `src/parser/text_cleaner.py` — Text cleaning pipeline (page numbers, abbreviations, dashes)
- `src/parser/credits_generator.py` — Generate opening/closing credits automatically

**Key Features:**
- Detects chapters: "Chapter I.", "Chapter 1:", "CHAPTER ONE", etc.
- Skips: copyright, TOC, prefaces, thank you pages
- Handles Roman numeral conversion
- Cleans text for TTS (expands Dr.→Doctor, normalizes dashes)

**Acceptance:** Parse Sherlock Holmes manuscript (0906*), verify 12+ chapters, credits formatted correctly

---

### PROMPT-03: Parser API & Library Scanner
**File:** `PROMPT-03-PARSER-API.md`

Create API endpoints for library discovery and manuscript parsing.

**Endpoints:**
- `POST /api/library/scan` — Index all books in Formatted Manuscripts/
- `GET /api/library` — Get all books with filtering by status
- `GET /api/book/{id}` — Get single book detail
- `POST /api/book/{id}/parse` — Parse DOCX file and store chapters
- `GET /api/book/{id}/chapters` — List chapters with text
- `PUT /api/book/{id}/chapter/{n}/text` — Edit chapter text before generation

**Deliverables:**
- `src/api/library.py` — LibraryScanner for indexing ~873 manuscripts
- `src/api/routes.py` — FastAPI route handlers with Pydantic models
- Database integration with proper error handling

**Acceptance:** Scan library returns 873+ books, parse specific book returns chapters with opening/closing credits

---

### PROMPT-04: Library Home Page & Book Card Component
**File:** `PROMPT-04-LIBRARY-UI.md`

Create main library page with search, filtering, sorting, and book grid.

**Deliverables:**
- `frontend/src/pages/Library.jsx` — Home page with stats bar, search, filters
- `frontend/src/components/BookCard.jsx` — Reusable book card with status badge

**Features:**
- Search by title or author (instant filtering)
- Filter by status (Not Started, Parsed, Generating, Generated, QA, Exported)
- Sort by ID, title, author, page count
- Stats bar showing counts per status
- Color-coded status badges (gray, blue, amber, green, purple, yellow)
- Responsive grid (1-4 columns)

**Acceptance:** Fetch library from API, display grid, search/filter work, click navigates to `/book/{id}`

---

### PROMPT-05: Book Detail Page & Chapter Editing Interface
**File:** `PROMPT-05-BOOK-DETAIL-UI.md`

Create three-panel book detail layout for viewing, editing, and configuring narration.

**Deliverables:**
- `frontend/src/pages/BookDetail.jsx` — Three-panel layout
- `frontend/src/components/ChapterList.jsx` — Sidebar with chapters + status icons
- `frontend/src/components/TextPreview.jsx` — Center panel for viewing/editing text
- `frontend/src/components/NarrationSettings.jsx` — Right panel for voice config

**Panels:**
1. **Left:** Chapter list with status icons (not started, generating, complete, error, QA)
2. **Center:** Text preview/editor with Save button
3. **Right:** Voice dropdown, emotion presets, speed slider (0.8-1.3x), narration presets

**Acceptance:** Display book metadata, edit chapter text, update word count via API, narration settings persist

---

### PROMPT-06: TTS Engine Abstraction & Qwen3-TTS Adapter
**File:** `PROMPT-06-TTS-ENGINE-ADAPTER.md`

Create abstract TTS engine interface and concrete Qwen3-TTS adapter.

**Deliverables:**
- `src/engines/base.py` — Abstract TTSEngine class with interface
- `src/engines/qwen3_tts.py` — Qwen3-TTS concrete implementation using MLX
- `src/engines/chunker.py` — Text chunking at sentence boundaries + audio stitching
- `src/api/voice_lab.py` — Voice test endpoint

**Endpoints:**
- `POST /api/voice-lab/test` — Generate test audio with custom settings
- `GET /api/voice-lab/voices` — List available voices

**Features:**
- Load/unload TTS model
- List available voices (Ethan, Nova, Aria, Leo)
- Generate audio with emotion and speed control
- Chunk text at sentence boundaries
- Stitch chunks with 30ms crossfade
- RMS normalize audio

**Acceptance:** Generate "Hello, this is a test" with different voices, verify WAV validity

---

### PROMPT-07: Voice Lab UI & Audio Player
**File:** `PROMPT-07-VOICE-LAB-UI.md`

Create Voice Lab page for testing and fine-tuning narration voices.

**Deliverables:**
- `frontend/src/pages/VoiceLab.jsx` — Voice testing interface
- `frontend/src/components/AudioPlayer.jsx` — Interactive audio player with waveform
- `frontend/src/components/VoicePresetManager.jsx` — Save/load preset management

**Modes:**
- **Single Voice:** Test one voice, see/hear results
- **Compare:** A/B compare two voices with same text

**Features:**
- Text input (max 5000 chars) with counter
- Voice selector dropdown
- Emotion/style input with preset buttons (neutral, warm, dramatic, etc.)
- Speed slider (0.8-1.3x)
- Generate button → calls API → displays audio player
- Audio player: play/pause, progress bar, time display, waveform, download
- Save/load narration presets (localStorage)

**Acceptance:** Generate audio for both single and compare modes, audio player works, presets persist

---

### PROMPT-08: Audio Generation Pipeline & Job Queue
**File:** `PROMPT-08-GENERATION-PIPELINE.md`

Create backend generation pipeline with async job queue (no external broker).

**Deliverables:**
- `src/pipeline/generator.py` — AudiobookGenerator for chapter-by-chapter generation
- `src/pipeline/queue_manager.py` — GenerationQueue using asyncio (FIFO, single worker)
- `src/api/generation.py` — Generation API endpoints

**Endpoints:**
- `POST /api/book/{id}/generate` — Queue full book
- `POST /api/book/{id}/chapter/{n}/generate` — Queue single chapter
- `GET /api/job/{id}` — Get job status and progress (0-100%)
- `DELETE /api/job/{id}` — Cancel job
- `GET /api/book/{id}/chapter/{n}/audio` — Stream audio file

**Process:**
1. For each chapter: chunk text at sentences, generate audio via TTS, stitch chunks
2. Opening/closing credits: generated with slower speed (0.9x)
3. Regular chapters: generated at normal speed (1.0x)
4. Save to outputs/{book_id}-{slug}/chapters/{nn}-{title}.wav
5. Update chapter DB: audio_path, duration_seconds, status
6. Job queue tracks: queued → running → completed/failed

**Acceptance:** Generate opening credits + one chapter, verify WAV files created with correct names and durations

---

## Execution Order

Execute the prompts in this sequence:

1. **PROMPT-01** — Foundation (project structure, database, framework)
2. **PROMPT-02** — Core logic (manuscript parsing, text cleaning)
3. **PROMPT-03** — Backend APIs (library scanning, parsing endpoints)
4. **PROMPT-04** — Frontend discovery (library page, book grid)
5. **PROMPT-05** — Frontend interaction (book detail, text editing)
6. **PROMPT-06** — TTS integration (engine abstraction, Qwen3 adapter)
7. **PROMPT-07** — Voice testing (Voice Lab UI, audio player)
8. **PROMPT-08** — Generation system (generation pipeline, job queue)

---

## Key Conventions

**From CLAUDE.md:**

- **Python:** Type hints on all functions, FastAPI with Pydantic, SQLAlchemy ORM, async where appropriate
- **Frontend:** React functional components, Tailwind CSS, fetch() for API calls
- **Testing:** pytest for backend, integration tests for APIs, parsing tests
- **File Naming:** Output files: `outputs/{book_id}-{slug}/chapters/{nn}-{chapter-slug}.wav`
- **Git Commits:** Prefix with prompt number: `[PROMPT-01] Initial project scaffolding`
- **Narrator:** All audiobooks narrated by Kent Zimering
- **Database:** SQLite via SQLAlchemy, timestamps in UTC

---

## Reference Documents

- **Project Conventions:** `/sessions/focused-happy-pasteur/mnt/Coding Folder/Qwen3-TTS - Test/CLAUDE.md`
- **Full Specification:** `/sessions/focused-happy-pasteur/mnt/Coding Folder/Alexandria-Audiobook-Narrator-Specification.pdf` (if available)
- **Project State:** `PROJECT-STATE.md` (track progress across prompts)

---

## Quick Links

| Prompt | Files | Focus |
|--------|-------|-------|
| 01 | src/main.py, database.py, frontend/ | Scaffolding |
| 02 | src/parser/ | Parsing |
| 03 | src/api/library.py, routes.py | API |
| 04 | frontend/pages/Library.jsx, BookCard.jsx | Discovery UI |
| 05 | frontend/pages/BookDetail.jsx, components/ | Editing UI |
| 06 | src/engines/base.py, qwen3_tts.py | TTS Core |
| 07 | frontend/pages/VoiceLab.jsx, AudioPlayer.jsx | Testing UI |
| 08 | src/pipeline/generator.py, queue_manager.py | Generation |

---

**Total Scope:** 8 prompts, ~873 books, ~12 chapters per book, local TTS, modern React UI

Good luck! Execute these prompts sequentially and reference CLAUDE.md for all conventions.
