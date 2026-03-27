# Codex Message for PROMPT-39

Read the following files in order:

1. `CLAUDE.md` (repo root)
2. `Codex Prompts/PROMPT-39-CRITICAL-BUGS-AND-PRODUCTION-HARDENING.md`

Then implement all 6 tasks described in PROMPT-39 one by one:
1. Fix speed control bug (BUG-01)
2. Fix audio normalization clipping (BUG-02)
3. Fix chapter status race condition (BUG-04)
4. Sentence chunker improvements (abbreviations, skip rules, UTF-8)
5. Chunk-level audio validation
6. Generation timeout

Run tests after each task. Commit and push when all done.
