# PROMPT-13: Settings & Configuration Management

**Objective:** Create a comprehensive settings system that persists configuration across server restarts and provides a user-friendly settings interface.

**Owner:** Codex
**Reference:** CLAUDE.md, PROJECT-STATE.md

---

## Scope

### Configuration Management System

#### File: `src/config.py`

**Settings Data Structure:**
```python
from pydantic import BaseModel
from typing import Optional

class VoiceSettings(BaseModel):
    """Voice configuration."""
    name: str = "Ethan"  # Voice name (e.g., "Ethan", "Cloned-Kent", etc.)
    emotion: str = "neutral"  # 'neutral', 'calm', 'happy', 'sad', 'angry'
    speed: float = 1.0  # Speed multiplier (0.5 - 2.0)

class EngineSettings(BaseModel):
    """TTS engine configuration."""
    model_path: str = "models/Qwen3-TTS-12Hz-1.7B-Base-8bit"
    # Future: api_key for cloud engines, etc.

class OutputSettings(BaseModel):
    """Export output preferences."""
    mp3_bitrate: int = 192  # kbps (128, 192, 256, 320)
    sample_rate: int = 44100  # Hz (44100, 48000)
    silence_duration_chapters: float = 2.0  # seconds between chapters
    silence_duration_opening: float = 3.0  # seconds after opening credits
    silence_duration_closing: float = 3.0  # seconds before closing credits
    include_album_art: bool = True

class ApplicationSettings(BaseModel):
    """Application-wide settings."""
    narrator_name: str = "Kent Zimering"
    manuscript_source_folder: str = "Formatted Manuscripts"
    default_voice: VoiceSettings = VoiceSettings()
    engine_config: EngineSettings = EngineSettings()
    output_preferences: OutputSettings = OutputSettings()

    class Config:
        validate_assignment = True
```

**Settings Manager Class:**
```python
import json
from pathlib import Path
from sqlalchemy import Column, String, Integer, Float
from src.database import Base, engine, SessionLocal

class ConfigModel(Base):
    """SQLAlchemy model for storing settings in database."""
    __tablename__ = 'settings'

    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, nullable=False)
    value = Column(String, nullable=False)  # JSON-serialized

class SettingsManager:
    """
    Manage application settings with database persistence.
    Fallback to config.json if database unavailable.
    """

    DB_KEY = 'application_settings'
    CONFIG_FILE = Path('config.json')

    def __init__(self):
        self.settings: ApplicationSettings = self._load_settings()

    def _load_settings(self) -> ApplicationSettings:
        """
        Load settings in priority order:
        1. Database (if available)
        2. config.json file
        3. Default values
        """
        # Try database first
        try:
            db = SessionLocal()
            db_config = db.query(ConfigModel).filter(
                ConfigModel.key == self.DB_KEY
            ).first()
            if db_config:
                settings_dict = json.loads(db_config.value)
                return ApplicationSettings(**settings_dict)
        except Exception as e:
            logger.warning(f"Failed to load settings from database: {e}")

        # Fallback to config.json
        if self.CONFIG_FILE.exists():
            try:
                with open(self.CONFIG_FILE, 'r') as f:
                    settings_dict = json.load(f)
                return ApplicationSettings(**settings_dict)
            except Exception as e:
                logger.warning(f"Failed to load settings from config.json: {e}")

        # Default settings
        return ApplicationSettings()

    def save_settings(self, settings: ApplicationSettings) -> None:
        """Save settings to both database and config.json file."""
        settings_dict = settings.dict()

        # Save to database
        try:
            db = SessionLocal()
            db_config = db.query(ConfigModel).filter(
                ConfigModel.key == self.DB_KEY
            ).first()
            if db_config:
                db_config.value = json.dumps(settings_dict)
            else:
                db_config = ConfigModel(
                    key=self.DB_KEY,
                    value=json.dumps(settings_dict)
                )
                db.add(db_config)
            db.commit()
            logger.info("Settings saved to database")
        except Exception as e:
            logger.error(f"Failed to save settings to database: {e}")

        # Save to config.json as fallback
        try:
            with open(self.CONFIG_FILE, 'w') as f:
                json.dump(settings_dict, f, indent=2)
            logger.info("Settings saved to config.json")
        except Exception as e:
            logger.error(f"Failed to save settings to config.json: {e}")

        # Update in-memory settings
        self.settings = settings

    def get_settings(self) -> ApplicationSettings:
        """Get current settings."""
        return self.settings

    def update_setting(self, path: str, value) -> None:
        """
        Update a single setting using dot notation.

        Example:
            manager.update_setting('output_preferences.mp3_bitrate', 256)
            manager.update_setting('default_voice.speed', 1.2)
        """
        parts = path.split('.')
        settings_dict = self.settings.dict()

        # Navigate to nested key
        current = settings_dict
        for part in parts[:-1]:
            if part not in current:
                raise ValueError(f"Invalid setting path: {path}")
            current = current[part]

        # Set value
        current[parts[-1]] = value

        # Validate and save
        self.settings = ApplicationSettings(**settings_dict)
        self.save_settings(self.settings)

# Global instance
_settings_manager: Optional[SettingsManager] = None

def get_settings_manager() -> SettingsManager:
    """Get or create the global settings manager."""
    global _settings_manager
    if _settings_manager is None:
        _settings_manager = SettingsManager()
    return _settings_manager
```

