import React from "react";

function Gauge({ label, valueLabel, percent }) {
  return (
    <div className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold text-slate-900">{label}</div>
        <div className="text-sm text-slate-500">{valueLabel}</div>
      </div>
      <div className="mt-4 overflow-hidden rounded-full bg-slate-200">
        <div
          className="h-3 rounded-full bg-gradient-to-r from-amber-500 to-orange-500 transition-all"
          style={{ width: `${Math.min(Math.max(percent, 0), 100)}%` }}
        />
      </div>
    </div>
  );
}

export default function ResourceGauges({ modelStats, resources }) {
  const chaptersRemaining = Math.max(
    (modelStats?.cooldown_threshold_chapters ?? 0) - (modelStats?.chapters_generated ?? 0),
    0,
  );
  const chunksRemaining = Math.max(
    (modelStats?.cooldown_threshold_chunks ?? 0) - (modelStats?.chunks_generated ?? 0),
    0,
  );

  return (
    <section>
      <div className="mb-4">
        <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">
          System Resources
        </p>
        <h2 className="mt-2 text-2xl font-semibold text-slate-950">Disk, memory, and model cooldown headroom</h2>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Gauge
          label="Disk"
          percent={resources?.disk_used_percent ?? 0}
          valueLabel={`${resources?.disk_free_gb ?? 0} GB free`}
        />
        <Gauge
          label="RAM"
          percent={resources?.memory_used_percent ?? 0}
          valueLabel={`${resources?.memory_used_mb ?? 0} / ${resources?.memory_total_mb ?? 0} MB`}
        />
        <div className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="text-sm font-semibold text-slate-900">Model cooldown</div>
          <div className="mt-3 text-3xl font-semibold text-slate-950">{chaptersRemaining}</div>
          <div className="mt-1 text-sm text-slate-600">chapters until reload threshold</div>
          <div className="mt-4 flex items-center justify-between text-sm text-slate-500">
            <span>{modelStats?.reload_count ?? 0} reloads</span>
            <span>{chunksRemaining} chunks left</span>
          </div>
        </div>
      </div>
    </section>
  );
}
