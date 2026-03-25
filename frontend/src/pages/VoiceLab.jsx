import React, { useEffect, useRef, useState } from "react";
import AppShell from "../components/AppShell";
import AudioPlayer from "../components/AudioPlayer";
import ClonedVoicesList from "../components/ClonedVoicesList";
import ProgressHeartbeat from "../components/ProgressHeartbeat";
import VoiceCloneForm from "../components/VoiceCloneForm";
import VoicePresetManager from "../components/VoicePresetManager";
import VoiceSelector from "../components/VoiceSelector";

const DEFAULT_TEST_TEXT = "This is the Alexandria Audiobook Narrator. Test your voice settings here with any text you like.";
const EMOTION_PRESETS = ["neutral", "warm", "dramatic", "energetic", "contemplative", "authoritative"];
const PRESET_STORAGE_KEY = "voicePresets";
const VOICE_LOAD_MAX_RETRIES = 20;

function readStoredPresets() {
  try {
    const savedPresets = window.localStorage.getItem(PRESET_STORAGE_KEY);
    if (!savedPresets) {
      return [];
    }

    const parsedPresets = JSON.parse(savedPresets);
    if (!Array.isArray(parsedPresets)) {
      return [];
    }

    return parsedPresets.filter((preset) => preset && preset.name && preset.voice);
  } catch (error) {
    console.error("Failed to load presets:", error);
    return [];
  }
}

function writeStoredPresets(presets) {
  if (presets.length === 0) {
    window.localStorage.removeItem(PRESET_STORAGE_KEY);
    return;
  }

  window.localStorage.setItem(PRESET_STORAGE_KEY, JSON.stringify(presets));
}

function selectVoiceValue(voices, currentVoice, fallbackIndex = 0) {
  if (voices.some((voiceOption) => voiceOption.name === currentVoice)) {
    return currentVoice;
  }

  return voices[fallbackIndex]?.name ?? voices[0]?.name ?? "";
}

