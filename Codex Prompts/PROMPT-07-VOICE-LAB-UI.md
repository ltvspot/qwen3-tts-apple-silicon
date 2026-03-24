# PROMPT-07: Voice Lab UI & Audio Player

**Objective:** Create the Voice Lab page with real-time TTS testing, audio playback, and voice preset management.

**Project Context:** See `/sessions/focused-happy-pasteur/mnt/Coding Folder/Qwen3-TTS - Test/CLAUDE.md` for conventions and architecture.

---

## Scope & Requirements

### 1. Voice Lab Page

**File:** `frontend/src/pages/VoiceLab.jsx`

Create a dedicated page for testing and fine-tuning narration voices.

```jsx
import React, { useState, useEffect } from 'react';
import AudioPlayer from '../components/AudioPlayer';
import VoicePresetManager from '../components/VoicePresetManager';

export default function VoiceLab() {
  const [testText, setTestText] = useState(
    'This is the Alexandria Audiobook Narrator. Test your voice settings here with any text you like.'
  );
  const [voice, setVoice] = useState('Ethan');
  const [emotion, setEmotion] = useState('neutral');
  const [speed, setSpeed] = useState(1.0);
  const [voices, setVoices] = useState([]);
  const [generating, setGenerating] = useState(false);
  const [audioUrl, setAudioUrl] = useState(null);
  const [duration, setDuration] = useState(0);
  const [error, setError] = useState(null);
  const [mode, setMode] = useState('single'); // 'single' or 'compare'
  const [compareAudioUrl, setCompareAudioUrl] = useState(null);
  const [compareVoice, setCompareVoice] = useState('Nova');
  const [compareEmotion, setCompareEmotion] = useState('neutral');
  const [compareSpeed, setCompareSpeed] = useState(1.0);
  const [compareDuration, setCompareDuration] = useState(0);
  const [presets, setPresets] = useState([]);

  // ========================================================================
  // Data Fetching
  // ========================================================================

  useEffect(() => {
    fetchVoices();
    loadPresets();
  }, []);

  const fetchVoices = async () => {
    try {
      const response = await fetch('/api/voice-lab/voices');
      if (!response.ok) throw new Error('Failed to fetch voices');
      const data = await response.json();
      setVoices(data.voices || []);
      if (data.voices && data.voices.length > 0) {
        setVoice(data.voices[0].name);
      }
    } catch (error) {
      console.error('Error fetching voices:', error);
      setError('Failed to load voices');
    }
  };

  const loadPresets = () => {
    // Load from localStorage
    const saved = localStorage.getItem('voicePresets');
    if (saved) {
      try {
        setPresets(JSON.parse(saved));
      } catch (e) {
        console.error('Failed to load presets:', e);
      }
    }
  };

  // ========================================================================
  // Audio Generation
  // ========================================================================

  const generateAudio = async (isCompare = false) => {
    try {
      setError(null);
      setGenerating(true);

      const [txt, v, e, s] = isCompare
        ? [testText, compareVoice, compareEmotion, compareSpeed]
        : [testText, voice, emotion, speed];

      if (!txt || txt.trim().length === 0) {
        throw new Error('Please enter text to generate');
      }

      const response = await fetch('/api/voice-lab/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: txt,
          voice: v,
          emotion: e,
          speed: s,
        }),
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Generation failed');
      }

      const data = await response.json();

      if (isCompare) {
        setCompareAudioUrl(data.audio_url);
        setCompareDuration(data.duration_seconds);
      } else {
        setAudioUrl(data.audio_url);
        setDuration(data.duration_seconds);
      }
    } catch (err) {
      setError(err.message || 'Audio generation failed');
      console.error('Generation error:', err);
    } finally {
      setGenerating(false);
    }
  };

  // ========================================================================
  // Preset Management
  // ========================================================================

  const savePreset = () => {
    const newPreset = {
      id: Date.now(),
      name: prompt('Preset name:'),
      voice,
      emotion,
      speed,
    };

    if (newPreset.name) {
      const updated = [...presets, newPreset];
      setPresets(updated);
      localStorage.setItem('voicePresets', JSON.stringify(updated));
    }
  };

  const loadPreset = (preset) => {
    setVoice(preset.voice);
    setEmotion(preset.emotion);
    setSpeed(preset.speed);
  };

  const deletePreset = (id) => {
    const updated = presets.filter(p => p.id !== id);
    setPresets(updated);
    localStorage.setItem('voicePresets', JSON.stringify(updated));
  };

  // ========================================================================
  // Render
  // ========================================================================

  const EMOTION_PRESETS = ['neutral', 'warm', 'dramatic', 'energetic', 'contemplative', 'authoritative'];

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 to-slate-800">
      {/* Header */}
      <header className="border-b border-slate-700 bg-slate-900 shadow-lg sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-6 py-6">
          <h1 className="text-3xl font-bold text-white mb-2">Voice Lab</h1>
          <p className="text-slate-400">Test and refine narration voices before generation</p>
        </div>
      </header>

      {/* Mode Toggle */}
      <div className="max-w-7xl mx-auto px-6 py-4 border-b border-slate-700">
        <div className="flex gap-2">
          <button
            onClick={() => setMode('single')}
            className={`px-4 py-2 rounded-lg font-medium transition ${
              mode === 'single'
                ? 'bg-blue-600 text-white'
                : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
            }`}
          >
            Single Voice
          </button>
          <button
            onClick={() => setMode('compare')}
            className={`px-4 py-2 rounded-lg font-medium transition ${
              mode === 'compare'
                ? 'bg-blue-600 text-white'
                : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
            }`}
          >
            Compare Two Voices
          </button>
        </div>
      </div>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-6 py-8">
        {error && (
          <div className="mb-6 p-4 bg-red-900/20 border border-red-500 rounded-lg text-red-300">
            {error}
          </div>
        )}

        {/* Text Input */}
        <div className="mb-8 bg-slate-800 border border-slate-700 rounded-lg p-6">
          <label className="block text-sm font-semibold text-white mb-3">
            Test Text
          </label>
          <textarea
            value={testText}
            onChange={(e) => setTestText(e.target.value)}
            placeholder="Enter text to generate audio..."
            maxLength={5000}
            className="w-full px-4 py-3 bg-slate-900 border border-slate-600 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none h-32 font-serif"
          />
          <div className="text-xs text-slate-400 mt-2">
            {testText.length} / 5000 characters
          </div>
        </div>

        {/* Single Voice Mode */}
        {mode === 'single' && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-8">
            {/* Settings Panel */}
            <div className="lg:col-span-1">
              <div className="bg-slate-800 border border-slate-700 rounded-lg p-6 space-y-6">
                {/* Voice Selection */}
                <div>
                  <label className="block text-sm font-semibold text-white mb-2">Voice</label>
                  <select
                    value={voice}
                    onChange={(e) => setVoice(e.target.value)}
                    className="w-full px-3 py-2 bg-slate-900 border border-slate-600 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    {voices.map(v => (
                      <option key={v.name} value={v.name}>{v.name}</option>
                    ))}
                  </select>
                </div>

                {/* Emotion */}
                <div>
                  <label className="block text-sm font-semibold text-white mb-2">
                    Emotion / Style
                  </label>
                  <input
                    type="text"
                    value={emotion}
                    onChange={(e) => setEmotion(e.target.value)}
                    placeholder="neutral, warm, dramatic..."
                    className="w-full px-3 py-2 bg-slate-900 border border-slate-600 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500 mb-2 text-sm"
                  />
                  <div className="flex flex-wrap gap-2">
                    {EMOTION_PRESETS.map(e => (
                      <button
                        key={e}
                        onClick={() => setEmotion(e)}
                        className={`px-2 py-1 rounded text-xs font-medium transition ${
                          emotion === e
                            ? 'bg-blue-600 text-white'
                            : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                        }`}
                      >
                        {e}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Speed */}
                <div>
                  <label className="block text-sm font-semibold text-white mb-2">
                    Speed: <span className="text-blue-400">{speed.toFixed(2)}x</span>
                  </label>
                  <input
                    type="range"
                    min="0.8"
                    max="1.3"
                    step="0.05"
                    value={speed}
                    onChange={(e) => setSpeed(parseFloat(e.target.value))}
                    className="w-full"
                  />
                  <div className="flex justify-between text-xs text-slate-400 mt-1">
                    <span>0.8x</span>
                    <span>1.0x</span>
                    <span>1.3x</span>
                  </div>
                </div>

                {/* Buttons */}
                <div className="space-y-2 pt-4 border-t border-slate-700">
                  <button
                    onClick={() => generateAudio(false)}
                    disabled={generating}
                    className="w-full px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium disabled:opacity-50 transition"
                  >
                    {generating ? 'Generating...' : 'Generate Audio'}
                  </button>
                  <button
                    onClick={savePreset}
                    className="w-full px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg font-medium transition"
                  >
                    Save as Preset
                  </button>
                </div>
              </div>
            </div>

            {/* Audio Player */}
            <div className="lg:col-span-2">
              {audioUrl ? (
                <AudioPlayer
                  audioUrl={audioUrl}
                  title="Generated Audio"
                  duration={duration}
                />
              ) : (
                <div className="bg-slate-800 border border-slate-700 rounded-lg p-8 h-full flex items-center justify-center">
                  <div className="text-slate-400 text-center">
                    <div className="text-lg font-medium mb-2">No audio generated yet</div>
                    <div className="text-sm">Click "Generate Audio" to create test audio</div>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Compare Mode */}
        {mode === 'compare' && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
            {/* Left Voice Settings */}
            <div className="bg-slate-800 border border-slate-700 rounded-lg p-6">
              <h3 className="text-lg font-bold text-white mb-4">Voice A</h3>
              <div className="space-y-4 mb-4">
                <div>
                  <label className="block text-sm font-semibold text-white mb-2">Voice</label>
                  <select
                    value={voice}
                    onChange={(e) => setVoice(e.target.value)}
                    className="w-full px-3 py-2 bg-slate-900 border border-slate-600 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    {voices.map(v => (
                      <option key={v.name} value={v.name}>{v.name}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-semibold text-white mb-2">Emotion</label>
                  <input
                    type="text"
                    value={emotion}
                    onChange={(e) => setEmotion(e.target.value)}
                    className="w-full px-3 py-2 bg-slate-900 border border-slate-600 rounded-lg text-white text-sm"
                  />
                </div>
                <div>
                  <label className="block text-sm font-semibold text-white mb-2">
                    Speed: {speed.toFixed(2)}x
                  </label>
                  <input
                    type="range"
                    min="0.8"
                    max="1.3"
                    step="0.05"
                    value={speed}
                    onChange={(e) => setSpeed(parseFloat(e.target.value))}
                    className="w-full"
                  />
                </div>
              </div>
              {audioUrl && (
                <AudioPlayer
                  audioUrl={audioUrl}
                  title="Voice A"
                  duration={duration}
                />
              )}
            </div>

            {/* Right Voice Settings */}
            <div className="bg-slate-800 border border-slate-700 rounded-lg p-6">
              <h3 className="text-lg font-bold text-white mb-4">Voice B</h3>
              <div className="space-y-4 mb-4">
                <div>
                  <label className="block text-sm font-semibold text-white mb-2">Voice</label>
                  <select
                    value={compareVoice}
                    onChange={(e) => setCompareVoice(e.target.value)}
                    className="w-full px-3 py-2 bg-slate-900 border border-slate-600 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    {voices.map(v => (
                      <option key={v.name} value={v.name}>{v.name}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-semibold text-white mb-2">Emotion</label>
                  <input
                    type="text"
                    value={compareEmotion}
                    onChange={(e) => setCompareEmotion(e.target.value)}
                    className="w-full px-3 py-2 bg-slate-900 border border-slate-600 rounded-lg text-white text-sm"
                  />
                </div>
                <div>
                  <label className="block text-sm font-semibold text-white mb-2">
                    Speed: {compareSpeed.toFixed(2)}x
                  </label>
                  <input
                    type="range"
                    min="0.8"
                    max="1.3"
                    step="0.05"
                    value={compareSpeed}
                    onChange={(e) => setCompareSpeed(parseFloat(e.target.value))}
                    className="w-full"
                  />
                </div>
              </div>
              {compareAudioUrl && (
                <AudioPlayer
                  audioUrl={compareAudioUrl}
                  title="Voice B"
                  duration={compareDuration}
                />
              )}
            </div>
          </div>
        )}

        {/* Generate Button for Compare Mode */}
        {mode === 'compare' && (
          <div className="mb-8 flex gap-3">
            <button
              onClick={() => generateAudio(false)}
              disabled={generating}
              className="px-6 py-3 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium disabled:opacity-50 transition flex-1"
            >
              Generate Voice A
            </button>
            <button
              onClick={() => generateAudio(true)}
              disabled={generating}
              className="px-6 py-3 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium disabled:opacity-50 transition flex-1"
            >
              Generate Voice B
            </button>
          </div>
        )}

        {/* Preset Manager */}
        {mode === 'single' && (
          <VoicePresetManager
            presets={presets}
            onLoadPreset={loadPreset}
            onDeletePreset={deletePreset}
          />
        )}
      </main>
    </div>
  );
}
```

