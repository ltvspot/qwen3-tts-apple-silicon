import React from "react";
import PropTypes from "prop-types";
import { Link } from "react-router-dom";
import { formatAverageSeconds, formatQueueDuration, statusMeta } from "./queueFormatting";

function isPending(actionState, action) {
  return Boolean(actionState?.[action]);
}

export default function QueueJobCard({ job, actionState, onAction, onViewDetails }) {
  const meta = statusMeta(job.status);
  const pauseDisabled = isPending(actionState, "pause") || isPending(actionState, "resume") || isPending(actionState, "cancel");
  const moveDisabled = isPending(actionState, "move_up") || isPending(actionState, "move_down");

  return (
    <article
      className="rounded-[1.8rem] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/60"
      data-job-id={job.job_id}
    >
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <Link className="text-xl font-semibold text-slate-950 transition hover:text-sky-700" to={`/book/${job.book_id}`}>
              {job.book_title}
            </Link>
            <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${meta.badgeClass}`}>
              {meta.label}
            </span>
            <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600">
              Priority {job.priority}
            </span>
          </div>
          <div className="mt-2 text-sm text-slate-600">{job.book_author}</div>
          <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-500">
            <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1">
              {job.chapters_completed} / {job.chapters_total} chapters
            </span>
            <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1">
              ETA {formatQueueDuration(job.eta_seconds)}
            </span>
            <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1">
              {formatAverageSeconds(job.avg_seconds_per_chapter)}
            </span>
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          {(job.status === "queued" || job.status === "generating") ? (
            <button
              className="rounded-full border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-950 hover:text-slate-950 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={pauseDisabled}
              onClick={() => onAction(job, "pause")}
              type="button"
            >
              {isPending(actionState, "pause") ? "Pausing..." : "Pause"}
            </button>
          ) : null}

          {job.status === "paused" ? (
            <button
              className="rounded-full border border-sky-300 bg-sky-50 px-4 py-2 text-sm font-medium text-sky-900 transition hover:border-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={pauseDisabled}
              onClick={() => onAction(job, "resume")}
              type="button"
            >
              {isPending(actionState, "resume") ? "Resuming..." : "Resume"}
            </button>
          ) : null}

          {job.status !== "completed" && job.status !== "error" ? (
            <button
              className="rounded-full border border-orange-300 bg-orange-50 px-4 py-2 text-sm font-medium text-orange-900 transition hover:border-orange-400 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={pauseDisabled}
              onClick={() => onAction(job, "cancel")}
              type="button"
            >
              {isPending(actionState, "cancel") ? "Cancelling..." : "Cancel"}
            </button>
          ) : null}

          {job.status === "queued" ? (
            <>
              <button
                className="rounded-full border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-950 hover:text-slate-950 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={moveDisabled}
                onClick={() => onAction(job, "move_up")}
                type="button"
              >
                Up
              </button>
              <button
                className="rounded-full border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-950 hover:text-slate-950 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={moveDisabled}
                onClick={() => onAction(job, "move_down")}
                type="button"
              >
                Down
              </button>
            </>
          ) : null}

          <button
            className="rounded-full border border-slate-950 bg-slate-950 px-4 py-2 text-sm font-medium text-white transition hover:bg-slate-800"
            onClick={() => onViewDetails(job.job_id)}
            type="button"
          >
            View Details
          </button>
        </div>
      </div>

      <div className="mt-5">
        <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
          <span>{Math.round(job.progress_percent)}%</span>
          <span>{job.current_chapter_title || "Awaiting next chapter"}</span>
        </div>
        <div className="mt-2 h-3 overflow-hidden rounded-full bg-slate-100">
          <div
            className="h-full rounded-full bg-[linear-gradient(90deg,#0f172a_0%,#38bdf8_55%,#f59e0b_100%)] transition-[width] duration-300"
            style={{ width: `${Math.max(0, Math.min(job.progress_percent, 100))}%` }}
          />
        </div>
      </div>

      {job.error_message ? (
        <div className="mt-4 rounded-[1.2rem] border border-orange-200 bg-orange-50 px-4 py-3 text-sm text-orange-900">
          {job.error_message}
        </div>
      ) : null}
    </article>
  );
}

QueueJobCard.propTypes = {
  actionState: PropTypes.objectOf(PropTypes.bool),
  job: PropTypes.shape({
    avg_seconds_per_chapter: PropTypes.number,
    book_author: PropTypes.string.isRequired,
    book_id: PropTypes.number.isRequired,
    book_title: PropTypes.string.isRequired,
    chapters_completed: PropTypes.number.isRequired,
    chapters_total: PropTypes.number.isRequired,
    current_chapter_title: PropTypes.string,
    error_message: PropTypes.string,
    eta_seconds: PropTypes.number,
    job_id: PropTypes.number.isRequired,
    priority: PropTypes.number.isRequired,
    progress_percent: PropTypes.number.isRequired,
    status: PropTypes.string.isRequired,
  }).isRequired,
  onAction: PropTypes.func.isRequired,
  onViewDetails: PropTypes.func.isRequired,
};
