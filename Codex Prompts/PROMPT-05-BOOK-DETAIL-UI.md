# PROMPT-05: Book Detail Page & Chapter Editing Interface

**Objective:** Create a three-panel book detail page with chapter list, text preview/editor, and narration settings.

**Project Context:** See `/sessions/focused-happy-pasteur/mnt/Coding Folder/Qwen3-TTS - Test/CLAUDE.md` for conventions and architecture.

---

## Scope & Requirements

### 1. Book Detail Page

**File:** `frontend/src/pages/BookDetail.jsx`

Create a three-panel layout for viewing and editing a book's chapters.

```jsx
import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import ChapterList from '../components/ChapterList';
import TextPreview from '../components/TextPreview';
import NarrationSettings from '../components/NarrationSettings';

export default function BookDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [book, setBook] = useState(null);
  const [chapters, setChapters] = useState([]);
  const [selectedChapter, setSelectedChapter] = useState(null);
  const [editMode, setEditMode] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [narrationSettings, setNarrationSettings] = useState({
    voice: 'Ethan',
    emotion: 'neutral',
    speed: 1.0,
    engine: 'qwen3_tts',
  });

  // ========================================================================
  // Data Fetching
  // ========================================================================

  useEffect(() => {
    fetchBookData();
  }, [id]);

  const fetchBookData = async () => {
    try {
      setLoading(true);

      // Fetch book details
      const bookResponse = await fetch(`/api/book/${id}`);
      if (!bookResponse.ok) throw new Error('Failed to fetch book');
      const bookData = await bookResponse.json();
      setBook(bookData);

      // Fetch chapters
      const chaptersResponse = await fetch(`/api/book/${id}/chapters`);
      if (!chaptersResponse.ok) throw new Error('Failed to fetch chapters');
      const chaptersData = await chaptersResponse.json();
      setChapters(chaptersData);

      // Select first chapter by default
      if (chaptersData.length > 0) {
        setSelectedChapter(chaptersData[0]);
      }
    } catch (error) {
      console.error('Error fetching book data:', error);
    } finally {
      setLoading(false);
    }
  };

  // ========================================================================
  // Handlers
  // ========================================================================

  const handleChapterSelect = (chapter) => {
    if (editMode) {
      // Warn before switching chapters in edit mode
      if (window.confirm('Discard unsaved changes?')) {
        setEditMode(false);
        setSelectedChapter(chapter);
      }
    } else {
      setSelectedChapter(chapter);
    }
  };

  const handleTextChange = (newText) => {
    setSelectedChapter({ ...selectedChapter, text_content: newText });
  };

  const handleSaveText = async () => {
    try {
      setSaving(true);
      const response = await fetch(
        `/api/book/${id}/chapter/${selectedChapter.number}/text`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text_content: selectedChapter.text_content }),
        }
      );

      if (!response.ok) throw new Error('Failed to save chapter text');

      const updatedChapter = await response.json();
      setSelectedChapter(updatedChapter);
      setEditMode(false);

      // Update chapter in list
      setChapters(
        chapters.map(ch => ch.id === updatedChapter.id ? updatedChapter : ch)
      );
    } catch (error) {
      console.error('Error saving chapter text:', error);
      alert('Failed to save chapter text. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  const handleNarrationSettingsChange = (newSettings) => {
    setNarrationSettings(newSettings);
  };

  // ========================================================================
  // Render
  // ========================================================================

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="text-slate-400 text-lg">Loading book details...</div>
      </div>
    );
  }

  if (!book) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="text-center">
          <div className="text-slate-400 text-lg mb-4">Book not found</div>
          <button
            onClick={() => navigate('/')}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium transition"
          >
            Back to Library
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 to-slate-800">
      {/* Header */}
      <header className="border-b border-slate-700 bg-slate-900 shadow-lg sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <div className="flex items-center justify-between mb-2">
            <button
              onClick={() => navigate('/')}
              className="text-slate-400 hover:text-white transition text-sm font-medium"
            >
              ← Back to Library
            </button>
            <div className="text-sm text-slate-500">
              ID: {book.id} • {book.page_count} pages
            </div>
          </div>
          <h1 className="text-3xl font-bold text-white mb-1">{book.title}</h1>
          {book.subtitle && (
            <p className="text-slate-400 text-lg mb-3">{book.subtitle}</p>
          )}
          <p className="text-slate-400">
            by <span className="text-slate-200 font-medium">{book.author}</span>
            {' '} • Narrated by <span className="text-slate-200 font-medium">{book.narrator}</span>
          </p>
        </div>
      </header>

      {/* Three-Panel Layout */}
      <main className="max-w-7xl mx-auto px-6 py-6">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 h-[calc(100vh-200px)]">
          {/* Left Panel: Chapter List */}
          <div className="lg:col-span-1">
            <ChapterList
              chapters={chapters}
              selectedChapter={selectedChapter}
              onSelectChapter={handleChapterSelect}
            />
          </div>

          {/* Center Panel: Text Preview/Editor */}
          <div className="lg:col-span-1">
            {selectedChapter ? (
              <TextPreview
                chapter={selectedChapter}
                editMode={editMode}
                onEditModeChange={setEditMode}
                onTextChange={handleTextChange}
                onSave={handleSaveText}
                saving={saving}
              />
            ) : (
              <div className="bg-slate-800 border border-slate-700 rounded-lg p-6 h-full flex items-center justify-center">
                <div className="text-slate-400 text-center">
                  <div className="text-lg font-medium mb-2">No chapter selected</div>
                  <div className="text-sm">Select a chapter from the list to view and edit</div>
                </div>
              </div>
            )}
          </div>

          {/* Right Panel: Narration Settings */}
          <div className="lg:col-span-1">
            <NarrationSettings
              settings={narrationSettings}
              onChange={handleNarrationSettingsChange}
              selectedChapter={selectedChapter}
            />
          </div>
        </div>
      </main>
    </div>
  );
}
```

