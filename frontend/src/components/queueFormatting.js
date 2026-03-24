export const QUEUE_STATUS_META = {
  queued: {
    badgeClass: "border-amber-300/70 bg-amber-50 text-amber-900",
    label: "Queued",
  },
  generating: {
    badgeClass: "border-sky-300/70 bg-sky-50 text-sky-900",
    label: "Generating",
  },
  paused: {
    badgeClass: "border-slate-300/80 bg-slate-100 text-slate-700",
    label: "Paused",
  },
  completed: {
    badgeClass: "border-emerald-300/70 bg-emerald-50 text-emerald-900",
    label: "Completed",
  },
  error: {
    badgeClass: "border-orange-300/70 bg-orange-50 text-orange-900",
    label: "Error",
  },
};

export function formatQueueDuration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) {
    return "Calculating";
  }

  const totalSeconds = Math.max(0, Math.round(seconds));
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const remainingSeconds = totalSeconds % 60;

  if (days > 0) {
    return `${days}d ${hours}h ${minutes}m`;
  }
  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  if (minutes > 0) {
    return `${minutes}m`;
  }
  return `${remainingSeconds}s`;
}

export function formatAverageSeconds(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) {
    return "Awaiting samples";
  }

  return `${seconds.toFixed(1).replace(/\.0$/, "")}s / chapter`;
}

export function statusMeta(status) {
  return QUEUE_STATUS_META[status] ?? QUEUE_STATUS_META.queued;
}
