# Alexandria Audiobook Narrator

Local audiobook production software for the Alexandria publishing catalog. The application ingests formatted manuscripts, parses chapter structure, generates narration with local TTS engines, and prepares titles for QA and export.

## Current Scope

- Backend: FastAPI service on `http://localhost:8080`
- Frontend: React + Tailwind shell in `frontend/`
- Database: SQLite schema for books, chapters, voice presets, and generation jobs
- Delivery plan: 16 prompt-driven milestones in `Codex Prompts/`

## Project Layout

```text
src/                    FastAPI backend code
frontend/               React frontend shell
tests/                  Pytest suite
Formatted Manuscripts/  Source manuscripts
models/                 Local TTS model downloads
outputs/                Generated audiobook assets
voices/                 Voice references
Codex Prompts/          16-prompt implementation plan
```

## Quick Start

### 1. Backend setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
brew install ffmpeg
```

### 2. Start the API

```bash
source .venv/bin/activate
python src/main.py
```

### 3. Frontend setup

```bash
cd frontend
nvm use
npm ci
npm run build
```

## Implemented in Prompt 01

- FastAPI app skeleton with CORS, startup initialization, logging, and JSON error handling
- SQLite schema for `books`, `chapters`, `voice_presets`, and `generation_jobs`
- React application shell with routed page stubs
- Pytest coverage for the health endpoint and core database CRUD

## Next Steps

Prompt 02 builds the DOCX parser and chapter extraction pipeline on top of this scaffold.
