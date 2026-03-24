# PROMPT-14: Voice Cloning Integration

**Objective:** Add voice cloning capability to the Voice Lab, allowing users to create custom voices from reference audio and use them in audiobook generation.

**Owner:** Codex
**Reference:** CLAUDE.md, PROJECT-STATE.md, PROMPT-06 (TTS Engine), PROMPT-07 (Voice Lab)

---

## Scope

### Voice Cloning System

#### Backend: Voice Cloning Logic

**File: `src/engines/voice_cloner.py`**

```python
from pathlib import Path
from typing import Tuple
import subprocess
import numpy as np
from pydub import AudioSegment
import logging

logger = logging.getLogger(__name__)

class VoiceCloner:
    """
    Clone voices using Qwen3-TTS-12Hz-1.7B-Base-8bit model.
    """

    VOICES_DIR = Path("voices")
    SUPPORTED_FORMATS = ['.wav', '.mp3', '.m4a']

    def __init__(self):
        """Initialize voice cloner."""
        self.VOICES_DIR.mkdir(exist_ok=True)

    def clone_voice(
        self,
        voice_name: str,
        reference_audio_path: str,
        transcript: str
    ) -> Tuple[str, str]:
        """
        Clone a voice from reference audio and transcript.

        Args:
            voice_name: Name for the cloned voice (e.g., "kent-zimering")
            reference_audio_path: Path to reference audio file (WAV/MP3/M4A)
            transcript: Text that was spoken in the reference audio

        Returns:
            Tuple of (audio_reference_path, transcript_path)

        Process:
            1. Convert audio to WAV if needed
            2. Validate audio length (3-10 seconds recommended)
            3. Save reference audio and transcript to voices/ folder
            4. Register voice in database
            5. Return paths for use in generation
        """
        try:
            # Step 1: Convert audio to WAV if needed
            wav_path = self._convert_to_wav(reference_audio_path, voice_name)

            # Step 2: Validate audio
            audio = AudioSegment.from_wav(wav_path)
            duration_seconds = len(audio) / 1000.0

            if duration_seconds < 1.0:
                raise ValueError(f"Reference audio too short: {duration_seconds:.1f}s (minimum 1s)")
            if duration_seconds > 30.0:
                logger.warning(f"Reference audio long: {duration_seconds:.1f}s (recommend < 10s)")

            # Step 3: Save transcript
            transcript_path = self.VOICES_DIR / f"{voice_name}.txt"
            transcript_path.write_text(transcript, encoding='utf-8')

            logger.info(f"Voice cloned: {voice_name}")
            logger.info(f"  Reference audio: {wav_path} ({duration_seconds:.1f}s)")
            logger.info(f"  Transcript: {transcript_path}")

            return (str(wav_path), str(transcript_path))

        except Exception as e:
            logger.error(f"Voice cloning failed: {e}")
            raise

    def _convert_to_wav(self, input_path: str, voice_name: str) -> str:
        """
        Convert audio file to WAV format if needed.

        Args:
            input_path: Original audio file path
            voice_name: Name for output WAV file

        Returns:
            Path to WAV file (either original or converted)
        """
        input_file = Path(input_path)

        # If already WAV, no conversion needed
        if input_file.suffix.lower() == '.wav':
            dest_path = self.VOICES_DIR / f"{voice_name}.wav"
            if input_path != str(dest_path):
                # Copy to voices folder if not already there
                audio = AudioSegment.from_wav(input_path)
                audio.export(str(dest_path), format='wav')
            return str(dest_path)

        # Convert MP3, M4A, etc. to WAV
        dest_path = self.VOICES_DIR / f"{voice_name}.wav"

        try:
            audio = AudioSegment.from_file(input_path)
            audio.export(str(dest_path), format='wav')
            logger.info(f"Converted {input_file.suffix} to WAV: {dest_path}")
            return str(dest_path)
        except Exception as e:
            raise ValueError(f"Failed to convert audio to WAV: {e}")

    def list_cloned_voices(self) -> list:
        """
        List all available cloned voices.

        Returns:
            List of voice names (without .wav extension)
        """
        voices = []
        for wav_file in self.VOICES_DIR.glob("*.wav"):
            if (self.VOICES_DIR / f"{wav_file.stem}.txt").exists():
                voices.append(wav_file.stem)
        return sorted(voices)

    def delete_voice(self, voice_name: str) -> None:
        """Delete a cloned voice and its transcript."""
        wav_path = self.VOICES_DIR / f"{voice_name}.wav"
        txt_path = self.VOICES_DIR / f"{voice_name}.txt"

        wav_path.unlink(missing_ok=True)
        txt_path.unlink(missing_ok=True)

        logger.info(f"Deleted voice: {voice_name}")
```

