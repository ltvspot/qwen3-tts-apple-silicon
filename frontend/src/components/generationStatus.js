export const GENERATION_STATUS_META = {
  completed: {
    accentClass: "border-emerald-300/30 bg-emerald-400/10 text-emerald-100",
    colorClass: "text-emerald-300",
    icon: "✓",
    label: "Completed",
    tooltip: "Chapter audio is ready.",
  },
  error: {
    accentClass: "border-amber-300/30 bg-amber-400/10 text-amber-100",
    colorClass: "text-amber-300",
    icon: "⚠",
    label: "Error",
    tooltip: "Generation failed.",
  },
  generating: {
    accentClass: "border-sky-300/30 bg-sky-400/10 text-sky-100",
    colorClass: "text-sky-300",
    icon: "⏳",
    label: "Generating",
    tooltip: "Generation is in progress.",
  },
  pending: {
    accentClass: "border-white/10 bg-white/[0.04] text-slate-300",
    colorClass: "text-slate-400",
    icon: "⏱",
    label: "Pending",
    tooltip: "Generation has not started.",
  },
};

export function mapChapterGenerationState(status) {
  if (status === "generated") {
    return "completed";
  }

  if (status === "failed" || status === "generated_no_qa") {
    return "error";
  }

  if (status === "completed" || status === "error" || status === "generating") {
    return status;
  }

  return "pending";
}

export function formatCompactDuration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) {
    return null;
  }

  const totalSeconds = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(totalSeconds / 60);
  const remainingSeconds = totalSeconds % 60;

  if (minutes === 0) {
    return `${remainingSeconds}s`;
  }

  return `${minutes}m ${remainingSeconds}s`;
}

export function formatClock(seconds) {
  if (!seconds || Number.isNaN(seconds)) {
    return "0:00";
  }

  const minutes = Math.floor(seconds / 60);
  const remainder = Math.floor(seconds % 60);
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

export function formatDetailedDuration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) {
    return "0s";
  }

  if (seconds < 60) {
    return `${seconds.toFixed(1).replace(/\.0$/, "")}s`;
  }

  const totalSeconds = Math.round(seconds);
  const minutes = Math.floor(totalSeconds / 60);
  const remainingSeconds = totalSeconds % 60;
  return `${minutes}m ${String(remainingSeconds).padStart(2, "0")}s`;
}

export function formatEta(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) {
    return "Calculating ETA";
  }

  if (seconds < 60) {
    return `${Math.max(1, Math.round(seconds))}s remaining`;
  }

  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.round(seconds % 60);
  return `${minutes}m ${String(remainingSeconds).padStart(2, "0")}s remaining`;
}

export function formatFileSize(bytes) {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) {
    return "Unknown size";
  }

  if (bytes < 1024) {
    return `${bytes} B`;
  }

  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }

  if (bytes < 1024 * 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

export function getChapterLabel(chapter) {
  if (chapter.type === "opening_credits") {
    return "Opening Credits";
  }

  if (chapter.type === "closing_credits") {
    return "Closing Credits";
  }

  if (chapter.type === "introduction") {
    return chapter.title ? `Introduction: ${chapter.title}` : "Introduction";
  }

  return chapter.title ? `Chapter ${chapter.number}: ${chapter.title}` : `Chapter ${chapter.number}`;
}