### Backend API Endpoints

#### GET /api/settings
**Purpose:** Retrieve all application settings

**Response:**
```json
{
  "narrator_name": "Kent Zimering",
  "manuscript_source_folder": "Formatted Manuscripts",
  "default_voice": {
    "name": "Ethan",
    "emotion": "neutral",
    "speed": 1.0
  },
  "engine_config": {
    "model_path": "models/Qwen3-TTS-12Hz-1.7B-Base-8bit"
  },
  "output_preferences": {
    "mp3_bitrate": 192,
    "sample_rate": 44100,
    "silence_duration_chapters": 2.0,
    "silence_duration_opening": 3.0,
    "silence_duration_closing": 3.0,
    "include_album_art": true
  }
}
```

**Implementation:**
```python
@router.get("/settings")
async def get_settings() -> ApplicationSettings:
    """Get all application settings."""
    manager = get_settings_manager()
    return manager.get_settings()
```

#### PUT /api/settings
**Purpose:** Update application settings (partial or full)

**Request Body:**
```json
{
  "output_preferences": {
    "mp3_bitrate": 256,
    "silence_duration_chapters": 2.5
  },
  "default_voice": {
    "speed": 1.1
  }
}
```

**Response:**
```json
{
  "success": true,
  "message": "Settings updated successfully",
  "updated_fields": [
    "output_preferences.mp3_bitrate",
    "output_preferences.silence_duration_chapters",
    "default_voice.speed"
  ]
}
```

**Implementation:**
```python
@router.put("/settings")
async def update_settings(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Update application settings (deep merge, not replacement)."""
    manager = get_settings_manager()
    current = manager.get_settings()
    current_dict = current.dict()

    # Deep merge updates into current settings
    def deep_merge(target: dict, source: dict) -> dict:
        for key, value in source.items():
            if isinstance(value, dict) and key in target and isinstance(target[key], dict):
                target[key] = deep_merge(target[key], value)
            else:
                target[key] = value
        return target

    updated_dict = deep_merge(current_dict, updates)

    # Validate
    try:
        new_settings = ApplicationSettings(**updated_dict)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Save
    manager.save_settings(new_settings)

    return {
        "success": True,
        "message": "Settings updated successfully",
        "updated_fields": list(updates.keys())
    }
```

#### GET /api/settings/schema
**Purpose:** Get JSON schema for settings (for frontend form generation)

**Response:**
```json
{
  "type": "object",
  "properties": {
    "narrator_name": {
      "type": "string",
      "description": "Name of the narrator for audiobook credits"
    },
    "manuscript_source_folder": {
      "type": "string",
      "description": "Path to folder containing formatted manuscripts"
    },
    "default_voice": {
      "type": "object",
      "properties": {
        "name": {
          "type": "string",
          "enum": ["Ethan", "Cloned-Kent", ...]
        },
        "emotion": {
          "type": "string",
          "enum": ["neutral", "calm", "happy", "sad", "angry"]
        },
        "speed": {
          "type": "number",
          "minimum": 0.5,
          "maximum": 2.0
        }
      }
    },
    "output_preferences": {
      "type": "object",
      "properties": {
        "mp3_bitrate": {
          "type": "integer",
          "enum": [128, 192, 256, 320]
        },
        "sample_rate": {
          "type": "integer",
          "enum": [44100, 48000]
        },
        "silence_duration_chapters": {
          "type": "number",
          "minimum": 0.5,
          "maximum": 10.0
        },
        "silence_duration_opening": {
          "type": "number",
          "minimum": 0.5,
          "maximum": 10.0
        },
        "silence_duration_closing": {
          "type": "number",
          "minimum": 0.5,
          "maximum": 10.0
        },
        "include_album_art": {
          "type": "boolean"
        }
      }
    }
  }
}
```

