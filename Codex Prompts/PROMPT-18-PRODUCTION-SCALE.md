# PROMPT-18: Production Scale — Batch Orchestration, Resource Monitoring & Catalog Dashboard

**Objective:** Add all systems needed to reliably generate, QA, and export 873 audiobooks at scale with full visibility, resource management, and batch operations.

**Owner:** Codex
**Reference:** CLAUDE.md, PROJECT-STATE.md, COMPREHENSIVE-AUDIT-V2.md, PROMPT-17

---

## Scope

### 1. Model Lifecycle Management (Cooldown & Restart)

**File:** `src/engines/model_manager.py` (NEW)

The Qwen3-TTS MLX model (1.7B params, ~3GB VRAM) accumulates memory fragmentation during extended generation. After 50+ chapters the model must be reloaded to prevent OOM and quality degradation.

```python
import gc
import time
import logging
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

@dataclass
class ModelStats:
    """Track model usage for cooldown decisions."""
    chunks_generated: int = 0
    chapters_generated: int = 0
    last_reload_time: float = field(default_factory=time.time)
    total_generation_seconds: float = 0.0
    peak_memory_mb: float = 0.0

class ModelManager:
    """Manages TTS engine lifecycle: loading, cooldown, restart, memory tracking."""

    COOLDOWN_CHAPTER_THRESHOLD = 50       # Reload after N chapters
    COOLDOWN_CHUNK_THRESHOLD = 2000       # Or after N chunks
    COOLDOWN_TIME_THRESHOLD = 7200        # Or after N seconds (2 hours)
    MEMORY_PRESSURE_THRESHOLD_MB = 12000  # Force reload if process memory > 12GB

    def __init__(self, engine_factory):
        """
        Args:
            engine_factory: Callable that creates and loads a fresh TTS engine instance.
        """
        self._engine_factory = engine_factory
        self._engine = None
        self._stats = ModelStats()
        self._lock = asyncio.Lock()

    @property
    def engine(self):
        return self._engine

    @property
    def stats(self) -> ModelStats:
        return self._stats

    async def get_engine(self):
        """Get the engine, reloading if cooldown thresholds exceeded."""
        async with self._lock:
            if self._engine is None:
                await self._load_engine()
            elif self._needs_cooldown():
                logger.info(
                    "Model cooldown triggered after %d chapters / %d chunks / %.0fs",
                    self._stats.chapters_generated,
                    self._stats.chunks_generated,
                    time.time() - self._stats.last_reload_time,
                )
                await self._reload_engine()
            return self._engine

    def _needs_cooldown(self) -> bool:
        """Check if model needs a cooldown/reload."""
        if self._stats.chapters_generated >= self.COOLDOWN_CHAPTER_THRESHOLD:
            return True
        if self._stats.chunks_generated >= self.COOLDOWN_CHUNK_THRESHOLD:
            return True
        elapsed = time.time() - self._stats.last_reload_time
        if elapsed >= self.COOLDOWN_TIME_THRESHOLD:
            return True
        # Check system memory
        current_memory = self._get_process_memory_mb()
        if current_memory > self.MEMORY_PRESSURE_THRESHOLD_MB:
            logger.warning("Memory pressure: %.0f MB > threshold %.0f MB",
                           current_memory, self.MEMORY_PRESSURE_THRESHOLD_MB)
            return True
        return False

    async def _load_engine(self):
        """Load a fresh engine instance."""
        logger.info("Loading TTS engine...")
        self._engine = self._engine_factory()
        self._engine.load()
        self._stats = ModelStats()
        logger.info("TTS engine loaded successfully")

    async def _reload_engine(self):
        """Unload current engine, force GC, then load fresh."""
        logger.info("Reloading TTS engine (cooldown)...")
        old_engine = self._engine
        self._engine = None

        # Explicitly delete and garbage collect
        del old_engine
        gc.collect()

        # Brief pause to let memory settle
        await asyncio.sleep(1.0)

        await self._load_engine()
        logger.info("TTS engine reloaded after cooldown")

    def record_chunk(self, generation_seconds: float = 0.0):
        """Record a chunk generation for cooldown tracking."""
        self._stats.chunks_generated += 1
        self._stats.total_generation_seconds += generation_seconds

    def record_chapter(self):
        """Record a chapter completion for cooldown tracking."""
        self._stats.chapters_generated += 1

    def _get_process_memory_mb(self) -> float:
        """Get current process memory usage in MB."""
        try:
            import psutil
            process = psutil.Process()
            return process.memory_info().rss / (1024 * 1024)
        except ImportError:
            return 0.0  # Can't check without psutil

    async def force_reload(self):
        """Force an immediate engine reload."""
        async with self._lock:
            await self._reload_engine()

    def to_dict(self) -> dict:
        """Return stats as dict for API responses."""
        return {
            "chunks_generated": self._stats.chunks_generated,
            "chapters_generated": self._stats.chapters_generated,
            "seconds_since_reload": round(time.time() - self._stats.last_reload_time, 1),
            "total_generation_seconds": round(self._stats.total_generation_seconds, 1),
            "process_memory_mb": round(self._get_process_memory_mb(), 1),
            "cooldown_threshold_chapters": self.COOLDOWN_CHAPTER_THRESHOLD,
            "cooldown_threshold_chunks": self.COOLDOWN_CHUNK_THRESHOLD,
        }
```

