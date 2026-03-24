import React from "react";
import PropTypes from "prop-types";
import { formatQueueDuration, statusMeta } from "./queueFormatting";

export default function JobDetailsModal({ job = null, loading = false, onClose }) {
  if (!loading && !job) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 px-4 py-6" role="dialog" aria-modal="true">
      <div className="max-h-[90vh] w-full max-w-4xl overflow-y-auto rounded-[2rem] bg-white p-6 shadow-2xl shadow-slate-950/20">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">Job Details</div>
            <h2 className="mt-2 text-2xl font-semibold text-slate-950">
              {job ? job.book_title : "Loading job"}
            </h2>
            {job ? (
              <div className="mt-2 flex flex-wrap gap-2 text-sm text-slate-600">
                <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${statusMeta(job.status).badgeClass}`}>
                  {statusMeta(job.status).label}
                </span>
                <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-700">
                  Priority {job.priority}
                </span>
                <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-700">
                  ETA {formatQueueDuration(job.eta_seconds)}
                </span>
              </div>
            ) : null}
          </div>

          <button
            className="rounded-full border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-950 hover:text-slate-950"
            onClick={onClose}
            type="button"
          >
            Close
          </button>
        </div>

        {loading ? (
          <div className="mt-8 rounded-[1.5rem] border border-slate-200 bg-slate-50 px-5 py-10 text-center text-sm text-slate-600">
            Loading job details...
          </div>
        ) : null}

        {job ? (
          <>
            <section className="mt-6 grid gap-4 md:grid-cols-3">
              <div className="rounded-[1.5rem] border border-slate-200 bg-slate-50 p-4">
                <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-500">Progress</div>
                <div className="mt-3 text-3xl font-semibold text-slate-950">
                  {job.chapters_completed} / {job.chapters_total}
                </div>
                <div className="mt-2 text-sm text-slate-600">Completed chapters</div>
              </div>
              <div className="rounded-[1.5rem] border border-slate-200 bg-slate-50 p-4">
                <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-500">Failures</div>
                <div className="mt-3 text-3xl font-semibold text-slate-950">{job.chapters_failed}</div>
                <div className="mt-2 text-sm text-slate-600">Failed chapters</div>
              </div>
              <div className="rounded-[1.5rem] border border-slate-200 bg-slate-50 p-4">
                <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-500">Average Pace</div>
                <div className="mt-3 text-3xl font-semibold text-slate-950">
                  {job.avg_seconds_per_chapter ? `${job.avg_seconds_per_chapter.toFixed(1)}s` : "N/A"}
                </div>
                <div className="mt-2 text-sm text-slate-600">Per completed chapter</div>
              </div>
            </section>

            <section className="mt-6">
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-semibold text-slate-950">Chapter Breakdown</h3>
                {job.error_message ? (
                  <span className="rounded-full border border-orange-200 bg-orange-50 px-3 py-1 text-xs font-semibold text-orange-900">
                    {job.error_message}
                  </span>
                ) : null}
              </div>

              <div className="mt-4 overflow-hidden rounded-[1.5rem] border border-slate-200">
                <div className="grid grid-cols-[1.25fr,0.8fr,0.7fr,0.7fr] gap-3 border-b border-slate-200 bg-slate-50 px-4 py-3 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">
                  <div>Chapter</div>
                  <div>Status</div>
                  <div>Expected</div>
                  <div>Observed</div>
                </div>
                {job.chapter_breakdown.map((chapter) => (
                  <div
                    key={chapter.chapter_n}
                    className="grid grid-cols-[1.25fr,0.8fr,0.7fr,0.7fr] gap-3 border-b border-slate-100 px-4 py-3 text-sm text-slate-700 last:border-b-0"
                  >
                    <div>
                      <div className="font-medium text-slate-950">{chapter.chapter_title || `Chapter ${chapter.chapter_n}`}</div>
                      {chapter.error_message ? (
                        <div className="mt-1 text-xs text-orange-800">{chapter.error_message}</div>
                      ) : null}
                    </div>
                    <div>{statusMeta(chapter.status).label}</div>
                    <div>{formatQueueDuration(chapter.expected_total_seconds)}</div>
                    <div>
                      {chapter.progress_seconds !== null && chapter.progress_seconds !== undefined
                        ? `${formatQueueDuration(chapter.progress_seconds)} / ${formatQueueDuration(chapter.expected_total_seconds)}`
                        : formatQueueDuration(chapter.duration_seconds)}
                    </div>
                  </div>
                ))}
              </div>
            </section>

            <section className="mt-6">
              <h3 className="text-lg font-semibold text-slate-950">History</h3>
              <div className="mt-4 space-y-3">
                {job.history.length ? job.history.map((entry, index) => (
                  <div
                    key={`${entry.action}-${entry.timestamp}-${index}`}
                    className="rounded-[1.25rem] border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700"
                  >
                    <div className="font-medium text-slate-950">{entry.action}</div>
                    <div className="mt-1">{entry.details || "No details recorded."}</div>
                  </div>
                )) : (
                  <div className="rounded-[1.25rem] border border-dashed border-slate-300 px-4 py-3 text-sm text-slate-500">
                    No history recorded yet.
                  </div>
                )}
              </div>
            </section>
          </>
        ) : null}
      </div>
    </div>
  );
}

JobDetailsModal.propTypes = {
  job: PropTypes.shape({
    avg_seconds_per_chapter: PropTypes.number,
    book_title: PropTypes.string.isRequired,
    chapter_breakdown: PropTypes.arrayOf(PropTypes.shape({
      chapter_n: PropTypes.number.isRequired,
      chapter_title: PropTypes.string,
      duration_seconds: PropTypes.number,
      error_message: PropTypes.string,
      expected_total_seconds: PropTypes.number,
      progress_seconds: PropTypes.number,
      status: PropTypes.string.isRequired,
    })).isRequired,
    chapters_completed: PropTypes.number.isRequired,
    chapters_failed: PropTypes.number.isRequired,
    chapters_total: PropTypes.number.isRequired,
    error_message: PropTypes.string,
    eta_seconds: PropTypes.number,
    history: PropTypes.arrayOf(PropTypes.shape({
      action: PropTypes.string.isRequired,
      details: PropTypes.string,
      timestamp: PropTypes.string.isRequired,
    })).isRequired,
    priority: PropTypes.number.isRequired,
    status: PropTypes.string.isRequired,
  }),
  loading: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
};
