import React from "react";

export default function VoicePresetManager({ presets, onDeletePreset, onLoadPreset }) {
  return (
    <section className="rounded-[2rem] border border-slate-200 bg-white p-6 shadow-xl shadow-slate-900/5">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">
            Preset Library
          </p>
          <h3 className="mt-2 text-2xl font-semibold text-slate-950">
            Saved Presets ({presets.length})
          </h3>
        </div>
        <p className="max-w-xl text-sm text-slate-600">
          Save combinations you want to revisit before sending a title through the full narration pipeline.
        </p>
      </div>

      {presets.length === 0 ? (
        <div className="mt-6 rounded-[1.5rem] border border-dashed border-slate-300 bg-slate-50 px-6 py-10 text-center text-sm text-slate-500">
          No presets saved yet. Generate a clip, then use &quot;Save Preset&quot; to keep the settings.
        </div>
      ) : (
        <div className="mt-6 grid gap-4 lg:grid-cols-2">
          {presets.map((preset) => (
            <article
              key={preset.id}
              className="rounded-[1.5rem] border border-slate-200 bg-[linear-gradient(135deg,rgba(15,23,42,0.04)_0%,rgba(245,158,11,0.08)_100%)] p-5"
              data-preset-id={preset.id}
            >
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h4 className="text-lg font-semibold text-slate-950">{preset.name}</h4>
                  <div className="mt-3 flex flex-wrap gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
                    <span className="rounded-full bg-slate-900 px-3 py-1 text-amber-200">
                      {preset.voice}
                    </span>
                    <span className="rounded-full bg-slate-200 px-3 py-1 text-slate-700">
                      {preset.emotion}
                    </span>
                    <span className="rounded-full bg-sky-100 px-3 py-1 text-sky-700">
                      {Number(preset.speed).toFixed(2)}x
                    </span>
                  </div>
                </div>
              </div>

              <div className="mt-5 flex gap-3">
                <button
                  className="inline-flex flex-1 items-center justify-center rounded-full bg-slate-950 px-4 py-2 text-sm font-medium text-white transition hover:bg-slate-800"
                  data-action="load"
                  onClick={() => onLoadPreset(preset)}
                  type="button"
                >
                  Load
                </button>
                <button
                  className="inline-flex items-center justify-center rounded-full border border-rose-200 px-4 py-2 text-sm font-medium text-rose-700 transition hover:border-rose-300 hover:bg-rose-50"
                  data-action="delete"
                  onClick={() => onDeletePreset(preset.id)}
                  type="button"
                >
                  Delete
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
