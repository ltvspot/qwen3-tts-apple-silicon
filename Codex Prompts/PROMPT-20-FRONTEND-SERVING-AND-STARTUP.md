# PROMPT-20: Wire Frontend into FastAPI + Startup Script

## Context

The FastAPI backend runs on port 8080, and the React frontend is fully built at `frontend/build/`. However, **there is zero static file serving** in `src/main.py` — no `StaticFiles` mount, no SPA catch-all route. Visiting `localhost:8080` returns `{"detail":"Not Found"}`.

The app must serve the frontend from FastAPI on a single port (8080) so Tim can simply run one command and open `localhost:8080` in his browser.

## Task 1: Serve the Frontend from FastAPI

**File: `src/main.py`**

Add static file serving and an SPA catch-all route. This MUST be added **after** all API routers to avoid intercepting API calls.

### 1a. Add imports

At the top of `src/main.py`, add:

```python
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
```

### 1b. Add a config setting for the frontend build path

**File: `src/config.py`**

Add to the `RuntimeSettings` class:

```python
FRONTEND_BUILD_DIR: str = "./frontend/build"
```

### 1c. Add static file serving and SPA catch-all

At the **bottom** of `src/main.py`, **after** the health check endpoint and **before** the `if __name__ == "__main__"` block, add:

```python
# --- Frontend static file serving ---
_frontend_build = Path(settings.FRONTEND_BUILD_DIR)

if _frontend_build.exists() and (_frontend_build / "index.html").exists():
    # Serve /static/js/*, /static/css/*, etc.
    app.mount("/static", StaticFiles(directory=str(_frontend_build / "static")), name="frontend-static")

    @app.get("/asset-manifest.json")
    async def asset_manifest():
        return FileResponse(str(_frontend_build / "asset-manifest.json"))

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Catch-all: serve index.html for any non-API route (React Router handles client routing)."""
        file_path = _frontend_build / full_path
        if full_path and file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(_frontend_build / "index.html"))
else:
    logger.warning(
        "Frontend build not found at %s — run 'cd frontend && npm run build' first. "
        "API endpoints are still available.",
        _frontend_build,
    )
```

**Key requirements:**
- The catch-all route MUST be the very last route defined (after all routers and the health check)
- The `app.mount("/static", ...)` MUST come before the catch-all
- If `frontend/build/` doesn't exist, log a warning but keep the API functional
- The catch-all must check if the requested path is an actual file first (for favicon.ico, manifest.json, etc.), then fall back to index.html for React Router

### 1d. Verification

After making changes:
1. `pytest tests/` must still pass — the catch-all should not break existing API tests
2. Starting the server with `python src/main.py` and opening `http://localhost:8080` should show the React frontend
3. `http://localhost:8080/api/health` must still return the health check JSON
4. `http://localhost:8080/static/js/main.*.js` must serve the JavaScript bundle
5. `http://localhost:8080/library` (or any React route) must return index.html (SPA routing)

## Task 2: Create a Startup Script

**File: `start.sh`** (project root)

Create a single startup script that:
1. Activates the venv if it exists
2. Checks that `frontend/build/` exists, and if not, builds the frontend
3. Starts the FastAPI server

```bash
#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "=== Alexandria Audiobook Narrator ==="

# Activate venv
if [ -d ".venv" ]; then
    source .venv/bin/activate
    echo "✓ Virtual environment activated"
elif [ -d "venv" ]; then
    source venv/bin/activate
    echo "✓ Virtual environment activated"
else
    echo "⚠ No virtual environment found — using system Python"
fi

# Check frontend build
if [ ! -f "frontend/build/index.html" ]; then
    echo "→ Frontend not built. Building now..."
    cd frontend
    npm install --silent
    npm run build
    cd ..
    echo "✓ Frontend built"
else
    echo "✓ Frontend build found"
fi

# Start the server
echo "→ Starting server on http://localhost:8080"
echo "  Open your browser to: http://localhost:8080"
echo ""
python src/main.py
```

Make it executable: `chmod +x start.sh`

## Task 3: Add Tests for Frontend Serving

**File: `tests/test_frontend_serving.py`** (new file)

```python
"""Tests for frontend static file serving."""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


def test_health_endpoint_still_works(client):
    """API health check must not be intercepted by the SPA catch-all."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


def test_api_routes_not_intercepted(client):
    """API routes must still return JSON, not index.html."""
    resp = client.get("/api/library/books")
    # Should be 200 with JSON or possibly empty list — but NOT index.html
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("application/json")


def test_root_serves_frontend_or_404(client):
    """Root path should serve index.html if frontend build exists."""
    resp = client.get("/")
    # Either 200 (frontend served) or 404 (no build dir in test env)
    assert resp.status_code in (200, 404)


def test_unknown_path_serves_spa_or_404(client):
    """Unknown non-API paths should serve index.html (SPA) or 404."""
    resp = client.get("/library")
    assert resp.status_code in (200, 404)
    # If 200, it should be HTML not JSON
    if resp.status_code == 200:
        content_type = resp.headers.get("content-type", "")
        assert "html" in content_type or "octet-stream" in content_type
```

## Commit Message

```
[PROMPT-20] Wire frontend into FastAPI + startup script

- Mount StaticFiles for frontend/build/static/ assets
- Add SPA catch-all route serving index.html for React Router
- Add FRONTEND_BUILD_DIR config setting
- Create start.sh one-command startup script
- Add tests for frontend serving + API route isolation
- Graceful fallback if frontend build doesn't exist
```

## Final Checklist

- [ ] `src/main.py` serves frontend from `frontend/build/`
- [ ] `src/config.py` has `FRONTEND_BUILD_DIR` setting
- [ ] `start.sh` exists and is executable
- [ ] All existing tests still pass
- [ ] New frontend-serving tests pass
- [ ] `localhost:8080` shows the React UI
- [ ] `localhost:8080/api/health` still returns JSON
- [ ] SPA routing works (e.g., `/library` serves index.html)
