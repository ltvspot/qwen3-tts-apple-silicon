# PROMPT-16: Polish & Production Hardening

**Objective:** Harden the entire application for production with comprehensive error handling, logging, health checks, and graceful degradation patterns.

**Owner:** Codex
**Reference:** CLAUDE.md, PROJECT-STATE.md, All previous prompts (01-15)

---

## Scope

### Comprehensive Error Handling

#### File: `src/api/error_handlers.py` (NEW)

**Global Exception Handler:**
```python
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
import logging
import traceback

logger = logging.getLogger(__name__)

async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Global exception handler for all unhandled exceptions.
    Log error and return user-friendly response.
    """
    # Log full error details
    logger.error(f"Unhandled exception: {exc}", exc_info=True)

    # Return appropriate HTTP response
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": "An unexpected error occurred. Please try again.",
            "request_id": request.headers.get("X-Request-ID", "unknown")
        }
    )

async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle Pydantic validation errors with helpful messages."""
    logger.warning(f"Validation error: {exc}")

    return JSONResponse(
        status_code=422,
        content={
            "error": "Validation error",
            "detail": str(exc),
            "fields": [
                {
                    "field": err.get("loc", ["unknown"])[1],
                    "message": err.get("msg", "Invalid value")
                }
                for err in exc.errors()
            ]
        }
    )

# Register handlers
def register_error_handlers(app: FastAPI):
    """Register all error handlers on FastAPI app."""
    app.add_exception_handler(Exception, global_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
```

#### API Endpoint Error Wrapping Pattern

All endpoints should follow this pattern:

```python
@router.post("/book/{id}/generate")
async def generate_chapter(id: int, chapter_n: int) -> dict:
    """Generate a chapter with comprehensive error handling."""
    try:
        # Validate inputs
        book = db.query(Book).filter(Book.id == id).first()
        if not book:
            raise HTTPException(status_code=404, detail="Book not found")

        chapter = db.query(Chapter).filter(
            Chapter.book_id == id,
            Chapter.chapter_n == chapter_n
        ).first()
        if not chapter:
            raise HTTPException(status_code=404, detail="Chapter not found")

        # Actual work
        logger.info(f"Starting generation: book {id}, chapter {chapter_n}")
        result = await generation_service.generate(id, chapter_n)

        logger.info(f"Generation complete: {id}/{chapter_n}")
        return {
            "success": True,
            "book_id": id,
            "chapter_n": chapter_n,
            "duration": result.duration_seconds
        }

    except HTTPException:
        raise  # Re-raise HTTP exceptions as-is
    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        raise HTTPException(status_code=400, detail="Audio file not found during generation")
    except Exception as e:
        logger.error(f"Generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Generation failed")
```

### Logging Configuration

#### File: `src/logging_config.py` (NEW)

```python
import logging
import logging.handlers
from pathlib import Path
import json

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

def configure_logging(level: str = "INFO"):
    """
    Configure application-wide logging with file rotation.

    Logs go to:
    - logs/app.log (general application logs)
    - logs/api.log (API request/response logs)
    - logs/generation.log (TTS generation details)
    - logs/error.log (errors and exceptions)
    """

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Log format
    log_format = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # File handler with rotation (10MB max, keep 5 backups)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "app.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5
    )
    file_handler.setFormatter(log_format)
    root_logger.addHandler(file_handler)

    # API request logger
    api_logger = logging.getLogger("api_requests")
    api_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "api.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=3
    )
    api_handler.setFormatter(log_format)
    api_logger.addHandler(api_handler)

    # Generation logger
    gen_logger = logging.getLogger("generation")
    gen_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "generation.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=3
    )
    gen_handler.setFormatter(log_format)
    gen_logger.addHandler(gen_handler)

    # Error logger (always logs errors)
    error_logger = logging.getLogger("errors")
    error_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "error.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(log_format)
    error_logger.addHandler(error_handler)

    # Console handler for development
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_format)
    root_logger.addHandler(console_handler)

    logging.info("Logging configured")
```

#### API Request Logging Middleware

**File: `src/api/middleware.py`**