### Frontend: Settings Page

#### File: `frontend/src/pages/Settings.jsx`

**Layout:**

1. **Page Header**
   - Title: "Settings"
   - Description: "Configure Alexandria Audiobook Narrator"

2. **Settings Sections** (Collapsible Cards)

   **a) Narrator Settings**
   - Narrator Name (text input)
     - Label: "Narrator Name"
     - Default: "Kent Zimering"
     - Help text: "Name used in opening/closing credits"

   **b) Voice Configuration**
   - Voice Name (dropdown)
     - Options: "Ethan" (default), "Cloned-Kent" (if available), other cloned voices
     - Label: "Default Voice"
   - Emotion (dropdown)
     - Options: "neutral", "calm", "happy", "sad", "angry"
     - Label: "Voice Emotion"
   - Speed (slider + number input)
     - Range: 0.5 to 2.0
     - Step: 0.1
     - Label: "Speech Speed"
     - Display: "1.0x" format

   **c) Output Preferences**
   - MP3 Bitrate (dropdown)
     - Options: 128, 192 (default), 256, 320
     - Label: "MP3 Bitrate (kbps)"
     - Help text: "Higher = better quality but larger file"
   - Sample Rate (dropdown)
     - Options: 44100 (default), 48000
     - Label: "Sample Rate (Hz)"
   - Silence Between Chapters (slider + number input)
     - Range: 0.5 to 10 seconds
     - Step: 0.1
     - Default: 2.0
     - Label: "Silence Between Chapters (seconds)"
   - Silence After Opening (slider + number input)
     - Range: 0.5 to 10 seconds
     - Step: 0.1
     - Default: 3.0
     - Label: "Silence After Opening Credits (seconds)"
   - Silence Before Closing (slider + number input)
     - Range: 0.5 to 10 seconds
     - Step: 0.1
     - Default: 3.0
     - Label: "Silence Before Closing Credits (seconds)"
   - Include Album Art (checkbox)
     - Default: checked
     - Label: "Include album art in exported MP3"

   **d) Manuscript Configuration**
   - Manuscript Folder Path (text input + folder picker button)
     - Label: "Formatted Manuscripts Folder"
     - Default: "Formatted Manuscripts"
     - Help text: "Path to folder containing manuscript subfolders"
     - Button: "Browse..." (opens file picker in Electron/native)

   **e) Engine Configuration** (Advanced section, collapsed by default)
   - Model Path (text input, read-only)
     - Display only: "models/Qwen3-TTS-12Hz-1.7B-Base-8bit"
     - Help text: "Cannot be changed in UI; requires manual file management"

3. **Action Buttons**
   - Save button (primary, enabled only if changes made)
   - Reset to Defaults button (secondary, with confirmation)
   - Discard Changes button (tertiary, if unsaved changes exist)

4. **Feedback Messages**
   - Success toast: "Settings saved successfully" (after save)
   - Error toast: "Failed to save settings: {error}" (on error)
   - Unsaved changes indicator (visual cue, e.g., "*" in tab title)

#### Settings Form Component

**File: `frontend/src/components/SettingsForm.jsx`

Reusable settings form that:
- Loads schema from `GET /api/settings/schema`
- Renders form fields based on schema
- Handles input validation (client-side)
- Tracks unsaved changes
- Submits via `PUT /api/settings`

