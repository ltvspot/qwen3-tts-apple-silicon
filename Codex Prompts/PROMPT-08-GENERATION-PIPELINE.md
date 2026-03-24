# PROMPT-08: Audio Generation Pipeline & Job Queue

**Objective:** Create the backend audio generation pipeline with chapter-by-chapter processing, async job queue management, and progress tracking.

**Project Context:** See `/sessions/focused-happy-pasteur/mnt/Coding Folder/Qwen3-TTS - Test/CLAUDE.md` for conventions and architecture.

---

## Scope & Requirements

### 1. Generation Pipeline

**File:** `src/pipeline/generator.py`

Orchestrate the generation of audiobooks chapter by chapter.

```python
import logging
import asyncio
from pathlib import Path
from typing import Optional, Callable
from sqlalchemy.orm import Session
from pydub.audio_segment import AudioSegment

from src.database import Book, Chapter, GenerationJob
from src.engines.base import TTSEngine
from src.engines.chunker import TextChunker, AudioStitcher
from src.config import OUTPUTS_PATH, NARRATOR_NAME

logger = logging.getLogger(__name__)

class AudiobookGenerator:
    """
    Generate audiobook files from parsed chapters.

    Handles:
    - Loading TTS engine
    - Processing each chapter (chunk, generate, stitch, save)
    - Updating database with progress
    - Error handling and recovery
    """

    def __init__(self, engine: TTSEngine):
        """
        Initialize generator with TTS engine.

        Args:
            engine: TTSEngine instance (should be pre-loaded)
        """
        self.engine = engine
        self.output_path = Path(OUTPUTS_PATH)

    async def generate_book(
        self,
        book_id: int,
        db_session: Session,
        progress_callback: Optional[Callable] = None,
    ) -> dict:
        """
        Generate full audiobook (all chapters).

        Args:
            book_id: ID of book to generate
            db_session: SQLAlchemy session
            progress_callback: Optional async callback(chapter_num, progress_pct)

        Returns:
            Dict with keys:
            - status: "success" | "failed"
            - total_chapters: int
            - generated_chapters: int
            - failed_chapters: List[int]
            - total_duration: float
            - errors: List[str]

        Process:
        1. Load book and chapters from DB
        2. For each chapter: generate_chapter()
        3. Update book status and duration
        4. Return summary
        """
        book = db_session.query(Book).filter(Book.id == book_id).first()
        if not book:
            raise ValueError(f"Book {book_id} not found")

        chapters = db_session.query(Chapter).filter(
            Chapter.book_id == book_id
        ).order_by(Chapter.number).all()

        if not chapters:
            raise ValueError(f"No chapters found for book {book_id}")

        logger.info(f"Starting generation for book {book_id}: {len(chapters)} chapters")

        generated = 0
        failed = []
        errors = []
        total_duration = 0.0

        for i, chapter in enumerate(chapters):
            try:
                progress = (i / len(chapters)) * 100
                if progress_callback:
                    await progress_callback(chapter.number, progress)

                duration = await self.generate_chapter(
                    book_id, chapter, db_session
                )
                total_duration += duration
                generated += 1

            except Exception as e:
                error_msg = f"Chapter {chapter.number}: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
                failed.append(chapter.number)
                chapter.status = "failed"
                db_session.commit()

        # Update book status
        book.status = "generated"
        book.updated_at = asyncio.get_event_loop().time()
        db_session.commit()

        logger.info(
            f"Generation complete for book {book_id}: "
            f"{generated}/{len(chapters)} chapters, {total_duration:.1f}s total"
        )

        return {
            "status": "success" if len(failed) == 0 else "partial",
            "total_chapters": len(chapters),
            "generated_chapters": generated,
            "failed_chapters": failed,
            "total_duration": total_duration,
            "errors": errors,
        }

    async def generate_chapter(
        self,
        book_id: int,
        chapter: Chapter,
        db_session: Session,
    ) -> float:
        """
        Generate audio for a single chapter.

        Args:
            book_id: Book ID
            chapter: Chapter object
            db_session: SQLAlchemy session

        Returns:
            float: Duration of generated audio in seconds

        Process:
        1. Validate chapter has text
        2. Chunk text at sentence boundaries
        3. Generate audio for each chunk via TTS
        4. Stitch chunks with crossfade
        5. Save WAV file
        6. Update chapter DB record (audio_path, duration_seconds, status)
        7. Return duration
        """
        if not chapter.text_content:
            raise ValueError(f"Chapter {chapter.number} has no text content")

        logger.info(
            f"Generating audio for book {book_id}, "
            f"chapter {chapter.number} ({chapter.word_count} words)"
        )

        # Update status in DB
        chapter.status = "generating"
        db_session.commit()

        try:
            # Chunk text
            chunks = TextChunker.chunk_text(
                chapter.text_content,
                self.engine.max_chunk_chars
            )
            logger.debug(f"Chapter {chapter.number}: {len(chunks)} chunks")

            # Generate audio for each chunk
            audio_chunks = []
            for i, chunk in enumerate(chunks):
                # Determine speed: opening/closing credits at 0.9x, chapters at 1.0x
                speed = 0.9 if chapter.type in ["opening_credits", "closing_credits"] else 1.0

                audio = await asyncio.to_thread(
                    self.engine.generate,
                    chunk,
                    "Ethan",  # Default voice (should come from book settings)
                    None,  # emotion
                    speed,
                )
                audio_chunks.append(audio)

            # Stitch chunks
            if len(audio_chunks) > 1:
                final_audio = AudioStitcher.stitch(audio_chunks)
            else:
                final_audio = audio_chunks[0]

            # Save WAV file
            audio_path = self._get_chapter_audio_path(book_id, chapter)
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            final_audio.export(str(audio_path), format="wav")

            # Update chapter record
            duration = len(final_audio) / 1000.0  # milliseconds to seconds
            chapter.audio_path = str(audio_path.relative_to(self.output_path))
            chapter.duration_seconds = duration
            chapter.status = "generated"
            db_session.commit()

            logger.info(
                f"Generated chapter {chapter.number}: "
                f"{duration:.1f}s, saved to {chapter.audio_path}"
            )

            return duration

        except Exception as e:
            chapter.status = "failed"
            db_session.commit()
            raise

    def _get_chapter_audio_path(self, book_id: int, chapter: Chapter) -> Path:
        """
        Get the audio file path for a chapter.

        Path format: outputs/{book_id}-{slug}/chapters/{nn}-{chapter-slug}.wav

        Args:
            book_id: Book ID
            chapter: Chapter object

        Returns:
            Path object
        """
        book = chapter.book  # Assumes relationship is loaded

        # Create slug from book title
        slug = book.title.lower().replace(" ", "-")[:30]
        folder = f"{book_id}-{slug}"

        # Chapter filename
        chapter_slug = chapter.title.lower().replace(" ", "-")[:20] if chapter.title else f"ch{chapter.number}"
        filename = f"{chapter.number:02d}-{chapter_slug}.wav"

        return self.output_path / folder / "chapters" / filename
```

