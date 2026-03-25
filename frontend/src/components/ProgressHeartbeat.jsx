import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";

function clampPercent(value) {
  return Math.max(0, Math.min(value, 100));
}

function resolveStartTimeMs(startTime) {
  if (!startTime) {
    return null;
  }

  if (startTime instanceof Date) {
    const value = startTime.getTime();
    return Number.isNaN(value) ? null : value;
  }

  if (typeof startTime === "number") {
    return Number.isFinite(startTime) ? startTime : null;
  }

  const parsed = new Date(startTime).getTime();
  return Number.isNaN(parsed) ? null : parsed;
}

function getElapsedSeconds(startTime) {
  const startTimeMs = resolveStartTimeMs(startTime);
  if (startTimeMs === null) {
    return 0;
  }
  return Math.max(Math.floor((Date.now() - startTimeMs) / 1000), 0);
}

export function formatHeartbeatDuration(totalSeconds) {
  const safeSeconds = Math.max(Number(totalSeconds) || 0, 0);
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60);
  const seconds = safeSeconds % 60;

  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }

  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

const SIZE_STYLES = {
  lg: {
    body: "p-5",
    eyebrow: "text-sm",
    meta: "text-sm",
    stage: "text-base",
    track: "h-3.5",
  },
  md: {
    body: "p-4",
    eyebrow: "text-xs",
    meta: "text-xs",
    stage: "text-sm",
    track: "h-3",
  },
  sm: {
    body: "p-3.5",
    eyebrow: "text-[11px]",
    meta: "text-[11px]",
    stage: "text-sm",
    track: "h-2.5",
  },
};

export default function ProgressHeartbeat({
  isActive,
  progressPercent = null,
  showElapsed = true,
  showETA = null,
  size = "md",
  stage,
  startTime = null,
}) {
  const [elapsedSeconds, setElapsedSeconds] = useState(() => getElapsedSeconds(startTime));

  useEffect(() => {
    setElapsedSeconds(getElapsedSeconds(startTime));
  }, [startTime]);

  useEffect(() => {
    if (!isActive) {
      return undefined;
    }

    const intervalId = window.setInterval(() => {
      setElapsedSeconds(getElapsedSeconds(startTime));
    }, 1000);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [isActive, startTime]);

  const styles = SIZE_STYLES[size] ?? SIZE_STYLES.md;
  const determinateProgress = useMemo(() => {
    if (typeof progressPercent !== "number" || Number.isNaN(progressPercent)) {
      return null;
    }
    return clampPercent(progressPercent);
  }, [progressPercent]);

  return (
    <div className={`rounded-[1.75rem] border border-white/10 bg-slate-950/45 text-white shadow-xl shadow-slate-950/20 transition-all duration-300 ${styles.body}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className={`flex items-center gap-2 font-semibold uppercase tracking-[0.22em] text-sky-200/80 ${styles.eyebrow}`}>
            <span
              aria-hidden="true"
              className={`h-2.5 w-2.5 rounded-full ${isActive ? "animate-pulse bg-emerald-300" : "bg-slate-500"}`}
            />
            Heartbeat
          </div>
          <div className={`mt-2 font-semibold text-white ${styles.stage}`}>
            {stage || (isActive ? "Working..." : "Idle")}
          </div>
        </div>

        <div className={`flex flex-wrap gap-2 text-slate-300 ${styles.meta}`}>
          {showElapsed ? (
            <span className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1">
              Elapsed: {formatHeartbeatDuration(elapsedSeconds)}
            </span>
          ) : null}
          {showETA ? (
            <span className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1">
              {showETA}
            </span>
          ) : null}
          {determinateProgress !== null ? (
            <span className="rounded-full border border-sky-300/20 bg-sky-400/10 px-3 py-1 font-semibold text-sky-100">
              {Math.round(determinateProgress)}%
            </span>
          ) : (
            <span className="rounded-full border border-amber-300/20 bg-amber-400/10 px-3 py-1 font-semibold text-amber-100">
              Live
            </span>
          )}
        </div>
      </div>

      <div
        aria-label={stage || "Progress"}
        aria-valuemax={determinateProgress !== null ? 100 : undefined}
        aria-valuemin={determinateProgress !== null ? 0 : undefined}
        aria-valuenow={determinateProgress !== null ? Math.round(determinateProgress) : undefined}
        aria-valuetext={determinateProgress !== null ? `${Math.round(determinateProgress)} percent` : stage || "In progress"}
        className={`mt-4 overflow-hidden rounded-full bg-white/10 ${styles.track}`}
        role="progressbar"
      >
        {determinateProgress !== null ? (
          <div
            className="h-full rounded-full bg-[linear-gradient(90deg,rgba(56,189,248,0.72)_0%,rgba(251,191,36,0.88)_100%)] transition-[width] duration-500"
            style={{ width: `${determinateProgress}%` }}
          />
        ) : (
          <div className="h-full w-2/5 animate-[pulse_1.4s_ease-in-out_infinite] rounded-full bg-[linear-gradient(90deg,rgba(56,189,248,0.2)_0%,rgba(56,189,248,0.9)_50%,rgba(251,191,36,0.82)_100%)]" />
        )}
      </div>
    </div>
  );
}

ProgressHeartbeat.propTypes = {
  isActive: PropTypes.bool.isRequired,
  progressPercent: PropTypes.number,
  showElapsed: PropTypes.bool,
  showETA: PropTypes.string,
  size: PropTypes.oneOf(["sm", "md", "lg"]),
  stage: PropTypes.string,
  startTime: PropTypes.oneOfType([
    PropTypes.instanceOf(Date),
    PropTypes.number,
    PropTypes.string,
  ]),
};