```python
from fastapi import Request
from datetime import datetime
import logging
import time
import json

api_logger = logging.getLogger("api_requests")

async def log_request_middleware(request: Request, call_next):
    """Log all API requests and responses."""
    start_time = time.time()
    request_id = request.headers.get("X-Request-ID", "no-id")

    # Log request
    api_logger.info(f"[{request_id}] {request.method} {request.url.path}")

    try:
        response = await call_next(request)
    except Exception as e:
        # Log error response
        process_time = time.time() - start_time
        api_logger.error(
            f"[{request_id}] {request.method} {request.url.path} "
            f"ERROR ({process_time:.3f}s): {e}"
        )
        raise

    # Log response
    process_time = time.time() - start_time
    api_logger.info(
        f"[{request_id}] {request.method} {request.url.path} "
        f"{response.status_code} ({process_time:.3f}s)"
    )

    return response
```

### Startup Health Checks

#### File: `src/health_checks.py` (NEW)

```python
from pathlib import Path
import logging
import subprocess
from src.database import SessionLocal, engine
from src.config import get_settings_manager

logger = logging.getLogger(__name__)

class HealthCheckError(Exception):
    """Raised when a health check fails."""
    pass

async def check_database_connection():
    """Verify database is accessible."""
    try:
        db = SessionLocal()
        db.execute("SELECT 1")
        db.close()
        logger.info("✓ Database connection OK")
    except Exception as e:
        raise HealthCheckError(f"Database connection failed: {e}")

async def check_model_files_exist():
    """Verify TTS model files are present."""
    try:
        manager = get_settings_manager()
        settings = manager.get_settings()
        model_path = Path(settings.engine_config.model_path)

        if not model_path.exists():
            raise HealthCheckError(
                f"TTS model not found: {model_path}\n"
                f"Download models or update Settings.engine_config.model_path"
            )

        logger.info(f"✓ TTS model found: {model_path}")
    except Exception as e:
        raise HealthCheckError(f"Model check failed: {e}")

async def check_ffmpeg_installed():
    """Verify ffmpeg is installed and accessible."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=5
        )
        if result.returncode != 0:
            raise HealthCheckError("ffmpeg not working")

        logger.info("✓ ffmpeg installed and working")
    except FileNotFoundError:
        raise HealthCheckError(
            "ffmpeg not found. Install with: brew install ffmpeg (macOS) "
            "or apt-get install ffmpeg (Linux)"
        )
    except Exception as e:
        raise HealthCheckError(f"ffmpeg check failed: {e}")

async def check_manuscript_folder_exists():
    """Verify manuscript source folder exists."""
    try:
        manager = get_settings_manager()
        settings = manager.get_settings()
        ms_folder = Path(settings.manuscript_source_folder)

        if not ms_folder.exists():
            logger.warning(
                f"⚠ Manuscript folder not found: {ms_folder}\n"
                f"Configure in Settings page or update manuscript_source_folder"
            )
        else:
            logger.info(f"✓ Manuscript folder found: {ms_folder}")
    except Exception as e:
        logger.error(f"Manuscript folder check failed: {e}")

async def check_output_directory_writable():
    """Verify output directory is writable."""
    try:
        output_dir = Path("outputs")
        output_dir.mkdir(exist_ok=True)

        test_file = output_dir / ".write_test"
        test_file.write_text("test")
        test_file.unlink()

        logger.info("✓ Output directory writable")
    except Exception as e:
        raise HealthCheckError(f"Output directory not writable: {e}")

async def run_all_health_checks():
    """Run all startup health checks."""
    logger.info("Running startup health checks...")

    checks = [
        ("Database Connection", check_database_connection),
        ("TTS Model Files", check_model_files_exist),
        ("ffmpeg Installation", check_ffmpeg_installed),
        ("Manuscript Folder", check_manuscript_folder_exists),
        ("Output Directory", check_output_directory_writable)
    ]

    failed = []

    for check_name, check_func in checks:
        try:
            await check_func()
        except HealthCheckError as e:
            logger.error(f"✗ {check_name}: {e}")
            failed.append((check_name, str(e)))

    if failed:
        logger.error("\n⚠ STARTUP HEALTH CHECK FAILED")
        logger.error("Fix the following issues before continuing:\n")
        for name, error in failed:
            logger.error(f"  • {name}: {error}")
        raise HealthCheckError(f"{len(failed)} health checks failed")

    logger.info("\n✓ All health checks passed!")
```

