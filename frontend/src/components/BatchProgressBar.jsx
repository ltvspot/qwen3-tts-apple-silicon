import React from "react";

function formatPercent(value) {
  return `${value.toFixed(1)}%`;
}

export default function BatchProgressBar({ completed, total }) {
  const safeTotal = total > 0 ? total : 1;
  const percent = Math.min((completed / safeTotal) * 100, 100);

  return (
    <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">
            Catalog Progress
          </p>
          <h2 className="mt-2 text-2xl font-semibold text-slate-950">
            Production readiness across the full catalog
          </h2>
        </div>
        <div className="text-sm font-medium text-slate-600">
          {completed} / {total} books
        </div>
      </div>

      <div className="mt-5 overflow-hidden rounded-full bg-slate-200">
        <div
          className="h-4 rounded-full bg-gradient-to-r from-emerald-500 via-teal-500 to-sky-500 transition-all"
          style={{ width: `${percent}%` }}
        />
      </div>

      <div className="mt-3 text-sm text-slate-600">{formatPercent(percent)} production-ready</div>
    </section>
  );
}