function VoiceControls({
  audioUrl,
  duration,
  emotion,
  generateLabel,
  generating,
  label,
  loadingVoices,
  loadingMessage = "",
  onEmotionChange,
  onGenerate,
  onQuickEmotionSelect,
  onRetryPreview,
  previewState = null,
  onSpeedChange,
  onVoiceChange,
  speed,
  showPreview = true,
  subtitle,
  voice,
  voices,
}) {
  const fieldKey = label.toLowerCase().replace(/\s+/g, "-");

  return (
    <section className="rounded-[2rem] border border-slate-200 bg-white p-6 shadow-xl shadow-slate-900/5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">{label}</p>
          <h3 className="mt-2 text-2xl font-semibold text-slate-950">{subtitle}</h3>
        </div>
        <div className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] text-slate-600">
          {loadingVoices ? "Loading voices" : `${voices.length} voices`}
        </div>
      </div>

      <div className="mt-6 space-y-6">
        <div>
          <label className="block text-sm font-semibold text-slate-900" htmlFor={`${fieldKey}-voice`}>
            Voice
          </label>
          <VoiceSelector
            ariaLabel={label === "Voice A" ? "Primary voice" : "Compare voice"}
            className="mt-2 w-full rounded-2xl border border-slate-300 bg-slate-50 px-4 py-3 text-slate-900 outline-none transition focus:border-slate-950 focus:bg-white"
            disabled={loadingVoices}
            emptyLabel="No voices available"
            id={`${fieldKey}-voice`}
            onChange={onVoiceChange}
            value={voice}
            voices={voices}
          />
          {loadingVoices && loadingMessage ? (
            <p className="mt-3 text-sm text-slate-500">{loadingMessage}</p>
          ) : null}
        </div>

        <div>
          <label className="block text-sm font-semibold text-slate-900" htmlFor={`${fieldKey}-emotion`}>
            Emotion / Style
          </label>
          <input
            aria-label={label === "Voice A" ? "Primary emotion" : "Compare emotion"}
            className="mt-2 w-full rounded-2xl border border-slate-300 bg-slate-50 px-4 py-3 text-slate-900 outline-none transition focus:border-slate-950 focus:bg-white"
            id={`${fieldKey}-emotion`}
            onChange={(event) => onEmotionChange(event.target.value)}
            placeholder="neutral, warm, dramatic..."
            type="text"
            value={emotion}
          />
          <div className="mt-3 flex flex-wrap gap-2">
            {EMOTION_PRESETS.map((emotionPreset) => (
              <button
                className={`rounded-full px-3 py-2 text-xs font-semibold uppercase tracking-[0.16em] transition ${
                  emotionPreset === emotion
                    ? "bg-slate-950 text-amber-200"
                    : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                }`}
                key={`${label}-${emotionPreset}`}
                onClick={() => onQuickEmotionSelect(emotionPreset)}
                type="button"
              >
                {emotionPreset}
              </button>
            ))}
          </div>
        </div>

        <div>
          <div className="flex items-center justify-between">
            <label className="block text-sm font-semibold text-slate-900" htmlFor={`${fieldKey}-speed`}>
              Speed
            </label>
            <span className="text-sm font-semibold text-sky-700">{Number(speed).toFixed(2)}x</span>
          </div>
          <input
            aria-label={label === "Voice A" ? "Primary speed" : "Compare speed"}
            className="mt-3 w-full accent-slate-950"
            id={`${fieldKey}-speed`}
            max="2.0"
            min="0.5"
            onChange={(event) => onSpeedChange(Number.parseFloat(event.target.value))}
            step="0.05"
            type="range"
            value={speed}
          />
          <div className="mt-2 flex justify-between text-xs font-medium uppercase tracking-[0.14em] text-slate-500">
            <span>0.5x</span>
            <span>1.0x</span>
            <span>2.0x</span>
          </div>
        </div>

        <button
          className="inline-flex w-full items-center justify-center rounded-full bg-slate-950 px-5 py-3 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
          disabled={generating || loadingVoices || voices.length === 0}
          onClick={onGenerate}
          type="button"
        >
          {generating ? "Generating preview..." : generateLabel}
        </button>

        {previewState?.errorMessage ? (
          <div className="rounded-[1.5rem] border border-rose-200 bg-rose-50 px-4 py-4 text-sm text-rose-700">
            <div>{previewState.errorMessage}</div>
            <button
              className="mt-3 inline-flex items-center rounded-full border border-rose-300 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-rose-700 transition hover:bg-rose-100"
              onClick={onRetryPreview}
              type="button"
            >
              Retry
            </button>
          </div>
        ) : null}

        {previewState?.isActive ? (
          <ProgressHeartbeat
            isActive={previewState.isActive}
            progressPercent={null}
            showETA={null}
            size="sm"
            stage={previewState.stage}
            startTime={previewState.startTime}
          />
        ) : null}
      </div>

      {showPreview ? (
        <div className="mt-6">
          {audioUrl ? (
            <AudioPlayer audioUrl={audioUrl} duration={duration} title={`${label} Preview`} />
          ) : (
            <div className="rounded-[1.75rem] border border-dashed border-slate-300 bg-slate-50 px-6 py-10 text-center text-sm text-slate-500">
              Generate a clip to audition {subtitle.toLowerCase()} here.
            </div>
          )}
        </div>
      ) : null}
    </section>
  );
}