#### Startup Integration

**File: `src/main.py` (MODIFIED)**

```python
from fastapi import FastAPI
import logging
from src.logging_config import configure_logging
from src.health_checks import run_all_health_checks
from src.api.error_handlers import register_error_handlers
from src.api.middleware import log_request_middleware

# Configure logging early
configure_logging(level="INFO")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Alexandria Audiobook Narrator",
    description="Convert manuscripts to audiobooks using local TTS"
)

# Register middleware
app.middleware("http")(log_request_middleware)

# Register error handlers
register_error_handlers(app)

@app.on_event("startup")
async def startup_event():
    """Run startup checks and initialization."""
    logger.info("=" * 50)
    logger.info("Alexandria Audiobook Narrator - Starting Up")
    logger.info("=" * 50)

    try:
        await run_all_health_checks()
    except Exception as e:
        logger.error(f"Startup failed: {e}")
        raise

    logger.info("Application started successfully\n")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    logger.info("Shutting down...")
```

### Graceful Degradation Patterns

#### Generation Pipeline Resilience

**File: `src/pipeline/generator.py` (MODIFIED from PROMPT-08)**

```python
async def generate_chapter_with_retry(
    book_id: int,
    chapter_n: int,
    max_retries: int = 2
) -> GenerationResult:
    """
    Generate chapter with automatic retry on transient failures.

    Retries: TTS engine timeouts, temporary OOM, disk errors
    No retry: Model missing, invalid text, database errors
    """
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Generating book {book_id} ch {chapter_n} (attempt {attempt}/{max_retries})")

            result = await engine.generate_audio(
                text=chapter_text,
                voice_name=voice_name
            )

            logger.info(f"Generation successful: {result.duration_seconds}s")
            return result

        except TimeoutError as e:
            if attempt < max_retries:
                logger.warning(f"Timeout, retrying... ({attempt}/{max_retries})")
                await asyncio.sleep(5)  # Wait before retry
                continue
            else:
                raise

        except MemoryError as e:
            if attempt < max_retries:
                logger.warning(f"Out of memory, retrying... ({attempt}/{max_retries})")
                await asyncio.sleep(10)  # Longer wait for OOM
                continue
            else:
                raise

        except (FileNotFoundError, ValueError) as e:
            # Don't retry for these permanent errors
            logger.error(f"Generation failed (not retrying): {e}")
            raise

    # Should never reach here, but handle just in case
    raise GenerationError("Generation failed after retries")
```

#### Mid-Generation Failure Recovery

**File: `src/pipeline/generator.py` (MODIFIED)**

```python
async def generate_book_with_checkpointing(
    book_id: int,
    resume_from_chapter: int = 0
) -> GenerationResult:
    """
    Generate entire book with progress checkpointing.

    If generation fails mid-book, can resume from last completed chapter.
    """
    book = db.query(Book).filter(Book.id == book_id).first()
    chapters = db.query(Chapter).filter(Chapter.book_id == book_id).all()

    # Determine starting point
    start_chapter = resume_from_chapter
    logger.info(f"Generating book {book_id}: {len(chapters)} chapters (starting from chapter {start_chapter})")

    successful = 0
    failed = []

    for chapter in chapters[start_chapter:]:
        try:
            # Generate this chapter
            result = await generate_chapter_with_retry(book_id, chapter.chapter_n)

            # Store result
            chapter.status = 'completed'
            chapter.audio_duration_seconds = result.duration_seconds
            chapter.completed_at = datetime.now()
            db.commit()

            successful += 1
            logger.info(f"Progress: {successful}/{len(chapters)} chapters generated")

        except Exception as e:
            logger.error(f"Failed to generate chapter {chapter.chapter_n}: {e}")
            failed.append((chapter.chapter_n, str(e)))

            # Update status to error but continue with next chapter
            chapter.status = 'error'
            chapter.error_message = str(e)
            db.commit()

            # Optionally pause after N failures to avoid cascading errors
            if len(failed) >= 3:
                logger.error("Too many failures, pausing generation")
                raise GenerationError(f"Generation paused after {len(failed)} failures")

    if failed:
        logger.warning(f"Generation complete with {len(failed)} failures")
        return {
            "book_id": book_id,
            "successful": successful,
            "failed": len(failed),
            "failed_chapters": failed
        }

    logger.info(f"Generation complete: all {successful} chapters successful")
    return {
        "book_id": book_id,
        "successful": successful,
        "failed": 0
    }
```