---

### 2. Audio Player Component

**File:** `frontend/src/components/AudioPlayer.jsx`

Interactive audio player with waveform visualization.

```jsx
import React, { useState, useRef, useEffect } from 'react';

export default function AudioPlayer({ audioUrl, title, duration }) {
  const audioRef = useRef(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [audioLoaded, setAudioLoaded] = useState(false);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    const handleTimeUpdate = () => {
      setCurrentTime(audio.currentTime);
    };

    const handleEnded = () => {
      setIsPlaying(false);
    };

    const handleLoadedMetadata = () => {
      setAudioLoaded(true);
    };

    audio.addEventListener('timeupdate', handleTimeUpdate);
    audio.addEventListener('ended', handleEnded);
    audio.addEventListener('loadedmetadata', handleLoadedMetadata);

    return () => {
      audio.removeEventListener('timeupdate', handleTimeUpdate);
      audio.removeEventListener('ended', handleEnded);
      audio.removeEventListener('loadedmetadata', handleLoadedMetadata);
    };
  }, []);

  const handlePlayPause = () => {
    const audio = audioRef.current;
    if (audio.paused) {
      audio.play();
      setIsPlaying(true);
    } else {
      audio.pause();
      setIsPlaying(false);
    }
  };

  const handleProgressChange = (e) => {
    const audio = audioRef.current;
    const rect = e.currentTarget.getBoundingClientRect();
    const percent = (e.clientX - rect.left) / rect.width;
    const newTime = percent * (audio.duration || 0);
    audio.currentTime = newTime;
    setCurrentTime(newTime);
  };

  const formatTime = (seconds) => {
    if (!seconds || isNaN(seconds)) return '0:00';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  const totalDuration = duration || (audioRef.current?.duration || 0);
  const progress = totalDuration ? (currentTime / totalDuration) * 100 : 0;

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-lg p-6">
      {/* Title */}
      <h4 className="text-lg font-bold text-white mb-4">{title}</h4>

      {/* Waveform / Progress Bar */}
      <div
        onClick={handleProgressChange}
        className="mb-4 h-8 bg-slate-900 rounded-lg cursor-pointer overflow-hidden border border-slate-600 relative"
      >
        {/* Waveform background */}
        <div className="absolute inset-0 opacity-30 flex items-center">
          {Array.from({ length: 40 }).map((_, i) => (
            <div
              key={i}
              className="flex-1 bg-slate-500"
              style={{
                height: `${30 + Math.random() * 40}%`,
                marginRight: '2px',
              }}
            />
          ))}
        </div>

        {/* Progress fill */}
        <div
          className="absolute inset-y-0 left-0 bg-gradient-to-r from-blue-600 to-blue-500"
          style={{ width: `${progress}%` }}
        />
      </div>

      {/* Controls */}
      <div className="flex items-center justify-between mb-4">
        {/* Play/Pause Button */}
        <button
          onClick={handlePlayPause}
          disabled={!audioLoaded}
          className="p-2 bg-blue-600 hover:bg-blue-700 text-white rounded-full disabled:opacity-50 disabled:cursor-not-allowed transition"
        >
          {isPlaying ? (
            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
              <path d="M5.5 3a1.5 1.5 0 011.5 1.5v10a1.5 1.5 0 01-3 0V4.5A1.5 1.5 0 015.5 3zm8 0a1.5 1.5 0 011.5 1.5v10a1.5 1.5 0 01-3 0V4.5A1.5 1.5 0 0113.5 3z" />
            </svg>
          ) : (
            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
              <path d="M6.3 2.841A1.5 1.5 0 004 4.11V15.89a1.5 1.5 0 002.3 1.269l9.344-5.89a1.5 1.5 0 000-2.538L6.3 2.84z" />
            </svg>
          )}
        </button>

        {/* Time Display */}
        <div className="flex-1 ml-4 text-sm text-slate-400 font-mono">
          <span>{formatTime(currentTime)}</span>
          <span className="mx-2">•</span>
          <span>{formatTime(totalDuration)}</span>
        </div>

        {/* Download Button */}
        <a
          href={audioUrl}
          download={title.replace(/\s+/g, '-')}
          className="p-2 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
          </svg>
        </a>
      </div>

      {/* Hidden Audio Element */}
      <audio ref={audioRef} src={audioUrl} />
    </div>
  );
}
```

