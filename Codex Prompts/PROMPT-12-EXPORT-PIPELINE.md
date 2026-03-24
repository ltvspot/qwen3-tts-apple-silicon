# PROMPT-12: Audio Export Pipeline (MP3 & M4B)

**Objective:** Create a complete audio export pipeline that concatenates chapters, inserts silence, normalizes levels, and exports to MP3 and M4B with embedded metadata.

**Owner:** Codex
**Reference:** CLAUDE.md, PROJECT-STATE.md, PROMPT-08 (Generation), PROMPT-11 (QA System)

---

## Scope

### Export Pipeline

#### File: `src/pipeline/exporter.py`

**Main Export Function:**
```python
async def export_book(
    book_id: int,
    export_formats: List[str] = ['mp3', 'm4b'],
    include_only_approved: bool = True
) -> ExportResult:
    """
    Export a completed book to MP3 and M4B formats.

    Args:
        book_id: Book ID to export
        export_formats: List of formats ('mp3', 'm4b')
        include_only_approved: If True, only include chapters with manual_status='approved'
                              If False, include all chapters that are not 'flagged'

    Returns: ExportResult with paths to exported files
    """
    pass
```

**Processing Steps:**

1. **Gather Chapter Files**
   - Load opening credits WAV (from PROMPT-08)
   - Load chapter WAVs in order (ch 0, 1, 2, ... N)
   - Load closing credits WAV (from PROMPT-08)
   - Verify all files exist and are valid WAV
   - If QA-approved-only mode: skip flagged chapters

2. **Build Audio Sequence with Silence**
   ```
   opening_credits.wav  (auto-generated)
   [silence: 3.0 seconds]
   ch0-introduction.wav (if present)
   [silence: 2.0 seconds]
   ch1.wav
   [silence: 2.0 seconds]
   ch2.wav
   ...
   [silence: 2.0 seconds]
   chN.wav
   [silence: 3.0 seconds]
   closing_credits.wav  (auto-generated)
   ```
   - Opening credits silence: 3.0s (configurable via settings)
   - Chapter silence: 2.0s default (configurable via settings)
   - Closing credits silence: 3.0s (configurable via settings)

3. **Concatenate All Audio**
   - Use pydub to concatenate WAV files
   - Generate intermediate master WAV file
   - Path: `outputs/{book_id}-{slug}/master.wav` (temporary)

4. **LUFS Normalization**
   - Normalize to -19 LUFS (loudness standard for audiobooks)
   - Use ffmpeg `loudnorm` filter
   - Implement:
     ```python
     def normalize_loudness(input_wav: str, output_wav: str, target_lufs: float = -19.0):
         """Normalize audio to target LUFS using ffmpeg loudnorm filter."""
         cmd = [
             'ffmpeg', '-i', input_wav,
             '-af', f'loudnorm=I={target_lufs}:TP=-1.5:LRA=11',
             '-y', output_wav
         ]
         subprocess.run(cmd, check=True, capture_output=True)
     ```

5. **Export to MP3**
   - Codec: MP3 (MPEG-1 Audio Layer 3)
   - Bitrate: 192 kbps CBR (Constant Bit Rate)
   - Sample Rate: 44.1 kHz
   - Channels: Mono
   - ffmpeg command:
     ```bash
     ffmpeg -i master.wav \
       -codec:a libmp3lame \
       -b:a 192k \
       -ar 44100 \
       -ac 1 \
       -metadata title="Book Title" \
       -metadata artist="Author Name" \
       -metadata album="Book Title" \
       -metadata comment="Narrated by Kent Zimering" \
       output.mp3
     ```
   - Embed ID3 metadata:
     - Title: Book title
     - Artist: Book author
     - Album: Book title
     - Comment: "Narrated by Kent Zimering"
     - Album art: Placeholder 200x200px image

6. **Export to M4B (with Chapter Markers)**
   - Codec: AAC (Advanced Audio Coding)
   - Bitrate: 128 kbps
   - Sample Rate: 44.1 kHz
   - Channels: Mono
   - Chapter markers: One marker per chapter
   - ffmpeg command:
     ```bash
     ffmpeg -i master.wav \
       -codec:a aac \
       -b:a 128k \
       -ar 44100 \
       -ac 1 \
       output.m4b
     ```
   - Add chapter markers:
     - Use mp4chaps tool or ffmpeg to insert chapter metadata
     - Chapter 0 (opening): Start 0s, end at opening_duration + 3s silence
     - Chapter 1: Start at opening end, title from manuscript
     - ... (all chapters)
     - Closing: Final chapter, start at last chapter end + silence
     - Chapter format: `Chapter Title` (timestamp: 00:00:00)

