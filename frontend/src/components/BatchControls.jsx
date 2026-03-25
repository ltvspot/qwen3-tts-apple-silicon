import React from "react";

function ActionButton({ disabled, label, onClick, tone = "dark" }) {
  const toneClass = {
    dark: "bg-slate-950 text-white hover:bg-slate-800",
    danger: "bg-rose-600 text-white hover:bg-rose-500",
    light: "bg-white text-slate-900 ring-1 ring-slate-200 hover:bg-slate-50",
  }[tone];

  return (
    <button
      className={`rounded-full px-4 py-2 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-60 ${toneClass}`}
      disabled={disabled}
      onClick={onClick}
      type="button"
    >
      {label}
    </button>
  );
}

export default function BatchControls({ actionLoading, batch, exportProgress, onAction }) {
  const batchStatus = batch?.status ?? "idle";
  const exportStatus = exportProgress?.status ?? "idle";
  const activeBatch = batchStatus === "running" || batchStatus === "paused";
  const queuedBooks = batch
    ? Math.max(
      batch.total_books - batch.books_completed - batch.books_failed - batch.books_skipped - batch.books_in_progress,
      0,
    )
    : 0;

  return (
    <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="max-w-2xl">
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">
            Batch Status
          </p>
          <h2 className="mt-2 text-2xl font-semibold text-slate-950">
            {batchStatus === "idle" ? "No active generation batch" : `Batch ${batchStatus}`}
          </h2>
          <div className="mt-3 space-y-1 text-sm text-slate-600">
            <div>
              Current book: {batch?.current_book_title ?? "Waiting for a catalog batch to start."}
            </div>
            <div>
              ETA: {batch?.estimated_completion ? new Date(batch.estimated_completion).toLocaleString() : "Pending"}
            </div>
            <div>
              Speed: {batch?.avg_seconds_per_book ?? 0}s/book average, {queuedBooks} queued behind the active slot
            </div>
            {batch?.summary ? (
              <div>
                Summary: {batch.summary}
              </div>
            ) : null}
            <div>
              Export batch: {exportStatus}
              {exportProgress ? ` (${exportProgress.completed}/${exportProgress.queued} completed)` : ""}
            </div>
          </div>
        </div>

        <div className="flex flex-wrap gap-3">
          {!activeBatch && (
            <ActionButton
              disabled={actionLoading}
              label="Start Batch"
              onClick={() => onAction("start")}
            />
          )}
          {batchStatus === "running" && (
            <ActionButton
              disabled={actionLoading}
              label="Pause Batch"
              onClick={() => onAction("pause")}
              tone="light"
            />
          )}
          {batchStatus === "paused" && (
            <ActionButton
              disabled={actionLoading}
              label="Resume Batch"
              onClick={() => onAction("resume")}
              tone="light"
            />
          )}
          {activeBatch && (
            <ActionButton
              disabled={actionLoading}
              label="Cancel Batch"
              onClick={() => onAction("cancel")}
              tone="danger"
            />
          )}
          <ActionButton
            disabled={actionLoading}
            label="Force Model Reload"
            onClick={() => onAction("reload_model")}
            tone="light"
          />
        </div>
      </div>

      {batch?.pause_reason && (
        <div className="mt-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          {batch.pause_reason}
        </div>
      )}
    </section>
  );
}
