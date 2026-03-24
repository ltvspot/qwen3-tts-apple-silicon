# PROMPT-10: Production Queue Management

**Objective:** Create a queue management system to track and manage active audiobook generation jobs with real-time progress, ETAs, and control operations.

**Owner:** Codex
**Reference:** CLAUDE.md, PROJECT-STATE.md, PROMPT-08 (Generation Pipeline), PROMPT-09 (Progress UI)

---

## Scope

### Backend Queue Management

#### Data Model: Job Queue
**New Database Table: `generation_jobs`**
```sql
CREATE TABLE generation_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id INTEGER NOT NULL UNIQUE,
  job_type TEXT NOT NULL,  -- 'single_chapter', 'full_book', 'batch_all'
  status TEXT NOT NULL,  -- 'queued', 'generating', 'paused', 'completed', 'error'
  chapters_total INTEGER NOT NULL,
  chapters_completed INTEGER NOT NULL DEFAULT 0,
  chapters_failed INTEGER NOT NULL DEFAULT 0,
  current_chapter_n INTEGER,  -- Chapter being generated, or null if not started
  priority INTEGER NOT NULL DEFAULT 0,  -- Higher = generate first (0-100)
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  started_at TIMESTAMP,
  paused_at TIMESTAMP,
  completed_at TIMESTAMP,
  eta_seconds INTEGER,  -- Estimated seconds until completion
  avg_seconds_per_chapter FLOAT,  -- Observed average from this job
  error_message TEXT,
  FOREIGN KEY (book_id) REFERENCES books(id)
);

CREATE TABLE job_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL,
  book_id INTEGER NOT NULL,
  action TEXT,  -- 'paused', 'resumed', 'cancelled', 'completed', 'error'
  details TEXT,  -- JSON string with context
  timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (job_id) REFERENCES generation_jobs(id),
  FOREIGN KEY (book_id) REFERENCES books(id)
);
```

#### Relationship to Books Table
- `books.current_job_id` (foreign key to `generation_jobs.id`)
- When a job completes or is cancelled, set `current_job_id = NULL`

### Backend API Endpoints

#### GET /api/queue
**Purpose:** List all active and recent generation jobs

**Query Parameters:**
- `limit` (int, default 50): Max jobs to return
- `offset` (int, default 0): Pagination offset
- `status` (str, optional): Filter by status ('queued', 'generating', 'completed', 'error')

**Response:**
```json
{
  "jobs": [
    {
      "job_id": 1,
      "book_id": 15,
      "book_title": "The Count of Monte Cristo",
      "book_author": "Alexandre Dumas",
      "job_type": "full_book",
      "status": "generating",
      "priority": 10,
      "chapters_total": 117,
      "chapters_completed": 23,
      "chapters_failed": 0,
      "current_chapter_n": 24,
      "current_chapter_title": "Chapter XXIV: The Luncheon",
      "created_at": "2026-03-24T09:00:00Z",
      "started_at": "2026-03-24T09:05:00Z",
      "paused_at": null,
      "completed_at": null,
      "eta_seconds": 1845,
      "avg_seconds_per_chapter": 15.3,
      "progress_percent": 19.66
    }
  ],
  "total_count": 5,
  "active_job_count": 2,
  "queue_stats": {
    "total_books_in_queue": 5,
    "total_chapters": 400,
    "estimated_total_time_seconds": 6000
  }
}
```

**Implementation Notes:**
- `progress_percent = (chapters_completed / chapters_total) * 100`
- `eta_seconds = (chapters_total - chapters_completed) * avg_seconds_per_chapter`
- Return only jobs from last 7 days (for completed/error jobs) to keep list manageable
- Order by: status (generating first), then priority (high first), then created_at

#### GET /api/queue/{job_id}
**Purpose:** Detailed view of a single job with chapter breakdown

**Response:**
```json
{
  "job_id": 1,
  "book_id": 15,
  "book_title": "The Count of Monte Cristo",
  "status": "generating",
  "priority": 10,
  "chapters_total": 117,
  "chapters_completed": 23,
  "chapters_failed": 0,
  "current_chapter_n": 24,
  "created_at": "2026-03-24T09:00:00Z",
  "started_at": "2026-03-24T09:05:00Z",
  "eta_seconds": 1845,
  "avg_seconds_per_chapter": 15.3,
  "chapter_breakdown": [
    {
      "chapter_n": 0,
      "chapter_title": "Opening Credits",
      "status": "completed",
      "duration_seconds": 23.45,
      "completed_at": "2026-03-24T09:10:00Z"
    },
    {
      "chapter_n": 1,
      "chapter_title": "Chapter I",
      "status": "completed",
      "duration_seconds": 847.23,
      "completed_at": "2026-03-24T09:25:00Z"
    },
    {
      "chapter_n": 24,
      "chapter_title": "Chapter XXIV",
      "status": "generating",
      "progress_seconds": 320.5,
      "expected_total_seconds": 847.0,
      "started_at": "2026-03-24T11:45:00Z"
    }
  ],
  "history": [
    {
      "action": "resumed",
      "details": "User resumed from pause",
      "timestamp": "2026-03-24T11:00:00Z"
    }
  ]
}
```

