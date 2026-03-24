import React, { useEffect, useRef, useState } from "react";

const WAVEFORM_BARS = [
  28, 42, 56, 34, 62, 46, 30, 54, 68, 38,
  52, 44, 26, 58, 40, 64, 32, 48, 60, 36,
  55, 41, 29, 63, 47, 35, 57, 43, 31, 66,
];

function formatTime(seconds) {
  if (!seconds || Number.isNaN(seconds)) {
    return "0:00";
  }

  const minutes = Math.floor(seconds / 60);
  const remainder = Math.floor(seconds % 60);
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

export default function AudioPlayer({ audioUrl, title, duration = 0 }) {
  const audioRef = useRef(null);

  const [audioLoaded, setAudioLoaded] = useState(false);
  const [audioError, setAudioError] = useState("");
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [resolvedDuration, setResolvedDuration] = useState(0);

  useEffect(() => {
    setAudioLoaded(false);
    setAudioError("");
    setCurrentTime(0);
    setIsPlaying(false);
    setResolvedDuration(0);
  }, [audioUrl]);

  const totalDuration = resolvedDuration || duration || 0;
  const progress = totalDuration > 0 ? Math.min((currentTime / totalDuration) * 100, 100) : 0;

  const handlePlayPause = async () => {
    const audio = audioRef.current;
    if (!audio || !audioLoaded) {
      return;
    }

    if (audio.paused) {
      try {
        await audio.play();
        setIsPlaying(true);
      } catch (error) {
        setAudioError("Playback is unavailable in this browser.");
        console.error("Playback failed:", error);
      }
      return;
    }

    audio.pause();
    setIsPlaying(false);
  };

  const handleSeek = (event) => {
    const audio = audioRef.current;
    if (!audio || !totalDuration) {
      return;
    }

    const rect = event.currentTarget.getBoundingClientRect();
    const percent = Math.min(Math.max((event.clientX - rect.left) / rect.width, 0), 1);
    const nextTime = percent * totalDuration;

    audio.currentTime = nextTime;
    setCurrentTime(nextTime);
  };

  return (
    <section className="rounded-[2rem] border border-slate-200 bg-white/95 p-6 shadow-xl shadow-slate-900/5">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">
            Preview Player
          </p>
          <h3 className="mt-2 text-2xl font-semibold text-slate-950">{title}</h3>
        </div>
        <div className="inline-flex items-center rounded-full bg-slate-950 px-4 py-2 text-xs font-semibold uppercase tracking-[0.24em] text-amber-200">
          {audioLoaded ? "Ready" : "Loading preview"}
        </div>
      </div>

      <div
        aria-label={`Seek ${title}`}
        className="relative mt-6 h-24 cursor-pointer overflow-hidden rounded-[1.5rem] border border-slate-200 bg-[linear-gradient(135deg,#0f172a_0%,#1e293b_50%,#334155_100%)] px-4 py-5"
        onClick={handleSeek}
        role="slider"
        tabIndex={0}
        aria-valuemax={Math.max(totalDuration, 0)}
        aria-valuemin={0}
        aria-valuenow={Math.min(currentTime, totalDuration)}
      >
        <div
          className="absolute inset-y-0 left-0 bg-[linear-gradient(90deg,rgba(245,158,11,0.55)_0%,rgba(14,165,233,0.45)_100%)]"
          style={{ width: `${progress}%` }}
        />
        <div className="relative flex h-full items-end gap-[3px]">
          {WAVEFORM_BARS.map((barHeight, index) => {
            const completed = (index + 1) / WAVEFORM_BARS.length <= progress / 100;

            return (
              <div
                key={`${title}-${index}`}
                className={`flex-1 rounded-full transition ${
                  completed ? "bg-amber-200/95" : "bg-white/28"
                }`}
                style={{ height: `${barHeight}%` }}
              />
            );
          })}
        </div>
      </div>

      <div className="mt-6 flex flex-col gap-4 border-t border-slate-200 pt-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <button
            aria-label={isPlaying ? `Pause ${title}` : `Play ${title}`}
            className="inline-flex h-12 w-12 items-center justify-center rounded-full bg-slate-950 text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
            disabled={!audioLoaded}
            onClick={() => {
              void handlePlayPause();
            }}
            type="button"
          >
            {isPlaying ? (
              <svg aria-hidden="true" className="h-5 w-5" fill="currentColor" viewBox="0 0 20 20">
                <path d="M5.5 3A1.5 1.5 0 0 1 7 4.5v11a1.5 1.5 0 0 1-3 0v-11A1.5 1.5 0 0 1 5.5 3Zm7 0A1.5 1.5 0 0 1 14 4.5v11a1.5 1.5 0 0 1-3 0v-11A1.5 1.5 0 0 1 12.5 3Z" />
              </svg>
            ) : (
              <svg aria-hidden="true" className="h-5 w-5 translate-x-px" fill="currentColor" viewBox="0 0 20 20">
                <path d="M6.26 2.84A1.5 1.5 0 0 0 4 4.11v11.78a1.5 1.5 0 0 0 2.26 1.27l9.34-5.89a1.5 1.5 0 0 0 0-2.54L6.26 2.84Z" />
              </svg>
            )}
          </button>
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
              Timeline
            </p>
            <p className="mt-1 font-mono text-sm text-slate-700">
              {formatTime(currentTime)} / {formatTime(totalDuration)}
            </p>
          </div>
        </div>

        <a
          className="inline-flex items-center justify-center rounded-full border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-900 hover:text-slate-950"
          download={title.toLowerCase().replace(/\s+/g, "-")}
          href={audioUrl}
        >
          Download WAV
        </a>
      </div>

      {audioError ? (
        <p className="mt-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {audioError}
        </p>
      ) : null}

      <audio
        onEnded={() => {
          setCurrentTime(0);
          setIsPlaying(false);
        }}
        onError={() => {
          setAudioError("Unable to load the generated preview.");
          setAudioLoaded(false);
          setIsPlaying(false);
        }}
        onLoadedMetadata={(event) => {
          setAudioLoaded(true);
          setAudioError("");
          setResolvedDuration(event.currentTarget.duration || 0);
        }}
        onTimeUpdate={(event) => {
          setCurrentTime(event.currentTarget.currentTime);
        }}
        ref={audioRef}
        src={audioUrl}
      />
    </section>
  );
}
