import React from "react";

function statusTone(status) {
  if (status === "critical") {
    return {
      badge: "bg-rose-100 text-rose-800",
      border: "border-rose-200",
      fill: "from-rose-500 to-red-500",
    };
  }
  if (status === "warning") {
    return {
      badge: "bg-amber-100 text-amber-800",
      border: "border-amber-200",
      fill: "from-amber-500 to-orange-500",
    };
  }
  return {
    badge: "bg-emerald-100 text-emerald-800",
    border: "border-emerald-200",
    fill: "from-emerald-500 to-teal-500",
  };
}

function Gauge({ label, percent, status, statusLabel, valueLabel }) {
  const tone = statusTone(status);

  return (
    <div className={`rounded-3xl border bg-white p-5 shadow-sm ${tone.border}`}>
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold text-slate-900">{label}</div>
        <div className={`rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] ${tone.badge}`}>
          {statusLabel}
        </div>
      </div>
      <div className="mt-2 text-sm text-slate-500">{valueLabel}</div>
      <div className="mt-4 overflow-hidden rounded-full bg-slate-200">
        <div
          className={`h-3 rounded-full bg-gradient-to-r transition-all ${tone.fill}`}
          style={{ width: `${Math.min(Math.max(percent, 0), 100)}%` }}
        />
      </div>
    </div>
  );
}

export default function ResourceGauges({ modelStats, resources }) {
  const diskFree = resources?.disk_free_gb ?? 0;
  const diskStatus = diskFree < 2 ? "critical" : diskFree < 5 ? "warning" : "healthy";
  const memoryPercent = resources?.memory_used_percent ?? 0;
  const memoryStatus = memoryPercent >= 80 ? "critical" : memoryPercent >= 65 ? "warning" : "healthy";
  const chaptersSinceRestart = modelStats?.chapters_since_restart ?? modelStats?.chapters_generated ?? 0;
  const restartInterval = modelStats?.restart_interval ?? modelStats?.cooldown_threshold_chapters ?? 0;
  const chaptersRemaining = Math.max(
    restartInterval - chaptersSinceRestart,
    0,
  );
  const chunksRemaining = Math.max(
    (modelStats?.cooldown_threshold_chunks ?? 0) - (modelStats?.chunks_generated ?? 0),
    0,
  );
  const cooldownStatus = chaptersRemaining === 0
    ? "critical"
    : chaptersRemaining <= 5
      ? "warning"
      : "healthy";

  return (
    <section>
      <div className="mb-4">
        <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">
          System Resources
        </p>
        <h2 className="mt-2 text-2xl font-semibold text-slate-950">Disk, memory, and model cooldown headroom</h2>
      </div>

      <div className="grid gap-4 lg:grid-cols-4">
        <Gauge
          label="Disk"
          percent={resources?.disk_used_percent ?? 0}
          valueLabel={`${resources?.disk_free_gb ?? 0} GB free`}
          status={diskStatus}
          statusLabel={diskStatus}
        />
        <Gauge
          label="RAM"
          percent={memoryPercent}
          valueLabel={`${resources?.memory_used_mb ?? 0} / ${resources?.memory_total_mb ?? 0} MB`}
          status={memoryStatus}
          statusLabel={memoryStatus}
        />
        <Gauge
          label="Throughput"
          percent={Math.min((resources?.throughput_chapters_per_hour ?? 0) * 10, 100)}
          valueLabel={`${resources?.throughput_chapters_per_hour ?? 0} chapters/hour`}
          status={(resources?.throughput_chapters_per_hour ?? 0) > 0 ? "healthy" : "warning"}
          statusLabel={(resources?.throughput_chapters_per_hour ?? 0) > 0 ? "active" : "idle"}
        />
        <div className={`rounded-3xl border bg-white p-5 shadow-sm ${statusTone(cooldownStatus).border}`}>
          <div className="flex items-center justify-between">
            <div className="text-sm font-semibold text-slate-900">Model cooldown</div>
            <div className={`rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] ${statusTone(cooldownStatus).badge}`}>
              {cooldownStatus}
            </div>
          </div>
          <div className="mt-3 text-3xl font-semibold text-slate-950">{chaptersRemaining}</div>
          <div className="mt-1 text-sm text-slate-600">chapters until reload threshold</div>
          <div className="mt-4 flex items-center justify-between text-sm text-slate-500">
            <span>{modelStats?.reload_count ?? 0} reloads</span>
            <span>{chunksRemaining} chunks left</span>
          </div>
          <div className="mt-2 text-sm text-slate-500">
            Output size {resources?.output_directory_size_gb ?? 0} GB
          </div>
        </div>
      </div>
    </section>
  );
}