---

### 2. Job Queue Manager

**File:** `src/pipeline/queue_manager.py`

Simple async-based job queue (no external broker like Celery).

```python
import asyncio
import logging
from typing import Dict, Optional, List
from dataclasses import dataclass
from enum import Enum
from datetime import datetime
from sqlalchemy.orm import Session

from src.database import GenerationJob, Book, Chapter

logger = logging.getLogger(__name__)

class JobStatus(str, Enum):
    """Job status enumeration."""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class JobInfo:
    """In-memory job information."""
    job_id: int
    book_id: int
    chapter_id: Optional[int]
    status: JobStatus
    progress: float  # 0-100
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    error_message: Optional[str]

class GenerationQueue:
    """
    Simple async job queue for audio generation.

    Features:
    - FIFO queue (first in, first out)
    - Single worker (processes one job at a time)
    - Track progress per chapter
    - Cancel jobs
    - No external broker (uses asyncio)
    """

    def __init__(self, max_workers: int = 1):
        """
        Initialize queue.

        Args:
            max_workers: Max concurrent jobs (default 1 for stability)
        """
        self.max_workers = max_workers
        self.queue: asyncio.Queue = asyncio.Queue()
        self.jobs: Dict[int, JobInfo] = {}  # in-memory job tracking
        self.active_jobs: set = set()  # IDs of running jobs
        self.workers = []

    async def start(self, db_session_maker, generator):
        """
        Start queue workers.

        Args:
            db_session_maker: SQLAlchemy session factory
            generator: AudiobookGenerator instance
        """
        logger.info(f"Starting generation queue with {self.max_workers} worker(s)")
        self.workers = [
            asyncio.create_task(self._worker(db_session_maker, generator))
            for _ in range(self.max_workers)
        ]

    async def stop(self):
        """Stop queue workers gracefully."""
        logger.info("Stopping generation queue")
        for _ in range(self.max_workers):
            await self.queue.put(None)  # Sentinel to stop workers
        await asyncio.gather(*self.workers, return_exceptions=True)

    async def enqueue_book(
        self,
        book_id: int,
        db_session: Session,
    ) -> int:
        """
        Queue a full book for generation.

        Args:
            book_id: Book to generate
            db_session: SQLAlchemy session

        Returns:
            int: Job ID
        """
        book = db_session.query(Book).filter(Book.id == book_id).first()
        if not book:
            raise ValueError(f"Book {book_id} not found")

        # Create job record
        job = GenerationJob(
            book_id=book_id,
            chapter_id=None,
            status="queued",
            progress=0.0,
        )
        db_session.add(job)
        db_session.commit()

        # Add to queue
        await self.queue.put(job)

        # Track in memory
        self.jobs[job.id] = JobInfo(
            job_id=job.id,
            book_id=book_id,
            chapter_id=None,
            status=JobStatus.QUEUED,
            progress=0.0,
            started_at=None,
            completed_at=None,
            error_message=None,
        )

        logger.info(f"Enqueued book {book_id} as job {job.id}")
        return job.id

    async def enqueue_chapter(
        self,
        book_id: int,
        chapter_number: int,
        db_session: Session,
    ) -> int:
        """
        Queue a single chapter for generation.

        Args:
            book_id: Book ID
            chapter_number: Chapter number
            db_session: SQLAlchemy session

        Returns:
            int: Job ID
        """
        chapter = db_session.query(Chapter).filter(
            Chapter.book_id == book_id,
            Chapter.number == chapter_number,
        ).first()

        if not chapter:
            raise ValueError(f"Chapter {chapter_number} not found in book {book_id}")

        # Create job record
        job = GenerationJob(
            book_id=book_id,
            chapter_id=chapter.id,
            status="queued",
            progress=0.0,
        )
        db_session.add(job)
        db_session.commit()

        # Add to queue
        await self.queue.put(job)

        # Track in memory
        self.jobs[job.id] = JobInfo(
            job_id=job.id,
            book_id=book_id,
            chapter_id=chapter.id,
            status=JobStatus.QUEUED,
            progress=0.0,
            started_at=None,
            completed_at=None,
            error_message=None,
        )

        logger.info(f"Enqueued chapter {chapter_number} of book {book_id} as job {job.id}")
        return job.id

    async def cancel_job(self, job_id: int, db_session: Session) -> bool:
        """
        Cancel a queued or running job.

        Args:
            job_id: Job to cancel
            db_session: SQLAlchemy session

        Returns:
            bool: True if cancelled, False if already completed
        """
        job_info = self.jobs.get(job_id)
        if not job_info:
            return False

        if job_info.status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]:
            return False

        # Update status
        job_info.status = JobStatus.CANCELLED
        db_job = db_session.query(GenerationJob).filter(GenerationJob.id == job_id).first()
        if db_job:
            db_job.status = "cancelled"
            db_session.commit()

        logger.info(f"Cancelled job {job_id}")
        return True

    async def get_job_status(self, job_id: int) -> Optional[JobInfo]:
        """
        Get status of a job.

        Args:
            job_id: Job ID

        Returns:
            JobInfo or None if not found
        """
        return self.jobs.get(job_id)

    async def get_all_jobs(self) -> List[JobInfo]:
        """
        Get all jobs (running, queued, completed).

        Returns:
            List of JobInfo objects
        """
        return list(self.jobs.values())

    # ========================================================================
    # Internal Worker
    # ========================================================================

    async def _worker(self, db_session_maker, generator):
        """
        Worker loop: process jobs from queue.

        Args:
            db_session_maker: SQLAlchemy session factory
            generator: AudiobookGenerator instance
        """
        while True:
            try:
                # Get next job from queue
                job = await self.queue.get()

                if job is None:  # Sentinel to stop
                    logger.info("Worker stopping")
                    break

                await self._process_job(job, db_session_maker, generator)

            except Exception as e:
                logger.error(f"Worker error: {e}", exc_info=True)

    async def _process_job(self, job, db_session_maker, generator):
        """
        Process a single job.

        Args:
            job: GenerationJob from DB
            db_session_maker: SQLAlchemy session factory
            generator: AudiobookGenerator instance
        """
        db_session = db_session_maker()
        job_info = self.jobs[job.id]

        try:
            # Mark as running
            job_info.status = JobStatus.RUNNING
            job_info.started_at = datetime.now()
            job.status = "running"
            job.started_at = job_info.started_at
            db_session.commit()

            logger.info(f"Processing job {job.id}")

            # Progress callback to update in-memory progress
            async def progress_callback(chapter_num, progress_pct):
                job_info.progress = progress_pct
                job.progress = progress_pct
                db_session.commit()

            # Generate
            if job.chapter_id:
                # Single chapter
                chapter = db_session.query(Chapter).filter(
                    Chapter.id == job.chapter_id
                ).first()
                await generator.generate_chapter(job.book_id, chapter, db_session)
            else:
                # Full book
                result = await generator.generate_book(
                    job.book_id,
                    db_session,
                    progress_callback,
                )

                if result["status"] == "failed":
                    raise Exception(f"Generation failed: {result['errors']}")

            # Mark as completed
            job_info.status = JobStatus.COMPLETED
            job_info.progress = 100.0
            job_info.completed_at = datetime.now()
            job.status = "completed"
            job.progress = 100.0
            job.completed_at = job_info.completed_at
            db_session.commit()

            logger.info(f"Job {job.id} completed successfully")

        except Exception as e:
            # Mark as failed
            job_info.status = JobStatus.FAILED
            job_info.error_message = str(e)
            job_info.completed_at = datetime.now()
            job.status = "failed"
            job.error_message = str(e)
            job.completed_at = job_info.completed_at
            db_session.commit()

            logger.error(f"Job {job.id} failed: {e}")

        finally:
            db_session.close()
```

