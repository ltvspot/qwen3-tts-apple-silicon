# Alexandria Audiobook Narrator — Codex Conventions

## Project Overview

This is the **Alexandria Audiobook Narrator** — a local web application that transforms formatted manuscripts (DOCX/EPUB/PDF) into production-ready audiobooks using local TTS models. It serves 873 titles from the Library of Alexandria publishing catalog.

## Architecture

- **Backend:** Python 3.11+ / FastAPI (runs on localhost:8080)
- **Frontend:** React + Tailwind CSS (runs on localhost:3000, proxied through FastAPI in production)
- **Database:** SQLite via SQLAlchemy
- **TTS Engine:** Abstracted engine layer — Day 1: Qwen3-TTS (MLX) for Apple Silicon
- **Audio Processing:** pydub + ffmpeg
- **Manuscript Parsing:** python-docx (primary), ebooklib (EPUB fallback), pdfplumber (PDF fallback)

## Key Directories

```
src/                    # Backend Python code
  parser/               # Manuscript parsing (DOCX, EPUB, PDF)
  engines/              # TTS engine abstraction + adapters
  pipeline/             # Generation, queue, export, QA
  api/                  # FastAPI route handlers
frontend/               # React frontend
  src/pages/            # Page components
  src/components/       # Reusable UI components
tests/                  # pytest tests
outputs/                # Generated audiobook files
voices/                 # Cloned voice references
models/                 # TTS model files (Qwen3-TTS MLX)
Formatted Manuscripts/  # Source manuscripts (873 folders)
Codex Prompts/          # Prompt files from Claude
```

## Coding Standards

### Python
- Python 3.11+ with type hints on all functions
- FastAPI with Pydantic models for request/response validation
- SQLAlchemy ORM for all database access
- Async endpoints where appropriate (especially for long-running operations)
- All imports at top of file, stdlib first, then third-party, then local
- Use `logging` module, not `print()` for production code
- Docstrings on all public functions

### Frontend
- React functional components with hooks
- Tailwind CSS for styling — no separate CSS files
- Use fetch() for API calls with proper error handling
- Components in `frontend/src/components/`, pages in `frontend/src/pages/`

### Testing
- pytest for all backend tests
- Every new feature must include tests
- Parser tests: verify correct chapter detection against known manuscripts
- API tests: verify endpoints return correct status codes and data shapes
- Engine tests: verify audio generation produces valid WAV files

## Critical Business Rules

### Narration Structure
Every audiobook follows this exact structure:
1. **Opening Credits** (auto-generated): "This is [Title]. [Subtitle]. Written by [Author]. Narrated by Kent Zimering."
2. **Introduction** (if present in manuscript): Narrated as Chapter 0
3. **Chapters** (I, II, III...): Each chapter = one audio file
4. **Closing Credits** (auto-generated): "This was [Title]. [Subtitle]. Written by [Author]. Narrated by Kent Zimering."

### Skip Rules (NEVER narrate these)
- Title page (extract metadata only)
- Copyright page
- Table of Contents (use for validation, not narration)
- "Preface — Message to the Reader" (Alexandria marketing content)
- "Thank You for Reading" (back matter)

### Narrator
- All titles narrated by: **Kent Zimering**
- Default voice: Ethan (Qwen3-TTS) until Kent Zimering voice clone is available

## TTS Engine Contract

Every TTS engine adapter must implement the abstract interface in `src/engines/base.py`. This enables swapping engines without changing any other code. See the specification PDF for the full interface.

## File Naming

Output audio files follow this pattern:
```
outputs/{book_id}-{slug}/
  chapters/
    00-opening-credits.wav
    01-introduction.wav
    02-ch01-{chapter-slug}.wav
    ...
    XX-closing-credits.wav
  exports/
    {Title}.mp3
    {Title}.m4b
```

## Commit Convention

- Prefix commits with the prompt number: `[PROMPT-01] Initial project scaffolding`
- Always run tests before committing
- Never commit broken code

## Reference Documents

- Full specification: `Alexandria-Audiobook-Narrator-Specification.pdf` (in parent Coding Folder)
- Individual prompt files: `Codex Prompts/PROMPT-XX-*.md`
- Project state: `PROJECT-STATE.md` (this repo)
