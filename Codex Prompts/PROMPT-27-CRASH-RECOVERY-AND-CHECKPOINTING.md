# PROMPT-27: Crash Recovery, Checkpointing & Graceful Shutdown

## Context
If the server crashes mid-generation (OOM kill, Mac sleep, power loss, user quit), in-progress jobs are stuck in RUNNING state forever. There's no way to resume a 50-chapter book from chapter 48 — it restarts from scratch. SQLite write contention causes "database is locked" errors under load. This prompt fixes all of these.

---

## Task 1: Orphaned Job Recovery on Startup

### Problem
Jobs in RUNNING or GENERATING state when the server crashes remain in that state forever on restart. The queue manager never picks them up again.

### Implementation

In `src/main.py` or a new `src/startup.py`, add a startup hook:

```python
async def recover_orphaned_jobs(db_session: Session):
    """Detect and recover jobs that were interrupted by a server crash."""
    STALE_THRESHOLD_MINUTES = 5

    stale_cutoff = datetime.utcnow() - timedelta(minutes=STALE_THRESHOLD_MINUTES)

    # Find jobs stuck in active states with no recent updates
    orphaned_jobs = db_session.query(GenerationJob).filter(
        GenerationJob.status.in_(["running", "generating"]),
        GenerationJob.updated_at < stale_cutoff,
    ).all()

    for job in orphaned_jobs:
        logger.warning(f"Recovering orphaned job {job.id} (book: {job.book_id}, "
                       f"status: {job.status}, last update: {job.updated_at})")

        # Mark the job as failed with recovery info
        job.status = "failed"
        job.error_message = (
            f"Server restarted during generation. "
            f"Last active: {job.updated_at.isoformat()}. "
            f"Completed {job.completed_chapters}/{job.total_chapters} chapters. "
            f"Use 'Retry' to resume from the last checkpoint."
        )
        job.updated_at = datetime.utcnow()

        # Mark any in-progress chapters back to PENDING
        in_progress_chapters = db_session.query(Chapter).filter(
            Chapter.book_id == job.book_id,
            Chapter.generation_status == "generating",
        ).all()
        for ch in in_progress_chapters:
            ch.generation_status = "pending"
            ch.updated_at = datetime.utcnow()

    if orphaned_jobs:
        db_session.commit()
        logger.info(f"Recovered {len(orphaned_jobs)} orphaned jobs")
```

Call this during FastAPI `lifespan` startup, before the queue manager starts.

---

## Task 2: Per-Chapter Checkpointing

### Problem
When a job fails on chapter 48 of 50, retrying regenerates all 50 chapters. Hours of work wasted.

### Implementation

Add to `GenerationJob` model in `src/database.py`:
```python
last_completed_chapter: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
```

In `src/pipeline/generator.py`, after each chapter completes successfully:
```python
# After chapter generation and QA pass:
job.last_completed_chapter = chapter.number
job.completed_chapters += 1
job.updated_at = datetime.utcnow()
db_session.commit()  # Immediate checkpoint
```

In `generate_book()`, when starting/resuming:
```python
# Skip already-completed chapters
for chapter in chapters:
    if chapter.generation_status == "generated" and not force:
        logger.info(f"Skipping chapter {chapter.number} (already generated)")
        continue
    # Generate this chapter...
```

In the retry/resume logic (`queue_routes.py` or wherever jobs are retried):
```python
# When retrying a failed job:
new_job.last_completed_chapter = old_job.last_completed_chapter
# Generator will skip chapters up to last_completed_chapter
```

---

## Task 3: Graceful Shutdown

### Problem
When the user quits the app or sends SIGTERM, the current chunk is abandoned mid-generation, leaving the chapter in an inconsistent state.

### Implementation

