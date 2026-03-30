import React, { useEffect, useRef, useState } from "react";
import AppShell from "../components/AppShell";
import AudioPlayer from "../components/AudioPlayer";
import ClonedVoicesList from "../components/ClonedVoicesList";
import ConfirmDialog from "../components/ConfirmDialog";
import ProgressHeartbeat from "../components/ProgressHeartbeat";
import VoiceCloneForm from "../components/VoiceCloneForm";
import VoicePresetManager from "../components/VoicePresetManager";
import VoiceSelector from "../components/VoiceSelector";

const DEFAULT_TEST_TEXT =
  "This is the Alexandria Audiobook Narrator. Test your voice settings here with any text you like.";
const VOICE_DESIGN_DEFAULT_TEXT =
  "The old lighthouse keeper closed his journal and set it on the windowsill.";
const VOICE_DESIGN_PRESETS = [
  "A deep, authoritative American male narrator with a warm baritone, clear diction, and a steady measured pace",
  "A young, energetic American male voice with a bright midrange and natural enthusiasm",
  "A mature British male narrator with a rich, resonant voice and dignified pacing",
  "A smooth, calm American male voice with a velvet timbre, perfect for late-night storytelling",
  "A warm, friendly American male voice with a slight rasp, like a trusted uncle telling stories",
  "A crisp, professional American male newsreader voice, neutral and articulate",
];
const EMOTION_PRESETS = [
  "neutral",
  "warm",
  "dramatic",
  "energetic",
  "contemplative",
  "authoritative",
];
const PRESET_STORAGE_KEY = "voicePresets";
const VOICE_LOAD_MAX_RETRIES = 20;
const CLOSED_CONFIRM_DIALOG = { data: null, open: false, type: null };
const ENGINE_COPY = {
  qwen3_tts: {
    description: "9 preset voices + VoiceDesign + emotion control",
    display_name: "Qwen3 TTS",
    fallback_voice: "Ethan",
  },
  voxtral_tts: {
    description: "20 built-in voices across 9 languages — ElevenLabs-quality",
    display_name: "Voxtral TTS",
    fallback_voice: "Casual Male",
  },
};

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

    return parsedPresets.filter(
      (preset) => preset && preset.name && preset.voice,
    );
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

function labelEngine(engineName) {
  return ENGINE_COPY[engineName]?.display_name ?? engineName;
}