---

### 3. Generation API Endpoints

**File:** `src/api/generation.py`

Add API endpoints for queuing and monitoring generation.

```python
import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session
from src.database import Book, Chapter
from src.pipeline.queue_manager import GenerationQueue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["generation"])

# Global queue instance
_queue = None

def get_queue() -> GenerationQueue:
    """Get generation queue instance."""
    global _queue
    if _queue is None:
        _queue = GenerationQueue(max_workers=1)
    return _queue

def get_db() -> Session:
    """Dependency for database session."""
    # Implementation in main.py
    ...

# ============================================================================
# Pydantic Models
# ============================================================================

class GenerationRequest(BaseModel):
    """Request to generate audiobook."""
    pass  # No additional params needed

class JobStatus(BaseModel):
    """Generation job status."""
    job_id: int
    book_id: int
    chapter_id: Optional[int]
    status: str  # "queued", "running", "completed", "failed"
    progress: float  # 0-100
    error_message: Optional[str]

# ============================================================================
# Endpoints
# ============================================================================

@router.post("/book/{book_id}/generate")
async def generate_book(
    book_id: int,
    request: GenerationRequest,
    db: Session = Depends(get_db),
) -> dict:
    """
    Queue a full book for audio generation.

    Args:
        book_id: Book ID

    Returns:
        {
            "job_id": int,
            "status": "queued",
            "book_id": int,
            "message": "Book queued for generation..."
        }

    Process:
    1. Check book exists and is parsed
    2. Check all chapters have text content
    3. Enqueue book in generation queue
    4. Return job info
    """
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")

    if book.status != "parsed":
        raise HTTPException(
            status_code=400,
            detail=f"Book must be parsed first. Current status: {book.status}"
        )

    # Check chapters exist and have content
    chapters = db.query(Chapter).filter(Chapter.book_id == book_id).all()
    if not chapters:
        raise HTTPException(status_code=400, detail="Book has no chapters")

    missing_text = [ch.number for ch in chapters if not ch.text_content]
    if missing_text:
        raise HTTPException(
            status_code=400,
            detail=f"Chapters missing text content: {missing_text}"
        )

    try:
        queue = get_queue()
        job_id = await queue.enqueue_book(book_id, db)

        return {
            "job_id": job_id,
            "status": "queued",
            "book_id": book_id,
            "message": f"Book {book_id} queued for generation"
        }

    except Exception as e:
        logger.error(f"Failed to enqueue book {book_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/book/{book_id}/chapter/{chapter_number}/generate")
async def generate_chapter(
    book_id: int,
    chapter_number: int,
    db: Session = Depends(get_db),
) -> dict:
    """
    Queue a single chapter for audio generation.

    Args:
        book_id: Book ID
        chapter_number: Chapter number

    Returns:
        {
            "job_id": int,
            "status": "queued",
            "book_id": int,
            "chapter_number": int,
            "message": "Chapter queued for generation..."
        }
    """
    chapter = db.query(Chapter).filter(
        Chapter.book_id == book_id,
        Chapter.number == chapter_number,
    ).first()

    if not chapter:
        raise HTTPException(status_code=404, detail=f"Chapter not found")

    if not chapter.text_content:
        raise HTTPException(status_code=400, detail="Chapter has no text content")

    try:
        queue = get_queue()
        job_id = await queue.enqueue_chapter(book_id, chapter_number, db)

        return {
            "job_id": job_id,
            "status": "queued",
            "book_id": book_id,
            "chapter_number": chapter_number,
            "message": f"Chapter {chapter_number} queued for generation"
        }

    except Exception as e:
        logger.error(f"Failed to enqueue chapter: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/job/{job_id}")
async def get_job_status(job_id: int) -> JobStatus:
    """
    Get status of a generation job.

    Args:
        job_id: Job ID

    Returns:
        JobStatus with current progress and status
    """
    queue = get_queue()
    job_info = await queue.get_job_status(job_id)

    if not job_info:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return JobStatus(
        job_id=job_info.job_id,
        book_id=job_info.book_id,
        chapter_id=job_info.chapter_id,
        status=job_info.status.value,
        progress=job_info.progress,
        error_message=job_info.error_message,
    )

@router.delete("/job/{job_id}")
async def cancel_job(job_id: int, db: Session = Depends(get_db)) -> dict:
    """
    Cancel a queued or running job.

    Args:
        job_id: Job ID to cancel

    Returns:
        {
            "cancelled": bool,
            "job_id": int,
            "message": str
        }
    """
    queue = get_queue()
    cancelled = await queue.cancel_job(job_id, db)

    if not cancelled:
        raise HTTPException(status_code=400, detail="Job cannot be cancelled")

    return {
        "cancelled": True,
        "job_id": job_id,
        "message": "Job cancelled"
    }

@router.get("/book/{book_id}/chapter/{chapter_number}/audio")
async def get_chapter_audio(
    book_id: int,
    chapter_number: int,
    db: Session = Depends(get_db),
):
    """
    Stream audio file for a chapter.

    Args:
        book_id: Book ID
        chapter_number: Chapter number

    Returns:
        Audio file (WAV format) or 404 if not yet generated
    """
    from fastapi.responses import FileResponse

    chapter = db.query(Chapter).filter(
        Chapter.book_id == book_id,
        Chapter.number == chapter_number,
    ).first()

    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    if not chapter.audio_path:
        raise HTTPException(status_code=404, detail="Audio not yet generated")

    from pathlib import Path
    from src.config import OUTPUTS_PATH

    audio_file = Path(OUTPUTS_PATH) / chapter.audio_path

    if not audio_file.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")

    return FileResponse(
        str(audio_file),
        media_type="audio/wav",
        filename=f"ch{chapter_number}.wav"
    )
```