#### Silence Generation
```python
def create_silence(duration_seconds: float, sample_rate: int = 44100) -> AudioSegment:
    """Generate silence audio segment."""
    return AudioSegment.silent(duration=int(duration_seconds * 1000))
```

#### Concatenation Implementation
```python
async def concatenate_chapters(
    book_id: int,
    include_only_approved: bool = True,
    chapter_silence_seconds: float = 2.0,
    opening_silence_seconds: float = 3.0,
    closing_silence_seconds: float = 3.0
) -> str:
    """
    Concatenate all chapter WAVs with silence.

    Returns: Path to concatenated master WAV file
    """
    from pydub import AudioSegment

    # Load book and chapters from DB
    book = db.query(Book).filter(Book.id == book_id).first()
    chapters = db.query(Chapter).filter(
        Chapter.book_id == book_id
    ).order_by(Chapter.chapter_n).all()

    # Build audio sequence
    audio_sequence = []

    # 1. Opening credits
    opening_wav = f"outputs/{book_id}-{book.slug}/chapters/00-opening-credits.wav"
    audio_sequence.append(AudioSegment.from_wav(opening_wav))
    audio_sequence.append(create_silence(opening_silence_seconds))

    # 2. Chapters (respecting QA approval)
    for chapter in chapters:
        # Check QA status if approval-only mode
        if include_only_approved:
            qa = db.query(QAStatus).filter(
                QAStatus.book_id == book_id,
                QAStatus.chapter_n == chapter.chapter_n
            ).first()
            if qa and qa.manual_status == 'flagged':
                continue  # Skip flagged chapters

        # Load chapter audio
        chapter_wav = f"outputs/{book_id}-{book.slug}/chapters/{chapter.chapter_n:02d}-*.wav"
        chapter_files = glob.glob(chapter_wav)
        if chapter_files:
            audio_sequence.append(AudioSegment.from_wav(chapter_files[0]))
            audio_sequence.append(create_silence(chapter_silence_seconds))

    # 3. Closing credits
    closing_wav = f"outputs/{book_id}-{book.slug}/chapters/{len(chapters)+2:02d}-closing-credits.wav"
    audio_sequence.append(AudioSegment.from_wav(closing_wav))
    audio_sequence.append(create_silence(closing_silence_seconds))

    # Concatenate all
    master = sum(audio_sequence[1:], audio_sequence[0])

    # Save
    master_path = f"outputs/{book_id}-{book.slug}/master.wav"
    master.export(master_path, format="wav")

    return master_path
```

#### Metadata Embedding
```python
def embed_id3_metadata(
    mp3_path: str,
    title: str,
    artist: str,
    album: str,
    album_art_path: str = None,
    comment: str = "Narrated by Kent Zimering"
) -> None:
    """Embed ID3 metadata into MP3 file."""
    from mutagen.id3 import ID3, TIT2, TPE1, TALB, COMM, APIC

    audio = ID3(mp3_path)

    # Basic metadata
    audio['TIT2'] = TIT2(encoding=3, text=[title])
    audio['TPE1'] = TPE1(encoding=3, text=[artist])
    audio['TALB'] = TALB(encoding=3, text=[album])
    audio['COMM'] = COMM(encoding=3, lang='eng', desc='', text=[comment])

    # Album art (if provided)
    if album_art_path and Path(album_art_path).exists():
        with open(album_art_path, 'rb') as f:
            audio['APIC'] = APIC(
                encoding=3,
                mime='image/jpeg',
                type=3,  # Cover front
                desc='Cover Art',
                data=f.read()
            )

    audio.save()
```

#### Chapter Markers for M4B
```python
def add_chapter_markers_m4b(
    m4b_path: str,
    chapters: List[ChapterMarker]
) -> None:
    """
    Add chapter markers to M4B file.

    ChapterMarker = namedtuple('ChapterMarker', ['title', 'start_ms', 'end_ms'])
    """
    # Use mutagen-m4b or mp4chaps
    # For MVP, use ffmpeg with metadata file approach
    pass
```

### Backend API Endpoint

#### POST /api/book/{id}/export
**Purpose:** Trigger book export

**Request Body:**
```json
{
  "formats": ["mp3", "m4b"],
  "include_only_approved": true
}
```