---

### 2. Chapter List Component

**File:** `frontend/src/components/ChapterList.jsx`

Sidebar component showing all chapters with status indicators.

```jsx
import React from 'react';

const CHAPTER_STATUS_ICONS = {
  'pending': {
    icon: '○',
    color: 'text-gray-400',
    tooltip: 'Not started',
  },
  'generating': {
    icon: '◑',
    color: 'text-blue-400 animate-spin',
    tooltip: 'Generating',
  },
  'generated': {
    icon: '✓',
    color: 'text-green-400',
    tooltip: 'Complete',
  },
  'failed': {
    icon: '✕',
    color: 'text-red-400',
    tooltip: 'Error',
  },
};

const CHAPTER_QA_ICONS = {
  'not_reviewed': {
    icon: '?',
    color: 'text-yellow-400',
    tooltip: 'Needs review',
  },
  'needs_review': {
    icon: '!',
    color: 'text-yellow-500',
    tooltip: 'Review required',
  },
  'approved': {
    icon: '✓',
    color: 'text-green-400',
    tooltip: 'Approved',
  },
};

export default function ChapterList({ chapters, selectedChapter, onSelectChapter }) {
  const getChapterLabel = (chapter) => {
    if (chapter.type === 'opening_credits') return 'Opening Credits';
    if (chapter.type === 'closing_credits') return 'Closing Credits';
    if (chapter.type === 'introduction') return `Intro${chapter.title ? ': ' + chapter.title : ''}`;
    return `Ch ${chapter.number}${chapter.title ? ': ' + chapter.title : ''}`;
  };

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-lg h-full flex flex-col">
      {/* Header */}
      <div className="border-b border-slate-700 p-4 bg-slate-750">
        <h2 className="text-lg font-bold text-white">Chapters ({chapters.length})</h2>
        <p className="text-xs text-slate-400 mt-1">Select a chapter to edit</p>
      </div>

      {/* Chapter List */}
      <div className="flex-1 overflow-y-auto">
        {chapters.length === 0 ? (
          <div className="p-4 text-slate-400 text-sm text-center py-8">
            No chapters available. Parse the manuscript first.
          </div>
        ) : (
          <ul className="divide-y divide-slate-700">
            {chapters.map(chapter => {
              const statusIcon = CHAPTER_STATUS_ICONS[chapter.status] || CHAPTER_STATUS_ICONS['pending'];
              const qaIcon = CHAPTER_QA_ICONS[chapter.qa_status] || CHAPTER_QA_ICONS['not_reviewed'];
              const isSelected = selectedChapter?.id === chapter.id;

              return (
                <li
                  key={chapter.id}
                  onClick={() => onSelectChapter(chapter)}
                  className={`p-3 cursor-pointer transition border-l-4 ${
                    isSelected
                      ? 'bg-blue-900/30 border-l-blue-500'
                      : 'bg-slate-800 border-l-transparent hover:bg-slate-700/50'
                  }`}
                >
                  <div className="flex items-start gap-2">
                    {/* Status Icons */}
                    <div className="flex gap-1 flex-shrink-0 mt-0.5">
                      <span
                        className={`text-sm font-bold ${statusIcon.color}`}
                        title={statusIcon.tooltip}
                      >
                        {statusIcon.icon}
                      </span>
                      <span
                        className={`text-sm font-bold ${qaIcon.color}`}
                        title={qaIcon.tooltip}
                      >
                        {qaIcon.icon}
                      </span>
                    </div>

                    {/* Chapter Info */}
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium text-white truncate">
                        {getChapterLabel(chapter)}
                      </div>
                      {chapter.word_count && (
                        <div className="text-xs text-slate-400 mt-1">
                          {chapter.word_count} words
                        </div>
                      )}
                      {chapter.duration_seconds && (
                        <div className="text-xs text-slate-400">
                          {Math.round(chapter.duration_seconds / 60)}m {Math.round(chapter.duration_seconds % 60)}s
                        </div>
                      )}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
```

