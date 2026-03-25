import PropTypes from "prop-types";
import React from "react";
import ProgressHeartbeat from "./ProgressHeartbeat";

function buildStageLabel({
  currentChapterN,
  currentFormat,
  currentStage,
  totalChapters,
}) {
  if (currentStage) {
    return currentStage;
  }

  if (currentFormat) {
    const chapterSuffix = currentChapterN && totalChapters
      ? ` (chapter ${currentChapterN}/${totalChapters})`
      : "";
    return `Exporting ${currentFormat.toUpperCase()}${chapterSuffix}`;
  }

  return "Exporting audiobook package...";
}

export default function ExportProgressBar({
  currentChapterN = null,
  currentFormat = null,
  currentStage = null,
  label = "Building the audiobook package and validating output files.",
  progressPercent = null,
  startTime = null,
  totalChapters = null,
}) {
  const stageLabel = buildStageLabel({
    currentChapterN,
    currentFormat,
    currentStage,
    totalChapters,
  });

  return (
    <div className="rounded-3xl border border-sky-300/20 bg-sky-400/10 p-4">
      <div>
        <div className="text-xs font-semibold uppercase tracking-[0.26em] text-sky-200/75">
          Processing
        </div>
        <p className="mt-2 text-sm text-sky-50">{label}</p>
      </div>

      <div className="mt-4">
        <ProgressHeartbeat
          isActive
          progressPercent={progressPercent}
          showETA={null}
          size="sm"
          stage={stageLabel}
          startTime={startTime}
        />
      </div>
    </div>
  );
}

ExportProgressBar.propTypes = {
  currentChapterN: PropTypes.number,
  currentFormat: PropTypes.string,
  currentStage: PropTypes.string,
  label: PropTypes.string,
  progressPercent: PropTypes.number,
  startTime: PropTypes.oneOfType([
    PropTypes.instanceOf(Date),
    PropTypes.number,
    PropTypes.string,
  ]),
  totalChapters: PropTypes.number,
};
