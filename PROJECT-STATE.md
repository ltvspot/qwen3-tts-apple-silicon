# Alexandria Audiobook Narrator — Project State

**Owner:** Tim (tim@ltvspot.com)  
**Last Updated:** 2026-03-25  
**Status:** Prompts 01-30 COMPLETE in the local tree.

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

- Backend: `./.venv/bin/pytest -q` → `263 passed`
- Frontend tests: `cd frontend && CI=true npm test -- --watchAll=false` → `13 suites, 50 tests passed`
- Frontend production build: `cd frontend && npm run build` → passed
- Manual browser smoke: Library, Queue, QA, Book Detail, and Overseer were loaded against the live local app and checked for console errors

## Prompt Coverage

- Prompts 01-16: Core platform, parsing, generation, QA, export, settings, voice lab, and baseline hardening
- Prompts 17-20: Production hardening, scale features, critical bug fixes, frontend serving, and startup/runtime integration
- Prompts 21-30: Advanced hardening, heartbeats, Gate 1/2/3 QA, mastering, crash recovery, batch hardening, model-specific mitigations, and production overseer

## Operator Notes

- The repo should stay clean during normal operation once ignored runtime artifacts are excluded from git status.
- Git push to `origin` still depends on GitHub write access for the active credential.
- The next highest-value validation is long-form production sampling on populated books, especially around export readiness semantics, mastering consistency, and QA/operator workflows on real batches.

## Mandatory QA Loop

After every meaningful change:

1. Run backend tests.
2. Run frontend tests.
3. Build the frontend.
4. Smoke the changed flows in a browser against the live app.
5. Report what passed, what failed, and what still needs hardening.