```jsx
import React, { useState, useEffect } from 'react';

function SettingsForm() {
  const [settings, setSettings] = useState({});
  const [schema, setSchema] = useState({});
  const [hasChanges, setHasChanges] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    // Load current settings and schema
    Promise.all([
      fetch('/api/settings').then(r => r.json()),
      fetch('/api/settings/schema').then(r => r.json())
    ]).then(([currentSettings, schema]) => {
      setSettings(currentSettings);
      setSchema(schema);
    });
  }, []);

  const handleChange = (path, value) => {
    // Update settings with dot notation
    const newSettings = { ...settings };
    const keys = path.split('.');
    let current = newSettings;
    for (let i = 0; i < keys.length - 1; i++) {
      current[keys[i]] = current[keys[i]] || {};
      current = current[keys[i]];
    }
    current[keys[keys.length - 1]] = value;

    setSettings(newSettings);
    setHasChanges(true);
  };

  const handleSave = async () => {
    setIsSaving(true);
    try {
      const response = await fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings)
      });
      if (!response.ok) throw new Error('Failed to save');
      setHasChanges(false);
      setError(null);
      // Show success toast
    } catch (err) {
      setError(err.message);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="space-y-8">
      {/* Settings form sections */}
      {/* ... */}

      <div className="flex gap-2">
        <button
          onClick={handleSave}
          disabled={!hasChanges || isSaving}
          className="px-4 py-2 bg-blue-600 text-white rounded disabled:opacity-50"
        >
          {isSaving ? 'Saving...' : 'Save Settings'}
        </button>
        {hasChanges && (
          <button
            onClick={() => {/* reload from API */}}
            className="px-4 py-2 bg-gray-400 text-white rounded"
          >
            Discard Changes
          </button>
        )}
      </div>
    </div>
  );
}

export default SettingsForm;
```

### Database Schema

#### New Table: `settings`
```sql
CREATE TABLE settings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key TEXT UNIQUE NOT NULL,
  value TEXT NOT NULL  -- JSON-serialized value
);
```

### Startup Validation

**File: `src/main.py` (modified)**

During server startup, validate settings:
```python
async def startup_validation():
    """Validate critical settings and paths on startup."""
    manager = get_settings_manager()
    settings = manager.get_settings()

    # Check manuscript folder
    ms_folder = Path(settings.manuscript_source_folder)
    if not ms_folder.exists():
        logger.warning(f"Manuscript folder not found: {ms_folder}")
        logger.warning("Please configure in Settings page")

    # Check model files
    model_path = Path(settings.engine_config.model_path)
    if not model_path.exists():
        logger.error(f"TTS model not found: {model_path}")
        raise RuntimeError("TTS model files missing. Check Settings.")

    logger.info(f"Settings loaded: {settings.narrator_name}")

@app.on_event("startup")
async def startup():
    await startup_validation()
    # ... other startup tasks
```

---

## Acceptance Criteria

### Functional Requirements
- [ ] `GET /api/settings` returns all current settings
- [ ] `PUT /api/settings` accepts partial updates and performs deep merge
- [ ] Settings persist to database on save
- [ ] Settings persist to config.json as fallback
- [ ] Server restart loads saved settings from database/config.json
- [ ] Settings Manager handles database unavailability gracefully (fallback to file)
- [ ] Invalid settings rejected with validation error message
- [ ] Settings used throughout app:
  - [ ] Generation pipeline uses voice/emotion/speed from settings
  - [ ] Export pipeline uses silence durations and bitrate from settings
  - [ ] Opening/closing credits use narrator_name from settings
  - [ ] LUFS normalization uses output preferences

### Frontend Requirements
- [ ] Settings page loads current values from API
- [ ] All form fields match schema (dropdowns, sliders, text inputs)
- [ ] Unsaved changes indicator visible
- [ ] Save button disabled until changes made
- [ ] Success toast shown after save
- [ ] Error toast shown if save fails
- [ ] Reset to Defaults button shows confirmation dialog
- [ ] Discard Changes button cancels edits
- [ ] Form validation (e.g., speed 0.5-2.0, bitrate in allowed values)

### Code Quality
- [ ] Settings Manager properly handles exceptions
- [ ] Database and file I/O separated (no cross-dependencies)
- [ ] Logging of all settings changes
- [ ] Type hints on all functions
- [ ] Pydantic validation on all data
- [ ] No hardcoded config values in code (use settings system)

### Testing Requirements

1. **Settings Manager Unit Tests:**
   - [ ] `test_load_settings_from_db`: Load from database
   - [ ] `test_load_settings_fallback_to_file`: Fallback to config.json
   - [ ] `test_load_settings_default`: Use defaults if both unavailable
   - [ ] `test_save_settings_to_db`: Save to database
   - [ ] `test_save_settings_to_file`: Save to config.json
   - [ ] `test_update_setting_nested`: Update nested setting with dot notation
   - [ ] `test_validation_error`: Reject invalid settings