---

### 3. Text Preview & Editor Component

**File:** `frontend/src/components/TextPreview.jsx`

Center panel for viewing and editing chapter text.

```jsx
import React from 'react';

export default function TextPreview({
  chapter,
  editMode,
  onEditModeChange,
  onTextChange,
  onSave,
  saving,
}) {
  const charCount = chapter.text_content?.length || 0;

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-lg h-full flex flex-col">
      {/* Header */}
      <div className="border-b border-slate-700 p-4 bg-slate-750 flex items-center justify-between">
        <div>
          <h3 className="text-lg font-bold text-white">Text Content</h3>
          <p className="text-xs text-slate-400 mt-1">
            {chapter.word_count} words • {charCount} characters
          </p>
        </div>
        <button
          onClick={() => {
            if (editMode && chapter.text_content.length === 0) {
              alert('Chapter text cannot be empty');
              return;
            }
            if (editMode) {
              onSave();
            } else {
              onEditModeChange(true);
            }
          }}
          disabled={saving}
          className={`px-3 py-1 rounded-lg text-sm font-medium transition ${
            editMode
              ? 'bg-green-600 hover:bg-green-700 text-white disabled:opacity-50'
              : 'bg-blue-600 hover:bg-blue-700 text-white'
          }`}
        >
          {saving ? 'Saving...' : editMode ? 'Save' : 'Edit'}
        </button>
      </div>

      {/* Content Area */}
      <div className="flex-1 overflow-hidden flex flex-col">
        {editMode ? (
          <textarea
            value={chapter.text_content || ''}
            onChange={(e) => onTextChange(e.target.value)}
            className="flex-1 p-4 bg-slate-900 text-white font-mono text-sm resize-none focus:outline-none border-0"
            placeholder="Chapter text..."
          />
        ) : (
          <div className="flex-1 overflow-y-auto p-4">
            <div className="text-slate-300 whitespace-pre-wrap text-sm leading-relaxed font-serif">
              {chapter.text_content || <span className="text-slate-500">No text content</span>}
            </div>
          </div>
        )}
      </div>

      {/* Footer Info */}
      <div className="border-t border-slate-700 p-3 bg-slate-750 text-xs text-slate-400">
        {editMode ? (
          <div>
            <span className="text-yellow-400 font-medium">Edit Mode:</span> Make changes and click "Save" to commit
          </div>
        ) : (
          <div>
            <span className="text-blue-400 font-medium">View Mode:</span> Click "Edit" to make changes
          </div>
        )}
      </div>
    </div>
  );
}
```

