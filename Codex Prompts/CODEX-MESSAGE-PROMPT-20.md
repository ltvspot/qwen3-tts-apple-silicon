Read `Codex Prompts/PROMPT-20-FRONTEND-SERVING-AND-STARTUP.md` and implement all tasks exactly as specified. The core issue: FastAPI has zero static file serving — no StaticFiles mount, no SPA catch-all. Visiting localhost:8080 returns {"detail":"Not Found"} because the frontend build at frontend/build/ is never wired into the app.

Key changes:
1. Add `StaticFiles` mount + SPA catch-all route to `src/main.py` (AFTER all API routers)
2. Add `FRONTEND_BUILD_DIR` setting to `src/config.py`
3. Create `start.sh` startup script
4. Add `tests/test_frontend_serving.py`

CRITICAL: The catch-all route MUST be the very last route. API routes (/api/*) must NOT be intercepted. Run the full test suite to verify no regressions.
