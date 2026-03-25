# PROMPT-28: Bulk Generation Hardening (100+ Books)

## Context
The system works for single books and small batches, but breaks down at scale. This prompt hardens the batch generation pipeline for reliable production runs of 100+ books overnight.

---

## Task 1: Percentage-Based Failure Thresholds

### Problem
Current: 5 consecutive failures stops the entire batch. With 100 books (20,000+ chunks), a 0.5% failure rate produces ~100 failures. Five consecutive failures are statistically likely, killing the batch unnecessarily.

### Implementation

In `src/config.py`, replace:
```python
consecutive_failure_threshold: int = Field(default=5, ge=1, le=20)
```

With:
```python
class FailureThresholdSettings(BaseModel):
    """Configurable failure thresholds for batch resilience."""
    # Stop batch if failure RATE exceeds this percentage of total chunks processed
    max_failure_rate_percent: float = Field(default=5.0, ge=1.0, le=50.0)
    # Stop batch if this many failures happen in a ROW (safety valve)
    max_consecutive_failures: int = Field(default=10, ge=3, le=50)
    # Minimum chunks processed before rate-based threshold activates
    # (prevents stopping on 1 failure out of 2 chunks = 50% rate)
    min_chunks_for_rate: int = Field(default=50, ge=10, le=500)
```

In `queue_manager.py`, update the failure check logic:
```python
def _should_stop_batch(self) -> tuple[bool, str]:
    total_chunks = self._stats.total_chunks_processed
    failed_chunks = self._stats.failed_chunks
    consecutive = self._stats.consecutive_failures

    # Consecutive failure safety valve (always active)
    if consecutive >= settings.max_consecutive_failures:
        return True, f"Stopped: {consecutive} consecutive failures"

    # Rate-based threshold (only after minimum chunks processed)
    if total_chunks >= settings.min_chunks_for_rate:
        rate = (failed_chunks / total_chunks) * 100
        if rate > settings.max_failure_rate_percent:
            return True, f"Stopped: {rate:.1f}% failure rate ({failed_chunks}/{total_chunks})"

    return False, ""
```

---

## Task 2: Per-Book Failure Isolation

### Problem
If book #15 fails catastrophically (corrupt EPUB, unparseable text), it should be skipped — not kill the entire batch.

### Implementation

In the batch orchestrator or queue worker:
```python
async def _process_book(self, job: GenerationJob, db_session: Session):
    try:
        result = await self._generator.generate_book(...)
        if result["status"] == "failed":
            job.status = "failed"
            job.error_message = f"Book generation failed: {result.get('errors', 'unknown')}"
            self._stats.books_failed += 1
        else:
            job.status = "completed"
            self._stats.books_completed += 1
    except Exception as e:
        # Isolate the failure — don't propagate to batch
        logger.error(f"Book {job.book_id} failed with exception: {e}", exc_info=True)
        job.status = "failed"
        job.error_message = f"Unhandled error: {str(e)[:500]}"
        self._stats.books_failed += 1
    finally:
        db_session.commit()
        # CRITICAL: Continue to next book, do NOT re-raise
```

Add a batch summary that shows: `Completed: 85 | Failed: 3 | Skipped: 12 | Remaining: 0`

---

## Task 3: Pre-Batch Resource Estimation

### Problem
User queues 100 books. System starts generating, fills the disk after 20 books, then pauses indefinitely. No upfront warning.

### Implementation

Add endpoint `POST /api/batch/estimate`:
```python
@router.post("/api/batch/estimate")
async def estimate_batch_resources(request: BatchEstimateRequest, db: Session = Depends(get_db)):
    """Estimate resources needed for a batch run before starting."""
    books = get_books_for_batch(request.book_ids, db)

    total_chapters = sum(b.chapter_count for b in books)
    total_words = sum(b.word_count or 0 for b in books)

    # Audio estimation: ~2.5 words/sec → WAV at 24kHz 16-bit mono = 48KB/sec
    estimated_audio_seconds = total_words / 2.5
    estimated_wav_bytes = estimated_audio_seconds * 48000  # 48KB/sec for 24kHz 16-bit mono
    estimated_export_bytes = estimated_wav_bytes * 0.1  # MP3 ~10% of WAV size

    # Disk estimation
    total_disk_needed_gb = (estimated_wav_bytes + estimated_export_bytes) / (1024**3)

    # Time estimation
    rtf = 1.5  # Real-time factor for 1.7B model
    estimated_generation_hours = (estimated_audio_seconds * rtf) / 3600
    estimated_export_hours = (estimated_audio_seconds * 0.3) / 3600  # Export ~30% of generation time
    total_hours = estimated_generation_hours + estimated_export_hours

    # Resource check
    import shutil
    disk_free_gb = shutil.disk_usage("/").free / (1024**3)

    return {
        "books": len(books),
        "total_chapters": total_chapters,
        "total_words": total_words,
        "estimated_audio_hours": round(estimated_audio_seconds / 3600, 1),
        "estimated_disk_gb": round(total_disk_needed_gb, 1),
        "estimated_generation_hours": round(total_hours, 1),
        "disk_free_gb": round(disk_free_gb, 1),
        "can_proceed": disk_free_gb > total_disk_needed_gb * 1.2,  # 20% buffer
        "warnings": _generate_warnings(books, disk_free_gb, total_disk_needed_gb),
    }
```

