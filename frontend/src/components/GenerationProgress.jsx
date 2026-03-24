import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  GENERATION_STATUS_META,
  formatDetailedDuration,
  formatEta,
  getChapterLabel,
} from "./generationStatus";

function formatElapsed(startedAt, nowMs) {
  if (!startedAt) {
    return "0s elapsed";
  }

  const startedAtMs = new Date(startedAt).getTime();
  if (Number.isNaN(startedAtMs)) {
    return "0s elapsed";
  }

  return `${formatDetailedDuration(Math.max((nowMs - startedAtMs) / 1000, 0))} elapsed`;
}

export default function GenerationProgress({
  active = false,
  bookId,
  chapters,
  onChapterCompleted = () => {},
  onStatusUpdate = () => {},
}) {
  const [pollingError, setPollingError] = useState("");
  const [renderNow, setRenderNow] = useState(() => Date.now());
  const [snapshot, setSnapshot] = useState(null);

  const callbackRef = useRef({
    onChapterCompleted,
    onStatusUpdate,
  });
  const completedSetRef = useRef(null);

  useEffect(() => {
    callbackRef.current = {
      onChapterCompleted,
      onStatusUpdate,
    };
  }, [onChapterCompleted, onStatusUpdate]);

  useEffect(() => {
    if (!active) {
      setPollingError("");
      setSnapshot(null);
      completedSetRef.current = null;
      return undefined;
    }

    let cancelled = false;
    let failureCount = 0;
    let intervalId = null;

    async function pollStatus() {
      try {
        const response = await fetch(`/api/book/${bookId}/status`);
        if (!response.ok) {
          throw new Error("Failed to fetch generation progress.");
        }

        const payload = await response.json();
        if (cancelled) {
          return;
        }

        setSnapshot(payload);
        setPollingError("");
        failureCount = 0;
        callbackRef.current.onStatusUpdate(payload);

        const nextCompletedSet = new Set(
          payload.chapters
            .filter((chapter) => chapter.status === "completed")
            .map((chapter) => chapter.chapter_n),
        );

        if (completedSetRef.current !== null) {
          for (const chapterNumber of nextCompletedSet) {
            if (!completedSetRef.current.has(chapterNumber)) {
              const completedChapter = payload.chapters.find(
                (chapter) => chapter.chapter_n === chapterNumber,
              );
              callbackRef.current.onChapterCompleted(completedChapter);
            }
          }
        }

        completedSetRef.current = nextCompletedSet;
        if (payload.status !== "generating" && intervalId !== null) {
          window.clearInterval(intervalId);
          intervalId = null;
        }
      } catch (error) {
        if (cancelled) {
          return;
        }

        failureCount += 1;
        if (failureCount >= 3) {
          setPollingError("Generation progress lost connection after 3 retries.");
          if (intervalId !== null) {
            window.clearInterval(intervalId);
            intervalId = null;
          }
          return;
        }

        setPollingError(`Retrying generation status (${failureCount}/3)...`);
      }
    }

    void pollStatus();
    intervalId = window.setInterval(() => {
      void pollStatus();
    }, 2000);

    return () => {
      cancelled = true;
      if (intervalId !== null) {
        window.clearInterval(intervalId);
      }
    };
  }, [active, bookId]);

  useEffect(() => {
    if (!active) {
      return undefined;
    }

    const intervalId = window.setInterval(() => {
      setRenderNow(Date.now());
    }, 1000);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [active]);

  const chapterMap = useMemo(() => new Map(chapters.map((chapter) => [chapter.number, chapter])), [chapters]);
  const resolvedSnapshot = snapshot ?? {
    chapters: [],
    current_chapter_n: null,
    eta_seconds: null,
    status: "generating",
  };
  const completedCount = resolvedSnapshot.chapters.filter((chapter) => chapter.status === "completed").length;
  const totalCount = Math.max(chapters.length, resolvedSnapshot.chapters.length, 1);
  const currentChapterStatus = resolvedSnapshot.chapters.find(
    (chapter) => chapter.chapter_n === resolvedSnapshot.current_chapter_n,
  ) ?? null;
  const currentChapter = currentChapterStatus
    ? chapterMap.get(currentChapterStatus.chapter_n) ?? { number: currentChapterStatus.chapter_n, type: "chapter" }
    : null;
  const progressPercent = Math.min((completedCount / totalCount) * 100, 100);

  if (!active) {
    return null;
  }

  return (
    <section className="rounded-[2rem] border border-sky-300/20 bg-slate-950/50 p-5 shadow-2xl shadow-slate-950/30 backdrop-blur">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.28em] text-sky-200/80">
            Generation Progress
          </div>
          <h2 className="mt-2 text-2xl font-semibold text-white">
            Chapter {Math.min(Math.max(completedCount + (currentChapterStatus ? 1 : 0), 1), totalCount)} of {totalCount}
          </h2>
          <p className="mt-2 text-sm text-slate-300">
            {currentChapter
              ? `${getChapterLabel(currentChapter)} is processing now.`
              : "Preparing the next chapter for narration."}
          </p>
        </div>

        <div className="rounded-[1.5rem] border border-white/10 bg-white/[0.04] px-4 py-3 text-right">
          <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
            Run ETA
          </div>
          <div className="mt-2 text-lg font-semibold text-amber-100">
            {formatEta(resolvedSnapshot.eta_seconds)}
          </div>
        </div>
      </div>

      <div className="mt-5">
        <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
          <span>{completedCount} completed</span>
          <span>{Math.round(progressPercent)}%</span>
        </div>
        <div className="mt-2 h-3 overflow-hidden rounded-full bg-white/10">
          <div
            className="h-full rounded-full bg-[linear-gradient(90deg,rgba(56,189,248,0.75)_0%,rgba(251,191,36,0.9)_100%)] transition-[width] duration-500"
            style={{ width: `${progressPercent}%` }}
          />
        </div>
      </div>

      {currentChapterStatus ? (
        <div className="mt-5 rounded-[1.5rem] border border-white/10 bg-white/[0.04] p-4">
          <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">
            Current Chapter
          </div>
          <div className="mt-2 text-lg font-semibold text-white">
            {currentChapter ? getChapterLabel(currentChapter) : `Chapter ${currentChapterStatus.chapter_n}`}
          </div>
          <div className="mt-2 text-sm text-slate-300">
            Generating... {formatDetailedDuration(currentChapterStatus.progress_seconds ?? 0)} of{" "}
            {formatDetailedDuration(currentChapterStatus.expected_total_seconds ?? 0)} complete
          </div>
          <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-400">
            <span className="rounded-full border border-white/10 bg-slate-950/45 px-2 py-1">
              {formatElapsed(currentChapterStatus.started_at, renderNow)}
            </span>
            <span className="rounded-full border border-white/10 bg-slate-950/45 px-2 py-1">
              {formatEta(resolvedSnapshot.eta_seconds)}
            </span>
          </div>
        </div>
      ) : null}

      {pollingError ? (
        <div className="mt-5 rounded-[1.25rem] border border-amber-300/25 bg-amber-400/10 px-4 py-3 text-sm text-amber-100">
          {pollingError}
        </div>
      ) : null}

      <ul className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {chapters.map((chapter) => {
          const chapterStatus = resolvedSnapshot.chapters.find(
            (candidate) => candidate.chapter_n === chapter.number,
          );
          const statusKey = chapterStatus?.status ?? "pending";
          const statusMeta = GENERATION_STATUS_META[statusKey] ?? GENERATION_STATUS_META.pending;

          return (
            <li
              key={chapter.id}
              className="rounded-[1.35rem] border border-white/10 bg-slate-950/30 px-4 py-3"
              title={chapterStatus?.error_message || statusMeta.tooltip}
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold text-white">{getChapterLabel(chapter)}</div>
                  <div className="mt-1 text-[11px] uppercase tracking-[0.22em] text-slate-500">
                    {chapter.word_count ?? 0} words
                  </div>
                </div>
                <span className={`text-lg ${statusMeta.colorClass}`}>{statusMeta.icon}</span>
              </div>
              <div className="mt-3 text-xs text-slate-400">
                {statusMeta.label}
                {chapterStatus?.status === "generating" && chapterStatus.expected_total_seconds ? (
                  <span>
                    {" "}
                    · {formatDetailedDuration(chapterStatus.progress_seconds ?? 0)} /{" "}
                    {formatDetailedDuration(chapterStatus.expected_total_seconds)}
                  </span>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

GenerationProgress.propTypes = {
  active: PropTypes.bool,
  bookId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  chapters: PropTypes.arrayOf(PropTypes.shape({
    id: PropTypes.number.isRequired,
    number: PropTypes.number.isRequired,
    title: PropTypes.string,
    type: PropTypes.string.isRequired,
    word_count: PropTypes.number,
  })).isRequired,
  onChapterCompleted: PropTypes.func,
  onStatusUpdate: PropTypes.func,
};
