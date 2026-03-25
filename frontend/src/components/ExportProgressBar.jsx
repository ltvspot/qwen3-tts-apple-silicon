import PropTypes from "prop-types";
import React from "react";

export default function ExportProgressBar({ label = "Export in progress..." }) {
  return (
    <div className="rounded-3xl border border-sky-300/20 bg-sky-400/10 p-4">
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.26em] text-sky-200/75">
            Processing
          </div>
          <p className="mt-2 text-sm text-sky-50">{label}</p>
        </div>
        <div className="flex items-center gap-2 rounded-full border border-sky-300/20 bg-slate-950/50 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-sky-100">
          <span
            aria-hidden="true"
            className="h-2.5 w-2.5 animate-pulse rounded-full bg-sky-300"
          />
          Running
        </div>
      </div>

      <div
        aria-label={label}
        aria-valuetext={label}
        className="mt-4 h-3 overflow-hidden rounded-full bg-slate-950/70"
        role="progressbar"
      >
        <div className="h-full w-2/3 animate-pulse rounded-full bg-[linear-gradient(90deg,rgba(125,211,252,0.18),rgba(125,211,252,0.9),rgba(251,191,36,0.82))]" />
      </div>
    </div>
  );
}

ExportProgressBar.propTypes = {
  label: PropTypes.string,
};