**Integration:** Replace the `@lru_cache` engine caching in `src/api/voice_lab.py` with `ModelManager`. The `generator.py` pipeline should call `model_manager.get_engine()` instead of building the engine directly. After each chunk, call `model_manager.record_chunk()`. After each chapter, call `model_manager.record_chapter()`.

### 2. Resource Monitoring System

**File:** `src/monitoring/resource_monitor.py` (NEW)

```python
import shutil
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass
class ResourceSnapshot:
    """Point-in-time resource usage."""
    disk_free_gb: float
    disk_total_gb: float
    disk_used_percent: float
    memory_used_mb: float
    memory_total_mb: float
    memory_used_percent: float
    cpu_percent: Optional[float] = None
    gpu_memory_mb: Optional[float] = None

@dataclass
class ResourceThresholds:
    """Configurable resource thresholds."""
    min_disk_free_gb: float = 10.0           # Pause if < 10 GB free
    max_memory_percent: float = 85.0          # Pause if > 85% memory
    max_cpu_percent: float = 95.0             # Warn if > 95% CPU
    estimated_gb_per_book: float = 0.5        # ~500MB per book (WAV + exports)

class ResourceMonitor:
    """Monitor system resources and gate generation on thresholds."""

    def __init__(self, output_dir: Path, thresholds: Optional[ResourceThresholds] = None):
        self.output_dir = output_dir
        self.thresholds = thresholds or ResourceThresholds()
        self._last_snapshot: Optional[ResourceSnapshot] = None

    def snapshot(self) -> ResourceSnapshot:
        """Take a current resource snapshot."""
        # Disk
        disk = shutil.disk_usage(self.output_dir)
        disk_free_gb = disk.free / (1024 ** 3)
        disk_total_gb = disk.total / (1024 ** 3)
        disk_used_pct = (disk.used / disk.total) * 100

        # Memory
        try:
            import psutil
            mem = psutil.virtual_memory()
            mem_used_mb = mem.used / (1024 ** 2)
            mem_total_mb = mem.total / (1024 ** 2)
            mem_used_pct = mem.percent
            cpu_pct = psutil.cpu_percent(interval=0.1)
        except ImportError:
            import os
            mem_used_mb = 0
            mem_total_mb = 0
            mem_used_pct = 0
            cpu_pct = None

        snap = ResourceSnapshot(
            disk_free_gb=round(disk_free_gb, 2),
            disk_total_gb=round(disk_total_gb, 2),
            disk_used_percent=round(disk_used_pct, 1),
            memory_used_mb=round(mem_used_mb, 1),
            memory_total_mb=round(mem_total_mb, 1),
            memory_used_percent=round(mem_used_pct, 1),
            cpu_percent=round(cpu_pct, 1) if cpu_pct is not None else None,
        )
        self._last_snapshot = snap
        return snap

    def check_can_proceed(self) -> tuple[bool, list[str]]:
        """Check if resources allow generation to proceed.

        Returns:
            (can_proceed, list_of_warnings)
        """
        snap = self.snapshot()
        warnings = []
        can_proceed = True

        if snap.disk_free_gb < self.thresholds.min_disk_free_gb:
            warnings.append(
                f"LOW DISK: {snap.disk_free_gb:.1f} GB free "
                f"(minimum: {self.thresholds.min_disk_free_gb:.1f} GB)"
            )
            can_proceed = False

        if snap.memory_used_percent > self.thresholds.max_memory_percent:
            warnings.append(
                f"HIGH MEMORY: {snap.memory_used_percent:.1f}% used "
                f"(maximum: {self.thresholds.max_memory_percent:.1f}%)"
            )
            can_proceed = False

        if snap.cpu_percent and snap.cpu_percent > self.thresholds.max_cpu_percent:
            warnings.append(
                f"HIGH CPU: {snap.cpu_percent:.1f}% "
                f"(maximum: {self.thresholds.max_cpu_percent:.1f}%)"
            )
            # CPU warning doesn't block, just warns

        return can_proceed, warnings

    def estimate_remaining_capacity(self, books_remaining: int) -> dict:
        """Estimate if we have enough disk for remaining books."""
        snap = self.snapshot()
        needed_gb = books_remaining * self.thresholds.estimated_gb_per_book
        available_gb = snap.disk_free_gb - self.thresholds.min_disk_free_gb  # Keep reserve
        can_fit = int(available_gb / self.thresholds.estimated_gb_per_book)
        return {
            "books_remaining": books_remaining,
            "estimated_gb_needed": round(needed_gb, 1),
            "disk_free_gb": snap.disk_free_gb,
            "estimated_books_can_fit": max(0, can_fit),
            "sufficient": available_gb >= needed_gb,
        }

    def to_dict(self) -> dict:
        """Return latest snapshot for API."""
        snap = self._last_snapshot or self.snapshot()
        return {
            "disk_free_gb": snap.disk_free_gb,
            "disk_total_gb": snap.disk_total_gb,
            "disk_used_percent": snap.disk_used_percent,
            "memory_used_mb": snap.memory_used_mb,
            "memory_total_mb": snap.memory_total_mb,
            "memory_used_percent": snap.memory_used_percent,
            "cpu_percent": snap.cpu_percent,
        }
```