### Database Migrations Support

#### File: `src/database/alembic_init.py`

```python
"""
Setup basic Alembic migrations support for schema changes.
Not full Alembic setup, but enough for development migrations.
"""
from pathlib import Path
import json

MIGRATIONS_DIR = Path("migrations")
MIGRATIONS_DIR.mkdir(exist_ok=True)

def record_migration(name: str, description: str):
    """Record a migration in migration history."""
    migration_log = MIGRATIONS_DIR / "migration_log.json"

    if migration_log.exists():
        history = json.loads(migration_log.read_text())
    else:
        history = []

    history.append({
        "name": name,
        "description": description,
        "timestamp": datetime.now().isoformat()
    })

    migration_log.write_text(json.dumps(history, indent=2))
    print(f"Migration recorded: {name}")
```

### Performance Optimization

#### Response Caching

**File: `src/api/cache.py`**

```python
from functools import lru_cache
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

class TTLCache:
    """Simple time-limited cache for frequently accessed data."""

    def __init__(self, ttl_seconds: int = 300):
        self.cache = {}
        self.ttl_seconds = ttl_seconds

    def get(self, key: str):
        """Get value from cache if not expired."""
        if key not in self.cache:
            return None

        value, timestamp = self.cache[key]
        if datetime.now() - timestamp > timedelta(seconds=self.ttl_seconds):
            del self.cache[key]
            return None

        return value

    def set(self, key: str, value):
        """Store value in cache with timestamp."""
        self.cache[key] = (value, datetime.now())

    def clear(self):
        """Clear entire cache."""
        self.cache.clear()

# Cache for book library listing
library_cache = TTLCache(ttl_seconds=300)  # 5 minute TTL

@router.get("/books")
async def list_books(skip: int = 0, limit: int = 50) -> dict:
    """
    List books with caching for library page.

    Caches list for 5 minutes to avoid repeated DB queries.
    """
    cache_key = f"books_{skip}_{limit}"
    cached = library_cache.get(cache_key)

    if cached is not None:
        logger.debug(f"Returning cached book list")
        return cached

    # Fetch from database
    books = db.query(Book).offset(skip).limit(limit).all()
    total = db.query(Book).count()

    result = {
        "books": [book.to_dict() for book in books],
        "total": total,
        "skip": skip,
        "limit": limit
    }

    # Cache result
    library_cache.set(cache_key, result)

    return result
```

#### Pagination for Large Result Sets

```python
@router.get("/qa/chapters")
async def list_qa_chapters(skip: int = 0, limit: int = 50) -> dict:
    """
    List QA chapters with pagination (not loading all at once).

    Prevents memory issues when browsing large result sets.
    """
    if limit > 500:
        limit = 500  # Cap max per-page results

    chapters = db.query(QAStatus)\
        .order_by(QAStatus.created_at.desc())\
        .offset(skip)\
        .limit(limit)\
        .all()

    total = db.query(QAStatus).count()

    return {
        "chapters": chapters,
        "total": total,
        "skip": skip,
        "limit": limit,
        "has_more": (skip + limit) < total
    }
```

### Security Headers & CORS

**File: `src/main.py` (MODIFIED)**

```python
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

# Trust localhost for development
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["localhost", "127.0.0.1", "*.local"]
)

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Frontend development server
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization"]
)

# Security headers
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)

    # Prevent clickjacking
    response.headers["X-Frame-Options"] = "DENY"

    # Prevent MIME sniffing
    response.headers["X-Content-Type-Options"] = "nosniff"

    # Enable XSS protection
    response.headers["X-XSS-Protection"] = "1; mode=block"

    # Strict transport security (localhost won't have HTTPS)
    # response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    return response
```

### Frontend Error Boundaries

**File: `frontend/src/components/ErrorBoundary.jsx`**

