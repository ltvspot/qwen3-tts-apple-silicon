import React from "react";
import PropTypes from "prop-types";
import { formatQueueDuration } from "./queueFormatting";

const cards = [
  { key: "totalBooks", label: "Books In Queue", accentClass: "bg-sky-50 text-sky-900" },
  { key: "totalChapters", label: "Chapters Remaining", accentClass: "bg-amber-50 text-amber-900" },
  { key: "estimatedTime", label: "Estimated Completion", accentClass: "bg-emerald-50 text-emerald-900" },
  { key: "activeJobs", label: "Active Jobs", accentClass: "bg-slate-100 text-slate-800" },
];

export default function QueueStats({ stats }) {
  const values = {
    totalBooks: stats.total_books_in_queue,
    totalChapters: stats.total_chapters,
    estimatedTime: formatQueueDuration(stats.estimated_total_time_seconds),
    activeJobs: stats.active_job_count,
  };

  return (
    <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      {cards.map((card) => (
        <article
          key={card.key}
          className="rounded-[1.75rem] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/60"
        >
          <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-slate-500">
            {card.label}
          </div>
          <div className={`mt-4 inline-flex rounded-full px-3 py-1 text-xs font-semibold ${card.accentClass}`}>
            Snapshot
          </div>
          <div className="mt-5 text-3xl font-semibold text-slate-950">{values[card.key]}</div>
        </article>
      ))}
    </section>
  );
}

QueueStats.propTypes = {
  stats: PropTypes.shape({
    active_job_count: PropTypes.number.isRequired,
    estimated_total_time_seconds: PropTypes.number,
    total_books_in_queue: PropTypes.number.isRequired,
    total_chapters: PropTypes.number.isRequired,
  }).isRequired,
};
