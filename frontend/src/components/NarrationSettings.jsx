import React, { useEffect, useState } from "react";
import VoiceSelector from "./VoiceSelector";

const EMOTION_PRESETS = [
  "neutral",
  "warm",
  "dramatic",
  "energetic",
  "contemplative",
  "authoritative",
];

const NARRATOR_PRESETS = [
  {
    emotion: "warm",
    name: "Audiobook Narrator",
    speed: 1.0,
  },
  {
    emotion: "dramatic",
    name: "Dramatic Reading",
    speed: 0.95,
  },
  {
    emotion: "energetic",
    name: "Energetic Delivery",
    speed: 1.1,
  },
  {
    emotion: "contemplative",
    name: "Contemplative",
    speed: 0.9,
  },
];

function getChapterSummary(selectedChapter) {
  if (!selectedChapter) {
    return "No chapter selected";
  }

  if (selectedChapter.type === "opening_credits") {
    return "Opening Credits";
  }

  if (selectedChapter.type === "closing_credits") {
    return "Closing Credits";
  }

  if (selectedChapter.type === "introduction") {
    return selectedChapter.title ? `Introduction: ${selectedChapter.title}` : "Introduction";
  }

  return selectedChapter.title
    ? `Chapter ${selectedChapter.number}: ${selectedChapter.title}`
    : `Chapter ${selectedChapter.number}`;
}

export default function NarrationSettings({
  loadingVoices = false,
  loadingMessage = "",
  onChange,
  selectedChapter,
  settings,
  voices = [],
}) {
  const [customEmotion, setCustomEmotion] = useState(settings.emotion ?? "");

  const availableVoices = voices.length > 0
    ? voices
    : [
        { name: "Ethan", display_name: "Ethan", is_cloned: false },
        { name: "Nova", display_name: "Nova", is_cloned: false },
        { name: "Aria", display_name: "Aria", is_cloned: false },
      ];

  useEffect(() => {
    setCustomEmotion(settings.emotion ?? "");
  }, [settings.emotion]);

  function handleVoiceChange(voice) {
    onChange({
      ...settings,
      voice,
    });
  }

  function handleEmotionChange(emotion) {
    setCustomEmotion(emotion);
    onChange({
      ...settings,
      emotion,
    });
  }

  function handleSpeedChange(speed) {
    onChange({
      ...settings,
      speed: Number.parseFloat(speed),
    });
  }

  function handlePresetClick(preset) {
    setCustomEmotion(preset.emotion);
    onChange({
      ...settings,
      emotion: preset.emotion,
      speed: preset.speed,
    });
  }

  function handleGenerateChapter() {
    if (!selectedChapter) {
      window.alert("Please select a chapter first.");
      return;
    }

    window.alert("Audio generation arrives in a later prompt.");
  }

  return (
    <section className="flex h-full min-h-[24rem] flex-col overflow-hidden rounded-[2rem] border border-white/10 bg-white/[0.04] shadow-2xl shadow-slate-950/20">
      <div className="border-b border-white/10 bg-slate-950/35 px-5 py-4">
        <div className="text-xs font-semibold uppercase tracking-[0.28em] text-amber-200/75">
          Narration Settings
        </div>
        <h2 className="mt-2 text-xl font-semibold text-white">Voice and delivery</h2>
        <p className="mt-2 text-sm text-slate-400">
          These settings persist while you move between chapters.
        </p>
      </div>

      <div className="flex-1 space-y-6 overflow-y-auto p-5">
        <div className="rounded-[1.5rem] border border-white/10 bg-slate-950/35 p-4">
          <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">
            Active Segment
          </div>
          <div className="mt-2 text-sm font-semibold text-white">
            {getChapterSummary(selectedChapter)}
          </div>
          <div className="mt-2 text-xs text-slate-400">
            Engine: <span className="font-semibold text-slate-200">Qwen3-TTS</span>
          </div>
        </div>

        <div>
          <label className="block text-sm font-semibold text-white" htmlFor="voice-select">
            Voice
          </label>
          {loadingVoices ? (
            <div className="mt-3 text-sm text-slate-400">
              {loadingMessage || "Loading voices..."}
            </div>
          ) : (
            <VoiceSelector
              ariaLabel="Narration voice"
              className="mt-3 w-full rounded-2xl border border-white/10 bg-slate-950/55 px-4 py-3 text-sm text-white outline-none transition focus:border-sky-300/40"
              emptyLabel="No voices available"
              id="voice-select"
              onChange={handleVoiceChange}
              value={settings.voice}
              voices={availableVoices}
            />
          )}
        </div>

        <div>
          <label className="block text-sm font-semibold text-white" htmlFor="emotion-input">
            Emotion / style
          </label>
          <input
            className="mt-3 w-full rounded-2xl border border-white/10 bg-slate-950/55 px-4 py-3 text-sm text-white outline-none transition placeholder:text-slate-500 focus:border-sky-300/40"
            id="emotion-input"
            onChange={(event) => handleEmotionChange(event.target.value)}
            placeholder="warm, dramatic, contemplative..."
            type="text"
            value={customEmotion}
          />
          <div className="mt-3 flex flex-wrap gap-2">
            {EMOTION_PRESETS.map((emotion) => {
              const active = customEmotion === emotion;

              return (
                <button
                  className={`rounded-full px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.18em] transition ${
                    active
                      ? "bg-sky-300 text-slate-950"
                      : "border border-white/10 bg-white/[0.04] text-slate-300 hover:bg-white/[0.08]"
                  }`}
                  key={emotion}
                  onClick={() => handleEmotionChange(emotion)}
                  type="button"
                >
                  {emotion}
                </button>
              );
            })}
          </div>
        </div>

        <div>
          <label className="block text-sm font-semibold text-white" htmlFor="speed-range">
            Speed
          </label>
          <div className="mt-2 text-sm text-slate-300">
            <span className="font-semibold text-amber-100">{settings.speed.toFixed(2)}x</span> playback
          </div>
          <input
            className="mt-4 w-full accent-amber-300"
            id="speed-range"
            max="2.0"
            min="0.5"
            onChange={(event) => handleSpeedChange(event.target.value)}
            step="0.05"
            type="range"
            value={settings.speed}
          />
          <div className="mt-2 flex justify-between text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
            <span>0.5x</span>
            <span>1.0x</span>
            <span>2.0x</span>
          </div>
        </div>

        <div>
          <div className="text-sm font-semibold text-white">Narration presets</div>
          <div className="mt-3 space-y-3">
            {NARRATOR_PRESETS.map((preset) => (
              <button
                className="w-full rounded-[1.5rem] border border-white/10 bg-slate-950/35 px-4 py-3 text-left transition hover:border-amber-300/20 hover:bg-slate-950/55"
                key={preset.name}
                onClick={() => handlePresetClick(preset)}
                type="button"
              >
                <div className="text-sm font-semibold text-white">{preset.name}</div>
                <div className="mt-1 text-xs uppercase tracking-[0.18em] text-slate-400">
                  {preset.emotion} · {preset.speed.toFixed(2)}x
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="border-t border-white/10 bg-slate-950/35 p-5">
        <button
          className="w-full rounded-full bg-amber-300 px-4 py-3 text-sm font-semibold text-slate-950 transition hover:bg-amber-200 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
          disabled={!selectedChapter}
          onClick={handleGenerateChapter}
          type="button"
        >
          Generate Audio
        </button>
      </div>
    </section>
  );
}