2. **API Tests:**
   - [ ] `GET /api/settings` returns correct shape
   - [ ] `PUT /api/settings` with partial updates merges correctly
   - [ ] `PUT /api/settings` with full updates replaces correctly
   - [ ] `GET /api/settings/schema` returns valid JSON schema
   - [ ] 400 error on invalid settings

3. **Frontend Component Tests:**
   - [ ] Settings page loads and displays all settings
   - [ ] Form fields bind to correct values
   - [ ] Slider input updates value
   - [ ] Dropdown selection changes value
   - [ ] Save button enabled only when changes made
   - [ ] Save button disabled during API call
   - [ ] Error handling for failed save
   - [ ] Reset dialog confirmation

4. **Integration Tests:**
   - [ ] Generate chapter with custom voice speed from settings
   - [ ] Export with custom bitrate from settings
   - [ ] Silence durations in exported audio match settings
   - [ ] Settings change → new generation uses new settings
   - [ ] Server restart → settings persist

5. **Manual Testing Scenario:**
   - [ ] Open Settings page
   - [ ] Change narrator name to "Custom Narrator"
   - [ ] Change voice speed to 1.2x
   - [ ] Change MP3 bitrate to 256
   - [ ] Change chapter silence to 2.5 seconds
   - [ ] Click Save
   - [ ] Verify success toast
   - [ ] Refresh page → verify settings still show custom values
   - [ ] Restart server
   - [ ] Open Settings → verify custom values persisted
   - [ ] Generate chapter → verify narrator name in opening credits
   - [ ] Generate chapter → verify generated speech speed ~1.2x
   - [ ] Export book → verify MP3 is 256kbps

---

## File Structure

```
src/
  config.py                         # NEW: Settings Manager and data structures
  api/
    settings_routes.py              # NEW: Settings API endpoints

frontend/src/
  pages/
    Settings.jsx                    # NEW: Settings page
  components/
    SettingsForm.jsx                # NEW: Reusable settings form
    SettingsSection.jsx             # NEW: Collapsible settings section

tests/
  test_settings_manager.py          # NEW: Settings Manager tests
  test_settings_api.py              # NEW: Settings API tests
  test_settings_page.py             # NEW: Settings page component tests

config.json                         # NEW: Fallback configuration file (gitignored)
```

---

## Implementation Notes

### Settings Initialization
On first run:
1. Create `settings` table if not exists
2. Insert default `ApplicationSettings` into database
3. Write `config.json` with default values
4. Load into memory via `SettingsManager`

### Validation
Use Pydantic validators for:
- Bitrate must be in [128, 192, 256, 320]
- Speed must be 0.5 to 2.0
- Silence durations must be 0.5 to 10 seconds
- Sample rate must be in [44100, 48000]
- Narrator name must be non-empty string

### Performance
- Settings Manager is a singleton (one instance per process)
- Settings cached in memory after first load
- Save operations are synchronous (small data)
- Changes apply immediately to in-memory copy

### Extension Points
Settings system designed to grow:
- Add new sections without code changes
- New engine backends can read engine_config fields
- Custom voice profiles can be added to output_preferences

---

## References

- CLAUDE.md § Narrator, TTS Engine Contract
- PROMPT-08: Generation Pipeline (uses voice settings)
- PROMPT-12: Export Pipeline (uses output preferences)
- Pydantic: https://docs.pydantic.dev/
- SQLAlchemy: https://docs.sqlalchemy.org/

---

## Commit Message

```
[PROMPT-13] Implement settings & configuration management

- Create SettingsManager with database + config.json persistence
- Define ApplicationSettings Pydantic models
- Add GET/PUT /api/settings endpoints (with schema endpoint)
- Implement Settings page with collapsible sections
- Support partial updates (deep merge) for PUT endpoint
- Fallback to config.json if database unavailable
- Settings used throughout app (voice, export, narrator)
- Comprehensive tests for settings persistence and API
```

---

**Status:** READY FOR IMPLEMENTATION
**Estimated Effort:** 8-10 hours (settings system, API, UI, testing)
**Dependencies:** PROMPT-01 (schema), PROMPT-08 (generation), PROMPT-12 (export)