#### TTS Engine Integration

**File: `src/engines/qwen3_tts.py` (MODIFIED from PROMPT-06)**

Add voice cloning support:

```python
class Qwen3TTSEngine(BaseTTSEngine):
    """Qwen3-TTS engine with voice cloning support."""

    def __init__(self, model_path: str = None):
        super().__init__()
        self.model_path = model_path or "models/Qwen3-TTS-12Hz-1.7B-Base-8bit"
        self.voice_cloner = VoiceCloner()
        # ... rest of init

    async def generate_audio(
        self,
        text: str,
        voice_name: str = "Ethan",
        emotion: str = "neutral",
        speed: float = 1.0,
        use_cloned_voice: bool = False
    ) -> str:
        """
        Generate audio with optional cloned voice.

        Args:
            text: Text to synthesize
            voice_name: Voice name (built-in or cloned)
            emotion: Voice emotion
            speed: Speech speed multiplier
            use_cloned_voice: If True, use reference audio + transcript method

        Returns:
            Path to generated WAV file
        """
        if use_cloned_voice:
            return await self._generate_with_cloned_voice(text, voice_name)
        else:
            return await self._generate_with_builtin_voice(
                text, voice_name, emotion, speed
            )

    async def _generate_with_cloned_voice(
        self,
        text: str,
        voice_name: str
    ) -> str:
        """
        Generate audio using a cloned voice.

        Uses Base model with reference audio/transcript.
        """
        voice_dir = Path("voices")
        ref_audio = voice_dir / f"{voice_name}.wav"
        ref_transcript = voice_dir / f"{voice_name}.txt"

        if not ref_audio.exists() or not ref_transcript.exists():
            raise ValueError(f"Cloned voice not found: {voice_name}")

        # Load reference transcript
        reference_text = ref_transcript.read_text(encoding='utf-8')

        # Use MLX model with voice cloning mode
        # This is model-specific; adjust based on Qwen3-TTS API
        output_path = self._run_mlx_generation(
            text=text,
            reference_audio=str(ref_audio),
            reference_text=reference_text,
            model_type='Base'  # Use Base model for cloning
        )

        return output_path

    async def _generate_with_builtin_voice(
        self,
        text: str,
        voice_name: str,
        emotion: str,
        speed: float
    ) -> str:
        """Generate audio with built-in voice (existing logic from PROMPT-06)."""
        # Use existing generation logic
        pass
```

#### Database Schema

**New Table: `cloned_voices`**
```sql
CREATE TABLE cloned_voices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  voice_name TEXT UNIQUE NOT NULL,
  display_name TEXT NOT NULL,  -- User-friendly name
  reference_audio_path TEXT NOT NULL,
  transcript_path TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by TEXT,  -- 'Tim', 'Claude', etc.
  is_enabled INTEGER DEFAULT 1,
  notes TEXT
);
```

### Backend API Endpoints

#### POST /api/voice-lab/clone
**Purpose:** Clone a voice from reference audio

**Request (Multipart Form):**
- `voice_name` (string): Internal voice name (e.g., "kent-zimering")
- `display_name` (string): User-friendly name (e.g., "Kent Zimering Clone")
- `reference_audio` (file): WAV, MP3, or M4A file (1-30 seconds recommended)
- `transcript` (string): Text spoken in the reference audio
- `notes` (string, optional): User notes about the voice

**Response:**
```json
{
  "success": true,
  "voice_name": "kent-zimering",
  "display_name": "Kent Zimering Clone",
  "audio_duration_seconds": 5.2,
  "message": "Voice cloned successfully"
}
```