Add to `src/main.py`:
```python
from src.api.generation import router as generation_router
app.include_router(generation_router)

# Initialize generation queue on startup
@app.on_event("startup")
async def startup_generation_queue():
    from src.api.generation import get_queue
    queue = get_queue()
    await queue.start(SessionLocal, generator)  # TODO: initialize generator

@app.on_event("shutdown")
async def shutdown_generation_queue():
    from src.api.generation import get_queue
    queue = get_queue()
    await queue.stop()
```

---

## Tests

**File:** `tests/test_generation_pipeline.py`

```python
import pytest
import asyncio
from pathlib import Path
from src.pipeline.generator import AudiobookGenerator
from src.pipeline.queue_manager import GenerationQueue, JobStatus
from src.database import Book, Chapter
from src.engines.qwen3_tts import Qwen3TTS

@pytest.mark.asyncio
async def test_generate_chapter(test_db):
    """Test single chapter generation."""
    engine = Qwen3TTS()
    engine.load()

    generator = AudiobookGenerator(engine)

    # Create test book and chapter
    book = Book(
        title="Test Book",
        author="Test Author",
        folder_path="test",
        status="parsed"
    )
    test_db.add(book)
    test_db.commit()

    chapter = Chapter(
        book_id=book.id,
        number=1,
        title="Test Chapter",
        type="chapter",
        text_content="This is a test of the audiobook narrator. Hello world.",
        word_count=10,
        status="pending"
    )
    test_db.add(chapter)
    test_db.commit()

    # Generate
    duration = await generator.generate_chapter(book.id, chapter, test_db)

    # Verify
    assert duration > 0
    assert chapter.status == "generated"
    assert chapter.audio_path is not None
    assert chapter.duration_seconds == duration

    # Verify file exists
    from src.config import OUTPUTS_PATH
    audio_file = Path(OUTPUTS_PATH) / chapter.audio_path
    assert audio_file.exists()

@pytest.mark.asyncio
async def test_generation_queue(test_db):
    """Test generation queue."""
    queue = GenerationQueue(max_workers=1)

    # Create test book
    book = Book(
        title="Test Book",
        author="Test Author",
        folder_path="test",
        status="parsed"
    )
    test_db.add(book)
    test_db.commit()

    # Enqueue
    job_id = await queue.enqueue_book(book.id, test_db)
    assert job_id > 0

    # Check status
    job = await queue.get_job_status(job_id)
    assert job.status == JobStatus.QUEUED
    assert job.progress == 0.0

    # Cancel
    cancelled = await queue.cancel_job(job_id, test_db)
    assert cancelled is True

@pytest.mark.asyncio
async def test_generation_api(client, test_db):
    """Test generation API endpoints."""
    # Create test book
    book = Book(
        title="Test",
        author="Author",
        folder_path="test",
        status="parsed"
    )
    test_db.add(book)
    test_db.commit()

    chapter = Chapter(
        book_id=book.id,
        number=1,
        title="Ch1",
        type="chapter",
        text_content="Test text.",
        word_count=2,
    )
    test_db.add(chapter)
    test_db.commit()

    # Enqueue book
    response = client.post(f"/api/book/{book.id}/generate", json={})
    assert response.status_code == 200
    data = response.json()
    assert "job_id" in data
    assert data["status"] == "queued"

    # Check job status
    job_id = data["job_id"]
    response = client.get(f"/api/job/{job_id}")
    assert response.status_code == 200
    job_status = response.json()
    assert job_status["status"] == "queued"
```

