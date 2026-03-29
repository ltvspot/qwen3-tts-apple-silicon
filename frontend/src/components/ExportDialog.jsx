import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";

const M4B_BITRATE_OPTIONS = ["64k", "96k", "128k", "192k", "256k"];

function chapterDurationSeconds(chapter) {
  return chapter.audio_duration_seconds ?? chapter.duration_seconds ?? 0;
}

function formatEstimatedDuration(seconds) {
  if (!seconds || Number.isNaN(seconds) || seconds <= 0) {
    return "<1m";
  }

  const totalMinutes = Math.max(1, Math.round(seconds / 60));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;

  if (hours > 0 && minutes > 0) {
    return `${hours}h ${minutes}m`;
  }

  if (hours > 0) {
    return `${hours}h`;
  }

  return `${totalMinutes}m`;
}

function isFlaggedChapter(chapter) {
  return chapter?.qa_status === "needs_review" || chapter?.qa_status === "flagged";
}

function estimateM4bFileSize(durationSeconds, bitrate) {
  if (!durationSeconds || !bitrate) {
    return null;
  }

  const bitrateKbps = Number.parseInt(String(bitrate).replace("k", ""), 10);
  if (Number.isNaN(bitrateKbps) || bitrateKbps <= 0) {
    return null;
  }

  const estimatedBytes = (durationSeconds * bitrateKbps * 1000) / 8;
  const estimatedMegabytes = estimatedBytes / (1024 * 1024);
  return `${estimatedMegabytes.toFixed(1)} MB`;
}