**Response:**
```json
{
  "book_id": 15,
  "export_status": "processing",
  "job_id": "export_15_20260324_143000",
  "formats_requested": ["mp3", "m4b"],
  "expected_completion_seconds": 120,
  "started_at": "2026-03-24T14:30:00Z"
}
```

#### GET /api/book/{id}/export/status
**Purpose:** Check export progress

**Response:**
```json
{
  "book_id": 15,
  "export_status": "completed",
  "formats": {
    "mp3": {
      "status": "completed",
      "file_size_bytes": 487923048,
      "file_name": "The Count of Monte Cristo.mp3",
      "download_url": "/api/book/15/export/download/mp3",
      "completed_at": "2026-03-24T14:32:00Z"
    },
    "m4b": {
      "status": "completed",
      "file_size_bytes": 341234056,
      "file_name": "The Count of Monte Cristo.m4b",
      "download_url": "/api/book/15/export/download/m4b",
      "completed_at": "2026-03-24T14:33:00Z"
    }
  },
  "qa_report": {
    "chapters_included": 117,
    "chapters_approved": 115,
    "chapters_flagged": 2,
    "chapters_warnings": 12,
    "export_approved": true
  }
}
```

#### GET /api/book/{id}/export/download/{format}
**Purpose:** Download exported file

**Implementation:** Serve MP3 or M4B file with proper HTTP headers
```python
@router.get("/book/{id}/export/download/{format}")
async def download_export(id: int, format: str):
    # Validate format
    if format not in ['mp3', 'm4b']:
        raise HTTPException(status_code=400, detail="Invalid format")

    # Find export file
    book = db.query(Book).filter(Book.id == id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    export_path = f"outputs/{id}-{book.slug}/exports/{book.title}.{format}"
    if not Path(export_path).exists():
        raise HTTPException(status_code=404, detail="Export file not found")

    return FileResponse(
        path=export_path,
        media_type=f"audio/{format}",
        filename=f"{book.title}.{format}"
    )
```

### QA Report

#### File: `outputs/{book_id}-{slug}/exports/qa_report.json`

Generated during export, summarizes all chapter QA results:
```json
{
  "book_id": 15,
  "book_title": "The Count of Monte Cristo",
  "export_date": "2026-03-24T14:33:00Z",
  "chapters_included": 115,
  "chapters_approved": 115,
  "chapters_flagged": 2,
  "chapters_warnings": 12,
  "export_approved": true,
  "notes": "2 chapters flagged due to long silence gaps. 12 chapters with warnings (mostly duration). All approved chapters exported.",
  "chapter_summary": [
    {
      "chapter_n": 0,
      "chapter_title": "Opening Credits",
      "status": "pass",
      "file_size_bytes": 145230,
      "duration_seconds": 23.45
    },
    {
      "chapter_n": 1,
      "chapter_title": "Chapter I: Edmond Dantès",
      "status": "pass",
      "file_size_bytes": 3847293,
      "duration_seconds": 847.23
    }
  ]
}
```

### Frontend: Export Button & Download Links

#### Book Detail Page (MODIFIED from PROMPT-05)

Add export section to book detail:
```jsx
// In frontend/src/pages/BookDetail.jsx

<section className="border-t pt-6">
  <h2 className="text-lg font-semibold mb-4">Export Options</h2>

  {exportStatus === 'idle' ? (
    <button
      onClick={handleExportClick}
      className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
    >
      Export Audiobook
    </button>
  ) : exportStatus === 'processing' ? (
    <div>
      <p className="text-sm text-gray-600">Export in progress...</p>
      <ExportProgressBar progress={exportProgress} />
    </div>
  ) : exportStatus === 'completed' ? (
    <div className="space-y-3">
      {exportData.formats.mp3 && (
        <DownloadCard
          format="MP3"
          size={formatFileSize(exportData.formats.mp3.file_size_bytes)}
          url={exportData.formats.mp3.download_url}
        />
      )}
      {exportData.formats.m4b && (
        <DownloadCard
          format="M4B (with chapter markers)"
          size={formatFileSize(exportData.formats.m4b.file_size_bytes)}
          url={exportData.formats.m4b.download_url}
        />
      )}
    </div>
  ) : null}
</section>
```

#### Export Dialog Component

**File: `frontend/src/components/ExportDialog.jsx`**

Modal that appears when "Export" button clicked:
- Checkboxes: Include MP3 | Include M4B (both checked by default)
- Checkbox: "Include only QA-approved chapters" (checked by default)
- "Export" button
- "Cancel" button