#### POST /api/queue/{job_id}/pause
**Purpose:** Pause a generation job

**Request Body:**
```json
{
  "reason": "User paused (optional)"
}
```

**Response:**
```json
{
  "job_id": 1,
  "status": "paused",
  "paused_at": "2026-03-24T12:00:00Z"
}
```

**Implementation Notes:**
- Only pause jobs with status 'queued' or 'generating'
- If currently generating a chapter, finish that chapter then pause before next
- Update `paused_at` timestamp
- Store action in `job_history`

#### POST /api/queue/{job_id}/resume
**Purpose:** Resume a paused generation job

**Response:**
```json
{
  "job_id": 1,
  "status": "generating",
  "paused_at": null
}
```

**Implementation Notes:**
- Only resume jobs with status 'paused'
- Resume from `current_chapter_n + 1` (don't re-generate the current chapter)
- Actually, resume from `current_chapter_n` if it failed; otherwise `current_chapter_n + 1`
- Update `paused_at = NULL`
- Store action in `job_history`

#### POST /api/queue/{job_id}/cancel
**Purpose:** Cancel a generation job

**Request Body:**
```json
{
  "reason": "User cancelled (optional)"
}
```

**Response:**
```json
{
  "job_id": 1,
  "status": "error",
  "error_message": "Job cancelled by user"
}
```

**Implementation Notes:**
- Only cancel jobs with status 'queued', 'generating', or 'paused'
- Stop current chapter generation gracefully
- Do NOT delete chapters already generated
- Set `completed_at = NOW()`
- Store action in `job_history`

#### PUT /api/queue/{job_id}/priority
**Purpose:** Reorder a job in the queue

**Request Body:**
```json
{
  "priority": 50,  -- New priority (0-100, higher = earlier)
  "action": "move_up"  -- or "move_down" (alternative UI)
}
```

**Response:**
```json
{
  "job_id": 1,
  "priority": 50,
  "queue_position": 2  -- Among queued/generating jobs
}
```

**Implementation Notes:**
- Increase priority of current job by 10 (move_up) or decrease by 10 (move_down)
- Clamp priority to 0-100
- Reorder jobs on next check: generating jobs always first, then by priority, then by created_at
- Don't interrupt currently generating job, but change order for next jobs to start

#### POST /api/queue/batch-all
**Purpose:** Queue all parsed books for generation

**Request Body:**
```json
{
  "priority": 0,
  "voice": "Ethan",
  "emotion": "neutral",
  "speed": 1.0
}
```

**Response:**
```json
{
  "jobs_created": 120,
  "books_queued": 120,
  "total_chapters": 1842,
  "estimated_completion_seconds": 27630,
  "message": "All 120 parsed books queued for generation"
}
```

**Implementation Notes:**
- Query all books with status 'parsed' (from PROMPT-02)
- Create a `generation_jobs` record for each book with `job_type='full_book'`
- Each job starts with status 'queued'
- Store voice/emotion/speed in job metadata or pass to generation pipeline
- Return count of jobs created and total chapters to generate

### Frontend: Queue Page

#### File: `frontend/src/pages/Queue.jsx`

**Layout:**
1. **Header Section**
   - Page title: "Production Queue"
   - Stats cards:
     - Total books in queue
     - Total chapters remaining
     - Estimated completion time (formatted: "2d 14h 33m")
     - Active jobs count

2. **Controls Section**
   - "Generate All Parsed Books" button
     - Opens dialog with voice/emotion/speed selectors
     - Confirmation message: "Queue X books for generation?"
   - Filter dropdown: All | Queued | Generating | Completed | Error
   - Sort dropdown: Priority (high first) | Created (newest first) | Status

3. **Jobs List**
   - One row per job (or expandable card view)
   - **Per-job columns:**
     - Book title (clickable link to book detail)
     - Book author
     - Status badge (color-coded: blue=queued, green=generating, gray=paused, orange=error, checkmark=completed)
     - Progress bar (% complete with chapter count overlay)
     - Per-chapter breakdown (hoverable mini-chart or expandable)
       - Simple visualization: "23 / 117 chapters"
       - Expand to show per-chapter grid or timeline
     - ETA (formatted: "1h 30m" or "2m")
     - Action buttons:
       - Pause (if generating/queued)
       - Resume (if paused)
       - Cancel (if not completed)
       - Move Up / Move Down (if queued)
       - View Details (link to detailed job view)

4. **Detailed Job View Modal** (or separate page)
   - Show full chapter breakdown
   - List failed chapters with error messages
   - History log (paused/resumed/cancelled actions)
   - Option to retry failed chapters

#### UI Components (New)

**File: `frontend/src/components/QueueJobCard.jsx`**
- Display a single job with progress bar, controls, and quick stats
- Props: `job` (from API), `onAction` (pause/resume/cancel callback)

**File: `frontend/src/components/QueueStats.jsx`**
- Display queue statistics (total books, chapters, ETA)
- Props: `stats` (from API queue response)

**File: `frontend/src/components/JobDetailsModal.jsx`**
- Detailed view of a single job with chapter breakdown
- Expandable chapter rows showing individual status
- History timeline

### Real-Time Updates

#### Polling Strategy (from PROMPT-09, extended for Queue)
- `GET /api/queue` every 3-5 seconds to update job list
- Clear polling when user navigates away from Queue page
- Fallback error handling: retry up to 3 times, then pause polling

#### Optional WebSocket Enhancement
- For future scaling, consider WebSocket subscriptions to job updates
- For MVP, polling is sufficient

### Database Updates

**Extend `books` table:**
```sql
ALTER TABLE books ADD COLUMN (
  current_job_id INTEGER,
  FOREIGN KEY (current_job_id) REFERENCES generation_jobs(id)
);
```

---

## Acceptance Criteria

### Functional Requirements
- [ ] `GET /api/queue` returns list of jobs with correct shape
- [ ] Jobs ordered by: generating first, then by priority, then by created_at
- [ ] Job statistics (chapters_completed, avg_seconds_per_chapter) update as generation progresses
- [ ] ETA calculation accurate to within 20%
- [ ] `POST /api/queue/{job_id}/pause` pauses job (finishes current chapter first)
- [ ] `POST /api/queue/{job_id}/resume` resumes from next chapter
- [ ] `POST /api/queue/{job_id}/cancel` stops job and marks as error
- [ ] `PUT /api/queue/{job_id}/priority` reorders jobs in queue
- [ ] `POST /api/queue/batch-all` creates jobs for all parsed books
- [ ] Queue page displays all jobs with accurate progress
- [ ] Per-job progress bar shows chapters_completed / chapters_total
- [ ] Per-chapter breakdown expandable and shows status for each chapter
- [ ] Action buttons (Pause/Resume/Cancel) functional and update job status
- [ ] Stats cards show accurate totals and ETA
- [ ] "Generate All Parsed Books" button opens dialog with voice/emotion/speed options

### Code Quality
- [ ] All database queries use SQLAlchemy ORM
- [ ] All API responses validated with Pydantic models
- [ ] All endpoints have proper error handling (404 for missing jobs, etc.)
- [ ] Async/await used for long-running operations
- [ ] Frontend uses functional components with hooks
- [ ] Polling interval cleared on component unmount
- [ ] No memory leaks from polling or event listeners
- [ ] All button actions disabled during pending request
- [ ] PropTypes or TypeScript types on all components

### Testing Requirements

1. **Backend API Tests (pytest):**
   - [ ] Test `GET /api/queue` returns correct job list and ordering
   - [ ] Test `GET /api/queue/{job_id}` returns detailed job view
   - [ ] Test `POST /api/queue/{job_id}/pause` pauses job and updates status
   - [ ] Test `POST /api/queue/{job_id}/resume` resumes paused job
   - [ ] Test `POST /api/queue/{job_id}/cancel` cancels job without deleting generated chapters
   - [ ] Test `PUT /api/queue/{job_id}/priority` changes priority correctly
   - [ ] Test `POST /api/queue/batch-all` creates job per parsed book
   - [ ] Test ETA calculation with mock generation speed
   - [ ] Test job_history entries created for pause/resume/cancel actions
   - [ ] Test 404 errors when accessing non-existent jobs

2. **Frontend Component Tests:**
   - [ ] Queue page renders without errors
   - [ ] Job list displays all jobs with correct order
   - [ ] Progress bars show correct percentage
   - [ ] Status badges have correct colors
   - [ ] Pause button disables when job not generating
   - [ ] Resume button only appears for paused jobs
   - [ ] Cancel button removes job from list (optimistically)
   - [ ] "Generate All Parsed Books" button opens dialog
   - [ ] Stats cards update every 3-5 seconds via polling

3. **Integration Tests:**
   - [ ] Create book → queue generation → pause → resume → verify chapters generated
   - [ ] Batch queue all books → verify N jobs created with correct chapter counts
   - [ ] Change priority → verify queue reorders (next job to start uses new order)
   - [ ] Cancel job mid-generation → verify already-generated chapters preserved
   - [ ] Network error during polling → verify graceful retry and error message

4. **Manual Testing Scenario:**
   - [ ] Queue 5 books for generation
   - [ ] Verify Queue page shows all 5 jobs
   - [ ] Verify stats show correct total chapters and ETA
   - [ ] Pause a queued job → verify status changes to paused
   - [ ] Pause a generating job → verify finishes current chapter then pauses
   - [ ] Resume a paused job → verify continues from next chapter
   - [ ] Cancel a job → verify job marked as error, chapters already generated are preserved
   - [ ] Increase priority of a queued job → verify it moves up in queue
   - [ ] Navigate away and back to Queue page → verify polling resumes
   - [ ] Refresh page → verify job state persists from database

---

## File Structure

```
src/
  models/
    generation_job.py             # NEW: SQLAlchemy model for generation_jobs table
    job_history.py                # NEW: SQLAlchemy model for job_history table
  api/
    queue_routes.py               # NEW: All queue endpoints
    job_service.py                # NEW: Business logic for job management
  database/
    migrations/
      xxx_create_generation_jobs.py  # NEW: Database migration

frontend/src/
  pages/
    Queue.jsx                     # NEW: Production queue page
  components/
    QueueJobCard.jsx              # NEW: Single job display component
    QueueStats.jsx                # NEW: Queue statistics component
    JobDetailsModal.jsx           # NEW: Detailed job view modal

tests/
  test_queue_api.py               # NEW: Queue endpoint tests
  test_job_service.py             # NEW: Job management logic tests
  test_queue_page.py              # NEW: React component tests
```

---

## Implementation Notes

### Job Ordering Logic
```python
# Pseudo-code for GET /api/queue ordering
def sort_jobs(jobs):
  # 1. Separate by status
  generating = [j for j in jobs if j.status == 'generating']
  others = [j for j in jobs if j.status != 'generating']

  # 2. Sort each group
  generating.sort(by='started_at', reverse=True)  # Newest first
  others.sort(by=['priority', 'created_at'], reverse=[True, False])

  return generating + others
```

### ETA Calculation
```python
def calculate_eta(job):
  if job.chapters_completed == 0:
    return None  # Can't estimate until first chapter completes

  avg_per_chapter = job.avg_seconds_per_chapter
  remaining = job.chapters_total - job.chapters_completed
  return int(remaining * avg_per_chapter)
```

### Pause/Resume Strategy
- **Pause mid-chapter:** Finish current chapter, store state, then pause
- **Resume:** Continue from `current_chapter_n + 1` (current was completed during pause)
- **Cancellation:** Stop immediately, don't generate next chapter, save completed chapters

### Batch All Implementation
```python
async def batch_all(priority: int, voice: str, emotion: str, speed: float):
  parsed_books = db.query(Book).filter(Book.status == 'parsed').all()
  created = 0
  for book in parsed_books:
    job = GenerationJob(
      book_id=book.id,
      job_type='full_book',
      status='queued',
      chapters_total=len(book.chapters),
      priority=priority
    )
    db.add(job)
    created += 1
  db.commit()
  return created
```

---

## References

- CLAUDE.md § Critical Business Rules
- PROMPT-08: Generation Pipeline (job execution)
- PROMPT-09: Generation UI (progress display)
- SQLAlchemy: https://docs.sqlalchemy.org/
- Pydantic: https://docs.pydantic.dev/

---

## Commit Message

```
[PROMPT-10] Create production queue management system

- Add generation_jobs and job_history database tables
- Implement queue API endpoints: GET /api/queue, pause/resume/cancel, priority reordering
- Add POST /api/queue/batch-all for bulk job creation
- Create Queue.jsx page with job list, stats, and controls
- Implement job card component with progress bar and action buttons
- Real-time queue updates via polling (every 3-5 seconds)
- Comprehensive tests for queue API and UI
```

---

**Status:** READY FOR IMPLEMENTATION
**Estimated Effort:** 10-12 hours (includes queue logic, UI, and comprehensive testing)
**Dependencies:** PROMPT-01 (schema), PROMPT-08 (generation), PROMPT-09 (progress UI)