**Integration in queue_manager.py:** Before processing each job, call `resource_monitor.check_can_proceed()`. If resources are low, pause the queue and set a status message. Resume automatically when resources are available (check every 30 seconds).

### 3. Batch Generation Orchestration

**File:** `src/pipeline/batch_orchestrator.py` (NEW)

Orchestrates the generation of multiple books in sequence with resource management, cooldown, and progress tracking.

```python
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List

logger = logging.getLogger(__name__)

class BatchStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class BatchBookResult:
    """Result for a single book in the batch."""
    book_id: int
    title: str
    status: str            # "completed", "failed", "skipped", "pending"
    chapters_total: int = 0
    chapters_completed: int = 0
    chapters_failed: int = 0
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_seconds: float = 0.0

@dataclass
class BatchProgress:
    """Overall batch progress tracking."""
    batch_id: str
    status: BatchStatus = BatchStatus.PENDING
    total_books: int = 0
    books_completed: int = 0
    books_failed: int = 0
    books_skipped: int = 0
    books_in_progress: int = 0
    current_book_id: Optional[int] = None
    current_book_title: Optional[str] = None
    started_at: Optional[str] = None
    estimated_completion: Optional[str] = None
    elapsed_seconds: float = 0.0
    avg_seconds_per_book: float = 0.0
    book_results: List[BatchBookResult] = field(default_factory=list)
    resource_warnings: List[str] = field(default_factory=list)
    model_reloads: int = 0
    pause_reason: Optional[str] = None

class BatchOrchestrator:
    """Orchestrate bulk audiobook generation across the entire catalog."""

    def __init__(self, queue_manager, model_manager, resource_monitor, db_session_factory):
        self.queue_manager = queue_manager
        self.model_manager = model_manager
        self.resource_monitor = resource_monitor
        self.db_session_factory = db_session_factory
        self._progress: Optional[BatchProgress] = None
        self._cancel_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # Not paused initially
        self._task: Optional[asyncio.Task] = None

    @property
    def progress(self) -> Optional[BatchProgress]:
        return self._progress

    async def start_batch(
        self,
        book_ids: List[int],
        batch_id: Optional[str] = None,
        priority: str = "normal",
        skip_already_exported: bool = True,
    ) -> BatchProgress:
        """Start a batch generation run.

        Args:
            book_ids: List of book IDs to generate. If empty, generate all pending books.
            batch_id: Optional batch identifier. Auto-generated if not provided.
            priority: Priority tier ("urgent", "normal", "backlog")
            skip_already_exported: Skip books that already have successful exports.
        """
        if self._task and not self._task.done():
            raise RuntimeError("A batch is already running. Cancel it first.")

        if not batch_id:
            batch_id = f"batch_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

        self._progress = BatchProgress(
            batch_id=batch_id,
            total_books=len(book_ids),
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        self._cancel_event.clear()
        self._pause_event.set()

        self._task = asyncio.create_task(
            self._run_batch(book_ids, skip_already_exported)
        )
        return self._progress

    async def _run_batch(self, book_ids: List[int], skip_exported: bool):
        """Internal batch execution loop."""
        self._progress.status = BatchStatus.RUNNING
        start_time = datetime.now(timezone.utc)
        completed_durations = []

        for i, book_id in enumerate(book_ids):
            # Check cancellation
            if self._cancel_event.is_set():
                self._progress.status = BatchStatus.CANCELLED
                logger.info("Batch cancelled at book %d/%d", i + 1, self._progress.total_books)
                return

            # Wait if paused
            await self._pause_event.wait()

            # Check resources before each book
            can_proceed, warnings = self.resource_monitor.check_can_proceed()
            self._progress.resource_warnings = warnings
            if not can_proceed:
                self._progress.pause_reason = "; ".join(warnings)
                self._pause_event.clear()
                logger.warning("Batch paused due to resource constraints: %s", warnings)
                # Wait until resources free up or manual resume
                while not can_proceed and not self._cancel_event.is_set():
                    await asyncio.sleep(30)
                    can_proceed, warnings = self.resource_monitor.check_can_proceed()
                    self._progress.resource_warnings = warnings
                if self._cancel_event.is_set():
                    self._progress.status = BatchStatus.CANCELLED
                    return
                self._progress.pause_reason = None
                self._pause_event.set()

            # Get book info
            db = self.db_session_factory()
            try:
                book = db.query(Book).filter(Book.id == book_id).first()
                if not book:
                    self._progress.books_skipped += 1
                    continue

                # Skip already exported if requested
                if skip_exported and book.export_status == "completed":
                    self._progress.books_skipped += 1
                    result = BatchBookResult(
                        book_id=book_id, title=book.title, status="skipped"
                    )
                    self._progress.book_results.append(result)
                    continue

                # Update progress
                self._progress.current_book_id = book_id
                self._progress.current_book_title = book.title
                self._progress.books_in_progress = 1

                book_start = datetime.now(timezone.utc)
                result = BatchBookResult(
                    book_id=book_id,
                    title=book.title,
                    status="running",
                    started_at=book_start.isoformat(),
                )

                try:
                    # Queue the book for generation
                    job_id = await self.queue_manager.enqueue_book(book_id)

                    # Wait for completion
                    while True:
                        if self._cancel_event.is_set():
                            await self.queue_manager.cancel_job(job_id)
                            result.status = "cancelled"
                            break

                        job = self.queue_manager.get_job(job_id)
                        if job is None:
                            result.status = "failed"
                            result.error_message = "Job disappeared from queue"
                            break

                        if job.status in ("completed", "exported"):
                            result.status = "completed"
                            result.chapters_total = job.total_chapters
                            result.chapters_completed = job.completed_chapters
                            break
                        elif job.status == "failed":
                            result.status = "failed"
                            result.error_message = job.error_message
                            result.chapters_total = job.total_chapters
                            result.chapters_completed = job.completed_chapters
                            result.chapters_failed = job.failed_chapters
                            break

                        await asyncio.sleep(2)

                    # Record model usage
                    self.model_manager.record_chapter()

                except Exception as exc:
                    result.status = "failed"
                    result.error_message = str(exc)
                    logger.exception("Batch: book %d failed", book_id)

                # Finalize book result
                book_end = datetime.now(timezone.utc)
                result.completed_at = book_end.isoformat()
                result.duration_seconds = (book_end - book_start).total_seconds()
                self._progress.book_results.append(result)

                if result.status == "completed":
                    self._progress.books_completed += 1
                    completed_durations.append(result.duration_seconds)
                elif result.status == "failed":
                    self._progress.books_failed += 1

                self._progress.books_in_progress = 0

                # Update ETA
                elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
                self._progress.elapsed_seconds = elapsed
                if completed_durations:
                    avg = sum(completed_durations) / len(completed_durations)
                    self._progress.avg_seconds_per_book = avg
                    remaining = self._progress.total_books - (i + 1)
                    est_remaining_seconds = remaining * avg
                    est_completion = datetime.now(timezone.utc) + timedelta(seconds=est_remaining_seconds)
                    self._progress.estimated_completion = est_completion.isoformat()

            finally:
                db.close()

        # Batch complete
        if self._progress.status == BatchStatus.RUNNING:
            self._progress.status = BatchStatus.COMPLETED
        logger.info(
            "Batch %s finished: %d completed, %d failed, %d skipped",
            self._progress.batch_id,
            self._progress.books_completed,
            self._progress.books_failed,
            self._progress.books_skipped,
        )

    async def pause(self, reason: str = "Manual pause"):
        """Pause the batch after the current book finishes."""
        self._pause_event.clear()
        if self._progress:
            self._progress.status = BatchStatus.PAUSED
            self._progress.pause_reason = reason

    async def resume(self):
        """Resume a paused batch."""
        self._pause_event.set()
        if self._progress:
            self._progress.status = BatchStatus.RUNNING
            self._progress.pause_reason = None

    async def cancel(self):
        """Cancel the batch."""
        self._cancel_event.set()
        self._pause_event.set()  # Unblock if paused

    def to_dict(self) -> Optional[dict]:
        """Return progress for API."""
        if not self._progress:
            return None
        p = self._progress
        return {
            "batch_id": p.batch_id,
            "status": p.status.value,
            "total_books": p.total_books,
            "books_completed": p.books_completed,
            "books_failed": p.books_failed,
            "books_skipped": p.books_skipped,
            "books_in_progress": p.books_in_progress,
            "current_book_id": p.current_book_id,
            "current_book_title": p.current_book_title,
            "started_at": p.started_at,
            "estimated_completion": p.estimated_completion,
            "elapsed_seconds": round(p.elapsed_seconds, 1),
            "avg_seconds_per_book": round(p.avg_seconds_per_book, 1),
            "resource_warnings": p.resource_warnings,
            "model_reloads": p.model_reloads,
            "pause_reason": p.pause_reason,
            "percent_complete": round(
                (p.books_completed + p.books_skipped) / max(p.total_books, 1) * 100, 1
            ),
        }
```

