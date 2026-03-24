import React from "react";

const STATUS_COLORS = {
  not_started: {
    background: "bg-stone-500/20",
    label: "Not Started",
    text: "text-stone-100",
  },
  parsed: {
    background: "bg-sky-500/20",
    label: "Parsed",
    text: "text-sky-100",
  },
  generating: {
    background: "bg-amber-500/20",
    label: "Generating",
    text: "text-amber-100",
  },
  generated: {
    background: "bg-emerald-500/20",
    label: "Generated",
    text: "text-emerald-100",
  },
  qa: {
    background: "bg-fuchsia-500/20",
    label: "QA Review",
    text: "text-fuchsia-100",
  },
  qa_approved: {
    background: "bg-teal-500/20",
    label: "QA Approved",
    text: "text-teal-100",
  },
  exported: {
    background: "bg-yellow-500/20",
    label: "Exported",
    text: "text-yellow-100",
  },
};

const TWO_LINE_CLAMP_STYLE = {
  WebkitBoxOrient: "vertical",
  WebkitLineClamp: 2,
  display: "-webkit-box",
  overflow: "hidden",
};

const ONE_LINE_CLAMP_STYLE = {
  WebkitBoxOrient: "vertical",
  WebkitLineClamp: 1,
  display: "-webkit-box",
  overflow: "hidden",
};

export default function BookCard({ book, onClick }) {
  const statusKey = book.status ?? "not_started";
  const statusColor = STATUS_COLORS[statusKey] ?? STATUS_COLORS.not_started;

  return (
    <button
      aria-label={`Open ${book.title}`}
      className="group w-full overflow-hidden rounded-[1.75rem] border border-white/10 bg-white/[0.04] text-left transition duration-200 hover:-translate-y-1 hover:border-amber-300/35 hover:shadow-2xl hover:shadow-amber-500/10 focus:outline-none focus:ring-2 focus:ring-amber-300/40"
      data-book-id={book.id}
      onClick={onClick}
      type="button"
    >
      <div className="relative flex h-52 items-center justify-center overflow-hidden bg-[radial-gradient(circle_at_top,_rgba(251,191,36,0.28),_transparent_42%),linear-gradient(145deg,#f59e0b_0%,#7c2d12_38%,#111827_100%)]">
        <div className="absolute inset-0 bg-[linear-gradient(transparent_0,_transparent_88%,rgba(255,255,255,0.09)_88%,rgba(255,255,255,0.09)_90%,transparent_90%),linear-gradient(90deg,transparent_0,transparent_92%,rgba(255,255,255,0.08)_92%,rgba(255,255,255,0.08)_94%,transparent_94%)] bg-[length:100%_30px,26px_100%] opacity-20 transition group-hover:opacity-30" />
        <div className="absolute inset-x-6 top-6 flex items-center justify-between text-xs font-semibold uppercase tracking-[0.28em] text-amber-100/90">
          <span>Book {book.id}</span>
          <span>{book.trim_size ?? "Trim TBD"}</span>
        </div>
        <div className="relative z-10 px-6 text-center">
          <div
            className="text-xl font-semibold leading-tight text-white"
            style={TWO_LINE_CLAMP_STYLE}
          >
            {book.title}
          </div>
          <div className="mt-3 text-xs uppercase tracking-[0.28em] text-amber-50/80">
            Alexandria Edition
          </div>
        </div>
      </div>

      <div className="p-5">
        <div className="mb-4 flex items-start justify-between gap-3">
          <span
            className={`inline-flex rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.22em] ${statusColor.background} ${statusColor.text}`}
          >
            {statusColor.label}
          </span>
          <span className="text-xs font-medium uppercase tracking-[0.22em] text-slate-400">
            {book.narrator}
          </span>
        </div>

        <h3
          className="text-xl font-semibold leading-tight text-white transition group-hover:text-amber-100"
          data-testid={`book-card-title-${book.id}`}
          style={TWO_LINE_CLAMP_STYLE}
        >
          {book.title}
        </h3>

        {book.subtitle ? (
          <p
            className="mt-2 text-sm text-slate-300/90"
            style={ONE_LINE_CLAMP_STYLE}
          >
            {book.subtitle}
          </p>
        ) : (
          <div className="mt-2 text-sm text-slate-500">No subtitle indexed yet.</div>
        )}

        <p className="mt-4 text-sm text-slate-300">
          by <span className="font-medium text-white">{book.author}</span>
        </p>

        <div className="mt-5 grid grid-cols-3 gap-3 rounded-2xl border border-white/8 bg-slate-950/35 p-3 text-xs text-slate-300">
          <div>
            <div className="font-semibold uppercase tracking-[0.22em] text-slate-500">
              Pages
            </div>
            <div className="mt-2 text-base font-semibold text-white">
              {book.page_count ?? "?"}
            </div>
          </div>
          <div>
            <div className="font-semibold uppercase tracking-[0.22em] text-slate-500">
              Chapters
            </div>
            <div className="mt-2 text-base font-semibold text-white">
              {book.chapter_count}
            </div>
          </div>
          <div>
            <div className="font-semibold uppercase tracking-[0.22em] text-slate-500">
              Trim
            </div>
            <div className="mt-2 text-base font-semibold text-white">
              {book.trim_size ?? "TBD"}
            </div>
          </div>
        </div>
      </div>
    </button>
  );
}