---

## Acceptance Criteria

1. **Audio Generation:**
   - `AudiobookGenerator.generate_chapter()` successfully generates chapter audio
   - Generated WAV file is valid and playable
   - Duration is recorded in chapter.duration_seconds
   - File path is stored in chapter.audio_path
   - chapter.status updated to "generated"

2. **Generation Pipeline:**
   - `generate_book()` processes all chapters sequentially
   - Opening/closing credits generated with slower speed (0.9x)
   - Regular chapters generated at normal speed (1.0x)
   - Text chunking at sentence boundaries
   - Audio chunks stitched with crossfade

3. **Job Queue:**
   - `GenerationQueue.enqueue_book()` adds job to queue
   - `GenerationQueue.enqueue_chapter()` adds single chapter job
   - Jobs processed in FIFO order
   - Job status tracked: queued → running → completed
   - `get_job_status()` returns current progress (0-100%)
   - `cancel_job()` stops queued/running jobs

4. **API Endpoints:**
   - `POST /api/book/{id}/generate` queues full book, returns job_id
   - `POST /api/book/{id}/chapter/{n}/generate` queues single chapter
   - `GET /api/job/{id}` returns job status and progress
   - `DELETE /api/job/{id}` cancels job
   - `GET /api/book/{id}/chapter/{n}/audio` streams audio file