Frontend: Show this estimate before the user confirms the batch start, with warnings highlighted.

---

## Task 4: Batch Scheduling Strategy

### Problem
A 500-chapter book blocks everything for hours. Short books wait behind it unnecessarily.

### Implementation

Add to batch settings:
```python
class BatchSchedulingStrategy(str, Enum):
    FIFO = "fifo"                # First in, first out (current default)
    SHORTEST_FIRST = "shortest"  # Process shortest books first
    LONGEST_FIRST = "longest"    # Process longest books first
    PRIORITY = "priority"        # User-defined priority
```

In the queue manager, when selecting the next job:
```python
def _select_next_job(self, strategy: BatchSchedulingStrategy) -> GenerationJob | None:
    pending = [j for j in self.jobs.values() if j.status == "pending"]
    if not pending:
        return None

    if strategy == BatchSchedulingStrategy.SHORTEST_FIRST:
        return min(pending, key=lambda j: j.total_chapters)
    elif strategy == BatchSchedulingStrategy.LONGEST_FIRST:
        return max(pending, key=lambda j: j.total_chapters)
    elif strategy == BatchSchedulingStrategy.PRIORITY:
        return min(pending, key=lambda j: (j.priority, j.created_at))
    else:  # FIFO
        return min(pending, key=lambda j: j.created_at)
```

Default: `SHORTEST_FIRST` — produces completed audiobooks quickly, giving the user feedback and confidence that the system is working.

---

## Task 5: Streaming Export (Memory-Safe Concatenation)

### Problem
The exporter loads ALL chapter audio into memory simultaneously. A 50-chapter book at 100MB/chapter = 5GB RAM spike, causing OOM on 16GB Macs already running the 6GB model.

### Implementation

Replace in-memory concatenation in `src/pipeline/exporter.py`:

```python
async def _concatenate_chapters_streaming(
    self,
    chapter_paths: list[str],
    output_path: str,
    inter_chapter_silence_ms: int = 1500,
    target_sample_rate: int = 44100,
):
    """Concatenate chapters using streaming to avoid memory explosion.

    Maximum 2 chapters in memory at any time.
    """
    import wave
    import struct

    # First pass: compute total frames for WAV header
    total_frames = 0
    for path in chapter_paths:
        with wave.open(path, 'r') as wf:
            total_frames += wf.getnframes()
        # Add silence between chapters
        total_frames += int(target_sample_rate * inter_chapter_silence_ms / 1000)

    # Second pass: stream chapters to output
    silence_samples = b'\x00\x00' * int(target_sample_rate * inter_chapter_silence_ms / 1000)

    with wave.open(output_path, 'w') as out:
        out.setnchannels(1)
        out.setsampwidth(2)  # 16-bit
        out.setframerate(target_sample_rate)

        for i, path in enumerate(chapter_paths):
            # Load one chapter at a time
            audio = AudioSegment.from_wav(path)

            # Resample if needed
            if audio.frame_rate != target_sample_rate:
                audio = audio.set_frame_rate(target_sample_rate)

            # Ensure mono
            if audio.channels > 1:
                audio = audio.set_channels(1)

            # Write chapter audio
            out.writeframes(audio.raw_data)

            # Write inter-chapter silence (except after last chapter)
            if i < len(chapter_paths) - 1:
                out.writeframes(silence_samples)

            # Explicitly free memory
            del audio

            logger.info(f"Exported chapter {i+1}/{len(chapter_paths)}")
```

This keeps max 1 chapter (~100MB) in memory at a time instead of all chapters simultaneously.

---

## Task 6: ffmpeg Subprocess Timeouts

### Problem
ffmpeg can hang indefinitely on corrupt audio input. No timeout exists.

### Implementation

Create a utility wrapper:
```python
# src/utils/subprocess_utils.py
import subprocess

FFMPEG_TIMEOUT_SECONDS = 120  # 2 minutes per operation

def run_ffmpeg(args: list[str], timeout: int = FFMPEG_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    """Run ffmpeg with a timeout to prevent hangs on corrupt audio."""
    try:
        return subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            check=True,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"ffmpeg timed out after {timeout}s. "
            f"Command: {' '.join(args[:5])}... "
            f"This usually means corrupt audio input."
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"ffmpeg failed with code {e.returncode}: {e.stderr.decode()[:500]}"
        )
```

Replace all `subprocess.run(["ffmpeg", ...])` calls in `exporter.py` with `run_ffmpeg(...)`.

---

## Task 7: Tests

Create `tests/test_bulk_generation.py`:
1. `test_percentage_threshold_stops_batch` — 6% failure rate triggers stop
2. `test_consecutive_threshold_stops_batch` — 10 consecutive failures triggers stop
3. `test_rate_not_checked_below_minimum` — rate check inactive below 50 chunks
4. `test_book_failure_isolated` — one book failing doesn't stop batch
5. `test_batch_estimate_disk` — resource estimation calculates correctly
6. `test_batch_estimate_warns_low_disk` — warns when free disk < needed * 1.2
7. `test_shortest_first_scheduling` — 5-chapter book processed before 50-chapter
8. `test_streaming_export_low_memory` — concatenation uses < 200MB for 20 chapters
9. `test_ffmpeg_timeout` — corrupt audio triggers timeout error, not hang

All existing tests must still pass.
