# Codex Message for PROMPT-41

**Paste this into the Codex chatbox after PROMPT-40 completes:**

---

Please implement PROMPT-41: Production Scale Infrastructure. The full spec is at `Codex Prompts/PROMPT-41-PRODUCTION-SCALE-INFRASTRUCTURE.md`.

This prompt has 6 tasks:

1. **Credits Loudness Normalization** — After generating credits audio, apply the same normalization pipeline as chapter audio. Target -18.5 LUFS for all raw WAVs. Add post-generation loudness check: if any WAV deviates > 1.5 LU from mean, re-normalize. Log warning if credits deviate > 1 LU from chapter mean. Files: `src/pipeline/generator.py`, `src/engines/qwen3_tts.py`.

2. **Model Cooldown and Restart Logic** — Track chapters since last model load. After every MODEL_RESTART_INTERVAL (default 50, env TTS_MODEL_RESTART_INTERVAL) chapters: clear lru_cache, gc.collect(), mlx.core.metal.clear_cache() if available, lazy reload on next call. Add `/api/system/model-status` endpoint. Queue manager pauses during restart (max 10s). Files: `src/engines/qwen3_tts.py`, `src/pipeline/queue_manager.py`.

3. **Resource Monitoring System** — New `src/monitoring/resource_monitor.py` with ResourceMonitor class tracking disk space, process memory, throughput (chapters/hour rolling 1hr), output directory size. Pre-generation gate: disk >= 2GB, memory < 80% system RAM. API endpoints: `/api/system/resources`, `/api/system/resources/history`. Frontend widget with color-coded status.

4. **Pronunciation Dictionary** — New `src/engines/pronunciation_dictionary.py` and `data/pronunciation.json`. JSON dict with "global" and "per_book" sections. Pre-processing in chunker replaces words with phonetic respellings before TTS. CRUD API endpoints under `/api/pronunciation`. Frontend "Pronunciation" tab in Settings. Auto-detection: suggest adding words with WER mismatch that look like proper nouns.

5. **Batch Generation Orchestration** — New BatchRun model and `src/api/routes/batch_routes.py`. API: POST /api/batch/start, GET /api/batch/{id}, pause/resume/cancel. Sequential book processing, auto-QA after each book, model cooldown integration, resource checks, ETA tracking. Frontend "Batch Production" page with progress, controls, per-book status.

6. **Batch QA Approval and Export** — POST /api/qa/batch-approve (approve all chapters in a book >= threshold), POST /api/qa/batch-approve-all (catalog-wide). POST /api/export/batch (queue export for multiple books, sequential, skip non-approved). Auto-run QA on exported MP3. Frontend "Approve All Passing" and "Export All Ready" buttons.

Important notes:
- Python 3.11 compatibility required (no 3.12+ features like `type` keyword)
- All existing tests must pass (expected 410+ after PROMPT-39 and PROMPT-40)
- Add minimum 25 new tests across all 6 tasks
- Run `python -m pytest tests/ -x -q` to verify
- This builds on PROMPT-39 (bug fixes) and PROMPT-40 (QA pipeline) — both must be complete first
- The credits loudness issue was found via actual audio analysis: credits are -16 to -17 LUFS while chapters are -18 to -18.9 LUFS

Branch: `master`