```jsx
import React from 'react';

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error('Error caught:', error, errorInfo);
    // Could send to error logging service here
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="p-6 bg-red-50 border border-red-200 rounded">
          <h2 className="text-lg font-semibold text-red-800 mb-2">Something went wrong</h2>
          <p className="text-sm text-red-600 mb-4">{this.state.error?.message}</p>
          <button
            onClick={() => this.setState({ hasError: false })}
            className="px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700"
          >
            Try Again
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
```

### Wrap All Async Operations with Error Handling

**Pattern for Frontend:**

```jsx
function useAsyncData(url) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let mounted = true;

    fetch(url)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(data => {
        if (mounted) {
          setData(data);
          setError(null);
        }
      })
      .catch(err => {
        if (mounted) {
          setError(err.message);
          setData(null);
        }
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });

    return () => { mounted = false };  // Cleanup
  }, [url]);

  return { data, loading, error };
}
```

---

## Acceptance Criteria

### Error Handling
- [ ] All API endpoints wrapped in try/except
- [ ] HTTP error codes correct (404, 400, 500)
- [ ] User-friendly error messages returned
- [ ] Sensitive errors logged but not exposed to client
- [ ] Validation errors include field information
- [ ] Request ID included in error responses for debugging

### Logging
- [ ] Application logs to files with rotation
- [ ] API requests logged with method, path, status, duration
- [ ] Generation events logged (start, progress, completion)
- [ ] Errors logged with full stack trace
- [ ] Log files in `logs/` directory with different streams
- [ ] Logs readable and timestamped

### Health Checks
- [ ] Database connection verified at startup
- [ ] TTS model files verified to exist
- [ ] ffmpeg installation verified
- [ ] Output directory writable
- [ ] Manuscript folder path configured
- [ ] Clear error messages if checks fail
- [ ] Application won't start if critical checks fail

### Graceful Degradation
- [ ] Generation retries on transient errors (timeout, OOM)
- [ ] No retry on permanent errors (missing model, invalid text)
- [ ] Progress saved to DB so can resume on failure
- [ ] Mid-generation failure doesn't corrupt already-generated chapters
- [ ] Cascade failure protection (stop after N failures)

### Frontend Resilience
- [ ] Error boundaries catch component errors
- [ ] All async operations have error handling
- [ ] Loading states on all async actions
- [ ] Network errors show user-friendly messages
- [ ] Retry buttons for failed operations
- [ ] No console errors or unhandled promise rejections

### Performance
- [ ] Library page caches results (5 min TTL)
- [ ] Large result sets paginated (max 500 per page)
- [ ] No memory leaks from polling or listeners
- [ ] Response times < 1 second for cached endpoints
- [ ] Response times < 5 seconds for non-cached endpoints

### Security
- [ ] CORS configured for localhost:3000
- [ ] Security headers set (X-Frame-Options, etc.)
- [ ] Trusted hosts middleware active
- [ ] No SQL injection vectors (all using ORM)
- [ ] File uploads validated (whitelist formats)
- [ ] No sensitive data in logs

### Testing Requirements

1. **Error Handling Tests:**
   - [ ] Missing book → 404 error
   - [ ] Invalid chapter → 400 error
   - [ ] TTS timeout → retry and succeed
   - [ ] Model file missing → 500 error with message
   - [ ] Database error → 500 error with request ID

2. **Logging Tests:**
   - [ ] Verify logs written to file
   - [ ] Verify log rotation works (create 11MB file, verify rotated)
   - [ ] API request logged with status code
   - [ ] Generation events logged
   - [ ] Error logged with stack trace

3. **Health Check Tests:**
   - [ ] All checks pass on valid setup
   - [ ] Missing model fails with instructions
   - [ ] Missing ffmpeg fails with install command
   - [ ] No database fails with connection error
   - [ ] Read-only output dir fails clearly

4. **Resilience Tests:**
   - [ ] Generation timeout → retry succeeds
   - [ ] Generation fails mid-book → can resume from last chapter
   - [ ] Chapter error → continues with next chapter
   - [ ] Three consecutive failures → pause and error

5. **Frontend Tests:**
   - [ ] ErrorBoundary catches and displays errors
   - [ ] Network error shows message and retry button
   - [ ] Async operation shows loading state
   - [ ] Failed async shows error state

