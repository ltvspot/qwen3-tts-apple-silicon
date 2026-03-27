import React, { useEffect, useState } from "react";
import AppShell from "../components/AppShell";

async function parseResponse(response, fallbackMessage) {
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail ?? fallbackMessage);
  }
  return response.json();
}

function formatEta(value) {
  if (!value) {
    return "Pending";
  }
  return new Date(value).toLocaleString();
}

export default function BatchProduction() {
  const [batch, setBatch] = useState(null);
  const [errorMessage, setErrorMessage] = useState("");
  const [flashMessage, setFlashMessage] = useState("");
  const [loadingAction, setLoadingAction] = useState(false);
  const [modelStatus, setModelStatus] = useState(null);
  const [resources, setResources] = useState(null);

  function toneClass(status) {
    if (status === "critical") {
      return "border-rose-200 bg-rose-50 text-rose-800";
    }
    if (status === "warning") {
      return "border-amber-200 bg-amber-50 text-amber-800";
    }
    return "border-emerald-200 bg-emerald-50 text-emerald-800";
  }

  function resourceTone() {
    if ((resources?.disk_free_gb ?? 0) < 2 || (resources?.memory_used_percent ?? 0) >= 80) {
      return "critical";
    }
    if ((resources?.disk_free_gb ?? 0) < 5 || (resources?.memory_used_percent ?? 0) >= 65) {
      return "warning";
    }
    return "healthy";
  }

  function modelTone() {
    const chaptersRemaining = Math.max(
      (modelStatus?.restart_interval ?? 0) - (modelStatus?.chapters_since_restart ?? 0),
      0,
    );
    if (!modelStatus?.model_loaded || chaptersRemaining === 0) {
      return "critical";
    }
    if (chaptersRemaining <= 5) {
      return "warning";
    }
    return "healthy";
  }

  async function loadBatch() {
    try {
      const [batchResponse, resourceResponse, modelResponse] = await Promise.all([
        fetch("/api/batch/active"),
        fetch("/api/system/resources"),
        fetch("/api/system/model-status"),
      ]);
      const [payload, resourcePayload, modelPayload] = await Promise.all([
        parseResponse(batchResponse, "Failed to load batch production state."),
        parseResponse(resourceResponse, "Failed to load system resources."),
        parseResponse(modelResponse, "Failed to load model status."),
      ]);
      setBatch(payload);
      setResources(resourcePayload);
      setModelStatus(modelPayload);
      setErrorMessage("");
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Failed to load batch production state.");
    }
  }

  useEffect(() => {
    document.title = "Batch Production | Alexandria Audiobook Narrator";
    void loadBatch();
    const intervalId = window.setInterval(() => {
      void loadBatch();
    }, 5000);
    return () => {
      window.clearInterval(intervalId);
    };
  }, []);

  async function performAction(action) {
    setLoadingAction(true);
    setFlashMessage("");
    setErrorMessage("");
    try {
      let response;
      if (action === "start") {
        response = await fetch("/api/batch/start", {
          body: JSON.stringify({ priority: "normal", skip_already_exported: true }),
          headers: { "Content-Type": "application/json" },
          method: "POST",
        });
      } else {
        const batchId = batch?.batch_id;
        if (!batchId) {
          throw new Error("No active batch is available for that action.");
        }
        response = await fetch(`/api/batch/${batchId}/${action}`, {
          body: action === "pause" ? JSON.stringify({ reason: "Paused from Batch Production." }) : undefined,
          headers: action === "pause" ? { "Content-Type": "application/json" } : undefined,
          method: "POST",
        });
      }
      await parseResponse(response, "Batch action failed.");
      setFlashMessage(
        action === "start"
          ? "Started a new production batch."
          : `${action[0].toUpperCase()}${action.slice(1)} request applied.`,
      );
      await loadBatch();
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Batch action failed.");
    } finally {
      setLoadingAction(false);
    }
  }

  return (
    <AppShell
      title="Batch Production"
      description="Run catalog-scale production sequentially, watch live QA readiness, and control the active batch without leaving the dashboard."
    >
      <div className="space-y-6">
        {flashMessage ? (
          <div className="rounded-[1.75rem] border border-emerald-200 bg-emerald-50 px-5 py-4 text-sm text-emerald-800">
            {flashMessage}
          </div>
        ) : null}
        {errorMessage ? (
          <div className="rounded-[1.75rem] border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700">
            {errorMessage}
          </div>
        ) : null}

        <section className="rounded-[2rem] border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">Production Queue</div>
              <h2 className="mt-2 text-2xl font-semibold text-slate-950">
                {batch ? `Batch ${batch.status}` : "No active batch"}
              </h2>
              <p className="mt-2 text-sm text-slate-600">
                Current book: {batch?.current_book_title ?? "Waiting for a new run."}
              </p>
              <p className="mt-1 text-sm text-slate-500">
                ETA: {formatEta(batch?.estimated_completion)}
              </p>
            </div>
            <div className="flex flex-wrap gap-3">
              <button
                className="rounded-full bg-slate-950 px-5 py-3 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
                disabled={loadingAction}
                onClick={() => {
                  void performAction("start");
                }}
                type="button"
              >
                Start Batch
              </button>
              <button
                className="rounded-full border border-slate-300 px-5 py-3 text-sm font-semibold text-slate-700 transition hover:border-slate-950 hover:text-slate-950 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={loadingAction || !batch?.batch_id || batch?.status !== "running"}
                onClick={() => {
                  void performAction("pause");
                }}
                type="button"
              >
                Pause
              </button>
              <button
                className="rounded-full border border-slate-300 px-5 py-3 text-sm font-semibold text-slate-700 transition hover:border-slate-950 hover:text-slate-950 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={loadingAction || !batch?.batch_id || batch?.status !== "paused"}
                onClick={() => {
                  void performAction("resume");
                }}
                type="button"
              >
                Resume
              </button>
              <button
                className="rounded-full border border-rose-300 px-5 py-3 text-sm font-semibold text-rose-700 transition hover:border-rose-500 hover:text-rose-800 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={loadingAction || !batch?.batch_id}
                onClick={() => {
                  void performAction("cancel");
                }}
                type="button"
              >
                Cancel
              </button>
            </div>
          </div>

          <div className="mt-6 grid gap-4 md:grid-cols-4">
            <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Completed</div>
              <div className="mt-2 text-3xl font-semibold text-slate-950">{batch?.books_completed ?? 0}</div>
            </div>
            <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Failed</div>
              <div className="mt-2 text-3xl font-semibold text-slate-950">{batch?.books_failed ?? 0}</div>
            </div>
            <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Remaining</div>
              <div className="mt-2 text-3xl font-semibold text-slate-950">{batch?.books_remaining ?? 0}</div>
            </div>
            <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Progress</div>
              <div className="mt-2 text-3xl font-semibold text-slate-950">{batch?.percent_complete ?? 0}%</div>
            </div>
          </div>
        </section>

        <section className="grid gap-4 lg:grid-cols-3">
          <div className={`rounded-[2rem] border p-5 shadow-sm ${toneClass(resourceTone())}`}>
            <div className="text-xs font-semibold uppercase tracking-[0.2em]">Resources</div>
            <div className="mt-3 text-2xl font-semibold">
              {resources?.disk_free_gb ?? 0} GB free
            </div>
            <div className="mt-2 text-sm">
              RAM {resources?.memory_used_percent ?? 0}% · Throughput {resources?.throughput_chapters_per_hour ?? 0}/hr
            </div>
          </div>
          <div className={`rounded-[2rem] border p-5 shadow-sm ${toneClass(modelTone())}`}>
            <div className="text-xs font-semibold uppercase tracking-[0.2em]">Model</div>
            <div className="mt-3 text-2xl font-semibold">
              {modelStatus?.model_loaded ? "Loaded" : "Idle"}
            </div>
            <div className="mt-2 text-sm">
              {modelStatus?.chapters_since_restart ?? 0} / {modelStatus?.restart_interval ?? 0} chapters since restart
            </div>
          </div>
          <div className="rounded-[2rem] border border-slate-200 bg-white p-5 text-slate-700 shadow-sm">
            <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Storage</div>
            <div className="mt-3 text-2xl font-semibold text-slate-950">
              {resources?.output_directory_size_gb ?? 0} GB
            </div>
            <div className="mt-2 text-sm text-slate-500">
              Output footprint · Process memory {modelStatus?.memory_usage_mb ?? 0} MB
            </div>
          </div>
        </section>

        <section className="rounded-[2rem] border border-slate-200 bg-white p-6 shadow-sm">
          <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">Books</div>
          <h2 className="mt-2 text-2xl font-semibold text-slate-950">Per-book status</h2>
          <div className="mt-5 space-y-3">
            {(batch?.book_results ?? []).length === 0 ? (
              <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-500">
                No batch books have been recorded yet.
              </div>
            ) : (batch?.book_results ?? []).map((book) => (
              <div
                className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4"
                key={`${book.book_id}-${book.title}`}
              >
                <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
                  <div>
                    <div className="text-sm font-semibold text-slate-950">{book.title}</div>
                    <div className="mt-1 text-sm text-slate-600">
                      Status: {book.status}
                      {book.qa_average_score !== null && book.qa_average_score !== undefined
                        ? ` · QA ${book.qa_average_score}`
                        : ""}
                      {book.qa_ready_for_export === true ? " · Ready for export" : ""}
                    </div>
                  </div>
                  <div className="text-sm text-slate-500">
                    {book.completed_at ? new Date(book.completed_at).toLocaleString() : "In progress"}
                  </div>
                </div>
                {book.error_message ? (
                  <div className="mt-2 text-sm text-rose-700">{book.error_message}</div>
                ) : null}
              </div>
            ))}
          </div>
        </section>
      </div>
    </AppShell>
  );
}
