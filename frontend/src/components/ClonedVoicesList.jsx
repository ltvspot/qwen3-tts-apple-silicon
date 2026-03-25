import React from "react";

function formatCreatedAt(value) {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    return "Unknown date";
  }

  return timestamp.toLocaleDateString();
}

export default function ClonedVoicesList({
  deletingVoiceName = "",
  errorMessage = "",
  loading = false,
  onDelete,
  voices,
}) {
  return (
    <section className="rounded-[2rem] border border-slate-200 bg-white p-6 shadow-xl shadow-slate-900/5">
      <div className="flex items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">
            Cloned Voices
          </p>
          <h3 className="mt-2 text-2xl font-semibold text-slate-950">Reference library</h3>
        </div>
        <div className="rounded-full bg-slate-100 px-4 py-2 text-xs font-semibold uppercase tracking-[0.16em] text-slate-600">
          {voices.length} saved
        </div>
      </div>

      {errorMessage ? (
        <div className="mt-6 rounded-[1.5rem] border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700">
          {errorMessage}
        </div>
      ) : null}

      {loading ? (
        <div className="mt-6 rounded-[1.5rem] border border-slate-200 bg-slate-50 px-6 py-10 text-center text-sm text-slate-500">
          Loading cloned voices...
        </div>
      ) : null}

      {!loading && voices.length === 0 ? (
        <div className="mt-6 rounded-[1.5rem] border border-dashed border-slate-300 bg-slate-50 px-6 py-10 text-center text-sm text-slate-500">
          No cloned voices yet. Create one from a clean sample before using it in production generation.
        </div>
      ) : null}

      {!loading && voices.length > 0 ? (
        <div className="mt-6 space-y-4">
          {voices.map((voice) => (
            <article
              key={voice.voice_name}
              className="rounded-[1.5rem] border border-slate-200 bg-[linear-gradient(135deg,rgba(15,23,42,0.04)_0%,rgba(14,165,233,0.06)_100%)] p-5"
              data-voice-name={voice.voice_name}
            >
              <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                <div>
                  <h4 className="text-lg font-semibold text-slate-950">{voice.display_name}</h4>
                  <div className="mt-3 flex flex-wrap gap-2 text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">
                    <span className="rounded-full bg-slate-950 px-3 py-1 text-amber-200">
                      {voice.voice_name}
                    </span>
                    <span className="rounded-full bg-sky-100 px-3 py-1 text-sky-700">
                      {voice.audio_duration_seconds.toFixed(1)}s sample
                    </span>
                    <span className="rounded-full bg-slate-200 px-3 py-1 text-slate-700">
                      {voice.created_by || "Unknown source"}
                    </span>
                  </div>
                  <p className="mt-4 text-sm text-slate-600">
                    Created {formatCreatedAt(voice.created_at)}
                  </p>
                  {voice.notes ? (
                    <p className="mt-3 text-sm leading-7 text-slate-700">{voice.notes}</p>
                  ) : null}
                </div>

                <button
                  className="inline-flex items-center justify-center rounded-full border border-rose-200 px-4 py-2 text-sm font-semibold text-rose-700 transition hover:border-rose-300 hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={deletingVoiceName === voice.voice_name}
                  onClick={() => onDelete(voice.voice_name)}
                  type="button"
                >
                  {deletingVoiceName === voice.voice_name ? "Deleting..." : "Delete"}
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}