### 4. Batch API Endpoints

**File:** `src/api/batch_routes.py` (NEW)

```python
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional

router = APIRouter(prefix="/api/batch", tags=["batch"])

class BatchStartRequest(BaseModel):
    book_ids: Optional[List[int]] = None  # None = all pending books
    priority: str = "normal"
    skip_already_exported: bool = True

class BatchActionRequest(BaseModel):
    reason: Optional[str] = None

# POST /api/batch/start — Start batch generation
@router.post("/start")
async def start_batch(request: BatchStartRequest):
    """Start batch generation for selected or all pending books."""
    pass  # Implement using BatchOrchestrator

# GET /api/batch/progress — Get batch progress
@router.get("/progress")
async def get_batch_progress():
    """Get current batch progress with per-book status."""
    pass

# POST /api/batch/pause — Pause batch
@router.post("/pause")
async def pause_batch(request: BatchActionRequest):
    """Pause batch after current book finishes."""
    pass

# POST /api/batch/resume — Resume batch
@router.post("/resume")
async def resume_batch():
    """Resume paused batch."""
    pass

# POST /api/batch/cancel — Cancel batch
@router.post("/cancel")
async def cancel_batch():
    """Cancel batch (current book finishes, rest cancelled)."""
    pass

# GET /api/batch/history — Get batch history
@router.get("/history")
async def get_batch_history():
    """Get list of past batch runs with results."""
    pass
```