#### Download Card Component

**File: `frontend/src/components/DownloadCard.jsx`

Display exported file with download link:
- Format icon (MP3 or M4B)
- Format name
- File size (formatted: "487.9 MB")
- Download button (HTML `<a href>` or custom fetch)
- "Copy link" button (copy download URL to clipboard)

### Database Schema Updates

#### New Table: `export_jobs`
```sql
CREATE TABLE export_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id INTEGER NOT NULL UNIQUE,
  export_status TEXT NOT NULL,  -- 'processing', 'completed', 'error'
  formats_requested TEXT NOT NULL,  -- JSON: ['mp3', 'm4b']
  include_only_approved INTEGER DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  error_message TEXT,
  qa_report TEXT,  -- JSON (from qa_report.json)
  FOREIGN KEY (book_id) REFERENCES books(id)
);
```

#### Extend `books` table:
```sql
ALTER TABLE books ADD COLUMN (
  last_export_date TIMESTAMP,
  export_status TEXT  -- 'idle', 'processing', 'completed', 'error'
);
```

### File Output Structure

```
outputs/
  {book_id}-{slug}/
    chapters/
      00-opening-credits.wav
      01-introduction.wav
      02-ch01-{slug}.wav
      ...
      XX-closing-credits.wav
    exports/
      {book_title}.mp3                 # 192kbps CBR, 44.1kHz mono
      {book_title}.m4b                 # AAC 128kbps with chapter markers
      qa_report.json                   # QA summary
      master.wav                       # Temporary concatenated WAV (delete after export)
```

---

## Acceptance Criteria

### Functional Requirements - Export Pipeline
- [ ] Opening credits, chapters, closing credits concatenated in correct order
- [ ] Silence inserted: 3s after opening, 2s between chapters, 3s before closing
- [ ] All silence durations configurable via Settings (PROMPT-13)
- [ ] Flagged chapters excluded from export (if approval-only mode)
- [ ] LUFS normalization applied (target -19 LUFS)
- [ ] MP3 exported: 192kbps CBR, 44.1kHz, mono
- [ ] M4B exported: AAC 128kbps, 44.1kHz, mono
- [ ] ID3 metadata embedded in MP3 (title, artist, album, comment, album art)
- [ ] Chapter markers embedded in M4B
- [ ] QA report generated with chapter summary
- [ ] Export process handles missing chapters gracefully

### Functional Requirements - API
- [ ] `POST /api/book/{id}/export` triggers export job
- [ ] `GET /api/book/{id}/export/status` returns current export status
- [ ] `GET /api/book/{id}/export/download/{format}` serves audio file
- [ ] API returns proper HTTP headers for audio downloads
- [ ] File size accurately reported in status response

### Functional Requirements - UI
- [ ] Export button visible on Book Detail page
- [ ] Export dialog allows format selection
- [ ] Export dialog has approval-only checkbox
- [ ] Export progress bar shows during processing
- [ ] Download links appear when export completes
- [ ] Download buttons functional (trigger browser download)
- [ ] File sizes displayed in human-readable format (MB/GB)
- [ ] "Copy link" buttons work

### Code Quality
- [ ] All ffmpeg commands properly escaped (security)
- [ ] Subprocess calls use `capture_output` to avoid hangs
- [ ] Proper error handling for missing audio files
- [ ] Cleanup temporary files (master.wav) after export
- [ ] Logging of all export steps
- [ ] Database transactions for export job tracking
- [ ] Frontend components use functional syntax

### Testing Requirements

1. **Export Pipeline Unit Tests:**
   - [ ] `test_concatenate_chapters_basic`: 3 chapters concatenated in order
   - [ ] `test_concatenate_chapters_with_silence`: Silence inserted correctly
   - [ ] `test_silence_generation`: Silence audio correct duration
   - [ ] `test_exclude_flagged_chapters`: Flagged chapters skipped
   - [ ] `test_mp3_export`: Valid MP3 created with correct bitrate
   - [ ] `test_m4b_export`: Valid M4B created with chapter markers
   - [ ] `test_metadata_embedding`: ID3 tags readable in exported MP3
   - [ ] `test_loudness_normalization`: LUFS normalization applied

