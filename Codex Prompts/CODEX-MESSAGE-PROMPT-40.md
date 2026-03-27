# Codex Message for PROMPT-40

Read the following files in order:

1. `CLAUDE.md` (repo root)
2. `Codex Prompts/PROMPT-40-AUTOMATED-AUDIO-QA-PIPELINE.md`

Then implement all 5 tasks described in PROMPT-40 one by one:
1. Install dependencies and create module structure
2. Transcription accuracy checker (mlx-whisper + WER)
3. Timing and pacing analyzer (librosa)
4. Audio quality analyzer (LUFS, SNR, artifacts)
5. QA scorer and integration (scoring + API + frontend)

Run tests after each task. Commit and push when all done.

IMPORTANT: If mlx-whisper or librosa fail to install due to Python version issues, document the error and implement the module with graceful import fallbacks (try/except ImportError with clear error messages).
