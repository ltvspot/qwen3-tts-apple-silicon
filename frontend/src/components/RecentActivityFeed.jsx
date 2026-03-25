import React from "react";

function describeStatus(book) {
  if (book.export_status === "completed" || book.status === "exported") {
    return { label: "Exported", tone: "text-emerald-700" };
  }
  if (book.generation_status === "error") {
    return { label: "Generation error", tone: "text-rose-700" };
  }
  if (book.status === "generated") {
    return { label: "Generated", tone: "text-sky-700" };
  }
  if (book.status === "parsed") {
    return { label: "Parsed", tone: "text-amber-700" };
  }
  return { label: "Indexed", tone: "text-slate-600" };
}

export default function RecentActivityFeed({ books }) {
  return (
    <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="mb-4">
        <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">
          Recent Activity
        </p>
        <h2 className="mt-2 text-2xl font-semibold text-slate-950">Last 20 catalog updates</h2>
      </div>

      <div className="space-y-3">
        {books.length === 0 && (
          <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-500">
            No recent catalog activity yet.
          </div>
        )}

        {books.map((book) => {
          const status = describeStatus(book);

          return (
            <div
              key={book.id}
              className="flex flex-col gap-2 rounded-2xl border border-slate-200 px-4 py-3 md:flex-row md:items-center md:justify-between"
            >
              <div>
                <div className="font-semibold text-slate-900">{book.title}</div>
                <div className="text-sm text-slate-500">{book.author}</div>
              </div>
              <div className="text-right">
                <div className={`text-sm font-semibold ${status.tone}`}>{status.label}</div>
                <div className="text-xs text-slate-500">
                  {book.updated_at ? new Date(book.updated_at).toLocaleString() : "No update timestamp"}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