export default function ExportDialog({
  chapters = [],
  open = false,
  pending = false,
  onClose,
  onSubmit,
}) {
  const [includeM4b, setIncludeM4b] = useState(true);
  const [includeMp3, setIncludeMp3] = useState(true);
  const [includeOnlyApproved, setIncludeOnlyApproved] = useState(true);
  const [m4bBitrate, setM4bBitrate] = useState("128k");
  const [validationMessage, setValidationMessage] = useState("");
  const exportableChapters = chapters;
  const includedChapters = includeOnlyApproved
    ? exportableChapters.filter((chapter) => !isFlaggedChapter(chapter))
    : exportableChapters;
  const totalDurationSeconds = includedChapters.reduce(
    (totalDuration, chapter) => totalDuration + chapterDurationSeconds(chapter),
    0,
  );
  const estimatedDuration = formatEstimatedDuration(totalDurationSeconds);
  const estimatedM4bSize = estimateM4bFileSize(totalDurationSeconds, m4bBitrate);

  useEffect(() => {
    if (!open) {
      return;
    }

    setIncludeMp3(true);
    setIncludeM4b(true);
    setIncludeOnlyApproved(true);
    setM4bBitrate("128k");
    setValidationMessage("");
  }, [open]);

  function handleSubmit() {
    const formats = [];
    if (includeMp3) {
      formats.push("mp3");
    }
    if (includeM4b) {
      formats.push("m4b");
    }

    if (formats.length === 0) {
      setValidationMessage("Select at least one export format.");
      return;
    }

    setValidationMessage("");
    onSubmit({
      formats,
      include_only_approved: includeOnlyApproved,
      m4b_bitrate: includeM4b ? m4bBitrate : null,
    });
  }

  if (!open) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-950/80 px-4 backdrop-blur-sm">
      <div
        aria-modal="true"
        className="w-full max-w-xl rounded-[2rem] border border-white/10 bg-slate-950/95 p-6 text-white shadow-2xl shadow-slate-950/50"
        role="dialog"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.28em] text-amber-200/70">
              Export Pipeline
            </div>
            <h2 className="mt-3 text-2xl font-semibold">Export Audiobook</h2>
            <p className="mt-3 text-sm leading-7 text-slate-300">
              Package the generated narration into downloadable MP3 and M4B audiobook files.
            </p>
          </div>
          <button
            className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-white/10 text-slate-300 transition hover:border-white/20 hover:text-white"
            disabled={pending}
            onClick={onClose}
            type="button"
          >
            <span aria-hidden="true">×</span>
          </button>
        </div>

        <div className="mt-6 space-y-4">
          <label className="flex items-start gap-3 rounded-3xl border border-white/10 bg-white/[0.04] px-4 py-4">
            <input
              checked={includeMp3}
              className="mt-1 h-4 w-4 rounded border-white/20 bg-slate-900 text-amber-300 focus:ring-amber-300"
              onChange={(event) => setIncludeMp3(event.target.checked)}
              type="checkbox"
            />
            <span>
              <span className="block text-sm font-semibold text-white">Include MP3</span>
              <span className="mt-1 block text-sm text-slate-300">
                192 kbps CBR with embedded audiobook metadata and cover art.
              </span>
            </span>
          </label>

          <label className="flex items-start gap-3 rounded-3xl border border-white/10 bg-white/[0.04] px-4 py-4">
            <input
              checked={includeM4b}
              className="mt-1 h-4 w-4 rounded border-white/20 bg-slate-900 text-amber-300 focus:ring-amber-300"
              onChange={(event) => setIncludeM4b(event.target.checked)}
              type="checkbox"
            />
            <span>
              <span className="block text-sm font-semibold text-white">Include M4B</span>
              <span className="mt-1 block text-sm text-slate-300">
                AAC audiobook container with chapter markers for chapter-aware players.
              </span>
            </span>
          </label>

          {includeM4b ? (
            <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4">
              <label className="block text-sm font-semibold text-white" htmlFor="m4b-bitrate">
                M4B Bitrate
              </label>
              <p className="mt-2 text-sm text-slate-300">
                128k is recommended for spoken word. Higher bitrates increase file size with limited narration benefit.
              </p>
              <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <select
                  className="rounded-2xl border border-white/10 bg-slate-950 px-4 py-3 text-sm text-white outline-none transition focus:border-amber-300/40"
                  id="m4b-bitrate"
                  onChange={(event) => setM4bBitrate(event.target.value)}
                  value={m4bBitrate}
                >
                  {M4B_BITRATE_OPTIONS.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
                <div className="text-sm text-slate-400">
                  Estimated M4B size: {estimatedM4bSize ?? "Pending"}
                </div>
              </div>
            </div>
          ) : null}

          <label className="flex items-start gap-3 rounded-3xl border border-amber-300/20 bg-amber-400/10 px-4 py-4">
            <input
              checked={includeOnlyApproved}
              className="mt-1 h-4 w-4 rounded border-white/20 bg-slate-900 text-amber-300 focus:ring-amber-300"
              onChange={(event) => setIncludeOnlyApproved(event.target.checked)}
              type="checkbox"
            />
            <span>
              <span className="block text-sm font-semibold text-amber-100">Skip flagged chapters</span>
              <span className="mt-1 block text-sm text-amber-50/80">
                When checked, chapters flagged during QA will be excluded from the export.
              </span>
            </span>
          </label>

          <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-3 text-sm text-slate-300">
            Will export {includedChapters.length} of {exportableChapters.length} chapters ({estimatedDuration} estimated)
          </div>
        </div>

        {validationMessage ? (
          <div className="mt-5 rounded-2xl border border-amber-300/25 bg-amber-400/10 px-4 py-3 text-sm text-amber-100">
            {validationMessage}
          </div>
        ) : null}

        <div className="mt-6 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
          <button
            className="inline-flex items-center justify-center rounded-full border border-white/10 px-5 py-3 text-sm font-semibold text-slate-300 transition hover:border-white/20 hover:text-white"
            disabled={pending}
            onClick={onClose}
            type="button"
          >
            Cancel
          </button>
          <button
            className="inline-flex items-center justify-center rounded-full border border-amber-300/25 bg-amber-400/10 px-5 py-3 text-sm font-semibold text-amber-100 transition hover:bg-amber-400/20 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.04] disabled:text-slate-500"
            disabled={pending}
            onClick={handleSubmit}
            type="button"
          >
            {pending ? "Starting Export..." : "Export"}
          </button>
        </div>
      </div>
    </div>
  );
}

ExportDialog.propTypes = {
  chapters: PropTypes.arrayOf(
    PropTypes.shape({
      audio_duration_seconds: PropTypes.number,
      duration_seconds: PropTypes.number,
      qa_status: PropTypes.string,
    }),
  ),
  onClose: PropTypes.func.isRequired,
  onSubmit: PropTypes.func.isRequired,
  open: PropTypes.bool,
  pending: PropTypes.bool,
};
