import React from "react";
import PropTypes from "prop-types";
import {
  GENERATION_STATUS_META,
  formatCompactDuration,
  getChapterLabel,
  mapChapterGenerationState,
} from "./generationStatus";

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

function chapterActionLabel(status) {
  return status === "completed" ? "Re-generate" : "Generate This Chapter";
}

export default function ChapterList({
  chapters,
  generationDisabled = false,
  loadingChapterNumber = null,
  onGenerateChapter,
  onPreviewChapter,
  onSelectChapter,
  selectedChapterId = null,
}) {
  return (
    <section className="flex h-full min-h-[24rem] flex-col overflow-hidden rounded-[2rem] border border-white/10 bg-white/[0.04] shadow-2xl shadow-slate-950/20">
      <div className="border-b border-white/10 bg-slate-950/35 px-5 py-4">
        <div className="text-xs font-semibold uppercase tracking-[0.28em] text-amber-200/75">
          Manuscript Structure
        </div>
        <h2 className="mt-2 text-xl font-semibold text-white">Chapters ({chapters.length})</h2>
        <p className="mt-2 text-sm text-slate-400">
          Review text, trigger generation, and jump straight into completed audio.
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
              const generationStatus = mapChapterGenerationState(
                chapter.generation_status ?? chapter.status,
              );
              const qaMeta = CHAPTER_QA_META[chapter.qa_status ?? "not_reviewed"] ?? CHAPTER_QA_META.not_reviewed;
              const statusMeta = GENERATION_STATUS_META[generationStatus] ?? GENERATION_STATUS_META.pending;
              const durationLabel = formatCompactDuration(
                chapter.audio_duration_seconds ?? chapter.duration_seconds,
              );
              const isChapterLoading = loadingChapterNumber === chapter.number;

              return (
                <li key={chapter.id} className="px-4 py-4">
                  <div
                    className={`rounded-[1.5rem] border transition ${
                      isSelected
                        ? "border-amber-300/35 bg-amber-300/10"
                        : "border-white/10 bg-slate-950/20 hover:border-white/15 hover:bg-white/[0.05]"
                    }`}
                  >
                    <button
                      className="w-full px-4 pb-3 pt-4 text-left"
                      data-chapter-id={chapter.id}
                      onClick={() => onSelectChapter(chapter)}
                      type="button"
                    >
                      <div className="flex items-start gap-3">
                        <div className="mt-0.5 flex shrink-0 items-center gap-2 rounded-full border border-white/10 bg-slate-950/45 px-2 py-1 text-xs">
                          <span
                            aria-label={statusMeta.tooltip}
                            className={statusMeta.colorClass}
                            title={chapter.error_message || statusMeta.tooltip}
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
                            <span
                              className={`rounded-full border px-2 py-1 font-semibold ${statusMeta.accentClass}`}
                              title={chapter.error_message || statusMeta.tooltip}
                            >
                              {statusMeta.icon} {statusMeta.label}
                            </span>
                          </div>
                        </div>
                      </div>
                    </button>

                    <div className="flex flex-wrap items-center gap-2 border-t border-white/8 px-4 py-3">
                      {generationStatus === "completed" ? (
                        <button
                          className="inline-flex items-center rounded-full border border-white/10 bg-white/[0.04] px-3 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-200 transition hover:border-sky-300/35 hover:text-sky-100"
                          onClick={() => onPreviewChapter(chapter)}
                          type="button"
                        >
                          Preview Audio
                        </button>
                      ) : null}

                      <button
                        className="inline-flex items-center gap-2 rounded-full border border-amber-300/25 bg-amber-400/10 px-3 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-amber-100 transition hover:bg-amber-400/20 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.04] disabled:text-slate-500"
                        disabled={generationDisabled}
                        onClick={() => onGenerateChapter(chapter, generationStatus === "completed")}
                        type="button"
                      >
                        {isChapterLoading ? (
                          <span
                            aria-hidden="true"
                            className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-r-transparent"
                          />
                        ) : null}
                        {isChapterLoading ? "Queueing..." : chapterActionLabel(generationStatus)}
                      </button>

                      {chapter.error_message ? (
                        <div className="text-xs text-amber-100/85" title={chapter.error_message}>
                          {chapter.error_message}
                        </div>
                      ) : null}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}

ChapterList.propTypes = {
  chapters: PropTypes.arrayOf(PropTypes.shape({
    audio_duration_seconds: PropTypes.number,
    duration_seconds: PropTypes.number,
    error_message: PropTypes.string,
    generation_status: PropTypes.string,
    id: PropTypes.number.isRequired,
    number: PropTypes.number.isRequired,
    qa_status: PropTypes.string,
    status: PropTypes.string,
    text_content: PropTypes.string,
    title: PropTypes.string,
    type: PropTypes.string.isRequired,
    word_count: PropTypes.number,
  })).isRequired,
  generationDisabled: PropTypes.bool,
  loadingChapterNumber: PropTypes.number,
  onGenerateChapter: PropTypes.func.isRequired,
  onPreviewChapter: PropTypes.func.isRequired,
  onSelectChapter: PropTypes.func.isRequired,
  selectedChapterId: PropTypes.number,
};
