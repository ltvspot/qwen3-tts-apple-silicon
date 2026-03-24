import React from "react";

const CHAPTER_STATUS_META = {
  failed: {
    colorClass: "text-rose-300",
    icon: "×",
    tooltip: "Generation failed",
  },
  generated: {
    colorClass: "text-emerald-300",
    icon: "✓",
    tooltip: "Audio generated",
  },
  generating: {
    colorClass: "animate-pulse text-sky-300",
    icon: "◔",
    tooltip: "Generation in progress",
  },
  pending: {
    colorClass: "text-slate-400",
    icon: "○",
    tooltip: "Not started",
  },
};

const CHAPTER_QA_META = {
  approved: {
    colorClass: "text-emerald-300",
    icon: "✓",
    tooltip: "QA approved",
  },
  needs_review: {
    colorClass: "text-amber-300",
    icon: "!",
    tooltip: "Needs review",
  },
  not_reviewed: {
    colorClass: "text-yellow-200",
    icon: "?",
    tooltip: "Not reviewed",
  },
};

function formatDuration(durationSeconds) {
  if (!durationSeconds && durationSeconds !== 0) {
    return null;
  }

  const totalSeconds = Math.max(0, Math.round(durationSeconds));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;

  if (minutes === 0) {
    return `${seconds}s`;
  }

  return `${minutes}m ${seconds}s`;
}

function getChapterLabel(chapter) {
  if (chapter.type === "opening_credits") {
    return "Opening Credits";
  }

  if (chapter.type === "closing_credits") {
    return "Closing Credits";
  }

  if (chapter.type === "introduction") {
    return chapter.title ? `Introduction: ${chapter.title}` : "Introduction";
  }

  return chapter.title ? `Chapter ${chapter.number}: ${chapter.title}` : `Chapter ${chapter.number}`;
}

export default function ChapterList({ chapters, onSelectChapter, selectedChapterId }) {
  return (
    <section className="flex h-full min-h-[24rem] flex-col overflow-hidden rounded-[2rem] border border-white/10 bg-white/[0.04] shadow-2xl shadow-slate-950/20">
      <div className="border-b border-white/10 bg-slate-950/35 px-5 py-4">
        <div className="text-xs font-semibold uppercase tracking-[0.28em] text-amber-200/75">
          Manuscript Structure
        </div>
        <h2 className="mt-2 text-xl font-semibold text-white">Chapters ({chapters.length})</h2>
        <p className="mt-2 text-sm text-slate-400">
          Select a segment to review its text and generation settings.
        </p>
      </div>

      <div className="flex-1 overflow-y-auto">
        {chapters.length === 0 ? (
          <div className="flex h-full min-h-[18rem] items-center justify-center px-6 py-8 text-center">
            <div>
              <div className="text-sm font-semibold uppercase tracking-[0.28em] text-slate-500">
                No Chapters Yet
              </div>
              <p className="mt-3 text-sm leading-7 text-slate-400">
                Parse the manuscript before editing or generating narration.
              </p>
            </div>
          </div>
        ) : (
          <ul className="divide-y divide-white/8">
            {chapters.map((chapter) => {
              const isSelected = chapter.id === selectedChapterId;
              const qaMeta = CHAPTER_QA_META[chapter.qa_status ?? "not_reviewed"] ?? CHAPTER_QA_META.not_reviewed;
              const statusMeta = CHAPTER_STATUS_META[chapter.status ?? "pending"] ?? CHAPTER_STATUS_META.pending;
              const durationLabel = formatDuration(chapter.duration_seconds);

              return (
                <li key={chapter.id}>
                  <button
                    className={`w-full border-l-4 px-4 py-4 text-left transition ${
                      isSelected
                        ? "border-amber-300 bg-amber-300/10"
                        : "border-transparent hover:bg-white/[0.05]"
                    }`}
                    data-chapter-id={chapter.id}
                    onClick={() => onSelectChapter(chapter)}
                    type="button"
                  >
                    <div className="flex items-start gap-3">
                      <div className="mt-0.5 flex shrink-0 items-center gap-2 rounded-full border border-white/10 bg-slate-950/45 px-2 py-1 text-xs">
                        <span
                          aria-label={statusMeta.tooltip}
                          className={statusMeta.colorClass}
                          title={statusMeta.tooltip}
                        >
                          {statusMeta.icon}
                        </span>
                        <span
                          aria-label={qaMeta.tooltip}
                          className={qaMeta.colorClass}
                          title={qaMeta.tooltip}
                        >
                          {qaMeta.icon}
                        </span>
                      </div>

                      <div className="min-w-0 flex-1">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <div className="truncate text-sm font-semibold text-white">
                              {getChapterLabel(chapter)}
                            </div>
                            <div className="mt-1 text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">
                              {chapter.type.replaceAll("_", " ")}
                            </div>
                          </div>
                          <div className="rounded-full border border-white/10 bg-slate-950/40 px-2 py-1 text-[11px] font-semibold uppercase tracking-[0.22em] text-slate-400">
                            #{chapter.number}
                          </div>
                        </div>

                        <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-300">
                          <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-1">
                            {chapter.word_count ?? 0} words
                          </span>
                          {durationLabel ? (
                            <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-1">
                              {durationLabel}
                            </span>
                          ) : null}
                        </div>
                      </div>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}
