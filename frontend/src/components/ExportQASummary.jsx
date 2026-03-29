import PropTypes from "prop-types";
import React, { useState } from "react";

function formatDuration(seconds) {
  if (typeof seconds !== "number" || Number.isNaN(seconds) || seconds <= 0) {
    return "Pending";
  }

  const totalSeconds = Math.round(seconds);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const remainingSeconds = totalSeconds % 60;

  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  if (minutes > 0) {
    return `${minutes}m ${remainingSeconds}s`;
  }
  return `${remainingSeconds}s`;
}

function readinessTone(summary) {
  if (!summary) {
    return "border-white/10 bg-white/[0.04] text-slate-200";
  }
  if (summary.ready) {
    return "border-emerald-300/25 bg-emerald-400/10 text-emerald-100";
  }
  if (summary.export_anyway_allowed) {
    return "border-amber-300/25 bg-amber-400/10 text-amber-100";
  }
  return "border-rose-300/25 bg-rose-500/10 text-rose-100";
}

function checkTone(passed) {
  return passed
    ? "border-emerald-300/20 bg-emerald-400/10 text-emerald-100"
    : "border-rose-300/20 bg-rose-500/10 text-rose-100";
}

function gradeTone(grade) {
  if (grade === "A") {
    return "border-emerald-300/30 bg-emerald-400/10 text-emerald-100";
  }
  if (grade === "B") {
    return "border-cyan-300/30 bg-cyan-400/10 text-cyan-100";
  }
  if (grade === "C") {
    return "border-amber-300/30 bg-amber-400/10 text-amber-100";
  }
  return "border-rose-300/30 bg-rose-500/10 text-rose-100";
}