2. **API Tests:**
   - [ ] `POST /api/book/{id}/export` returns job_id
   - [ ] `GET /api/book/{id}/export/status` returns processing status
   - [ ] `GET /api/book/{id}/export/status` returns completed status with download URLs
   - [ ] `GET /api/book/{id}/export/download/mp3` serves valid audio file
   - [ ] `GET /api/book/{id}/export/download/m4b` serves valid audio file
   - [ ] 404 error if export job not found
   - [ ] 404 error if export not completed yet

3. **Integration Tests:**
   - [ ] Generate book → approve all chapters → export → verify MP3/M4B valid
   - [ ] Generate book → flag 2 chapters → export (approval-only) → verify flagged chapters excluded
   - [ ] Export job status updates from processing to completed
   - [ ] Download exported file → verify playable in audio player

4. **Manual Testing Scenario:**
   - [ ] Generate a 3-chapter test book
   - [ ] Open Book Detail page, scroll to Export section
   - [ ] Click "Export Audiobook"
   - [ ] Confirm both MP3 and M4B are selected, approval-only is checked
   - [ ] Click "Export"
   - [ ] Verify progress bar appears
   - [ ] Wait for completion
   - [ ] Verify both MP3 and M4B download links appear
   - [ ] Download MP3 → verify playable in audio player
   - [ ] Download M4B → verify playable with chapter markers visible
   - [ ] Verify file sizes match API response
   - [ ] Open exported MP3 properties → verify ID3 metadata correct

---

## File Structure

```
src/
  pipeline/
    exporter.py                       # NEW: Export logic

frontend/src/
  components/
    ExportDialog.jsx                  # NEW: Export options dialog
    ExportProgressBar.jsx             # NEW: Progress display
    DownloadCard.jsx                  # NEW: Download link card
  pages/
    BookDetail.jsx                    # MODIFIED: Add export section

tests/
  test_exporter.py                    # NEW: Export pipeline tests
  test_export_api.py                  # NEW: Export API tests
```

---

## Implementation Notes

### File Size Estimation
For export progress bar:
- Estimated size = sum(chapter durations) * bitrate / 8
- MP3: sum_seconds * 192000 / 8 bytes
- M4B: sum_seconds * 128000 / 8 bytes

### Error Handling
- If any chapter WAV missing: skip with warning (don't abort export)
- If ffmpeg not installed: raise clear error with installation instructions
- If disk space low: abort with user-friendly message
- If metadata embedding fails: warn but continue (audio is still valid)

### Performance Optimization
- Use threading for MP3 and M4B encoding in parallel
- Cache concatenated master.wav if exporting multiple times
- Consider chunked encoding for very large books (500+ chapters)

### LUFS Normalization Details
- -19 LUFS is ACX standard (Audible audiobook spec)
- TP (True Peak): -1.5dBFS
- LRA (Loudness Range): 11 LU
- Use ffmpeg `loudnorm` filter (always two-pass recommended for accuracy)

### Chapter Markers for M4B
For MVP, use simple ffmpeg metadata:
```bash
ffmpeg -i master.wav -c:a aac -b:a 128k \
  -metadata "title=Chapter 1" \
  output.m4b
```

For full chapter support, use mutagen library or mp4chaps tool post-export.

---

## References

- CLAUDE.md § File Naming, Critical Business Rules
- PROMPT-08: Generation Pipeline (audio generation details)
- PROMPT-11: QA System (approval status)
- PROMPT-13: Settings (silence durations configuration)
- ffmpeg documentation: https://ffmpeg.org/
- pydub: https://github.com/jiaaro/pydub
- mutagen (ID3): https://mutagen.readthedocs.io/
- ACX audiobook specs: https://www.amazon.com/gp/help/customer/display.html?nodeId=G5Z8R4VYMYXD8DZJ

---

## Commit Message

```
[PROMPT-12] Implement audio export pipeline (MP3 & M4B)

- Create concatenation pipeline with configurable silence durations
- Implement LUFS loudness normalization (-19 LUFS ACX standard)
- Export MP3: 192kbps CBR, 44.1kHz, mono with ID3 metadata
- Export M4B: AAC 128kbps with chapter markers
- Generate QA report summarizing chapter results
- Add export API endpoint (POST and GET status)
- Add export download endpoint
- Create frontend export dialog and download UI
- Comprehensive tests for export pipeline and API
```

---

**Status:** READY FOR IMPLEMENTATION
**Estimated Effort:** 10-14 hours (ffmpeg integration, metadata, UI, testing)
**Dependencies:** PROMPT-01 (schema), PROMPT-08 (generation), PROMPT-11 (QA system), PROMPT-13 (settings)