**Implementation:**
```python
from fastapi import File, Form, UploadFile
import shutil
import tempfile

@router.post("/voice-lab/clone")
async def clone_voice(
    voice_name: str = Form(...),
    display_name: str = Form(...),
    reference_audio: UploadFile = File(...),
    transcript: str = Form(...),
    notes: str = Form(default="")
) -> Dict[str, Any]:
    """Clone a voice from reference audio."""
    cloner = VoiceCloner()

    # Save uploaded file temporarily
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(reference_audio.filename).suffix) as tmp:
        content = await reference_audio.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # Clone the voice
        audio_path, transcript_path = cloner.clone_voice(voice_name, tmp_path, transcript)

        # Store in database
        cloned_voice = ClonedVoice(
            voice_name=voice_name,
            display_name=display_name,
            reference_audio_path=audio_path,
            transcript_path=transcript_path,
            created_by="Tim",  # From auth context
            notes=notes
        )
        db.add(cloned_voice)
        db.commit()

        # Get audio duration
        audio = AudioSegment.from_wav(audio_path)
        duration_seconds = len(audio) / 1000.0

        return {
            "success": True,
            "voice_name": voice_name,
            "display_name": display_name,
            "audio_duration_seconds": duration_seconds,
            "message": "Voice cloned successfully"
        }

    finally:
        Path(tmp_path).unlink(missing_ok=True)
```

#### GET /api/voice-lab/cloned-voices
**Purpose:** List all cloned voices

**Response:**
```json
{
  "cloned_voices": [
    {
      "voice_name": "kent-zimering",
      "display_name": "Kent Zimering Clone",
      "audio_duration_seconds": 5.2,
      "created_at": "2026-03-24T10:00:00Z",
      "created_by": "Tim",
      "is_enabled": true,
      "notes": "High-quality clone from professional recording"
    }
  ]
}
```

#### DELETE /api/voice-lab/cloned-voices/{voice_name}
**Purpose:** Delete a cloned voice

**Response:**
```json
{
  "success": true,
  "message": "Voice deleted: kent-zimering"
}
```

### Frontend: Voice Cloning UI

#### Voice Lab Updates (MODIFIED from PROMPT-07)

**File: `frontend/src/pages/VoiceLab.jsx`**

Add "Clone Voice" tab to existing Voice Lab:

```jsx
import React, { useState } from 'react';

function VoiceCloneTTab() {
  const [voiceName, setVoiceName] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [referenceAudio, setReferenceAudio] = useState(null);
  const [transcript, setTranscript] = useState('');
  const [notes, setNotes] = useState('');
  const [isCloning, setIsCloning] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(false);

  const handleAudioUpload = (e) => {
    const file = e.target.files[0];
    if (file) {
      if (['audio/wav', 'audio/mpeg', 'audio/mp4'].includes(file.type)) {
        setReferenceAudio(file);
        setError(null);
      } else {
        setError('Please upload a WAV, MP3, or M4A file');
      }
    }
  };

  const handleClone = async (e) => {
    e.preventDefault();

    if (!voiceName || !displayName || !referenceAudio || !transcript) {
      setError('Please fill in all required fields');
      return;
    }

    setIsCloning(true);
    setError(null);

    const formData = new FormData();
    formData.append('voice_name', voiceName);
    formData.append('display_name', displayName);
    formData.append('reference_audio', referenceAudio);
    formData.append('transcript', transcript);
    formData.append('notes', notes);

    try {
      const response = await fetch('/api/voice-lab/clone', {
        method: 'POST',
        body: formData
      });

      if (!response.ok) throw new Error('Clone failed');

      const data = await response.json();
      setSuccess(true);
      setVoiceName('');
      setDisplayName('');
      setReferenceAudio(null);
      setTranscript('');
      setNotes('');

      // Refresh cloned voices list
      // ... call parent function or trigger refresh

      setTimeout(() => setSuccess(false), 3000);
    } catch (err) {
      setError(err.message);
    } finally {
      setIsCloning(false);
    }
  };

  return (
    <div className="space-y-6">
      <h3 className="text-lg font-semibold">Clone Voice</h3>

      {success && (
        <div className="p-4 bg-green-100 text-green-700 rounded">
          Voice cloned successfully!
        </div>
      )}

      {error && (
        <div className="p-4 bg-red-100 text-red-700 rounded">
          {error}
        </div>
      )}

      <form onSubmit={handleClone} className="space-y-4">
        <div>
          <label className="block text-sm font-medium mb-1">Voice Name (ID)</label>
          <input
            type="text"
            value={voiceName}
            onChange={(e) => setVoiceName(e.target.value)}
            placeholder="e.g., kent-zimering"
            className="w-full border rounded px-3 py-2"
            pattern="^[a-z0-9\-]+$"
            title="Lowercase letters, numbers, and hyphens only"
          />
          <p className="text-xs text-gray-500 mt-1">Lowercase, no spaces</p>
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">Display Name</label>
          <input
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="e.g., Kent Zimering Clone"
            className="w-full border rounded px-3 py-2"
          />
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">Reference Audio</label>
          <div className="border-2 border-dashed rounded p-4">
            <input
              type="file"
              accept=".wav,.mp3,.m4a"
              onChange={handleAudioUpload}
              className="w-full"
            />
            {referenceAudio && (
              <p className="text-sm text-green-600 mt-2">
                ✓ {referenceAudio.name} selected
              </p>
            )}
          </div>
          <p className="text-xs text-gray-500 mt-1">
            Upload a 1-10 second audio sample (WAV, MP3, or M4A)
          </p>
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">Transcript</label>
          <textarea
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
            placeholder="Type the exact words spoken in the reference audio"
            className="w-full border rounded px-3 py-2 h-24"
          />
          <p className="text-xs text-gray-500 mt-1">
            Provide the text that is spoken in the reference audio
          </p>
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">Notes (Optional)</label>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="e.g., High-quality professional recording, friendly tone"
            className="w-full border rounded px-3 py-2 h-16"
          />
        </div>

        <button
          type="submit"
          disabled={isCloning}
          className="px-6 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
        >
          {isCloning ? 'Cloning Voice...' : 'Clone Voice'}
        </button>
      </form>
    </div>
  );
}
```

