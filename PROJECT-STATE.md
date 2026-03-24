# Alexandria Audiobook Narrator — Project State

**Owner:** Tim (tim@ltvspot.com)
**Last Updated:** 2026-03-24
**Status:** Active Development (Prompts 01 and 04 Complete)

---

## Current State

Prompts 01 and 04 are implemented. The project now has a FastAPI scaffold, SQLite schema, pytest coverage for the initial backend surface, and a functional React library view with book cards, search, status filtering, sorting, scan refresh, and frontend test coverage. The `Formatted Manuscripts/` folder contains 873 manuscript folders, each with DOCX/EPUB/PDF files. Three Qwen3-TTS MLX models (1.7B 8-bit) are downloaded in `models/`.

### What Exists
- `main.py` — Compatibility runner that boots the FastAPI app
- `src/` — FastAPI backend scaffold, configuration, and SQLAlchemy models
- `frontend/` — React + Tailwind frontend shell with page stubs
- `tests/` — Pytest fixtures plus health and database tests
- `models/` — Three 1.7B MLX models (~2.9GB each): CustomVoice, VoiceDesign, Base
- `Formatted Manuscripts/` — 873 manuscript folders with DOCX/EPUB/PDF files
- `voices/` — Directory for cloned voice references
- `outputs/` — Directory for generated audio
- `CLAUDE.md` — Codex conventions and project rules
- `Codex Prompts/` — All 16 prompt files for development

### What Needs to Be Built
- FastAPI backend with REST API
- React frontend dashboard
- DOCX manuscript parser
- TTS engine abstraction layer with Qwen3-TTS adapter
- Generation pipeline with queue management
- Automated QA system
- Audio export pipeline (MP3/M4B)

---

## Prompt Tracker

| # | Name | Status | Notes |
|---|------|--------|-------|
| 01 | Project Scaffolding | COMPLETE | FastAPI + React skeleton, DB schema committed |
| 02 | DOCX Manuscript Parser | NOT STARTED | Chapter detection, text cleaning |
| 03 | Parser API Integration | NOT STARTED | REST endpoints for parsing |
| 04 | Library UI | COMPLETE | Book grid, search, filter, sorting, stats, frontend tests |
| 05 | Book Detail UI | NOT STARTED | Chapter list, text preview |
| 06 | TTS Engine + Qwen3 Adapter | NOT STARTED | Abstract interface, MLX adapter |
| 07 | Voice Lab UI | NOT STARTED | Test voices, A/B compare |
| 08 | Generation Pipeline | NOT STARTED | Background tasks, chunking |
| 09 | Generation UI + Player | NOT STARTED | Progress, audio player |
| 10 | Production Queue | NOT STARTED | Queue management, ETA |
| 11 | Automated QA System | NOT STARTED | Duration, clipping, silence checks |
| 12 | Export Pipeline | NOT STARTED | MP3/M4B encoding, metadata |
| 13 | Settings + Configuration | NOT STARTED | Persist settings, engine config |
| 14 | Voice Cloning Integration | NOT STARTED | Reference audio, clone voices |
| 15 | EPUB + PDF Parsers | NOT STARTED | Fallback parsers |
| 16 | Polish + Hardening | NOT STARTED | Error handling, logging |

---

## Workflow Rules

Same as the Alexandria Cover Designer project:
- **Claude (Cowork)** = CEO / Project Manager — writes prompts, reviews output, manages state
- **Codex** = Senior Developer — implements code, runs tests, commits
- **Tim** = Founder / Product Owner — provides requirements, final approval

Claude NEVER writes production code directly. All code changes go through Codex prompts.
