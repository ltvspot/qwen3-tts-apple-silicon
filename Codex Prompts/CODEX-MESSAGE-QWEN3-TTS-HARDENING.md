Read Codex Prompts/QWEN3-TTS-PROMPT-HARDENING.md, then implement all 7 tasks.

This is the Qwen3-TTS Audiobook Narrator project. Key requirements:
1. Add disk space health check (warn >90%, fail >95%, pre-batch estimation)
2. Add audio preview endpoint + in-browser `<audio>` player on chapter detail page
3. Add macOS notification system via osascript — batch complete, QA failures, disk warnings, book complete
4. Harden CORS — localhost-only origins, security headers (X-Content-Type-Options, X-Frame-Options, X-XSS-Protection)
5. Add file integrity health check detecting zero-byte .py files (known corruption pattern)
6. Write tests, run ALL tests, commit and push to both `origin master` and `ltvspot master`

CRITICAL WARNING: This repo has a file corruption bug. After ANY git operation:
- Run: find . -name '*.py' -empty -not -path './.venv*'
- If any found: rm -f <file> && git show HEAD:<file> > <file>
- Always verify: python -c 'from src.main import app; print("OK")'

After completing, verify server starts: python main.py → curl http://localhost:8080/api/health
Include link in your completion: http://localhost:8080