export default function ExportQASummary({ loading = false, summary = null }) {
  const [detailsOpen, setDetailsOpen] = useState(false);

  if (loading) {
    return (
      <section className="rounded-[2rem] border border-white/10 bg-slate-950/50 p-6 text-white">
        <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-300/75">
          Export Readiness
        </div>
        <p className="mt-3 text-sm text-slate-300">
          Checking chapter QA, ACX compliance, and export blockers...
        </p>
      </section>
    );
  }

  if (!summary) {
    return null;
  }

  const blockingIssues = summary.blocking_issues ?? [];
  const warnings = summary.warnings ?? [];
  const acxChecks = summary.acx_checks ?? [];
  const chapters = summary.chapters ?? [];

  return (
    <section className="rounded-[2rem] border border-white/10 bg-slate-950/50 p-6 text-white">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-300/75">
            Export Readiness
          </div>
          <h3 className="mt-3 text-2xl font-semibold">Publishing QA Summary</h3>
          <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-300">
            Review chapter grades and ACX compliance before packaging the final audiobook files.
          </p>
        </div>
        <div className={`rounded-3xl border px-4 py-3 text-sm font-semibold ${readinessTone(summary)}`}>
          {summary.status_label}
          {summary.export_anyway_allowed ? (
            <div className="mt-1 text-xs font-medium uppercase tracking-[0.16em] text-amber-100/80">
              Warning-only export available
            </div>
          ) : null}
        </div>
      </div>

      <div className="mt-6 grid gap-3 md:grid-cols-3">
        <div className="rounded-3xl border border-white/10 bg-white/[0.04] px-4 py-4">
          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">
            Critical Issues
          </div>
          <div className="mt-2 text-2xl font-semibold">{blockingIssues.length}</div>
        </div>
        <div className="rounded-3xl border border-white/10 bg-white/[0.04] px-4 py-4">
          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">
            Warnings
          </div>
          <div className="mt-2 text-2xl font-semibold">{warnings.length}</div>
        </div>
        <div className="rounded-3xl border border-white/10 bg-white/[0.04] px-4 py-4">
          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">
            Chapters Reviewed
          </div>
          <div className="mt-2 text-2xl font-semibold">{chapters.length}</div>
        </div>
      </div>

      <div className="mt-6 grid gap-3 lg:grid-cols-2">
        {acxChecks.map((check) => (
          <div
            className={`rounded-3xl border px-4 py-4 ${checkTone(check.passed)}`}
            key={check.key}
          >
            <div className="text-xs font-semibold uppercase tracking-[0.18em] opacity-80">
              {check.passed ? "Pass" : "Fail"}
            </div>
            <div className="mt-2 text-sm font-semibold">{check.label}</div>
            <div className="mt-2 text-sm leading-6 opacity-90">{check.detail}</div>
          </div>
        ))}
      </div>

      {blockingIssues.length > 0 ? (
        <div className="mt-6 rounded-3xl border border-rose-300/20 bg-rose-500/10 px-4 py-4 text-sm text-rose-100">
          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-rose-200/80">
            Blocking Issues
          </div>
          <ul className="mt-3 space-y-2">
            {blockingIssues.slice(0, 6).map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {warnings.length > 0 ? (
        <div className="mt-4 rounded-3xl border border-amber-300/20 bg-amber-400/10 px-4 py-4 text-sm text-amber-100">
          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-amber-100/80">
            Non-Critical Warnings
          </div>
          <ul className="mt-3 space-y-2">
            {warnings.slice(0, 6).map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="mt-6 flex items-center justify-between gap-3">
        <div className="text-sm text-slate-400">
          {summary.ready
            ? "All chapters are export-ready."
            : "Review the chapter list before exporting."}
        </div>
        <button
          className="inline-flex items-center justify-center rounded-full border border-white/10 px-4 py-2 text-xs font-semibold uppercase tracking-[0.16em] text-slate-200 transition hover:border-white/20 hover:text-white"
          onClick={() => setDetailsOpen((current) => !current)}
          type="button"
        >
          {detailsOpen ? "Hide Chapter Details" : "Show Chapter Details"}
        </button>
      </div>

      {detailsOpen ? (
        <div className="mt-5 space-y-3">
          {chapters.map((chapter) => (
            <div
              className="rounded-3xl border border-white/10 bg-slate-950/55 px-4 py-4"
              key={chapter.id}
            >
              <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">
                    Chapter {chapter.number}
                  </div>
                  <div className="mt-2 text-lg font-semibold text-white">
                    {chapter.title ?? `Chapter ${chapter.number}`}
                  </div>
                  <div className="mt-2 text-sm text-slate-400">
                    Duration: {formatDuration(chapter.duration_seconds)}
                  </div>
                </div>
                <div className={`rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] ${gradeTone(chapter.grade)}`}>
                  Grade {chapter.grade}
                </div>
              </div>
              {(chapter.issues ?? []).length > 0 ? (
                <ul className="mt-4 space-y-2 text-sm leading-6 text-slate-300">
                  {(chapter.issues ?? []).map((issue) => (
                    <li key={`${chapter.id}-${issue}`}>{issue}</li>
                  ))}
                </ul>
              ) : (
                <div className="mt-4 text-sm text-slate-400">No major chapter-level issues detected.</div>
              )}
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

ExportQASummary.propTypes = {
  loading: PropTypes.bool,
  summary: PropTypes.shape({
    acx_checks: PropTypes.arrayOf(
      PropTypes.shape({
        critical: PropTypes.bool,
        detail: PropTypes.string,
        key: PropTypes.string,
        label: PropTypes.string,
        passed: PropTypes.bool,
      }),
    ),
    blocking_issues: PropTypes.arrayOf(PropTypes.string),
    chapters: PropTypes.arrayOf(
      PropTypes.shape({
        duration_seconds: PropTypes.number,
        grade: PropTypes.string,
        id: PropTypes.number,
        issues: PropTypes.arrayOf(PropTypes.string),
        number: PropTypes.number,
        title: PropTypes.string,
      }),
    ),
    export_anyway_allowed: PropTypes.bool,
    ready: PropTypes.bool,
    status_label: PropTypes.string,
    warnings: PropTypes.arrayOf(PropTypes.string),
  }),
};