---

### 3. Voice Preset Manager Component

**File:** `frontend/src/components/VoicePresetManager.jsx`

Manage saved voice presets.

```jsx
import React from 'react';

export default function VoicePresetManager({ presets, onLoadPreset, onDeletePreset }) {
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-lg p-6">
      <h3 className="text-lg font-bold text-white mb-4">Saved Presets ({presets.length})</h3>

      {presets.length === 0 ? (
        <div className="text-slate-400 text-sm text-center py-8">
          No presets saved yet. Generate a voice and click "Save as Preset" to create one.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {presets.map(preset => (
            <div
              key={preset.id}
              className="bg-slate-700 border border-slate-600 rounded-lg p-4 flex items-start justify-between hover:bg-slate-600 transition"
            >
              <div className="flex-1">
                <div className="text-white font-medium mb-2">{preset.name}</div>
                <div className="text-xs text-slate-400 space-y-1">
                  <div>
                    <span className="text-slate-300 font-mono">{preset.voice}</span>
                    <span className="ml-2">•</span>
                    <span className="ml-2 text-slate-300">{preset.emotion}</span>
                  </div>
                  <div>Speed: {preset.speed.toFixed(2)}x</div>
                </div>
              </div>
              <div className="flex gap-2 flex-shrink-0">
                <button
                  onClick={() => onLoadPreset(preset)}
                  className="p-2 bg-blue-600 hover:bg-blue-700 text-white rounded text-xs font-medium transition"
                >
                  Load
                </button>
                <button
                  onClick={() => onDeletePreset(preset.id)}
                  className="p-2 bg-red-600 hover:bg-red-700 text-white rounded text-xs font-medium transition"
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

---

## Acceptance Criteria

1. **Voice Lab Page:**
   - Page loads at `/voice-lab` without errors
   - Fetches voices from `GET /api/voice-lab/voices`
   - Text input area with 5000 character limit
   - Displays character count

2. **Single Voice Mode:**
   - Voice dropdown populated with available voices
   - Emotion/style input with preset buttons
   - Speed slider (0.8x - 1.3x) with live value
   - Generate button calls `POST /api/voice-lab/test`
   - Audio player displays generated audio
   - Save as Preset button saves to localStorage

3. **Compare Mode:**
   - Two side-by-side voice configuration panels
   - Each panel has voice, emotion, speed controls
   - Two separate AudioPlayer components
   - Two generate buttons (one per voice)
   - Both audio files play independently

4. **AudioPlayer Component:**
   - Displays play/pause button
   - Shows progress bar (clickable for seeking)
   - Displays current time and total duration
   - Waveform visualization (decorative or functional)
   - Download button to save audio file
   - Play/pause updates correctly
   - Shows loading state before audio loads

5. **Preset Management:**
   - Save as Preset button prompts for name
   - Presets saved to localStorage
   - Preset list shows voice, emotion, speed
   - Load button applies preset to controls
   - Delete button removes preset
   - Presets persist across page reloads

6. **Error Handling:**
   - Empty text shows error message
   - Generation failures display user-friendly error
   - Network errors handled gracefully

7. **Responsive Design:**
   - Works on mobile (stacked controls)
   - Works on tablet (2-column compare mode)
   - Works on desktop (optimal layout)
   - No horizontal scroll

8. **Git Commit:**
   - All changes committed with message: `[PROMPT-07] Voice Lab UI and audio player`

---

## Reference

- **Project Conventions:** `/sessions/focused-happy-pasteur/mnt/Coding Folder/Qwen3-TTS - Test/CLAUDE.md`
- **Web Audio API:** https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API
- **Tailwind CSS:** https://tailwindcss.com/