6. **Manual Testing Scenario:**
   - [ ] Start server → verify health checks pass
   - [ ] Check logs directory → verify logs created
   - [ ] Trigger API error (invalid ID) → verify error response
   - [ ] Generate chapter → verify logs show progress
   - [ ] Stop server mid-generation → resume from that chapter
   - [ ] Disable ffmpeg → start server → health check fails clearly
   - [ ] Browse book library → verify page loads quickly (cached)
   - [ ] Frontend network error → verify error message and retry

---

## File Structure

```
src/
  logging_config.py                 # NEW: Logging setup
  health_checks.py                  # NEW: Startup health checks
  api/
    error_handlers.py               # NEW: Global error handlers
    middleware.py                   # NEW: Request logging middleware
    cache.py                        # NEW: Response caching
  pipeline/
    generator.py                    # MODIFIED: Add retry and checkpointing
  main.py                           # MODIFIED: Register handlers, logging, checks

frontend/src/
  components/
    ErrorBoundary.jsx               # NEW: Error boundary component
  hooks/
    useAsyncData.js                 # NEW: Custom hook for async operations

logs/                               # NEW: Application logs (gitignored)
  .gitkeep
migrations/                         # NEW: Migration history (optional)
  .gitkeep
```

---

## Implementation Notes

### Log Levels
- **DEBUG:** Detailed diagnostic info (caching hits, retries)
- **INFO:** General application flow (startup, generation complete)
- **WARNING:** Something unexpected but recoverable (timeout retry, missing folder)
- **ERROR:** Something broke but caught gracefully (generation failed)
- **CRITICAL:** Application cannot start (missing model, no database)

### Health Check Order
1. Database (most critical)
2. Model files (required for core function)
3. ffmpeg (required for export)
4. Output directory (should be writable)
5. Manuscript folder (configure, but not critical)

### Caching Strategy
- Library listing: 5 minute TTL (changes infrequently)
- QA results: 2 minute TTL (updated as generation progresses)
- Settings: No cache (rarely change, direct DB read is fast)

### Retry Strategy
- Transient errors (timeout, OOM): Retry 2 times with exponential backoff
- Permanent errors (file not found, validation): No retry
- Network errors (API calls): Retry 3 times with circuit breaker

### Resume Strategy
- Store `last_successful_chapter_n` in database
- POST /api/book/{id}/resume endpoint to resume from that chapter
- Can be called manually or by retry logic

---

## References

- CLAUDE.md § Commit Convention
- All previous prompts (01-15) for integration points
- FastAPI error handling: https://fastapi.tiangolo.com/tutorial/handling-errors/
- Python logging: https://docs.python.org/3/library/logging.html
- React error boundaries: https://react.dev/reference/react/Component#catching-rendering-errors-with-an-error-boundary

---

## Commit Message

```
[PROMPT-16] Add comprehensive error handling and production hardening

- Implement global exception handler and validation error handling
- Configure file-based logging with rotation
- Add request logging middleware with request IDs
- Implement startup health checks (DB, models, ffmpeg, etc.)
- Add graceful degradation: retry logic and checkpointing
- Implement response caching (5min TTL) for library page
- Add pagination for large result sets
- Configure CORS and security headers
- Create ErrorBoundary and async error handling in frontend
- Comprehensive error testing and logging verification
```

---

**Status:** READY FOR IMPLEMENTATION
**Estimated Effort:** 12-15 hours (logging, health checks, resilience patterns, testing)
**Dependencies:** All previous prompts (01-15) for integration

---

## Final Project Completion Notes

With PROMPT-16 complete, the Alexandria Audiobook Narrator application is production-ready with:

✓ **Full manuscript parsing** (DOCX/EPUB/PDF)
✓ **Web-based library and book management** (React UI)
✓ **TTS generation pipeline** with queue management
✓ **Real-time progress tracking** and audio player
✓ **Automated QA system** with dashboard
✓ **Professional audio export** (MP3/M4B with metadata)
✓ **Voice cloning** for custom narrator voices
✓ **Configurable settings** (voice, output, silence)
✓ **Production hardening** (error handling, logging, health checks)

The app can now reliably handle 873 manuscripts and generate thousands of audiobooks with professional quality and comprehensive monitoring.