In `src/main.py`:
```python
import signal

_draining = False

async def graceful_shutdown(sig):
    """Handle shutdown signal — finish current work before exiting."""
    global _draining
    logger.info(f"Received {sig.name}, starting graceful shutdown (30s drain)...")
    _draining = True

    # Signal the queue manager to stop accepting new jobs
    queue_manager = get_queue_manager()
    if queue_manager:
        queue_manager.request_drain()

    # Wait up to 30 seconds for current chunk to complete
    for i in range(30):
        if not queue_manager or not queue_manager.has_active_work():
            break
        await asyncio.sleep(1)
        logger.info(f"Draining... {30-i}s remaining")

    # Save progress and transition active jobs to PAUSED
    if queue_manager:
        await queue_manager.save_and_pause_active_jobs()

    # Shutdown runtime
    await shutdown_generation_runtime()
    logger.info("Graceful shutdown complete")

# Register signal handlers
for sig in (signal.SIGTERM, signal.SIGINT):
    loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(graceful_shutdown(s)))
```

In `queue_manager.py`, add:
```python
def request_drain(self):
    """Stop accepting new jobs, let current work finish."""
    self._draining = True

def has_active_work(self) -> bool:
    """Check if any generation is currently in progress."""
    return any(j.status == "generating" for j in self.jobs.values())

async def save_and_pause_active_jobs(self):
    """Checkpoint all active jobs and transition to PAUSED."""
    for job in self.jobs.values():
        if job.status in ("running", "generating"):
            job.status = "paused"
            job.error_message = "Server shutdown — job paused. Will resume on restart."
            # Commit checkpoint
```

---

## Task 4: SQLite WAL Mode & Retry

### Problem
SQLite's default journal mode locks the entire database during writes. Concurrent reads during generation cause "database is locked" errors.

### Implementation

In `src/database.py`, when creating the engine:
```python
from sqlalchemy import event

engine = create_engine(database_url, **engine_kwargs)

# Enable WAL mode for SQLite (allows concurrent reads during writes)
if "sqlite" in database_url:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")  # 5-second retry on lock
        cursor.execute("PRAGMA synchronous=NORMAL")  # Faster writes, still safe with WAL
        cursor.close()
```

Also add a retry decorator for database operations:
```python
import time
from sqlalchemy.exc import OperationalError

def retry_on_locked(max_retries=3, backoff_ms=500):
    """Retry database operations that fail due to SQLite locking."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except OperationalError as e:
                    if "database is locked" in str(e) and attempt < max_retries - 1:
                        time.sleep(backoff_ms * (attempt + 1) / 1000)
                        continue
                    raise
        return wrapper
    return decorator
```

---

## Task 5: Mac Sleep Prevention

### Problem
During long batch generation (8+ hours overnight), the Mac sleeps, suspending all async tasks. On wake, timers are stale, connections may be broken, and generation state is unpredictable.

### Implementation

Create `src/utils/caffeinate.py`:
```python
import subprocess
import atexit

_caffeinate_process = None

def prevent_sleep():
    """Start caffeinate to prevent Mac from sleeping during generation."""
    global _caffeinate_process
    if _caffeinate_process is not None:
        return  # Already running

    try:
        _caffeinate_process = subprocess.Popen(
            ["caffeinate", "-i", "-s"],  # -i: prevent idle sleep, -s: prevent system sleep
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        atexit.register(allow_sleep)
    except FileNotFoundError:
        pass  # Not on macOS, skip

def allow_sleep():
    """Stop caffeinate, allow Mac to sleep normally."""
    global _caffeinate_process
    if _caffeinate_process is not None:
        _caffeinate_process.terminate()
        _caffeinate_process = None
```

Call `prevent_sleep()` when the queue starts processing jobs.
Call `allow_sleep()` when the queue is empty and all jobs are done.

---

## Task 6: Tests

Create `tests/test_crash_recovery.py`:
1. `test_orphaned_job_detected` — RUNNING job with old updated_at transitions to FAILED
2. `test_orphaned_chapter_reset` — GENERATING chapter transitions to PENDING
3. `test_fresh_job_not_recovered` — RUNNING job with recent updated_at left alone
4. `test_checkpoint_saved` — last_completed_chapter updated after each chapter
5. `test_resume_skips_completed` — retry job skips chapters up to checkpoint
6. `test_graceful_shutdown_pauses_job` — active job transitions to PAUSED on drain
7. `test_sqlite_wal_mode` — database uses WAL journal mode
8. `test_retry_on_locked` — decorator retries on "database is locked"

All existing tests must still pass.