### 5. Resource Monitoring API

**File:** `src/api/monitoring_routes.py` (NEW)

```python
router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])

# GET /api/monitoring/resources — Current resource usage
@router.get("/resources")
async def get_resources():
    """Get current disk, memory, CPU usage."""
    pass  # Return resource_monitor.to_dict()

# GET /api/monitoring/model — Model lifecycle stats
@router.get("/model")
async def get_model_stats():
    """Get model usage stats (chunks generated, time since reload, etc.)."""
    pass  # Return model_manager.to_dict()

# POST /api/monitoring/model/reload — Force model reload
@router.post("/model/reload")
async def force_model_reload():
    """Force immediate model reload (cooldown)."""
    pass  # Call model_manager.force_reload()

# GET /api/monitoring/capacity — Estimate remaining capacity
@router.get("/capacity")
async def get_capacity_estimate():
    """Estimate how many more books can fit on disk."""
    pass  # Return resource_monitor.estimate_remaining_capacity(books_remaining)
```

### 6. Batch QA Approval

**File:** `src/api/qa_routes.py` (MODIFIED)

Add batch operations to the existing QA routes:

```python
# POST /api/qa/batch-approve — Approve all passing chapters for a book
@router.post("/qa/batch-approve/{book_id}")
async def batch_approve_book(book_id: int, approve_warnings: bool = False):
    """
    Approve all chapters for a book that passed automated QA.

    Args:
        book_id: Book ID
        approve_warnings: If True, also approve chapters with warnings (not just PASS)

    Returns:
        {"approved": N, "skipped": M, "flagged": K}
    """
    pass

# POST /api/qa/batch-approve-all — Approve all passing chapters across ALL books
@router.post("/qa/batch-approve-all")
async def batch_approve_all(approve_warnings: bool = False):
    """Approve all passing chapters across all books."""
    pass

# GET /api/qa/catalog-summary — Catalog-wide QA summary
@router.get("/qa/catalog-summary")
async def get_catalog_qa_summary():
    """
    Returns:
        {
            "total_books": 873,
            "books_all_approved": 650,
            "books_with_flags": 23,
            "books_pending_qa": 200,
            "total_chapters": 43650,
            "chapters_approved": 38000,
            "chapters_flagged": 150,
            "chapters_pending": 5500
        }
    """
    pass
```