function resolvePreferredEngine(engines, preferredEngine = "qwen3_tts") {
  const current = engines.find(
    (engine) => engine.name === preferredEngine && engine.available,
  );
  if (current) {
    return current.name;
  }

  const qwen = engines.find(
    (engine) => engine.name === "qwen3_tts" && engine.available,
  );
  if (qwen) {
    return qwen.name;
  }

  return (
    engines.find((engine) => engine.available)?.name ??
    engines[0]?.name ??
    preferredEngine
  );
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
  supportsEmotion = true,
  speed,
  showPreview = true,
  subtitle,
  voice,
  voiceGrouping = "voice_type",
  voices,
}) {
  const fieldKey = label.toLowerCase().replace(/\s+/g, "-");
  const selectedVoice = voices.find(
    (voiceOption) => voiceOption.name === voice,
  );

  return (
    <section className="rounded-[2rem] border border-slate-200 bg-white p-6 shadow-xl shadow-slate-900/5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">
            {label}
          </p>
          <h3 className="mt-2 text-2xl font-semibold text-slate-950">
            {subtitle}
          </h3>
        </div>
        <div className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] text-slate-600">
          {loadingVoices ? "Loading voices" : `${voices.length} voices`}
        </div>
      </div>

      <div className="mt-6 space-y-6">
        <div>
          <label
            className="block text-sm font-semibold text-slate-900"
            htmlFor={`${fieldKey}-voice`}
          >
            Voice
          </label>
          <VoiceSelector
            ariaLabel={label === "Voice A" ? "Primary voice" : "Compare voice"}
            className="mt-2 w-full rounded-2xl border border-slate-300 bg-slate-50 px-4 py-3 text-slate-900 outline-none transition focus:border-slate-950 focus:bg-white"
            disabled={loadingVoices}
            emptyLabel="No voices available"
            grouping={voiceGrouping}
            id={`${fieldKey}-voice`}
            onChange={onVoiceChange}
            value={voice}
            voices={voices}
          />
          {selectedVoice?.description ? (
            <p
              className="voice-description-hint"
              style={{
                fontSize: "0.85rem",
                color: "#6b7280",
                marginTop: "4px",
                fontStyle: "italic",
              }}
            >
              {selectedVoice.description}
            </p>
          ) : null}
          {loadingVoices && loadingMessage ? (
            <p className="mt-3 text-sm text-slate-500">{loadingMessage}</p>
          ) : null}
        </div>

        {supportsEmotion ? (
          <div>
            <label
              className="block text-sm font-semibold text-slate-900"
              htmlFor={`${fieldKey}-emotion`}
            >
              Emotion / Style
            </label>
            <input
              aria-label={
                label === "Voice A" ? "Primary emotion" : "Compare emotion"
              }
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
        ) : (
          <div className="rounded-[1.5rem] border border-slate-200 bg-slate-50 px-4 py-4 text-sm text-slate-600">
            Voxtral TTS does not support emotion/style control.
          </div>
        )}

        <div>
          <div className="flex items-center justify-between">
            <label
              className="block text-sm font-semibold text-slate-900"
              htmlFor={`${fieldKey}-speed`}
            >
              Speed
            </label>
            <span className="text-sm font-semibold text-sky-700">
              {Number(speed).toFixed(2)}x
            </span>
          </div>
          <input
            aria-label={label === "Voice A" ? "Primary speed" : "Compare speed"}
            className="mt-3 w-full accent-slate-950"
            id={`${fieldKey}-speed`}
            max="2.0"
            min="0.5"
            onChange={(event) =>
              onSpeedChange(Number.parseFloat(event.target.value))
            }
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
            <AudioPlayer
              audioUrl={audioUrl}
              duration={duration}
              title={`${label} Preview`}
            />
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
  const [confirmDialog, setConfirmDialog] = useState(CLOSED_CONFIRM_DIALOG);
  const [deletingVoiceName, setDeletingVoiceName] = useState("");
  const [designedVoicesNotice, setDesignedVoicesNotice] = useState(null);
  const [duration, setDuration] = useState(0);
  const [engineNotice, setEngineNotice] = useState(null);
  const [engines, setEngines] = useState([]);
  const [errorMessage, setErrorMessage] = useState("");
  const [generationTarget, setGenerationTarget] = useState("");
  const [lockingVoiceName, setLockingVoiceName] = useState("");
  const [loadingClonedVoices, setLoadingClonedVoices] = useState(true);
  const [loadingEngines, setLoadingEngines] = useState(true);
  const [loadingVoices, setLoadingVoices] = useState(true);
  const [mode, setMode] = useState("single");
  const [presets, setPresets] = useState([]);
  const [previewStateByTarget, setPreviewStateByTarget] = useState({
    compare: null,
    designer: null,
    primary: null,
  });
  const [testText, setTestText] = useState(DEFAULT_TEST_TEXT);
  const [voice, setVoice] = useState("Ethan");
  const [voiceDesignAudioUrl, setVoiceDesignAudioUrl] = useState("");
  const [voiceDesignAvailable, setVoiceDesignAvailable] = useState(true);
  const [voiceDesignDescription, setVoiceDesignDescription] = useState("");
  const [voiceDesignDownloadCommand, setVoiceDesignDownloadCommand] =
    useState("");
  const [voiceDesignDuration, setVoiceDesignDuration] = useState(0);
  const [voiceDesignNotice, setVoiceDesignNotice] = useState(null);
  const [voiceDesignStatusError, setVoiceDesignStatusError] = useState("");
  const [voiceDesignText, setVoiceDesignText] = useState(
    VOICE_DESIGN_DEFAULT_TEXT,
  );
  const [voiceDesignSpeed, setVoiceDesignSpeed] = useState(1.0);
  const [voiceDesignVoiceToSave, setVoiceDesignVoiceToSave] = useState("");
  const [voiceLoadAttempt, setVoiceLoadAttempt] = useState(0);
  const [voiceLoadingMessage, setVoiceLoadingMessage] = useState("");
  const [voices, setVoices] = useState([]);
  const [unlockingVoiceName, setUnlockingVoiceName] = useState("");
  const [emotion, setEmotion] = useState("neutral");
  const [selectedEngine, setSelectedEngine] = useState("qwen3_tts");
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

  async function loadEngines(preferredEngine = selectedEngine) {
    setLoadingEngines(true);

    try {
      const response = await fetch("/api/voice-lab/engines");
      if (!response.ok) {
        throw new Error("Failed to fetch TTS engines.");
      }

      const payload = await response.json();
      const availableEngines = payload.engines ?? [];
      const nextEngine = resolvePreferredEngine(
        availableEngines,
        preferredEngine,
      );
      setEngines(availableEngines);
      setSelectedEngine(nextEngine);
      setEngineNotice(null);
      return nextEngine;
    } catch (error) {
      setEngines([]);
      setErrorMessage(
        error instanceof Error ? error.message : "Failed to fetch TTS engines.",
      );
      return preferredEngine;
    } finally {
      setLoadingEngines(false);
    }
  }

  async function loadVoices(engineName, attempt = 1) {
    const requestId = voiceLoadRequestRef.current + 1;
    voiceLoadRequestRef.current = requestId;
    setLoadingVoices(true);
    if (voiceRetryTimeoutRef.current) {
      window.clearTimeout(voiceRetryTimeoutRef.current);
      voiceRetryTimeoutRef.current = null;
    }
    let keepLoading = false;
    const resolvedEngine = engineName ?? selectedEngine;

    try {
      const response = await fetch(
        `/api/voice-lab/voices?engine=${encodeURIComponent(resolvedEngine)}`,
      );
      if (!response.ok) {
        throw new Error("Failed to fetch voices.");
      }

      const payload = await response.json();
      if (payload.loading) {
        const nextAttempt = Math.min(attempt, VOICE_LOAD_MAX_RETRIES);
        setVoiceLoadAttempt(nextAttempt);
        setVoiceLoadingMessage(
          `TTS engine is loading... retrying in 3s (attempt ${nextAttempt}/${VOICE_LOAD_MAX_RETRIES})`,
        );
        keepLoading = true;

        if (nextAttempt >= VOICE_LOAD_MAX_RETRIES) {
          setLoadingVoices(false);
          setErrorMessage(payload.message ?? "TTS engine is still loading.");
          return;
        }

        voiceRetryTimeoutRef.current = window.setTimeout(() => {
          void loadVoices(resolvedEngine, nextAttempt + 1);
        }, 3000);
        return;
      }

      if (voiceLoadRequestRef.current !== requestId) {
        return;
      }

      const availableVoices = payload.voices ?? [];
      setSelectedEngine(resolvedEngine);
      setVoices(availableVoices);
      const defaultVoice =
        ENGINE_COPY[resolvedEngine]?.fallback_voice ??
        availableVoices[0]?.name ??
        "";
      setVoice((currentVoice) =>
        selectVoiceValue(availableVoices, currentVoice || defaultVoice, 0),
      );
      setCompareVoice((currentVoice) =>
        selectVoiceValue(availableVoices, currentVoice || defaultVoice, 1),
      );
      setVoiceLoadAttempt(0);
      setVoiceLoadingMessage("");
      setErrorMessage("");
    } catch (error) {
      setVoiceLoadAttempt(0);
      setVoiceLoadingMessage("");
      setErrorMessage(
        error instanceof Error ? error.message : "Failed to load voices.",
      );
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
        error instanceof Error
          ? error.message
          : "Failed to load cloned voices.",
      );
      console.error("Error fetching cloned voices:", error);
    } finally {
      setLoadingClonedVoices(false);
    }
  }

  async function loadVoiceDesignStatus() {
    try {
      const response = await fetch("/api/voice-lab/voice-design/status");
      if (!response.ok) {
        throw new Error("Failed to fetch VoiceDesign status.");
      }

      const payload = await response.json();
      setVoiceDesignAvailable(Boolean(payload.available));
      setVoiceDesignDownloadCommand(payload.download_command ?? "");
      setVoiceDesignStatusError("");
    } catch (error) {
      setVoiceDesignStatusError(
        error instanceof Error
          ? error.message
          : "Failed to fetch VoiceDesign status.",
      );
      console.error("Error fetching VoiceDesign status:", error);
    }
  }

  useEffect(() => {
    let cancelled = false;

    async function initializeVoiceLab() {
      setPresets(readStoredPresets());
      const initialEngine = await loadEngines();
      if (cancelled) {
        return;
      }
      await Promise.all([
        loadVoices(initialEngine),
        loadClonedVoices(),
        loadVoiceDesignStatus(),
      ]);
    }

    void initializeVoiceLab();

    return () => {
      cancelled = true;
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

  const activeEngineDefinition =
    engines.find((engine) => engine.name === selectedEngine) ?? null;
  const clonedVoiceLookup = new Map(
    clonedVoices.map((clonedVoice) => [clonedVoice.voice_name, clonedVoice]),
  );
  const designedVoices =
    selectedEngine === "qwen3_tts"
      ? voices.filter((voiceOption) => voiceOption.voice_type === "designed")
      : [];
  const activeEngineCopy = ENGINE_COPY[selectedEngine] ?? {
    description: activeEngineDefinition?.description ?? "",
    display_name: activeEngineDefinition?.display_name ?? selectedEngine,
    fallback_voice: voices[0]?.name ?? "",
  };
  const supportsEmotion = selectedEngine !== "voxtral_tts";
  const showEngineToggle = engines.length > 1;

  const handleGenerateAudio = async (isCompare = false) => {
    const selectedVoice = isCompare ? compareVoice : voice;
    const selectedEmotion =
      selectedEngine === "voxtral_tts"
        ? "neutral"
        : isCompare
          ? compareEmotion
          : emotion;
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
          engine: selectedEngine,
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
        const payload = await response.json().catch(() => ({}));
        if (response.status === 503) {
          throw new Error(
            payload?.detail ??
              "Voice preview is temporarily unavailable. The TTS engine is busy generating audio.",
          );
        }

        throw new Error(payload?.detail ?? "Failed to generate preview.");
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
      const message =
        error instanceof TypeError
          ? "Could not connect to the server. It may have restarted — please refresh the page and try again."
          : error instanceof Error
            ? error.message
            : "Failed to generate preview.";
      clearPreviewStageTimeout(selectedTarget);
      setPreviewState(selectedTarget, {
        errorMessage: message,
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
    if (target === "designer") {
      void handleGenerateVoiceDesign();
      return;
    }
    void handleGenerateAudio(target === "compare");
  }

  async function handleEngineSelection(engineName) {
    const engineDefinition = engines.find(
      (engine) => engine.name === engineName,
    );
    if (engineDefinition?.available === false) {
      setEngineNotice({
        download_command: engineDefinition.download_command ?? "",
        message: `${engineDefinition.display_name ?? ENGINE_COPY[engineName]?.display_name ?? engineName} is not installed yet.`,
      });
      return;
    }

    setEngineNotice(null);
    setSelectedEngine(engineName);
    setAudioUrl("");
    setDuration(0);
    setCompareAudioUrl("");
    setCompareDuration(0);
    if (engineName === "qwen3_tts") {
      setVoice(ENGINE_COPY.qwen3_tts.fallback_voice);
      setCompareVoice("Nova");
    } else {
      setVoice(ENGINE_COPY.voxtral_tts.fallback_voice);
      setCompareVoice(ENGINE_COPY.voxtral_tts.fallback_voice);
    }
    await loadVoices(engineName);
  }

  const handleGenerateVoiceDesign = async () => {
    const startedAt = Date.now();
    const trimmedDescription = voiceDesignDescription.trim();

    if (!voiceDesignAvailable) {
      setPreviewState("designer", {
        errorMessage: "VoiceDesign model not installed.",
        isActive: false,
        stage: "Unavailable",
        startTime: startedAt,
      });
      return;
    }

    if (!trimmedDescription) {
      setPreviewState("designer", {
        errorMessage: "Please describe the voice you want to generate.",
        isActive: false,
        stage: "Ready",
        startTime: startedAt,
      });
      return;
    }

    if (!voiceDesignText.trim()) {
      setPreviewState("designer", {
        errorMessage: "Please enter text to generate audio.",
        isActive: false,
        stage: "Ready",
        startTime: startedAt,
      });
      return;
    }

    setVoiceDesignNotice(null);
    setVoiceDesignVoiceToSave("");
    setGenerationTarget("designer");
    clearPreviewStageTimeout("designer");
    setPreviewState("designer", {
      errorMessage: "",
      isActive: true,
      stage: "Synthesizing audio...",
      startTime: startedAt,
    });
    schedulePreviewStage("designer");
    setVoiceDesignAudioUrl("");
    setVoiceDesignDuration(0);

    try {
      const response = await fetch("/api/voice-lab/voice-design/test", {
        body: JSON.stringify({
          speed: voiceDesignSpeed,
          text: voiceDesignText,
          voice_description: trimmedDescription,
        }),
        headers: {
          "Content-Type": "application/json",
        },
        method: "POST",
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        if (response.status === 404) {
          await loadVoiceDesignStatus();
        }
        throw new Error(
          payload?.detail ?? "Failed to generate VoiceDesign preview.",
        );
      }

      const payload = await response.json();
      clearPreviewStageTimeout("designer");
      setPreviewState("designer", {
        errorMessage: "",
        isActive: false,
        stage: "Ready",
        startTime: startedAt,
      });
      setVoiceDesignAudioUrl(payload.audio_url);
      setVoiceDesignDuration(payload.duration_seconds ?? 0);
      setVoiceDesignVoiceToSave(trimmedDescription);
    } catch (error) {
      const message =
        error instanceof TypeError
          ? "Could not connect to the server. It may have restarted — please refresh the page and try again."
          : error instanceof Error
            ? error.message
            : "Failed to generate VoiceDesign preview.";
      clearPreviewStageTimeout("designer");
      setPreviewState("designer", {
        errorMessage: message,
        isActive: false,
        stage: "Processing...",
        startTime: startedAt,
      });
      console.error("VoiceDesign generation error:", error);
    } finally {
      setGenerationTarget("");
    }
  };

  const handleSavePreset = () => {
    setConfirmDialog({
      data: null,
      open: true,
      type: "save-preset",
    });
  };

  const handleLoadPreset = (preset) => {
    const presetVoiceExists = voices.some(
      (voiceOption) => voiceOption.name === preset.voice,
    );

    if (presetVoiceExists) {
      setVoice(preset.voice);
      setErrorMessage("");
    } else {
      setErrorMessage(
        `Preset "${preset.name}" uses a voice that is not currently available.`,
      );
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

  async function saveDesignedVoice(displayName) {
    const trimmedName = displayName.trim();
    if (!trimmedName || !voiceDesignVoiceToSave) {
      return;
    }

    try {
      const response = await fetch("/api/voice-lab/voice-design/save", {
        body: JSON.stringify({
          display_name: trimmedName,
          voice_description: voiceDesignVoiceToSave,
          voice_name: trimmedName,
        }),
        headers: {
          "Content-Type": "application/json",
        },
        method: "POST",
      });

      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload?.detail ?? "Failed to save designed voice.");
      }

      await loadVoices(selectedEngine);
      setVoice(payload.voice_name);
      setVoiceDesignNotice({
        message: `${payload.display_name} is ready in the Designed Voices list.`,
        tone: "success",
      });
    } catch (error) {
      setVoiceDesignNotice({
        message:
          error instanceof Error
            ? error.message
            : "Failed to save designed voice.",
        tone: "error",
      });
    }
  }

  function handleSaveDesignedVoice() {
    if (!voiceDesignVoiceToSave) {
      return;
    }

    setConfirmDialog({
      data: { scope: "designed-voice" },
      open: true,
      type: "save-designed-voice",
    });
  }

  async function handleCloneCreated() {
    await Promise.all([loadVoices(selectedEngine), loadClonedVoices()]);
    setActiveTab("clone");
  }

  async function deleteClonedVoice(voiceName) {
    setDeletingVoiceName(voiceName);
    setClonedVoicesError("");

    try {
      const response = await fetch(
        `/api/voice-lab/cloned-voices/${voiceName}`,
        {
          method: "DELETE",
        },
      );

      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.detail ?? "Failed to delete cloned voice.");
      }

      await Promise.all([loadVoices(selectedEngine), loadClonedVoices()]);
    } catch (error) {
      setClonedVoicesError(
        error instanceof Error
          ? error.message
          : "Failed to delete cloned voice.",
      );
    } finally {
      setDeletingVoiceName("");
    }
  }

  async function lockDesignedVoice(voiceName) {
    setLockingVoiceName(voiceName);
    setDesignedVoicesNotice(null);

    try {
      const response = await fetch(
        `/api/voice-lab/voice-design/${encodeURIComponent(voiceName)}/lock`,
        {
          method: "POST",
        },
      );
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(payload?.detail ?? "Failed to lock designed voice.");
      }

      await Promise.all([loadVoices(selectedEngine), loadClonedVoices()]);
      setDesignedVoicesNotice({
        message: `${payload.display_name} is now locked for production generation.`,
        tone: "success",
      });
    } catch (error) {
      setDesignedVoicesNotice({
        message:
          error instanceof Error
            ? error.message
            : "Failed to lock designed voice.",
        tone: "error",
      });
    } finally {
      setLockingVoiceName("");
    }
  }

  async function unlockDesignedVoice(voiceName) {
    setUnlockingVoiceName(voiceName);
    setDesignedVoicesNotice(null);

    try {
      const response = await fetch(
        `/api/voice-lab/voice-design/${encodeURIComponent(voiceName)}/lock`,
        {
          method: "DELETE",
        },
      );
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(payload?.detail ?? "Failed to unlock designed voice.");
      }

      await Promise.all([loadVoices(selectedEngine), loadClonedVoices()]);
      setDesignedVoicesNotice({
        message: `${voiceName} now uses its saved text description again.`,
        tone: "success",
      });
    } catch (error) {
      setDesignedVoicesNotice({
        message:
          error instanceof Error
            ? error.message
            : "Failed to unlock designed voice.",
        tone: "error",
      });
    } finally {
      setUnlockingVoiceName("");
    }
  }

  function handleConfirmDialogCancel() {
    setConfirmDialog(CLOSED_CONFIRM_DIALOG);
  }

  function handleConfirmDialogConfirm(value) {
    const activeDialog = confirmDialog;
    setConfirmDialog(CLOSED_CONFIRM_DIALOG);

    if (activeDialog.type === "save-preset") {
      const trimmedName = value?.trim();

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
      return;
    }

    if (
      activeDialog.type === "delete-cloned-voice" &&
      activeDialog.data?.voiceName
    ) {
      void deleteClonedVoice(activeDialog.data.voiceName);
      return;
    }

    if (activeDialog.type === "save-designed-voice") {
      const trimmedName = value?.trim();

      if (!trimmedName) {
        return;
      }

      void saveDesignedVoice(trimmedName);
    }
  }

  function handleDeleteClonedVoice(voiceName) {
    setConfirmDialog({
      data: { voiceName },
      open: true,
      type: "delete-cloned-voice",
    });
  }

  return (
    <AppShell
      description="Audition narration settings, compare alternate deliveries, and create reference-based cloned voices that can be reused in production generation."
      title="Voice Lab"
    >
      <div className="space-y-8">
        {showEngineToggle ? (
          <section className="rounded-[2rem] border border-slate-200 bg-white p-6 shadow-xl shadow-slate-900/5">
            <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">
                  TTS Engine
                </p>
                <h2 className="mt-2 text-2xl font-semibold text-slate-950">
                  Switch the synthesis engine before you audition voices.
                </h2>
                <p className="mt-3 text-sm leading-7 text-slate-600">
                  {activeEngineCopy.description}
                </p>
              </div>

              <div className="inline-flex flex-wrap gap-3 rounded-[1.5rem] border border-slate-200 bg-slate-50 p-2">
                {engines.map((engine) => {
                  const active = selectedEngine === engine.name;
                  return (
                    <button
                      className={`rounded-[1.15rem] px-5 py-3 text-sm font-semibold transition ${
                        active
                          ? "bg-slate-950 text-amber-200"
                          : engine.available
                            ? "bg-white text-slate-700 hover:bg-slate-100"
                            : "cursor-help bg-slate-200 text-slate-400"
                      }`}
                      key={engine.name}
                      onClick={() => {
                        void handleEngineSelection(engine.name);
                      }}
                      title={
                        engine.available ? undefined : "Model not installed."
                      }
                      type="button"
                    >
                      {engine.display_name ?? labelEngine(engine.name)}
                    </button>
                  );
                })}
              </div>
            </div>
          </section>
        ) : null}

        {engineNotice ? (
          <div className="rounded-[1.75rem] border border-amber-200 bg-amber-50 px-5 py-5 text-sm text-amber-900">
            <p className="font-semibold">{engineNotice.message}</p>
            <p className="mt-2 leading-7">
              Install the model first, then switch engines again.
            </p>
            {engineNotice.download_command ? (
              <code className="mt-4 block overflow-x-auto rounded-2xl bg-white px-4 py-3 text-xs text-slate-700">
                {engineNotice.download_command}
              </code>
            ) : null}
          </div>
        ) : null}

        <section className="grid gap-6 xl:grid-cols-[1.2fr,0.8fr]">
          <div className="overflow-hidden rounded-[2rem] border border-slate-200 bg-[linear-gradient(135deg,#0f172a_0%,#1e293b_52%,#7c2d12_100%)] px-8 py-8 text-white shadow-2xl shadow-slate-900/10">
            <p className="text-xs font-semibold uppercase tracking-[0.34em] text-amber-200/80">
              Narration Tuning
            </p>
            <h2 className="mt-4 max-w-2xl text-4xl font-semibold leading-tight">
              Pressure the voice before it reaches a full book.
            </h2>
            <p className="mt-4 max-w-2xl text-sm leading-7 text-slate-200/85">
              Test a paragraph, compare nearby settings, and promote only the
              voices that survive real scrutiny. If the built-ins miss the
              target, build a clone from a clean reference.
            </p>
          </div>

          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-1">
            <div className="rounded-[2rem] border border-slate-200 bg-white px-6 py-5 shadow-xl shadow-slate-900/5">
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">
                Engine
              </p>
              <p className="mt-3 text-3xl font-semibold text-slate-950">
                {loadingEngines
                  ? "Loading..."
                  : (activeEngineDefinition?.display_name ??
                    activeEngineCopy.display_name)}
              </p>
              <p className="mt-2 text-sm text-slate-600">
                {activeEngineCopy.description}
              </p>
            </div>

            <div className="rounded-[2rem] border border-slate-200 bg-white px-6 py-5 shadow-xl shadow-slate-900/5">
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">
                Voices Ready
              </p>
              <p className="mt-3 text-3xl font-semibold text-slate-950">
                {voices.length}
              </p>
              <p className="mt-2 text-sm text-slate-600">
                {voices.length === 1
                  ? "1 available voice"
                  : `${voices.length} available voices`}
              </p>
            </div>

            <div className="rounded-[2rem] border border-slate-200 bg-white px-6 py-5 shadow-xl shadow-slate-900/5">
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">
                Cloned Voices
              </p>
              <p className="mt-3 text-3xl font-semibold text-slate-950">
                {clonedVoices.length}
              </p>
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
                    <h3 className="mt-2 text-2xl font-semibold text-slate-950">
                      Text to synthesize
                    </h3>
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
                  <span>
                    Use a paragraph long enough to expose pacing issues.
                  </span>
                  <span>{testText.length} / 5000 characters</span>
                </div>
              </div>

              <div className="rounded-[2rem] border border-slate-200 bg-[linear-gradient(135deg,rgba(251,191,36,0.1)_0%,rgba(14,165,233,0.08)_100%)] p-6 shadow-xl shadow-slate-900/5">
                <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">
                  Workflow
                </p>
                <h3 className="mt-2 text-2xl font-semibold text-slate-950">
                  How to use this page well
                </h3>
                <ol className="mt-5 space-y-4 text-sm leading-7 text-slate-700">
                  <li>
                    1. Start with a neutral baseline before adding style
                    direction or speed changes.
                  </li>
                  <li>
                    2. Compare two nearby settings instead of jumping across
                    radically different voices.
                  </li>
                  <li>
                    3. If none of the built-ins land, create a cloned voice from
                    a high-quality sample.
                  </li>
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
                    supportsEmotion={supportsEmotion}
                    voice={voice}
                    voiceGrouping={
                      selectedEngine === "voxtral_tts"
                        ? "language"
                        : "voice_type"
                    }
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
                    <AudioPlayer
                      audioUrl={audioUrl}
                      duration={duration}
                      title="Generated Preview"
                    />
                  ) : (
                    <div className="flex min-h-[24rem] items-center justify-center rounded-[2rem] border border-dashed border-slate-300 bg-white px-8 py-12 text-center shadow-xl shadow-slate-900/5">
                      <div>
                        <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">
                          Preview Slot
                        </p>
                        <h3 className="mt-3 text-2xl font-semibold text-slate-950">
                          No audio generated yet
                        </h3>
                        <p className="mt-3 max-w-md text-sm leading-7 text-slate-600">
                          Run a preview from the settings panel to inspect
                          pacing, download the clip, and decide whether the
                          voice deserves a preset.
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
                  supportsEmotion={supportsEmotion}
                  voice={voice}
                  voiceGrouping={
                    selectedEngine === "voxtral_tts" ? "language" : "voice_type"
                  }
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
                  supportsEmotion={supportsEmotion}
                  voice={compareVoice}
                  voiceGrouping={
                    selectedEngine === "voxtral_tts" ? "language" : "voice_type"
                  }
                  voices={voices}
                />
              </div>
            )}

            {selectedEngine === "qwen3_tts" ? (
              <section className="rounded-[2rem] border border-slate-200 bg-white p-6 shadow-xl shadow-slate-900/5">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">
                      Voice Designer
                    </p>
                    <h3 className="mt-2 text-2xl font-semibold text-slate-950">
                      Create Custom Voices from Text Descriptions
                    </h3>
                    <p className="mt-3 max-w-3xl text-sm leading-7 text-slate-600">
                      Describe the narrator you want, audition it immediately,
                      and save the winners as reusable designed voices for
                      future generation.
                    </p>
                  </div>

                  <div
                    className={`rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] ${
                      voiceDesignAvailable
                        ? "bg-emerald-50 text-emerald-700"
                        : "bg-amber-100 text-amber-800"
                    }`}
                  >
                    {voiceDesignAvailable ? "Model installed" : "Model missing"}
                  </div>
                </div>

                {voiceDesignStatusError ? (
                  <div className="mt-6 rounded-[1.5rem] border border-rose-200 bg-rose-50 px-4 py-4 text-sm text-rose-700">
                    {voiceDesignStatusError}
                  </div>
                ) : null}

                {!voiceDesignAvailable ? (
                  <div className="mt-6 rounded-[1.75rem] border border-amber-200 bg-amber-50 px-5 py-5 text-sm text-amber-900">
                    <p className="font-semibold">
                      VoiceDesign model not installed.
                    </p>
                    <p className="mt-2 leading-7">
                      Download the model, then refresh this page to enable
                      text-described voice previews.
                    </p>
                    {voiceDesignDownloadCommand ? (
                      <code className="mt-4 block overflow-x-auto rounded-2xl bg-white px-4 py-3 text-xs text-slate-700">
                        {voiceDesignDownloadCommand}
                      </code>
                    ) : null}
                  </div>
                ) : (
                  <div className="mt-6 grid gap-6 xl:grid-cols-[1.05fr,0.95fr]">
                    <div className="space-y-6">
                      <div>
                        <label
                          className="block text-sm font-semibold text-slate-900"
                          htmlFor="voice-design-description"
                        >
                          Voice Description
                        </label>
                        <textarea
                          aria-label="Voice description"
                          className="mt-2 h-40 w-full resize-none rounded-[1.75rem] border border-slate-300 bg-slate-50 px-5 py-4 text-base leading-7 text-slate-900 outline-none transition focus:border-slate-950 focus:bg-white"
                          id="voice-design-description"
                          maxLength={500}
                          onChange={(event) =>
                            setVoiceDesignDescription(event.target.value)
                          }
                          placeholder="Describe the voice you want, e.g. 'A deep, authoritative American male narrator with a warm baritone, clear diction, and steady pace suitable for audiobooks'"
                          value={voiceDesignDescription}
                        />
                        <div className="mt-3 flex flex-wrap gap-2">
                          {VOICE_DESIGN_PRESETS.map((preset) => (
                            <button
                              className={`rounded-full px-3 py-2 text-left text-xs font-semibold uppercase tracking-[0.14em] transition ${
                                voiceDesignDescription === preset
                                  ? "bg-slate-950 text-amber-200"
                                  : "bg-slate-100 text-slate-700 hover:bg-slate-200"
                              }`}
                              key={preset}
                              onClick={() => setVoiceDesignDescription(preset)}
                              type="button"
                            >
                              {preset}
                            </button>
                          ))}
                        </div>
                      </div>

                      <div>
                        <label
                          className="block text-sm font-semibold text-slate-900"
                          htmlFor="voice-design-text"
                        >
                          Sample Text
                        </label>
                        <input
                          aria-label="Voice designer sample text"
                          className="mt-2 w-full rounded-2xl border border-slate-300 bg-slate-50 px-4 py-3 text-slate-900 outline-none transition focus:border-slate-950 focus:bg-white"
                          id="voice-design-text"
                          maxLength={5000}
                          onChange={(event) =>
                            setVoiceDesignText(event.target.value)
                          }
                          type="text"
                          value={voiceDesignText}
                        />
                      </div>

                      <div>
                        <div className="flex items-center justify-between">
                          <label
                            className="block text-sm font-semibold text-slate-900"
                            htmlFor="voice-design-speed"
                          >
                            Speed
                          </label>
                          <span className="text-sm font-semibold text-sky-700">
                            {voiceDesignSpeed.toFixed(2)}x
                          </span>
                        </div>
                        <input
                          aria-label="Voice designer speed"
                          className="mt-3 w-full accent-slate-950"
                          id="voice-design-speed"
                          max="2.0"
                          min="0.5"
                          onChange={(event) =>
                            setVoiceDesignSpeed(
                              Number.parseFloat(event.target.value),
                            )
                          }
                          step="0.05"
                          type="range"
                          value={voiceDesignSpeed}
                        />
                        <div className="mt-2 flex justify-between text-xs font-medium uppercase tracking-[0.14em] text-slate-500">
                          <span>0.5x</span>
                          <span>1.0x</span>
                          <span>2.0x</span>
                        </div>
                      </div>

                      <button
                        className="inline-flex w-full items-center justify-center rounded-full bg-slate-950 px-5 py-3 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
                        disabled={generationTarget === "designer"}
                        onClick={() => {
                          void handleGenerateVoiceDesign();
                        }}
                        type="button"
                      >
                        {generationTarget === "designer"
                          ? "Generating preview..."
                          : "Generate Preview"}
                      </button>

                      {previewStateByTarget.designer?.errorMessage ? (
                        <div className="rounded-[1.5rem] border border-rose-200 bg-rose-50 px-4 py-4 text-sm text-rose-700">
                          <div>
                            {previewStateByTarget.designer.errorMessage}
                          </div>
                          <button
                            className="mt-3 inline-flex items-center rounded-full border border-rose-300 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-rose-700 transition hover:bg-rose-100"
                            onClick={() => {
                              handleRetryPreview("designer");
                            }}
                            type="button"
                          >
                            Retry
                          </button>
                        </div>
                      ) : null}

                      {previewStateByTarget.designer?.isActive ? (
                        <ProgressHeartbeat
                          isActive={previewStateByTarget.designer.isActive}
                          progressPercent={null}
                          showETA={null}
                          size="sm"
                          stage={previewStateByTarget.designer.stage}
                          startTime={previewStateByTarget.designer.startTime}
                        />
                      ) : null}
                    </div>

                    <div className="space-y-6">
                      {voiceDesignNotice ? (
                        <div
                          className={`rounded-[1.5rem] border px-4 py-4 text-sm ${
                            voiceDesignNotice.tone === "error"
                              ? "border-rose-200 bg-rose-50 text-rose-700"
                              : "border-emerald-200 bg-emerald-50 text-emerald-700"
                          }`}
                        >
                          {voiceDesignNotice.message}
                        </div>
                      ) : null}

                      {voiceDesignAudioUrl ? (
                        <AudioPlayer
                          audioUrl={voiceDesignAudioUrl}
                          duration={voiceDesignDuration}
                          title="Voice Designer Preview"
                        />
                      ) : (
                        <div className="flex min-h-[20rem] items-center justify-center rounded-[2rem] border border-dashed border-slate-300 bg-slate-50 px-8 py-12 text-center">
                          <div>
                            <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">
                              Designer Output
                            </p>
                            <h4 className="mt-3 text-2xl font-semibold text-slate-950">
                              No generated voice yet
                            </h4>
                            <p className="mt-3 max-w-md text-sm leading-7 text-slate-600">
                              Generate a preview to hear how the prompt
                              translates into a spoken narrator.
                            </p>
                          </div>
                        </div>
                      )}

                      {voiceDesignVoiceToSave ? (
                        <button
                          className="inline-flex w-full items-center justify-center rounded-full border border-emerald-300 bg-emerald-50 px-5 py-3 text-sm font-semibold text-emerald-800 transition hover:border-emerald-400 hover:bg-emerald-100"
                          onClick={handleSaveDesignedVoice}
                          type="button"
                        >
                          Save This Voice
                        </button>
                      ) : null}
                    </div>
                  </div>
                )}

                <div className="mt-8 rounded-[1.75rem] border border-slate-200 bg-slate-50 p-5">
                  <div className="flex flex-wrap items-end justify-between gap-4">
                    <div>
                      <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">
                        Designed Voice Library
                      </p>
                      <h4 className="mt-2 text-xl font-semibold text-slate-950">
                        Lock the designs that are stable enough for long-form generation.
                      </h4>
                    </div>
                    <div className="rounded-full bg-white px-4 py-2 text-xs font-semibold uppercase tracking-[0.16em] text-slate-600">
                      {designedVoices.length} saved
                    </div>
                  </div>

                  {designedVoicesNotice ? (
                    <div
                      className={`mt-5 rounded-[1.5rem] border px-4 py-4 text-sm ${
                        designedVoicesNotice.tone === "error"
                          ? "border-rose-200 bg-rose-50 text-rose-700"
                          : "border-emerald-200 bg-emerald-50 text-emerald-700"
                      }`}
                    >
                      {designedVoicesNotice.message}
                    </div>
                  ) : null}

                  {designedVoices.length === 0 ? (
                    <div className="mt-5 rounded-[1.5rem] border border-dashed border-slate-300 bg-white px-6 py-8 text-sm text-slate-500">
                      Save a designed voice first, then lock it into a fixed clone reference before using it for long books.
                    </div>
                  ) : (
                    <div className="mt-5 space-y-4">
                      {designedVoices.map((designedVoice) => {
                        const lockedClone = clonedVoiceLookup.get(
                          designedVoice.name,
                        );
                        const isLocking = lockingVoiceName === designedVoice.name;
                        const isUnlocking =
                          unlockingVoiceName === designedVoice.name;
                        return (
                          <article
                            key={designedVoice.name}
                            className={`rounded-[1.5rem] border p-5 ${
                              lockedClone
                                ? "border-emerald-200 bg-white"
                                : "border-slate-200 bg-white"
                            }`}
                            data-designed-voice-name={designedVoice.name}
                          >
                            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                              <div>
                                <div className="flex flex-wrap items-center gap-2">
                                  <h5 className="text-lg font-semibold text-slate-950">
                                    {designedVoice.display_name || designedVoice.name}
                                  </h5>
                                  <span
                                    className={`rounded-full px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] ${
                                      lockedClone
                                        ? "bg-emerald-100 text-emerald-700"
                                        : "bg-slate-100 text-slate-600"
                                    }`}
                                  >
                                    {lockedClone ? "Locked" : "Unlocked"}
                                  </span>
                                </div>
                                <div className="mt-3 flex flex-wrap gap-2 text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">
                                  <span className="rounded-full bg-slate-950 px-3 py-1 text-amber-200">
                                    {designedVoice.name}
                                  </span>
                                  {lockedClone ? (
                                    <span className="rounded-full bg-emerald-100 px-3 py-1 text-emerald-700">
                                      {lockedClone.audio_duration_seconds.toFixed(1)}s locked sample
                                    </span>
                                  ) : null}
                                </div>
                                {designedVoice.description ? (
                                  <p className="mt-4 text-sm leading-7 text-slate-700">
                                    {designedVoice.description}
                                  </p>
                                ) : null}
                              </div>

                              <button
                                className={`inline-flex items-center justify-center rounded-full px-4 py-2 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-60 ${
                                  lockedClone
                                    ? "border border-amber-200 text-amber-800 hover:border-amber-300 hover:bg-amber-50"
                                    : "border border-slate-200 text-slate-800 hover:border-slate-300 hover:bg-slate-100"
                                }`}
                                disabled={isLocking || isUnlocking}
                                onClick={() => {
                                  if (lockedClone) {
                                    void unlockDesignedVoice(designedVoice.name);
                                    return;
                                  }
                                  void lockDesignedVoice(designedVoice.name);
                                }}
                                type="button"
                              >
                                {isLocking
                                  ? "Locking voice - generating reference sample..."
                                  : isUnlocking
                                    ? "Unlocking voice..."
                                    : lockedClone
                                      ? "Unlock Voice"
                                      : "Lock Voice"}
                              </button>
                            </div>
                          </article>
                        );
                      })}
                    </div>
                  )}
                </div>
              </section>
            ) : null}
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

      <ConfirmDialog
        confirmColor={
          confirmDialog.type === "delete-cloned-voice" ? "rose" : "amber"
        }
        confirmLabel={
          confirmDialog.type === "delete-cloned-voice"
            ? "Delete Voice"
            : confirmDialog.type === "save-designed-voice"
              ? "Save Voice"
              : "Save Preset"
        }
        message={
          confirmDialog.type === "delete-cloned-voice"
            ? `Are you sure you want to delete "${confirmDialog.data?.voiceName}"? This cannot be undone.`
            : confirmDialog.type === "save-designed-voice"
              ? "Enter a name for this designed voice."
              : "Enter a name for this voice configuration preset."
        }
        onCancel={handleConfirmDialogCancel}
        onConfirm={handleConfirmDialogConfirm}
        open={confirmDialog.open}
        promptDefault=""
        promptLabel={
          confirmDialog.type === "save-designed-voice"
            ? "Voice name"
            : "Preset name"
        }
        promptMode={
          confirmDialog.type === "save-preset" ||
          confirmDialog.type === "save-designed-voice"
        }
        theme="light"
        title={
          confirmDialog.type === "delete-cloned-voice"
            ? "Delete Cloned Voice"
            : confirmDialog.type === "save-designed-voice"
              ? "Save Designed Voice"
              : "Save Voice Preset"
        }
      />
    </AppShell>
  );
}