#### Cloned Voices List Component

**File: `frontend/src/components/ClonedVoicesList.jsx`**

Display available cloned voices with delete option:

```jsx
function ClonedVoicesList() {
  const [voices, setVoices] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/api/voice-lab/cloned-voices')
      .then(r => r.json())
      .then(data => {
        setVoices(data.cloned_voices);
        setLoading(false);
      });
  }, []);

  const handleDelete = async (voiceName) => {
    if (window.confirm(`Delete voice "${voiceName}"?`)) {
      await fetch(`/api/voice-lab/cloned-voices/${voiceName}`, {
        method: 'DELETE'
      });
      setVoices(voices.filter(v => v.voice_name !== voiceName));
    }
  };

  if (loading) return <p>Loading cloned voices...</p>;

  return (
    <div>
      <h3 className="text-lg font-semibold mb-4">Cloned Voices</h3>
      {voices.length === 0 ? (
        <p className="text-gray-500">No cloned voices yet</p>
      ) : (
        <div className="space-y-2">
          {voices.map(voice => (
            <div key={voice.voice_name} className="border rounded p-4 flex justify-between items-start">
              <div>
                <p className="font-semibold">{voice.display_name}</p>
                <p className="text-sm text-gray-600">ID: {voice.voice_name}</p>
                <p className="text-xs text-gray-500">
                  Created {new Date(voice.created_at).toLocaleDateString()}
                </p>
                {voice.notes && <p className="text-sm mt-2">{voice.notes}</p>}
              </div>
              <button
                onClick={() => handleDelete(voice.voice_name)}
                className="px-3 py-1 text-red-600 hover:bg-red-50 rounded"
              >
                Delete
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

#### Voice Selector Integration

**File: `frontend/src/components/VoiceSelector.jsx` (MODIFIED)**

Update voice dropdown to include cloned voices:

```jsx
function VoiceSelector({ value, onChange, includeCloned = true }) {
  const [builtInVoices] = useState(['Ethan', 'Alex', 'Maya']);
  const [clonedVoices, setClonedVoices] = useState([]);

  useEffect(() => {
    if (includeCloned) {
      fetch('/api/voice-lab/cloned-voices')
        .then(r => r.json())
        .then(data => {
          setClonedVoices(data.cloned_voices);
        });
    }
  }, [includeCloned]);

  return (
    <select value={value} onChange={e => onChange(e.target.value)} className="border rounded px-3 py-2">
      <optgroup label="Built-in Voices">
        {builtInVoices.map(voice => (
          <option key={voice} value={voice}>{voice}</option>
        ))}
      </optgroup>
      {clonedVoices.length > 0 && (
        <optgroup label="Cloned Voices">
          {clonedVoices.map(voice => (
            <option key={voice.voice_name} value={voice.voice_name}>
              {voice.display_name}
            </option>
          ))}
        </optgroup>
      )}
    </select>
  );
}
```

---

## Acceptance Criteria

### Functional Requirements - Voice Cloning
- [ ] Reference audio converted to WAV if not already
- [ ] Audio duration validation (minimum 1s, warning > 10s)
- [ ] Reference audio and transcript saved to `voices/` folder
- [ ] Voice registered in `cloned_voices` database table
- [ ] Cloned voices available in voice selector dropdowns throughout app
- [ ] Audio generation works with cloned voices (use_cloned_voice flag)
- [ ] Delete voice removes files and database entry

### Functional Requirements - API
- [ ] `POST /api/voice-lab/clone` accepts multipart form with audio + transcript
- [ ] `GET /api/voice-lab/cloned-voices` lists all cloned voices
- [ ] `DELETE /api/voice-lab/cloned-voices/{voice_name}` deletes voice
- [ ] Error handling for invalid audio formats
- [ ] Error handling for missing cloned voices during generation

### Functional Requirements - UI
- [ ] Voice Lab has "Clone Voice" tab
- [ ] Form fields: voice_name, display_name, reference_audio, transcript, notes
- [ ] Audio file upload with drag-drop support
- [ ] Validation: voice_name format (lowercase, no spaces)
- [ ] Success message after cloning
- [ ] Error messages for failed clones
- [ ] Cloned Voices list displays all custom voices
- [ ] Delete button removes cloned voice with confirmation
- [ ] VoiceSelector dropdown shows cloned voices in separate group
- [ ] Cloned voices available in all generation UIs (Book Detail, Voice Lab test)

### Code Quality
- [ ] VoiceCloner properly handles exceptions
- [ ] Audio file conversion uses pydub correctly
- [ ] Proper cleanup of temporary files
- [ ] Logging of all cloning operations
- [ ] Database transactions for voice registration
- [ ] Type hints on all functions
- [ ] No hardcoded voice names

### Testing Requirements

1. **Voice Cloning Unit Tests:**
   - [ ] `test_clone_voice_wav`: Clone from WAV file
   - [ ] `test_clone_voice_mp3`: Convert MP3 to WAV and clone
   - [ ] `test_clone_voice_m4a`: Convert M4A to WAV and clone
   - [ ] `test_clone_voice_short_audio`: Reject audio < 1s
   - [ ] `test_clone_voice_long_audio`: Warn on audio > 10s (but allow)
   - [ ] `test_clone_voice_invalid_format`: Reject invalid audio format
   - [ ] `test_list_cloned_voices`: List all cloned voices
   - [ ] `test_delete_voice`: Delete voice and files

2. **API Tests:**
   - [ ] `POST /api/voice-lab/clone` with valid audio/transcript creates voice
   - [ ] `POST /api/voice-lab/clone` returns correct response shape
   - [ ] `GET /api/voice-lab/cloned-voices` returns all cloned voices
   - [ ] `DELETE /api/voice-lab/cloned-voices/{voice_name}` deletes voice
   - [ ] 400 error on invalid voice_name (spaces, special chars)
   - [ ] 404 error deleting non-existent voice

3. **Frontend Component Tests:**
   - [ ] Clone form renders with all fields
   - [ ] Audio file upload works
   - [ ] Form validation prevents incomplete submission
   - [ ] Success message appears after clone
   - [ ] Cloned Voices list displays cloned voices
   - [ ] Delete button removes voice with confirmation
   - [ ] VoiceSelector includes cloned voices in dropdown

4. **Integration Tests:**
   - [ ] Clone voice → Use in test generation → Verify audio quality
   - [ ] Clone voice → Use in book generation → Verify all chapters use cloned voice
   - [ ] Clone voice → Export book → M4B metadata includes cloned voice name
   - [ ] Delete cloned voice → Cannot use in new generation (error)
   - [ ] Multiple cloned voices exist → All selectable in dropdowns

5. **Manual Testing Scenario:**
   - [ ] Go to Voice Lab, click "Clone Voice" tab
   - [ ] Upload a 5-second WAV file with voice sample
   - [ ] Enter voice name "kent-zimering"
   - [ ] Enter display name "Kent Zimering Clone"
   - [ ] Enter transcript (exact words from audio)
   - [ ] Add optional notes
   - [ ] Click "Clone Voice"
   - [ ] Verify success message
   - [ ] Check Cloned Voices list shows new voice
   - [ ] Go to Book Detail page
   - [ ] In Voice Lab test section, select cloned voice from dropdown
   - [ ] Generate test audio with cloned voice
   - [ ] Verify audio quality sounds like reference sample
   - [ ] Go to Settings → Default Voice dropdown
   - [ ] Verify cloned voice appears in list
   - [ ] Select as default
   - [ ] Generate book chapter → verify uses cloned voice
   - [ ] Export book → verify cloned voice used for all chapters
   - [ ] Return to Voice Lab, delete cloned voice
   - [ ] Verify voice removed from all dropdowns

---

## File Structure

```
src/
  engines/
    voice_cloner.py                   # NEW: Voice cloning logic
    qwen3_tts.py                      # MODIFIED: Add cloning support
  models/
    cloned_voice.py                   # NEW: SQLAlchemy model
  api/
    voice_lab_routes.py               # MODIFIED: Add clone endpoints