### 7. Batch Export

**File:** `src/api/export_routes.py` (MODIFIED)

Add batch export endpoint:

```python
# POST /api/export/batch — Export all ready books
@router.post("/export/batch")
async def batch_export(
    formats: List[str] = ["mp3", "m4b"],
    include_only_approved: bool = True,
    skip_already_exported: bool = True,
):
    """
    Queue exports for all books that have all chapters QA-approved.

    Returns:
        {"queued": N, "skipped": M, "not_ready": K}
    """
    pass

# GET /api/export/batch/progress — Batch export progress
@router.get("/export/batch/progress")
async def get_batch_export_progress():
    """Get progress of batch export operation."""
    pass
```

### 8. Catalog Progress Dashboard (Frontend)

**File:** `frontend/src/pages/CatalogDashboard.jsx` (NEW)

A dedicated page showing the overall production status of all 873 books.

**Layout:**

```
┌─────────────────────────────────────────────────────────┐
│  CATALOG PROGRESS                                       │
│  ███████████████████░░░░░░░  650 / 873 (74.5%)          │
│                                                         │
│  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐     │
│  │ 650  │  │ 23   │  │ 200  │  │ 3    │  │ 0    │     │
│  │ Done │  │ QA   │  │ Gen  │  │ Fail │  │ Queue│     │
│  └──────┘  └──────┘  └──────┘  └──────┘  └──────┘     │
│                                                         │
│  SYSTEM RESOURCES                                       │
│  Disk: ████████░░  450 GB / 1 TB (45%)                 │
│  RAM:  ██████░░░░  12.5 GB / 32 GB (39%)               │
│  Model: 47/50 chapters until cooldown                   │
│                                                         │
│  BATCH STATUS: Running                                  │
│  Current: "The Count of Monte Cristo" (ch 47/117)      │
│  ETA: ~18 days remaining                                │
│  Speed: ~45 min/book avg                                │
│                                                         │
│  [Pause Batch] [Cancel Batch] [Force Model Reload]     │
│                                                         │
│  RECENT ACTIVITY                                        │
│  ✅ "Pride and Prejudice" — completed (32 min)         │
│  ✅ "1984" — completed (28 min)                        │
│  ⚠️ "War and Peace" — 2 chapters flagged               │
│  ❌ "Ulysses" — failed (encoding error ch 12)          │
│                                                         │
│  QUICK ACTIONS                                          │
│  [Batch Approve All Passing] [Batch Export All Ready]  │
│  [Retry All Failed] [View Flagged Books]               │
└─────────────────────────────────────────────────────────┘
```

**Features:**
- Overall progress bar (books completed / total)
- Status breakdown cards (completed, QA review, generating, failed, queued)
- System resource gauges (disk, memory, model cooldown)
- Active batch status with ETA
- Batch control buttons (pause, resume, cancel, force reload)
- Recent activity feed (last 20 book completions/failures)
- Quick action buttons for batch QA approval and batch export
- Auto-refresh every 5 seconds via polling

**Data Sources:**
- `GET /api/batch/progress` — batch status
- `GET /api/monitoring/resources` — system resources
- `GET /api/monitoring/model` — model stats
- `GET /api/qa/catalog-summary` — QA overview
- `GET /api/library?sort=updated_at&limit=20` — recent activity

### 9. Frontend Route & Navigation

**File:** `frontend/src/App.jsx` (MODIFIED)

Add route:
```jsx
<Route path="/catalog" element={<CatalogDashboard />} />
```

**File:** `frontend/src/components/AppShell.jsx` (MODIFIED)

