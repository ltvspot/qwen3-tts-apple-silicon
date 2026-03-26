import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import AudioPlayer from "./AudioPlayer";
import {
  formatDetailedDuration,
  formatFileSize,
  getChapterLabel,
} from "./generationStatus";

function wordsPerSecondLabel(chapter) {
  const duration = chapter.audio_duration_seconds ?? chapter.duration_seconds;
  if (!duration || !chapter.word_count) {
    return "Words/sec unavailable";
  }

  return `${(chapter.word_count / duration).toFixed(2)} words/sec`;
}

export default function AudioPlayerPanel({
  bookId,
  chapterNumber = null,
  completedChapters,
  onClose = () => {},
  onSelectChapter = () => {},
  visible = false,
}) {
  const [expanded, setExpanded] = useState(true);
  const [selectedChapterNumber, setSelectedChapterNumber] = useState(chapterNumber);

  useEffect(() => {
    if (!visible) {
      return;
    }

    setExpanded(true);
  }, [chapterNumber, visible]);

  useEffect(() => {
    if (!completedChapters.length) {
      setSelectedChapterNumber(null);
      return;
    }

    const requestedChapter = completedChapters.find((chapter) => chapter.number === chapterNumber);
    if (requestedChapter) {
      setSelectedChapterNumber(chapterNumber);
      return;
    }

    if (!completedChapters.some((chapter) => chapter.number === selectedChapterNumber)) {
      setSelectedChapterNumber(completedChapters[0].number);
    }
  }, [chapterNumber, completedChapters, selectedChapterNumber]);

  const selectedChapter = useMemo(
    () => completedChapters.find((chapter) => chapter.number === selectedChapterNumber) ?? completedChapters[0] ?? null,
    [completedChapters, selectedChapterNumber],
  );

  if (!visible || !selectedChapter) {
    return null;
  }

  const audioUrl = `/api/book/${bookId}/chapter/${selectedChapter.number}/preview`;
  const generationLabel = selectedChapter.generation_seconds
    ? `Generated in ${formatDetailedDuration(selectedChapter.generation_seconds)}`
    : "Generation time unavailable";

  return (
    <aside
      className={`fixed inset-x-0 bottom-0 ${expanded ? "z-50" : "z-40"} border-t border-white/10 bg-slate-950/95 shadow-[0_-18px_40px_rgba(2,6,23,0.45)] backdrop-blur`}
    >
      <div className="mx-auto max-w-[110rem] px-4 py-4 sm:px-6 lg:px-8">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.26em] text-sky-200/75">
              Chapter Audio Player
            </div>
            <div className="mt-2 text-lg font-semibold text-white">
              {getChapterLabel(selectedChapter)}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <label className="sr-only" htmlFor="audio-player-chapter-select">
              Select completed chapter
            </label>
            <select
              className="rounded-full border border-white/10 bg-white/[0.06] px-4 py-2 text-sm text-white outline-none transition focus:border-sky-300/35"
              id="audio-player-chapter-select"
              onChange={(event) => {
                const nextChapterNumber = Number(event.target.value);
                setSelectedChapterNumber(nextChapterNumber);
                onSelectChapter(nextChapterNumber);
              }}
              value={selectedChapter.number}
            >
              {completedChapters.map((chapter) => (
                <option key={chapter.id} value={chapter.number}>
                  {getChapterLabel(chapter)}
                </option>
              ))}
            </select>

            <button
              className="inline-flex items-center rounded-full border border-white/10 bg-white/[0.05] px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-200 transition hover:border-sky-300/35 hover:text-sky-100"
              onClick={() => setExpanded((current) => !current)}
              type="button"
            >
              {expanded ? "Collapse" : "Expand"}
            </button>

            <button
              className="inline-flex items-center rounded-full border border-white/10 bg-white/[0.05] px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-200 transition hover:border-rose-300/35 hover:text-rose-100"
              onClick={onClose}
              type="button"
            >
              Close
            </button>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap gap-2 text-xs text-slate-300">
          <span className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1">
            {generationLabel}
          </span>
          <span className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1">
            {formatDetailedDuration(selectedChapter.audio_duration_seconds ?? selectedChapter.duration_seconds ?? 0)}
          </span>
          <span className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1">
            {wordsPerSecondLabel(selectedChapter)}
          </span>
          <span className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1">
            {formatFileSize(selectedChapter.audio_file_size_bytes)} WAV
          </span>
        </div>

        {expanded ? (
          <div className="mt-4">
            <AudioPlayer
              audioUrl={audioUrl}
              duration={selectedChapter.audio_duration_seconds ?? selectedChapter.duration_seconds ?? 0}
              title={getChapterLabel(selectedChapter)}
            />
          </div>
        ) : null}
      </div>
    </aside>
  );
}

AudioPlayerPanel.propTypes = {
  bookId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  chapterNumber: PropTypes.number,
  completedChapters: PropTypes.arrayOf(PropTypes.shape({
    audio_duration_seconds: PropTypes.number,
    audio_file_size_bytes: PropTypes.number,
    duration_seconds: PropTypes.number,
    generation_seconds: PropTypes.number,
    id: PropTypes.number.isRequired,
    number: PropTypes.number.isRequired,
    title: PropTypes.string,
    type: PropTypes.string.isRequired,
    word_count: PropTypes.number,
  })).isRequired,
  onClose: PropTypes.func,
  onSelectChapter: PropTypes.func,
  visible: PropTypes.bool,
};