frontend/src/
  pages/
    VoiceLab.jsx                      # MODIFIED: Add Clone Voice tab
  components/
    VoiceCloneForm.jsx                # NEW: Clone voice form
    ClonedVoicesList.jsx              # NEW: List cloned voices
    VoiceSelector.jsx                 # MODIFIED: Include cloned voices

tests/
  test_voice_cloner.py                # NEW: Voice cloning tests
  test_voice_clone_api.py             # NEW: Clone API tests
  test_voice_clone_ui.py              # NEW: Clone UI component tests

voices/                               # Directory for cloned voice files
  .gitkeep
```

---

## Implementation Notes

### Voice Cloning Workflow
1. User uploads reference audio (1-10 seconds)
2. User types transcript of what was spoken
3. Backend converts audio to WAV if needed
4. Backend saves to `voices/{voice_name}.wav` and `voices/{voice_name}.txt`
5. Database entry created in `cloned_voices` table
6. Voice available in all voice selector dropdowns
7. When generating, engine loads reference audio + transcript and uses cloning mode

### Reference Audio Guidelines
- **Optimal length:** 3-10 seconds of clear speech
- **Minimum:** 1 second
- **Content:** Any coherent speech in the target voice (doesn't need to match generation text)
- **Quality:** Clear audio without background noise
- **Format:** WAV, MP3, M4A

### Transcript Requirements
- Must be the exact text spoken in the reference audio
- Used by model to align speech patterns
- Should be grammatically correct and natural

### Voice Naming Convention
- Use kebab-case: `kent-zimering`, `female-narrator-v1`, etc.
- Lowercase letters, numbers, hyphens only
- Underscores allowed but discourage
- No spaces or special characters

### Database Relationships
- `cloned_voices.voice_name` referenced by:
  - Voice selector in book generation
  - Settings (default_voice.name)
  - Voice Lab test generator

### Error Handling
- Invalid audio format: Reject with clear message
- Audio too short: Error
- Audio conversion failure: Error with format info
- Duplicate voice_name: Overwrite with confirmation
- Missing reference files during generation: Error with repair suggestion

---

## References

- CLAUDE.md § Narrator, TTS Engine Contract
- PROMPT-06: TTS Engine Abstraction (engine interface)
- PROMPT-07: Voice Lab UI (voice testing UI)
- PROMPT-13: Settings (voice settings)
- Qwen3-TTS documentation (voice cloning specifics)
- pydub: https://github.com/jiaaro/pydub

---

## Commit Message

```
[PROMPT-14] Implement voice cloning integration

- Create VoiceCloner class with audio conversion and validation
- Add voice cloning support to Qwen3-TTS engine (Base model)
- Implement POST /api/voice-lab/clone endpoint (multipart form)
- Add cloned_voices database table
- Create Voice Lab "Clone Voice" tab with form and file upload
- Display cloned voices in VoiceSelector dropdown
- Add delete endpoint for cloned voices
- Comprehensive tests for voice cloning and API
```

---

**Status:** READY FOR IMPLEMENTATION
**Estimated Effort:** 8-10 hours (audio processing, API, UI, testing)
**Dependencies:** PROMPT-06 (TTS engine), PROMPT-07 (Voice Lab), PROMPT-13 (Settings)