Add navigation link:
```jsx
<NavLink to="/catalog">Catalog</NavLink>
```

Place it as the FIRST nav item since it's the primary production management view.

### 10. Database Schema Updates

**File:** `src/database.py` (MODIFIED)

Add batch tracking table:

```python
class BatchRun(Base):
    __tablename__ = "batch_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    total_books: Mapped[int] = mapped_column(default=0)
    books_completed: Mapped[int] = mapped_column(default=0)
    books_failed: Mapped[int] = mapped_column(default=0)
    books_skipped: Mapped[int] = mapped_column(default=0)
    started_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    elapsed_seconds: Mapped[float] = mapped_column(default=0.0)
    avg_seconds_per_book: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = mapped_column(default=func.now())

class BatchBookStatus(Base):
    __tablename__ = "batch_book_status"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(100), ForeignKey("batch_runs.batch_id"), index=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    chapters_total: Mapped[int] = mapped_column(default=0)
    chapters_completed: Mapped[int] = mapped_column(default=0)
    chapters_failed: Mapped[int] = mapped_column(default=0)
    error_message: Mapped[Optional[str]] = mapped_column(nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    duration_seconds: Mapped[float] = mapped_column(default=0.0)
```

Add indexes to existing tables for production query performance:

```python
# In Chapter model — add index on status for fast filtering
status: Mapped[str] = mapped_column(String(20), index=True, default="pending")

# In GenerationJob model — add index on status
status: Mapped[str] = mapped_column(String(20), index=True, default="queued")
```

### 11. Thread Safety for Queue Manager

**File:** `src/pipeline/queue_manager.py` (MODIFIED)

Add a lock for the `self.jobs` dict to prevent race conditions:

```python
import threading

class QueueManager:
    def __init__(self, ...):
        ...
        self._jobs_lock = threading.Lock()

    def _update_job(self, job_id: str, **kwargs):
        with self._jobs_lock:
            if job_id in self.jobs:
                for key, value in kwargs.items():
                    setattr(self.jobs[job_id], key, value)

    def get_job(self, job_id: str):
        with self._jobs_lock:
            return self.jobs.get(job_id)
```

Apply this pattern to ALL reads and writes to `self.jobs` throughout the file.

### 12. Consecutive Failure Threshold Configuration

**File:** `src/pipeline/queue_manager.py` (MODIFIED)

Make the consecutive failure threshold configurable and increase default:

```python
# In config.py
consecutive_failure_threshold: int = Field(
    default=5,
    ge=1,
    le=20,
    description="Number of consecutive chapter failures before pausing a book"
)
```

Also add distinction between transient and permanent failures:
- Transient (timeout, network, memory): retry with backoff
- Permanent (invalid text, corrupted file): skip chapter, continue to next

---

## Acceptance Criteria

### Model Manager
- [ ] ModelManager class tracks chunks/chapters generated
- [ ] Automatic reload after 50 chapters (configurable)
- [ ] Automatic reload on memory pressure > 12 GB
- [ ] Force reload API endpoint works
- [ ] Stats API returns current usage data

### Resource Monitor
- [ ] Monitors disk, memory, CPU
- [ ] Blocks generation when disk < 10 GB
- [ ] Blocks generation when memory > 85%
- [ ] Capacity estimation for remaining books
- [ ] API endpoint returns live data

### Batch Orchestration
- [ ] Start batch with book IDs or all pending
- [ ] Skip already-exported books
- [ ] Pause/resume/cancel batch
- [ ] Auto-pause on resource constraints
- [ ] Per-book progress tracking
- [ ] ETA calculation based on rolling average
- [ ] Batch history persisted to database

### Batch QA
- [ ] Batch approve all passing chapters for one book
- [ ] Batch approve all passing chapters across all books
- [ ] Catalog-wide QA summary endpoint

### Batch Export
- [ ] Batch export all ready books
- [ ] Progress tracking for batch export
- [ ] Skip already-exported books

### Catalog Dashboard
- [ ] Overall progress bar (N of 873 complete)
- [ ] Status breakdown cards
- [ ] Resource usage gauges (disk, memory, model)
- [ ] Active batch status with ETA
- [ ] Batch control buttons (pause, resume, cancel)
- [ ] Recent activity feed
- [ ] Quick action buttons (batch approve, batch export)
- [ ] Auto-refresh every 5 seconds

### Thread Safety
- [ ] All queue_manager.jobs access uses lock
- [ ] No race conditions under concurrent access

### Database
- [ ] batch_runs table created
- [ ] batch_book_status table created
- [ ] Indexes added to Chapter.status and GenerationJob.status