---

### 4. Narration Settings Component

**File:** `frontend/src/components/NarrationSettings.jsx`

Right panel for configuring voice and generation settings.

```jsx
import React, { useState, useEffect } from 'react';

const EMOTION_PRESETS = [
  'neutral',
  'warm',
  'dramatic',
  'energetic',
  'contemplative',
  'authoritative',
];

const NARRATOR_PRESETS = [
  {
    name: 'Audiobook Narrator',
    emotion: 'warm',
    speed: 1.0,
  },
  {
    name: 'Dramatic Reading',
    emotion: 'dramatic',
    speed: 0.95,
  },
  {
    name: 'Energetic Delivery',
    emotion: 'energetic',
    speed: 1.1,
  },
  {
    name: 'Contemplative',
    emotion: 'contemplative',
    speed: 0.9,
  },
];

export default function NarrationSettings({ settings, onChange, selectedChapter }) {
  const [voices, setVoices] = useState([]);
  const [customEmotion, setCustomEmotion] = useState(settings.emotion || '');
  const [loading, setLoading] = useState(true);

  // ========================================================================
  // Data Fetching
  // ========================================================================

  useEffect(() => {
    fetchVoices();
  }, []);

  const fetchVoices = async () => {
    try {
      setLoading(true);
      // TODO: Replace with actual endpoint when available
      // For now, use default voices
      setVoices(['Ethan', 'Nova', 'Aria']);
    } catch (error) {
      console.error('Error fetching voices:', error);
    } finally {
      setLoading(false);
    }
  };

  // ========================================================================
  // Handlers
  // ========================================================================

  const handleVoiceChange = (voice) => {
    onChange({ ...settings, voice });
  };

  const handleEmotionChange = (emotion) => {
    setCustomEmotion(emotion);
    onChange({ ...settings, emotion });
  };

  const handleSpeedChange = (speed) => {
    onChange({ ...settings, speed: parseFloat(speed) });
  };

  const handlePresetClick = (preset) => {
    onChange({
      ...settings,
      emotion: preset.emotion,
      speed: preset.speed,
    });
    setCustomEmotion(preset.emotion);
  };

  const handleGenerateChapter = async () => {
    if (!selectedChapter) {
      alert('Please select a chapter first');
      return;
    }
    // TODO: Implement chapter generation
    alert('Chapter generation not yet implemented');
  };

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-lg h-full flex flex-col">
      {/* Header */}
      <div className="border-b border-slate-700 p-4 bg-slate-750">
        <h3 className="text-lg font-bold text-white">Narration Settings</h3>
        <p className="text-xs text-slate-400 mt-1">Configure voice and generation</p>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-6">
        {/* Voice Selection */}
        <div>
          <label className="block text-sm font-semibold text-white mb-2">
            Voice
          </label>
          {loading ? (
            <div className="text-slate-400 text-sm">Loading voices...</div>
          ) : (
            <select
              value={settings.voice}
              onChange={(e) => handleVoiceChange(e.target.value)}
              className="w-full px-3 py-2 bg-slate-900 border border-slate-600 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {voices.map(voice => (
                <option key={voice} value={voice}>{voice}</option>
              ))}
            </select>
          )}
        </div>

        {/* Emotion/Style */}
        <div>
          <label className="block text-sm font-semibold text-white mb-2">
            Emotion / Style
          </label>
          <div className="space-y-2 mb-3">
            <input
              type="text"
              value={customEmotion}
              onChange={(e) => handleEmotionChange(e.target.value)}
              placeholder="e.g., warm, dramatic, energetic..."
              className="w-full px-3 py-2 bg-slate-900 border border-slate-600 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
            />
            <div className="flex flex-wrap gap-2">
              {EMOTION_PRESETS.map(emotion => (
                <button
                  key={emotion}
                  onClick={() => handleEmotionChange(emotion)}
                  className={`px-2 py-1 rounded text-xs font-medium transition ${
                    customEmotion === emotion
                      ? 'bg-blue-600 text-white'
                      : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                  }`}
                >
                  {emotion}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Speed Slider */}
        <div>
          <label className="block text-sm font-semibold text-white mb-2">
            Speed: <span className="text-blue-400 font-bold">{settings.speed.toFixed(2)}x</span>
          </label>
          <input
            type="range"
            min="0.8"
            max="1.3"
            step="0.05"
            value={settings.speed}
            onChange={(e) => handleSpeedChange(e.target.value)}
            className="w-full"
          />
          <div className="flex justify-between text-xs text-slate-400 mt-1">
            <span>0.8x</span>
            <span>1.0x (normal)</span>
            <span>1.3x</span>
          </div>
        </div>

        {/* Preset Buttons */}
        <div>
          <label className="block text-sm font-semibold text-white mb-2">
            Narration Presets
          </label>
          <div className="space-y-2">
            {NARRATOR_PRESETS.map(preset => (
              <button
                key={preset.name}
                onClick={() => handlePresetClick(preset)}
                className="w-full px-3 py-2 bg-slate-700 hover:bg-slate-600 text-slate-200 text-sm font-medium rounded-lg transition text-left"
              >
                <div>{preset.name}</div>
                <div className="text-xs text-slate-400 mt-1">
                  {preset.emotion} • {preset.speed.toFixed(2)}x
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Footer: Generate Button */}
      <div className="border-t border-slate-700 p-4 bg-slate-750">
        <button
          onClick={handleGenerateChapter}
          disabled={!selectedChapter}
          className="w-full px-4 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-lg font-medium disabled:opacity-50 disabled:cursor-not-allowed transition"
        >
          Generate Audio
        </button>
      </div>
    </div>
  );
}
```

