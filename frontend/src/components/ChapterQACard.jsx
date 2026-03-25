import PropTypes from "prop-types";
import React, { useState } from "react";
import AudioPlayerQuick from "./AudioPlayerQuick";
import CheckResultsTable from "./CheckResultsTable";
import QAStatusBadge from "./QAStatusBadge";

function getReviewBadge(chapter) {
  if (chapter.manual_status === "approved") {
    return { status: "pass", text: "Approved" };
  }

  if (chapter.manual_status === "flagged") {
    return { status: "fail", text: "Flagged" };
  }

  if (chapter.overall_status === "warning" || chapter.overall_status === "fail") {
    return { status: "pending", text: "Pending Review" };
  }

  return { status: chapter.overall_status, text: chapter.overall_status };
}

function getManualSummary(chapter) {
  if (chapter.manual_status === "approved") {
    return `Approved by ${chapter.manual_reviewed_by ?? "Reviewer"}`;
  }

  if (chapter.manual_status === "flagged") {
    return `Flagged by ${chapter.manual_reviewed_by ?? "Reviewer"}`;
  }

  if (chapter.overall_status === "warning" || chapter.overall_status === "fail") {
    return "Awaiting manual QA";
  }

  return "Automatic QA passed";
}

function getGradeBadge(grade) {
  if (grade === "A" || grade === "B") {
    return { status: "pass", text: `Grade ${grade}` };
  }

  if (grade === "C") {
    return { status: "warning", text: "Grade C" };
  }

  if (grade === "F") {
    return { status: "fail", text: "Grade F" };
  }

  return null;
}

export default function ChapterQACard({ actionPending = false, chapter, onApprove, onFlag }) {
  const [expanded, setExpanded] = useState(false);
  const reviewBadge = getReviewBadge(chapter);
  const gradeBadge = getGradeBadge(chapter.qa_grade);
  const needsManualActions =
    chapter.manual_status === null &&
    (chapter.overall_status === "warning" || chapter.overall_status === "fail");

  return (
    <article
      className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm"
      data-chapter-qa={`${chapter.book_id}-${chapter.chapter_n}`}
    >
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">
                Chapter {chapter.chapter_n}
              </p>
              <h4 className="mt-1 text-lg font-semibold text-slate-950">
                {chapter.chapter_title ?? `Chapter ${chapter.chapter_n}`}
              </h4>
            </div>
            {gradeBadge ? (
              <QAStatusBadge label={gradeBadge.text} status={gradeBadge.status} />
            ) : null}
            <QAStatusBadge label={reviewBadge.text} status={reviewBadge.status} />
            <QAStatusBadge
              label={`Auto ${chapter.overall_status}`}
              status={chapter.overall_status}
            />
          </div>

          <p className="text-sm text-slate-600">{getManualSummary(chapter)}</p>
          {chapter.manual_notes ? (
            <p className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
              {chapter.manual_notes}
            </p>
          ) : null}
        </div>

        <div className="flex min-w-[12rem] flex-col gap-3">
          <AudioPlayerQuick
            audioUrl={chapter.audio_url}
            chapterName={chapter.chapter_title ?? `Chapter ${chapter.chapter_n}`}
          />
          {needsManualActions ? (
            <div className="flex flex-wrap gap-2">
              <button
                className="inline-flex items-center justify-center rounded-full border border-emerald-200 bg-emerald-50 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-emerald-700 transition hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={actionPending}
                onClick={() => onApprove(chapter)}
                type="button"
              >
                Approve
              </button>
              <button
                className="inline-flex items-center justify-center rounded-full border border-rose-200 bg-rose-50 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-rose-700 transition hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={actionPending}
                onClick={() => onFlag(chapter)}
                type="button"
              >
                Flag
              </button>
            </div>
          ) : null}
          <button
            className="inline-flex items-center justify-center rounded-full border border-slate-200 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-600 transition hover:border-slate-300 hover:text-slate-900"
            onClick={() => setExpanded((current) => !current)}
            type="button"
          >
            {expanded ? "Hide Checks" : "Show Checks"}
          </button>
        </div>
      </div>

      {expanded ? (
        <div className="mt-5">
          <CheckResultsTable checks={chapter.automatic_checks} />
        </div>
      ) : null}
    </article>
  );
}

ChapterQACard.propTypes = {
  actionPending: PropTypes.bool,
  chapter: PropTypes.shape({
    audio_url: PropTypes.string,
    automatic_checks: PropTypes.arrayOf(PropTypes.shape({
      message: PropTypes.string.isRequired,
      name: PropTypes.string.isRequired,
      status: PropTypes.oneOf(["pass", "warning", "fail"]).isRequired,
      value: PropTypes.number,
    })).isRequired,
    book_id: PropTypes.number.isRequired,
    chapter_n: PropTypes.number.isRequired,
    chapter_title: PropTypes.string,
    manual_notes: PropTypes.string,
    manual_reviewed_by: PropTypes.string,
    manual_status: PropTypes.oneOf(["approved", "flagged", null]),
    overall_status: PropTypes.oneOf(["pass", "warning", "fail"]).isRequired,
    qa_grade: PropTypes.oneOf(["A", "B", "C", "F"]),
  }).isRequired,
  onApprove: PropTypes.func.isRequired,
  onFlag: PropTypes.func.isRequired,
};
