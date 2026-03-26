# Qwen3-TTS: Production Hardening — Disk Monitor, Audio Preview, Alerting, CORS

## Context
This is the Qwen3-TTS Audiobook Narrator project at `ltvspot/qwen3-tts-apple-silicon` (branch: `master`). It's a FastAPI + React app running locally on macOS at `http://localhost:8080`. Read `CLAUDE.md` if it exists.

**CRITICAL:** This repo has a known file corruption issue. After ANY git operation, run:
```bash
find . -name '*.py' -empty -not -path './.venv*'
```
If any files are found, restore them with: `rm -f <file> && git show HEAD:<file> > <file>`
Always verify the server starts before claiming success: `python -c 'from src.main import app; print("OK")'`

## Tasks

### Task 1: Disk Space Monitoring

**File:** `src/health_checks.py`

Add a new health check:
```python
def check_disk_space() -> HealthCheckResult:
    """Check available disk space for output directory."""
    import shutil
    output_dir = settings.output_dir or "outputs"
    total, used, free = shutil.disk_usage(output_dir)
    percent_used = (used / total) * 100

    if percent_used > 95:
        return HealthCheckResult(
            name="Disk Space",
            status="fail",
            detail=f"CRITICAL: {percent_used:.1f}% used, {free // (1024**3)}GB free",
            critical=True
        )
    elif percent_used > 90:
        return HealthCheckResult(
            name="Disk Space",
            status="warn",
            detail=f"WARNING: {percent_used:.1f}% used, {free // (1024**3)}GB free",
            critical=False
        )
    else:
        return HealthCheckResult(
            name="Disk Space",
            status="pass",
            detail=f"{percent_used:.1f}% used, {free // (1024**3)}GB free",
            critical=False
        )
```

Register this check in the startup health checks list.

Also add to the `/api/health` response:
```python
"disk": {
    "total_gb": total // (1024**3),
    "free_gb": free // (1024**3),
    "percent_used": round(percent_used, 1)
}
```

Add a pre-batch check in the batch processing logic:
- Before starting a batch, estimate total output size: `estimated_gb = num_chapters * avg_chapter_duration_seconds * 0.0001` (rough WAV estimate)
- If `estimated_gb > free_gb * 0.8`, return an error: "Insufficient disk space for batch. Estimated {estimated_gb}GB needed, {free_gb}GB available."

### Task 2: In-Browser Audio Preview

**File:** `src/api/routes.py`

Add a streaming audio endpoint:
```python
@router.get("/api/book/{book_id}/chapter/{chapter_number}/preview")
async def preview_chapter_audio(book_id: int, chapter_number: int):
    """Stream chapter audio for in-browser preview."""
    # Find the generated audio file for this chapter
    # Return it as a streaming response with proper Content-Type
    audio_path = find_chapter_audio(book_id, chapter_number)
    if not audio_path or not audio_path.exists():
        raise HTTPException(404, "Audio not yet generated for this chapter")

    return FileResponse(
        audio_path,
        media_type="audio/wav",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache"
        }
    )
```

**File:** `frontend/src/pages/` (or wherever the chapter detail page is)

Add an audio player widget to the chapter detail view:
```jsx
{chapter.audioGenerated && (
    <div className="audio-preview">
        <h4>Audio Preview</h4>
        <audio
            controls
            preload="metadata"
            src={`/api/book/${bookId}/chapter/${chapter.number}/preview`}
        >
            Your browser does not support audio playback.
        </audio>
        <p className="text-sm text-gray-500">
            Duration: {formatDuration(chapter.audioDuration)}
        </p>
    </div>
)}
```

### Task 3: Error Alerting via macOS Notifications

**File:** `src/pipeline/generator.py` (or a new `src/notifications.py`)

Create a notification system:
```python
import subprocess
import platform

def send_macos_notification(title: str, message: str, sound: str = "default"):
    """Send a native macOS notification."""
    if platform.system() != "Darwin":
        return
    script = f'''display notification "{message}" with title "{title}" sound name "{sound}"'''
    try:
        subprocess.run(["osascript", "-e", script], timeout=5, capture_output=True)
    except Exception:
        pass  # Notifications are best-effort

# Usage points:
# 1. When a batch completes: send_macos_notification("Audiobook Narrator", f"Batch complete: {success}/{total} chapters generated")
# 2. When a chapter fails QA: send_macos_notification("QA Alert", f"Chapter {n} failed quality check: {reason}")
# 3. When disk space is low: send_macos_notification("Disk Warning", f"Only {free_gb}GB remaining")
# 4. When all chapters of a book are done: send_macos_notification("Book Complete", f"'{book_title}' is ready for export")
```

Wire notifications into:
1. `generator.py` — after batch completion
2. `qa_checker.py` — on QA failures
3. `health_checks.py` — on disk space warnings
4. The batch endpoint handler — on batch start/complete/error

### Task 4: CORS Hardening

**File:** `src/main.py`

Update the CORS configuration:
```python
from fastapi.middleware.cors import CORSMiddleware

# Only allow localhost origins
ALLOWED_ORIGINS = [
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "http://localhost:3000",  # In case translator frontend needs to call
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
    max_age=3600,
)
```

Also add security headers middleware:
```python
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response
```

### Task 5: File Integrity Self-Check

**File:** `src/health_checks.py`

Add a file integrity health check that detects the known corruption pattern:
```python
def check_file_integrity() -> HealthCheckResult:
    """Detect zero-byte source files (known corruption pattern)."""
    import os
    src_dir = Path(__file__).parent
    empty_files = []
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in ('__pycache__', '.venv')]
        for f in files:
            if f.endswith('.py'):
                fp = Path(root) / f
                if fp.stat().st_size == 0:
                    empty_files.append(str(fp.relative_to(src_dir.parent)))

    if empty_files:
        return HealthCheckResult(
            name="File Integrity",
            status="fail",
            detail=f"CORRUPTED: {len(empty_files)} empty .py files: {', '.join(empty_files[:5])}",
            critical=True
        )
    return HealthCheckResult(name="File Integrity", status="pass", detail="All source files intact", critical=False)
```

### Task 6: Tests

- Test disk space health check with mocked `shutil.disk_usage`
- Test audio preview endpoint returns audio data (or 404 if not generated)
- Test notification function doesn't crash on non-macOS
- Test CORS headers are present in responses
- Test file integrity check detects empty files
- Run ALL existing tests to verify no regressions

### Task 7: Commit and Push

- `git add -A && git commit -m "Add disk monitoring, audio preview, alerting, CORS hardening, file integrity check"`
- `git push origin master`
- Also push to fork: `git push ltvspot master` (remote `ltvspot` points to `https://github.com/ltvspot/qwen3-tts-apple-silicon.git`)
- Verify server starts: `python main.py`
- Verify health: `curl http://localhost:8080/api/health`
- Run tests: `python -m pytest tests/ -v`
- Report webapp link: http://localhost:8080