---

## Acceptance Criteria

1. **Page Navigation:**
   - Page loads at `/book/:id`
   - Fetches book and chapter data on mount
   - Back button navigates to `/`

2. **Header:**
   - Displays book title, subtitle, author, narrator
   - Shows ID and page count
   - Back button works

3. **Left Panel (Chapter List):**
   - Lists all chapters with numbers/titles
   - Shows status icons (circle, spinner, checkmark, X)
   - Shows QA icons if applicable
   - Displays word count and duration
   - Click selects chapter (with unsaved changes warning in edit mode)
   - Highlights selected chapter

4. **Center Panel (Text Preview):**
   - Displays selected chapter's text
   - Shows word count and character count
   - Edit button toggles edit mode
   - In edit mode: textarea allows text modification
   - In edit mode: Save button calls API to update text
   - Save updates word count in DB
   - Prevents saving empty text

5. **Right Panel (Narration Settings):**
   - Voice dropdown populated (at least with default voices)
   - Emotion/style text input and preset buttons
   - Speed slider (0.8x - 1.3x) with live value display
   - Narration preset buttons (Audiobook Narrator, Dramatic Reading, etc.)
   - Presets update emotion and speed when clicked
   - Generate Audio button (placeholder for now, disabled without chapter selected)

6. **Responsive Design:**
   - Three-panel layout on desktop
   - Stacked layout on mobile/tablet
   - No horizontal scroll
   - Proper height management (full viewport)

7. **Data Management:**
   - Chapter selection persists narration settings
   - Unsaved changes warning before switching chapters in edit mode
   - All API calls include proper error handling

8. **Git Commit:**
   - All changes committed with message: `[PROMPT-05] Book detail page and chapter editing interface`

---

## Reference

- **Project Conventions:** `/sessions/focused-happy-pasteur/mnt/Coding Folder/Qwen3-TTS - Test/CLAUDE.md`
- **Tailwind CSS:** https://tailwindcss.com/
- **React Hooks:** https://react.dev/
