import PropTypes from "prop-types";
import React, { useState } from "react";
import { formatFileSize } from "./generationStatus";

export default function DownloadCard({ fileName, fileSizeBytes, formatLabel, url }) {
  const [copyStatus, setCopyStatus] = useState("idle");

  async function handleCopyLink() {
    if (!navigator.clipboard?.writeText) {
      setCopyStatus("unsupported");
      return;
    }

    try {
      await navigator.clipboard.writeText(new URL(url, window.location.origin).toString());
      setCopyStatus("copied");
    } catch (error) {
      setCopyStatus("error");
    }
  }

  return (
    <article className="rounded-3xl border border-white/10 bg-white/[0.04] p-4">
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.26em] text-emerald-200/75">
            Ready
          </div>
          <h3 className="mt-2 text-lg font-semibold text-white">{formatLabel}</h3>
          <p className="mt-1 text-sm text-slate-300">{fileName}</p>
          <p className="mt-3 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">
            {formatFileSize(fileSizeBytes)}
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          <a
            className="inline-flex items-center justify-center rounded-full border border-emerald-300/25 bg-emerald-400/10 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-emerald-100 transition hover:bg-emerald-400/20"
            download
            href={url}
          >
            Download
          </a>
          <button
            className="inline-flex items-center justify-center rounded-full border border-white/10 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-300 transition hover:border-white/20 hover:text-white"
            onClick={handleCopyLink}
            type="button"
          >
            {copyStatus === "copied"
              ? "Copied"
              : copyStatus === "unsupported"
                ? "Clipboard Off"
                : copyStatus === "error"
                  ? "Copy Failed"
                  : "Copy Link"}
          </button>
        </div>
      </div>
    </article>
  );
}

DownloadCard.propTypes = {
  fileName: PropTypes.string.isRequired,
  fileSizeBytes: PropTypes.number,
  formatLabel: PropTypes.string.isRequired,
  url: PropTypes.string.isRequired,
};