5. **Error Handling:**
   - Returns 404 if book not found
   - Returns 400 if book not parsed
   - Returns 400 if chapters missing text content
   - Generation failures logged and returned in job status
   - Failed chapters don't stop other chapters (graceful failure)

6. **Output Files:**
   - Audio files saved to outputs/{book_id}-{slug}/chapters/{nn}-{title}.wav
   - File naming is consistent and predictable
   - Opening/closing credits have standard names

7. **Tests:**
   - `pytest tests/test_generation_pipeline.py` passes all tests
   - No import errors

8. **Git Commit:**
   - All changes committed with message: `[PROMPT-08] Audio generation pipeline and job queue`

---

## Additional Notes

- **No External Broker:** Uses asyncio.Queue instead of Celery/RabbitMQ for simplicity
- **Single Worker Default:** Conservative approach for stability (can be scaled up later)
- **Progress Tracking:** In-memory tracking with periodic DB updates
- **Error Recovery:** Failed chapters don't block other chapters; full book generation continues
- **Speed Adjustments:** Opening/closing credits at 0.9x for clarity, regular chapters at 1.0x
- **Voice Settings:** Default to "Ethan" voice (should be configurable per book in future)

---

## Reference

- **Project Conventions:** `/sessions/focused-happy-pasteur/mnt/Coding Folder/Qwen3-TTS - Test/CLAUDE.md`
- **FastAPI Async:** https://fastapi.tiangolo.com/async-and-await/
- **SQLAlchemy Sessions:** https://docs.sqlalchemy.org/en/20/orm/session/