### Testing Requirements

1. **Model Manager Tests:**
   - [ ] `test_model_manager_initial_load`: engine loaded on first get_engine()
   - [ ] `test_model_manager_cooldown_after_chapters`: reload triggered after threshold
   - [ ] `test_model_manager_force_reload`: immediate reload works
   - [ ] `test_model_manager_stats`: stats tracked correctly

2. **Resource Monitor Tests:**
   - [ ] `test_resource_snapshot`: returns disk/memory/cpu data
   - [ ] `test_resource_check_disk_low`: blocks when disk < threshold
   - [ ] `test_resource_check_memory_high`: blocks when memory > threshold
   - [ ] `test_capacity_estimate`: correct book capacity calculation

3. **Batch Orchestrator Tests:**
   - [ ] `test_batch_start`: batch starts with book list
   - [ ] `test_batch_progress`: progress updates correctly
   - [ ] `test_batch_pause_resume`: pause and resume work
   - [ ] `test_batch_cancel`: cancellation stops processing
   - [ ] `test_batch_skip_exported`: exported books skipped
   - [ ] `test_batch_resource_pause`: auto-pauses on low resources

4. **Batch QA Tests:**
   - [ ] `test_batch_approve_book`: approves all passing chapters
   - [ ] `test_batch_approve_all`: approves across all books
   - [ ] `test_catalog_summary`: returns correct counts

5. **API Tests:**
   - [ ] All batch endpoints return correct responses
   - [ ] All monitoring endpoints return data
   - [ ] Batch export queues correctly

6. **Frontend Tests:**
   - [ ] CatalogDashboard renders with mock data
   - [ ] Progress bar shows correct percentage
   - [ ] Batch control buttons call correct API endpoints
   - [ ] Resource gauges display correctly

---

## File Structure

```
src/
  engines/
    model_manager.py                # NEW: Model lifecycle management
  monitoring/
    __init__.py                     # NEW
    resource_monitor.py             # NEW: Disk/memory/CPU monitoring
  pipeline/
    batch_orchestrator.py           # NEW: Batch generation orchestration
    queue_manager.py                # MODIFIED: thread safety, failure threshold
  api/
    batch_routes.py                 # NEW: Batch API endpoints
    monitoring_routes.py            # NEW: Resource monitoring API
    qa_routes.py                    # MODIFIED: batch QA approval
    export_routes.py                # MODIFIED: batch export
  config.py                         # MODIFIED: new settings
  database.py                       # MODIFIED: new tables, indexes
  main.py                           # MODIFIED: register new routers

frontend/src/
  pages/
    CatalogDashboard.jsx            # NEW: Catalog progress dashboard
  components/
    BatchProgressBar.jsx            # NEW: Overall batch progress
    ResourceGauges.jsx              # NEW: Disk/memory/CPU gauges
    BatchControls.jsx               # NEW: Pause/resume/cancel buttons
    RecentActivityFeed.jsx          # NEW: Activity log
    QuickActions.jsx                # NEW: Batch approve/export buttons
  App.jsx                           # MODIFIED: add /catalog route
  components/AppShell.jsx           # MODIFIED: add Catalog nav link

tests/
  test_model_manager.py             # NEW
  test_resource_monitor.py          # NEW
  test_batch_orchestrator.py        # NEW
  test_batch_api.py                 # NEW
  test_batch_qa.py                  # NEW
  test_monitoring_api.py            # NEW
  CatalogDashboard.test.jsx         # NEW
```

---

## Commit Message

```
[PROMPT-18] Production scale — batch orchestration, resource monitoring, catalog dashboard

- Add ModelManager for TTS engine lifecycle (cooldown/restart after 50 chapters)
- Add ResourceMonitor for disk/memory/CPU tracking with generation gating
- Add BatchOrchestrator for multi-book generation with pause/resume/cancel
- Add batch QA approval endpoints (per-book and catalog-wide)
- Add batch export endpoint for all ready books
- Add resource monitoring API (disk, memory, model stats)
- Add CatalogDashboard frontend page with progress, resources, controls
- Add thread safety locks to QueueManager
- Add database indexes for Chapter.status and GenerationJob.status
- Add batch_runs and batch_book_status tables
- Increase consecutive failure threshold to 5 (configurable)
- Comprehensive tests for all new systems
```

---

**Status:** READY FOR IMPLEMENTATION
**Estimated Effort:** 20-30 hours
**Dependencies:** PROMPT-17 (production hardening)
