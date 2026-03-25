import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  GENERATION_STATUS_META,
  formatDetailedDuration,
  formatEta,
  getChapterLabel,
} from "./generationStatus";
import ProgressHeartbeat from "./ProgressHeartbeat";

export default function GenerationProgress({
  active = false,
  bookId,
  chapters,
  onChapterCompleted = () => {},
  onStatusUpdate = () => {},
}) {
  const [pollingError, setPollingError] = useState("");
  const [pollRetryNonce, setPollRetryNonce] = useState(0);
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
    let timeoutId = null;

    function scheduleNextPoll(delayMs) {
      if (cancelled) {
        return;
      }
      timeoutId = window.setTimeout(() => {
        void pollStatus();
      }, delayMs);
    }

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
        if (payload.status === "generating") {
          scheduleNextPoll(2000);
        }
      } catch (error) {
        if (cancelled) {
          return;
        }

        failureCount += 1;
        if (failureCount >= 10) {
          setPollingError("Connection lost — click to retry");
          return;
        }

        const retryDelayMs = Math.min(2000 * (2 ** (failureCount - 1)), 8000);
        setPollingError(
          `Retrying generation status in ${Math.round(retryDelayMs / 1000)}s (${failureCount}/10)...`,
        );
        scheduleNextPoll(retryDelayMs);
      }
    }

    void pollStatus();

    return () => {
      cancelled = true;
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [active, bookId, pollRetryNonce]);

  const chapterMap = useMemo(() => new Map(chapters.map((chapter) => [chapter.number, chapter])), [chapters]);
  const resolvedSnapshot = snapshot ?? {
    chapters: [],
    current_chunk: null,
    current_chapter_n: null,
    eta_seconds: null,
    status: "generating",
    total_chunks: null,
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
  const currentChapterIndex = Math.min(
    Math.max(completedCount + (currentChapterStatus ? 1 : 0), 1),
    totalCount,
  );
  const currentChunk = resolvedSnapshot.current_chunk ?? currentChapterStatus?.current_chunk ?? null;
  const totalChunks = resolvedSnapshot.total_chunks ?? currentChapterStatus?.total_chunks ?? null;
  const hasChunkProgress = currentChunk !== null && totalChunks !== null;
  const chunkProgressPercent = hasChunkProgress
    ? Math.min((currentChunk / totalChunks) * 100, 100)
    : (
      currentChapterStatus?.progress_seconds != null
      && currentChapterStatus?.expected_total_seconds
        ? Math.min(
          (currentChapterStatus.progress_seconds / currentChapterStatus.expected_total_seconds) * 100,
          100,
        )
        : null
    );
  const chapterHeading = hasChunkProgress
    ? `Chapter ${currentChapterIndex} of ${totalCount} — Chunk ${currentChunk}/${totalChunks} (${Math.round(chunkProgressPercent ?? 0)}%)`
    : `Chapter ${currentChapterIndex} of ${totalCount}`;

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
          <h2 className="mt-2 text-2xl font-semibold text-white">{chapterHeading}</h2>
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
            {hasChunkProgress
              ? `Chunk ${currentChunk} of ${totalChunks} is rendering now.`
              : `Generating... ${formatDetailedDuration(currentChapterStatus.progress_seconds ?? 0)} of ${formatDetailedDuration(currentChapterStatus.expected_total_seconds ?? 0)} complete`}
          </div>
          <div className="mt-4">
            <ProgressHeartbeat
              isActive={resolvedSnapshot.status === "generating"}
              progressPercent={chunkProgressPercent}
              showETA={formatEta(resolvedSnapshot.eta_seconds)}
              size="sm"
              stage={
                hasChunkProgress
                  ? `Generating chunk ${currentChunk}/${totalChunks}`
                  : "Generating chapter audio..."
              }
              startTime={currentChapterStatus.started_at}
            />
          </div>
        </div>
      ) : null}

      {pollingError ? (
        <div className="mt-5 rounded-[1.25rem] border border-amber-300/25 bg-amber-400/10 px-4 py-3 text-sm text-amber-100">
          {pollingError === "Connection lost — click to retry" ? (
            <button
              className="font-semibold underline underline-offset-4"
              onClick={() => {
                setPollingError("");
                setPollRetryNonce((currentValue) => currentValue + 1);
              }}
              type="button"
            >
              {pollingError}
            </button>
          ) : (
            pollingError
          )}
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