export default function VoiceLab() {
  const previewStageTimeoutsRef = useRef({});
  const voiceLoadRequestRef = useRef(0);
  const voiceRetryTimeoutRef = useRef(null);

  const [activeTab, setActiveTab] = useState("audition");
  const [audioUrl, setAudioUrl] = useState("");
  const [clonedVoices, setClonedVoices] = useState([]);
  const [clonedVoicesError, setClonedVoicesError] = useState("");
  const [compareAudioUrl, setCompareAudioUrl] = useState("");
  const [compareDuration, setCompareDuration] = useState(0);
  const [compareEmotion, setCompareEmotion] = useState("neutral");
  const [compareSpeed, setCompareSpeed] = useState(1.0);
  const [compareVoice, setCompareVoice] = useState("Nova");
  const [deletingVoiceName, setDeletingVoiceName] = useState("");
  const [duration, setDuration] = useState(0);
  const [engineName, setEngineName] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [generationTarget, setGenerationTarget] = useState("");
  const [loadingClonedVoices, setLoadingClonedVoices] = useState(true);
  const [loadingVoices, setLoadingVoices] = useState(true);
  const [mode, setMode] = useState("single");
  const [presets, setPresets] = useState([]);
  const [previewStateByTarget, setPreviewStateByTarget] = useState({
    compare: null,
    primary: null,
  });
  const [testText, setTestText] = useState(DEFAULT_TEST_TEXT);
  const [voice, setVoice] = useState("Ethan");
  const [voiceLoadAttempt, setVoiceLoadAttempt] = useState(0);
  const [voiceLoadingMessage, setVoiceLoadingMessage] = useState("");
  const [voices, setVoices] = useState([]);
  const [emotion, setEmotion] = useState("neutral");
  const [speed, setSpeed] = useState(1.0);

  function setPreviewState(target, nextState) {
    setPreviewStateByTarget((currentState) => ({
      ...currentState,
      [target]: nextState,
    }));
  }

  function clearPreviewStageTimeout(target) {
    const timeoutId = previewStageTimeoutsRef.current[target];
    if (timeoutId) {
      window.clearTimeout(timeoutId);
      delete previewStageTimeoutsRef.current[target];
    }
  }

  function schedulePreviewStage(target) {
    clearPreviewStageTimeout(target);
    previewStageTimeoutsRef.current[target] = window.setTimeout(() => {
      setPreviewStateByTarget((currentState) => {
        const currentPreviewState = currentState[target];
        if (!currentPreviewState?.isActive) {
          return currentState;
        }

        return {
          ...currentState,
          [target]: {
            ...currentPreviewState,
            stage: "Processing...",
          },
        };
      });
    }, 1500);
  }

  async function loadVoices(attempt = 1) {
    const requestId = voiceLoadRequestRef.current + 1;
    voiceLoadRequestRef.current = requestId;
    setLoadingVoices(true);
    if (voiceRetryTimeoutRef.current) {
      window.clearTimeout(voiceRetryTimeoutRef.current);
      voiceRetryTimeoutRef.current = null;
    }
    let keepLoading = false;

    try {
      const response = await fetch("/api/voice-lab/voices");
      if (!response.ok) {
        throw new Error("Failed to fetch voices.");
      }

      const payload = await response.json();
      if (payload.loading) {
        const nextAttempt = Math.min(attempt, VOICE_LOAD_MAX_RETRIES);
        setVoiceLoadAttempt(nextAttempt);
        setVoiceLoadingMessage(`TTS engine is loading... retrying in 3s (attempt ${nextAttempt}/${VOICE_LOAD_MAX_RETRIES})`);
        keepLoading = true;

        if (nextAttempt >= VOICE_LOAD_MAX_RETRIES) {
          setLoadingVoices(false);
          setErrorMessage(payload.message ?? "TTS engine is still loading.");
          return;
        }

        voiceRetryTimeoutRef.current = window.setTimeout(() => {
          void loadVoices(nextAttempt + 1);
        }, 3000);
        return;
      }

      if (voiceLoadRequestRef.current !== requestId) {
        return;
      }

      const availableVoices = payload.voices ?? [];
      setEngineName(payload.engine ?? "");
      setVoices(availableVoices);
      setVoice((currentVoice) => selectVoiceValue(availableVoices, currentVoice, 0));
      setCompareVoice((currentVoice) => selectVoiceValue(availableVoices, currentVoice, 1));
      setVoiceLoadAttempt(0);
      setVoiceLoadingMessage("");
      setErrorMessage("");
    } catch (error) {
      setVoiceLoadAttempt(0);
      setVoiceLoadingMessage("");
      setErrorMessage(error instanceof Error ? error.message : "Failed to load voices.");
      console.error("Error fetching voices:", error);
    } finally {
      if (voiceLoadRequestRef.current === requestId && !keepLoading) {
        setLoadingVoices(false);
      }
    }
  }

  async function loadClonedVoices() {
    setLoadingClonedVoices(true);

    try {
      const response = await fetch("/api/voice-lab/cloned-voices");
      if (!response.ok) {
        throw new Error("Failed to fetch cloned voices.");
      }

      const payload = await response.json();
      setClonedVoices(payload.cloned_voices ?? []);
      setClonedVoicesError("");
    } catch (error) {
      setClonedVoices([]);
      setClonedVoicesError(
        error instanceof Error ? error.message : "Failed to load cloned voices.",
      );
      console.error("Error fetching cloned voices:", error);
    } finally {
      setLoadingClonedVoices(false);
    }
  }

  useEffect(() => {
    setPresets(readStoredPresets());
    void loadVoices();
    void loadClonedVoices();
    return () => {
      if (voiceRetryTimeoutRef.current) {
        window.clearTimeout(voiceRetryTimeoutRef.current);
      }
      Object.values(previewStageTimeoutsRef.current).forEach((timeoutId) => {
        window.clearTimeout(timeoutId);
      });
    };
  }, []);

  useEffect(() => {
    document.title = "Voice Lab | Alexandria Audiobook Narrator";
  }, []);

  const handleGenerateAudio = async (isCompare = false) => {
    const selectedVoice = isCompare ? compareVoice : voice;
    const selectedEmotion = isCompare ? compareEmotion : emotion;
    const selectedSpeed = isCompare ? compareSpeed : speed;
    const selectedTarget = isCompare ? "compare" : "primary";
    const startedAt = Date.now();

    if (!testText.trim()) {
      setErrorMessage("Please enter text to generate audio.");
      return;
    }

    if (!selectedVoice) {
      setErrorMessage("No voice is available to test yet.");
      return;
    }

    setErrorMessage("");
    setGenerationTarget(selectedTarget);
    clearPreviewStageTimeout(selectedTarget);
    setPreviewState(selectedTarget, {
      errorMessage: "",
      isActive: true,
      stage: "Synthesizing audio...",
      startTime: startedAt,
    });
    schedulePreviewStage(selectedTarget);

    if (isCompare) {
      setCompareAudioUrl("");
      setCompareDuration(0);
    } else {
      setAudioUrl("");
      setDuration(0);
    }

    try {
      const response = await fetch("/api/voice-lab/test", {
        body: JSON.stringify({
          text: testText,
          voice: selectedVoice,
          emotion: selectedEmotion,
          speed: selectedSpeed,
        }),
        headers: {
          "Content-Type": "application/json",
        },
        method: "POST",
      });

      if (!response.ok) {
        let detail = "Audio generation failed.";

        try {
          const payload = await response.json();
          if (payload?.detail) {
            detail = payload.detail;
          }
        } catch (error) {
          console.error("Failed to parse generation error:", error);
        }

        throw new Error(detail);
      }

      const payload = await response.json();
      clearPreviewStageTimeout(selectedTarget);
      setPreviewState(selectedTarget, {
        errorMessage: "",
        isActive: false,
        stage: "Ready",
        startTime: startedAt,
      });
      if (isCompare) {
        setCompareAudioUrl(payload.audio_url);
        setCompareDuration(payload.duration_seconds ?? 0);
      } else {
        setAudioUrl(payload.audio_url);
        setDuration(payload.duration_seconds ?? 0);
      }
    } catch (error) {
      clearPreviewStageTimeout(selectedTarget);
      setPreviewState(selectedTarget, {
        errorMessage: error instanceof Error ? error.message : "Audio generation failed.",
        isActive: false,
        stage: "Processing...",
        startTime: startedAt,
      });
      console.error("Generation error:", error);
    } finally {
      setGenerationTarget("");
    }
  };

  function handleRetryPreview(target) {
    void handleGenerateAudio(target === "compare");
  }

  const handleSavePreset = () => {
    const presetName = window.prompt("Preset name:");
    const trimmedName = presetName?.trim();

    if (!trimmedName) {
      return;
    }

    const nextPresets = [
      {
        id: `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
        name: trimmedName,
        voice,
        emotion,
        speed,
      },
      ...presets,
    ];

    setPresets(nextPresets);
    writeStoredPresets(nextPresets);
  };

  const handleLoadPreset = (preset) => {
    const presetVoiceExists = voices.some((voiceOption) => voiceOption.name === preset.voice);

    if (presetVoiceExists) {
      setVoice(preset.voice);
      setErrorMessage("");
    } else {
      setErrorMessage(`Preset "${preset.name}" uses a voice that is not currently available.`);
    }

    setEmotion(preset.emotion ?? "neutral");
    setSpeed(Number(preset.speed) || 1.0);
    setActiveTab("audition");
    setMode("single");
  };

  const handleDeletePreset = (presetId) => {
    const nextPresets = presets.filter((preset) => preset.id !== presetId);
    setPresets(nextPresets);
    writeStoredPresets(nextPresets);
  };

  async function handleCloneCreated() {
    await Promise.all([loadVoices(), loadClonedVoices()]);
    setActiveTab("clone");
  }

  async function handleDeleteClonedVoice(voiceName) {
    if (!window.confirm(`Delete voice "${voiceName}"?`)) {
      return;
    }

    setDeletingVoiceName(voiceName);
    setClonedVoicesError("");

    try {
      const response = await fetch(`/api/voice-lab/cloned-voices/${voiceName}`, {
        method: "DELETE",
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.detail ?? "Failed to delete cloned voice.");
      }

      await Promise.all([loadVoices(), loadClonedVoices()]);
    } catch (error) {
      setClonedVoicesError(
        error instanceof Error ? error.message : "Failed to delete cloned voice.",
      );
    } finally {
      setDeletingVoiceName("");
    }
  }

  return (
    <AppShell
      description="Audition narration settings, compare alternate deliveries, and create reference-based cloned voices that can be reused in production generation."
      title="Voice Lab"
    >
      <div className="space-y-8">
        <section className="grid gap-6 xl:grid-cols-[1.2fr,0.8fr]">
          <div className="overflow-hidden rounded-[2rem] border border-slate-200 bg-[linear-gradient(135deg,#0f172a_0%,#1e293b_52%,#7c2d12_100%)] px-8 py-8 text-white shadow-2xl shadow-slate-900/10">
            <p className="text-xs font-semibold uppercase tracking-[0.34em] text-amber-200/80">
              Narration Tuning
            </p>
            <h2 className="mt-4 max-w-2xl text-4xl font-semibold leading-tight">
              Pressure the voice before it reaches a full book.
            </h2>
            <p className="mt-4 max-w-2xl text-sm leading-7 text-slate-200/85">
              Test a paragraph, compare nearby settings, and promote only the voices that survive real scrutiny. If the built-ins miss the target, build a clone from a clean reference.
            </p>
          </div>

          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-1">
            <div className="rounded-[2rem] border border-slate-200 bg-white px-6 py-5 shadow-xl shadow-slate-900/5">
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">Engine</p>
              <p className="mt-3 text-3xl font-semibold text-slate-950">
                {engineName || (loadingVoices ? "Loading..." : "Unavailable")}
              </p>
              <p className="mt-2 text-sm text-slate-600">
                Current backend exposed by <code>/api/voice-lab/voices</code>.
              </p>
            </div>

            <div className="rounded-[2rem] border border-slate-200 bg-white px-6 py-5 shadow-xl shadow-slate-900/5">
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">Voices Ready</p>
              <p className="mt-3 text-3xl font-semibold text-slate-950">{voices.length}</p>
              <p className="mt-2 text-sm text-slate-600">
                {voices.length === 1 ? "1 available voice" : `${voices.length} available voices`}
              </p>
            </div>

            <div className="rounded-[2rem] border border-slate-200 bg-white px-6 py-5 shadow-xl shadow-slate-900/5">
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">Cloned Voices</p>
              <p className="mt-3 text-3xl font-semibold text-slate-950">{clonedVoices.length}</p>
              <p className="mt-2 text-sm text-slate-600">
                Reference-backed voices saved for future narration jobs.
              </p>
            </div>
          </div>
        </section>

        {errorMessage ? (
          <div className="rounded-[1.75rem] border border-rose-200 bg-rose-50 px-5 py-4 text-sm font-medium text-rose-700">
            {errorMessage}
          </div>
        ) : null}

        <section className="rounded-[2rem] border border-slate-200 bg-white p-4 shadow-xl shadow-slate-900/5">
          <div className="flex flex-wrap gap-3">
            <button
              className={`rounded-full px-5 py-3 text-sm font-semibold transition ${
                activeTab === "audition"
                  ? "bg-slate-950 text-amber-200"
                  : "bg-slate-100 text-slate-700 hover:bg-slate-200"
              }`}
              onClick={() => setActiveTab("audition")}
              type="button"
            >
              Audition Voices
            </button>
            <button
              className={`rounded-full px-5 py-3 text-sm font-semibold transition ${
                activeTab === "clone"
                  ? "bg-slate-950 text-amber-200"
                  : "bg-slate-100 text-slate-700 hover:bg-slate-200"
              }`}
              onClick={() => setActiveTab("clone")}
              type="button"
            >
              Clone Voice
            </button>
          </div>
        </section>

        {activeTab === "audition" ? (
          <div className="space-y-8">
            <section className="grid gap-6 lg:grid-cols-[1.1fr,0.9fr]">
              <div className="rounded-[2rem] border border-slate-200 bg-white p-6 shadow-xl shadow-slate-900/5">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">
                      Test Passage
                    </p>
                    <h3 className="mt-2 text-2xl font-semibold text-slate-950">Text to synthesize</h3>
                  </div>
                  <div className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] text-slate-600">
                    5000 char max
                  </div>
                </div>
                <textarea
                  aria-label="Test text"
                  className="mt-6 h-44 w-full resize-none rounded-[1.75rem] border border-slate-300 bg-slate-50 px-5 py-4 text-base leading-7 text-slate-900 outline-none transition focus:border-slate-950 focus:bg-white"
                  maxLength={5000}
                  onChange={(event) => setTestText(event.target.value)}
                  placeholder="Enter text to generate audio..."
                  value={testText}
                />
                <div className="mt-3 flex items-center justify-between text-sm text-slate-500">
                  <span>Use a paragraph long enough to expose pacing issues.</span>
                  <span>{testText.length} / 5000 characters</span>
                </div>
              </div>

              <div className="rounded-[2rem] border border-slate-200 bg-[linear-gradient(135deg,rgba(251,191,36,0.1)_0%,rgba(14,165,233,0.08)_100%)] p-6 shadow-xl shadow-slate-900/5">
                <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">Workflow</p>
                <h3 className="mt-2 text-2xl font-semibold text-slate-950">How to use this page well</h3>
                <ol className="mt-5 space-y-4 text-sm leading-7 text-slate-700">
                  <li>1. Start with a neutral baseline before adding style direction or speed changes.</li>
                  <li>2. Compare two nearby settings instead of jumping across radically different voices.</li>
                  <li>3. If none of the built-ins land, create a cloned voice from a high-quality sample.</li>
                </ol>
              </div>
            </section>

            <section className="rounded-[2rem] border border-slate-200 bg-white p-4 shadow-xl shadow-slate-900/5">
              <div className="flex flex-wrap gap-3">
                <button
                  className={`rounded-full px-5 py-3 text-sm font-semibold transition ${
                    mode === "single"
                      ? "bg-slate-950 text-amber-200"
                      : "bg-slate-100 text-slate-700 hover:bg-slate-200"
                  }`}
                  onClick={() => setMode("single")}
                  type="button"
                >
                  Single Voice
                </button>
                <button
                  className={`rounded-full px-5 py-3 text-sm font-semibold transition ${
                    mode === "compare"
                      ? "bg-slate-950 text-amber-200"
                      : "bg-slate-100 text-slate-700 hover:bg-slate-200"
                  }`}
                  onClick={() => setMode("compare")}
                  type="button"
                >
                  Compare Voices
                </button>
              </div>
            </section>

            {mode === "single" ? (
              <div className="grid gap-6 xl:grid-cols-[0.9fr,1.1fr]">
                <div className="space-y-6">
                  <VoiceControls
                    audioUrl=""
                    duration={0}
                    emotion={emotion}
                    generateLabel="Generate Preview"
                    generating={generationTarget === "primary"}
                    label="Voice A"
                    loadingVoices={loadingVoices}
                    loadingMessage={voiceLoadingMessage}
                    onEmotionChange={setEmotion}
                    onGenerate={() => {
                      void handleGenerateAudio(false);
                    }}
                    onQuickEmotionSelect={setEmotion}
                    onRetryPreview={() => {
                      handleRetryPreview("primary");
                    }}
                    onSpeedChange={setSpeed}
                    onVoiceChange={setVoice}
                    previewState={previewStateByTarget.primary}
                    speed={speed}
                    showPreview={false}
                    subtitle="Primary voice settings"
                    voice={voice}
                    voices={voices}
                  />

                  <button
                    className="inline-flex w-full items-center justify-center rounded-full border border-emerald-300 bg-emerald-50 px-5 py-3 text-sm font-semibold text-emerald-800 transition hover:border-emerald-400 hover:bg-emerald-100"
                    onClick={handleSavePreset}
                    type="button"
                  >
                    Save Preset
                  </button>
                </div>

                <div className="space-y-6">
                  {audioUrl ? (
                    <AudioPlayer audioUrl={audioUrl} duration={duration} title="Generated Preview" />
                  ) : (
                    <div className="flex min-h-[24rem] items-center justify-center rounded-[2rem] border border-dashed border-slate-300 bg-white px-8 py-12 text-center shadow-xl shadow-slate-900/5">
                      <div>
                        <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">
                          Preview Slot
                        </p>
                        <h3 className="mt-3 text-2xl font-semibold text-slate-950">No audio generated yet</h3>
                        <p className="mt-3 max-w-md text-sm leading-7 text-slate-600">
                          Run a preview from the settings panel to inspect pacing, download the clip, and decide whether the voice deserves a preset.
                        </p>
                      </div>
                    </div>
                  )}

                  <VoicePresetManager
                    onDeletePreset={handleDeletePreset}
                    onLoadPreset={handleLoadPreset}
                    presets={presets}
                  />
                </div>
              </div>
            ) : (
              <div className="grid gap-6 xl:grid-cols-2">
                <VoiceControls
                  audioUrl={audioUrl}
                  duration={duration}
                  emotion={emotion}
                  generateLabel="Generate Voice A"
                  generating={generationTarget === "primary"}
                  label="Voice A"
                  loadingVoices={loadingVoices}
                  loadingMessage={voiceLoadingMessage}
                  onEmotionChange={setEmotion}
                  onGenerate={() => {
                    void handleGenerateAudio(false);
                  }}
                  onQuickEmotionSelect={setEmotion}
                  onRetryPreview={() => {
                    handleRetryPreview("primary");
                  }}
                  onSpeedChange={setSpeed}
                  onVoiceChange={setVoice}
                  previewState={previewStateByTarget.primary}
                  speed={speed}
                  subtitle="Left-side comparison"
                  voice={voice}
                  voices={voices}
                />
                <VoiceControls
                  audioUrl={compareAudioUrl}
                  duration={compareDuration}
                  emotion={compareEmotion}
                  generateLabel="Generate Voice B"
                  generating={generationTarget === "compare"}
                  label="Voice B"
                  loadingVoices={loadingVoices}
                  loadingMessage={voiceLoadingMessage}
                  onEmotionChange={setCompareEmotion}
                  onGenerate={() => {
                    void handleGenerateAudio(true);
                  }}
                  onQuickEmotionSelect={setCompareEmotion}
                  onRetryPreview={() => {
                    handleRetryPreview("compare");
                  }}
                  onSpeedChange={setCompareSpeed}
                  onVoiceChange={setCompareVoice}
                  previewState={previewStateByTarget.compare}
                  speed={compareSpeed}
                  subtitle="Right-side comparison"
                  voice={compareVoice}
                  voices={voices}
                />
              </div>
            )}
          </div>
        ) : (
          <div className="grid gap-6 xl:grid-cols-[1fr,0.95fr]">
            <VoiceCloneForm onCloned={handleCloneCreated} />
            <ClonedVoicesList
              deletingVoiceName={deletingVoiceName}
              errorMessage={clonedVoicesError}
              loading={loadingClonedVoices}
              onDelete={(voiceName) => {
                void handleDeleteClonedVoice(voiceName);
              }}
              voices={clonedVoices}
            />
          </div>
        )}
      </div>
    </AppShell>
  );
}
