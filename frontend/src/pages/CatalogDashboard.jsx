import React, { useEffect, useRef, useState } from "react";
import AppShell from "../components/AppShell";
import BatchControls from "../components/BatchControls";
import BatchProgressBar from "../components/BatchProgressBar";
import ProgressHeartbeat from "../components/ProgressHeartbeat";
import QuickActions from "../components/QuickActions";
import RecentActivityFeed from "../components/RecentActivityFeed";
import ResourceGauges from "../components/ResourceGauges";

const EMPTY_QA_SUMMARY = {
  books_all_approved: 0,
  books_pending_qa: 0,
  books_with_flags: 0,
  chapters_approved: 0,
  chapters_flagged: 0,
  chapters_pending: 0,
  total_books: 0,
  total_chapters: 0,
};

const EMPTY_LIBRARY_STATS = {
  exported: 0,
  generated: 0,
  generating: 0,
  not_started: 0,
  parsed: 0,
  qa: 0,
  qa_approved: 0,
};

const DEFAULT_BATCH_START_CONFIG = {
  priority: "normal",
  schedulingStrategy: "shortest",
  skipAlreadyExported: true,
};

const DASHBOARD_SECTIONS = [
  {
    fallback: null,
    key: "batch",
    label: "Batch progress",
    request: () => fetch("/api/batch/progress").then((response) => parseResponse(response, "Failed to load batch progress.")),
  },
  {
    fallback: null,
    key: "exportProgress",
    label: "Export progress",
    request: () => fetch("/api/export/batch/progress").then((response) => parseResponse(response, "Failed to load batch export progress.")),
  },
  {
    fallback: null,
    key: "resources",
    label: "Resources",
    request: () => fetch("/api/monitoring/resources").then((response) => parseResponse(response, "Failed to load resource usage.")),
  },
  {
    fallback: null,
    key: "modelStats",
    label: "Model stats",
    request: () => fetch("/api/monitoring/model").then((response) => parseResponse(response, "Failed to load model stats.")),
  },
  {
    fallback: EMPTY_QA_SUMMARY,
    key: "qaSummary",
    label: "QA summary",
    request: () => fetch("/api/qa/catalog-summary").then((response) => parseResponse(response, "Failed to load QA summary.")),
  },
  {
    fallback: { books: [], stats: EMPTY_LIBRARY_STATS },
    key: "recentActivity",
    label: "Recent activity",
    request: () => fetch("/api/library?sort=updated_at&limit=20").then((response) => parseResponse(response, "Failed to load recent activity.")),
  },
];

function StatCard({ label, toneClass, value }) {
  return (
    <div className={`rounded-3xl border bg-white p-5 shadow-sm ${toneClass}`}>
      <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">{label}</div>
      <div className="mt-3 text-3xl font-semibold text-slate-950">{value}</div>
    </div>
  );
}

async function parseResponse(response, fallbackMessage) {
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail ?? fallbackMessage);
  }

  return response.json();
}

async function fetchDashboardPayload(onSectionSettled = () => {}) {
  const settled = await Promise.allSettled(
    DASHBOARD_SECTIONS.map((section) => section.request().finally(() => onSectionSettled(section.key))),
  );
  const data = {};
  const errors = [];

  settled.forEach((result, index) => {
    const section = DASHBOARD_SECTIONS[index];
    if (result.status === "fulfilled") {
      data[section.key] = result.value;
      return;
    }

    data[section.key] = section.fallback;
    errors.push(`Unable to load ${section.label.toLowerCase()}.`);
  });

  return { data, errors };
}

export default function CatalogDashboard() {
  const mountedRef = useRef(true);
  const requestRef = useRef(0);

  const [actionLoading, setActionLoading] = useState(false);
  const [batchEstimate, setBatchEstimate] = useState(null);
  const [batchEstimateError, setBatchEstimateError] = useState("");
  const [batchEstimateLoading, setBatchEstimateLoading] = useState(false);
  const [batchEstimateStartedAt, setBatchEstimateStartedAt] = useState(null);
  const [batchStartConfig, setBatchStartConfig] = useState(DEFAULT_BATCH_START_CONFIG);
  const [batchStartOpen, setBatchStartOpen] = useState(false);
  const [batchStartSubmitting, setBatchStartSubmitting] = useState(false);
  const [dashboard, setDashboard] = useState(null);
  const [errorMessage, setErrorMessage] = useState("");
  const [flashMessage, setFlashMessage] = useState("");
  const [loadChecklist, setLoadChecklist] = useState(
    Object.fromEntries(DASHBOARD_SECTIONS.map((section) => [section.key, false])),
  );
  const [loadStartedAt, setLoadStartedAt] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    document.title = "Catalog Dashboard | Alexandria Audiobook Narrator";
  }, []);

  async function loadBatchEstimate(config) {
    setBatchEstimateLoading(true);
    setBatchEstimateError("");
    setBatchEstimateStartedAt(Date.now());

    try {
      const response = await fetch("/api/batch/estimate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          skip_already_exported: config.skipAlreadyExported,
        }),
      });
      const payload = await parseResponse(response, "Failed to estimate the batch run.");
      if (!mountedRef.current) {
        return;
      }

      setBatchEstimate(payload);
    } catch (error) {
      if (mountedRef.current) {
        setBatchEstimateError(error instanceof Error ? error.message : "Failed to estimate the batch run.");
      }
    } finally {
      if (mountedRef.current) {
        setBatchEstimateLoading(false);
      }
    }
  }

  async function openBatchStartModal() {
    const nextConfig = { ...DEFAULT_BATCH_START_CONFIG };
    setBatchStartConfig(nextConfig);
    setBatchEstimate(null);
    setBatchEstimateError("");
    setBatchStartOpen(true);
    await loadBatchEstimate(nextConfig);
  }

  async function confirmBatchStart() {
    setBatchStartSubmitting(true);
    setFlashMessage("");
    setErrorMessage("");

    try {
      const response = await fetch("/api/batch/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          priority: batchStartConfig.priority,
          scheduling_strategy: batchStartConfig.schedulingStrategy,
          skip_already_exported: batchStartConfig.skipAlreadyExported,
        }),
      });
      await parseResponse(response, "Catalog action failed.");
      if (!mountedRef.current) {
        return;
      }

      setBatchStartOpen(false);
      setFlashMessage("Started a new catalog generation batch.");
      await loadDashboard({ showLoading: false });
    } catch (error) {
      if (mountedRef.current) {
        setErrorMessage(error instanceof Error ? error.message : "Catalog action failed.");
      }
    } finally {
      if (mountedRef.current) {
        setBatchStartSubmitting(false);
      }
    }
  }

  async function loadDashboard({ showLoading = true } = {}) {
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;

    if (showLoading) {
      setLoading(true);
      setLoadStartedAt(Date.now());
      setLoadChecklist(Object.fromEntries(DASHBOARD_SECTIONS.map((section) => [section.key, false])));
    }

    try {
      const payload = await fetchDashboardPayload((sectionKey) => {
        if (!mountedRef.current || requestRef.current !== requestId || !showLoading) {
          return;
        }

        setLoadChecklist((currentChecklist) => ({
          ...currentChecklist,
          [sectionKey]: true,
        }));
      });
      if (!mountedRef.current || requestRef.current !== requestId) {
        return;
      }

      setDashboard(payload.data);
      setErrorMessage(payload.errors.join(" "));
    } catch (error) {
      if (mountedRef.current && requestRef.current === requestId) {
        setErrorMessage(error instanceof Error ? error.message : "Unable to load the catalog dashboard.");
      }
    } finally {
      if (mountedRef.current && requestRef.current === requestId && showLoading) {
        setLoading(false);
      }
    }
  }

  useEffect(() => {
    let cancelled = false;
    let intervalId = null;

    async function poll(showLoading) {
      await loadDashboard({ showLoading });
      if (cancelled) {
        return;
      }
    }

    void poll(true);
    intervalId = window.setInterval(() => {
      void poll(false);
    }, 5000);

    return () => {
      cancelled = true;
      if (intervalId !== null) {
        window.clearInterval(intervalId);
      }
    };
  }, []);

  async function performAction(action) {
    if (action === "start") {
      await openBatchStartModal();
      return;
    }

    const requests = {
      approve: {
        method: "POST",
        successMessage: "Approved all passing chapters across the catalog.",
        url: "/api/qa/batch-approve-all?approve_warnings=false",
      },
      cancel: {
        method: "POST",
        successMessage: "Cancelled the active generation batch.",
        url: "/api/batch/cancel",
      },
      export: {
        body: JSON.stringify({
          formats: ["mp3", "m4b"],
          include_only_approved: true,
          skip_already_exported: true,
        }),
        method: "POST",
        successMessage: "Queued exports for all ready books.",
        url: "/api/export/batch",
      },
      pause: {
        body: JSON.stringify({ reason: "Paused from the catalog dashboard." }),
        method: "POST",
        successMessage: "Paused the active generation batch.",
        url: "/api/batch/pause",
      },
      reload_model: {
        method: "POST",
        successMessage: "Forced a shared model reload.",
        url: "/api/monitoring/model/reload",
      },
      resume: {
        method: "POST",
        successMessage: "Resumed the active generation batch.",
        url: "/api/batch/resume",
      },
    };

    const request = requests[action];
    if (!request) {
      return;
    }

    setActionLoading(true);
    setFlashMessage("");
    setErrorMessage("");

    try {
      const response = await fetch(request.url, {
        method: request.method,
        headers: request.body ? { "Content-Type": "application/json" } : undefined,
        body: request.body,
      });
      await parseResponse(response, "Catalog action failed.");
      if (!mountedRef.current) {
        return;
      }

      setFlashMessage(request.successMessage);
      await loadDashboard({ showLoading: false });
    } catch (error) {
      if (mountedRef.current) {
        setErrorMessage(error instanceof Error ? error.message : "Catalog action failed.");
      }
    } finally {
      if (mountedRef.current) {
        setActionLoading(false);
      }
    }
  }

  const qaSummary = dashboard?.qaSummary ?? EMPTY_QA_SUMMARY;
  const completedChecklistItems = Object.values(loadChecklist).filter(Boolean).length;
  const recentActivity = dashboard?.recentActivity ?? { books: [], stats: EMPTY_LIBRARY_STATS };
  const libraryStats = recentActivity.stats ?? EMPTY_LIBRARY_STATS;
  const totalBooks = qaSummary.total_books || Object.values(libraryStats).reduce((sum, count) => sum + count, 0);
  const doneBooks = Math.max(libraryStats.exported ?? 0, qaSummary.books_all_approved ?? 0);
  const qaBooks = (qaSummary.books_pending_qa ?? 0) + (qaSummary.books_with_flags ?? 0);
  const generatingBooks = (libraryStats.generating ?? 0) + (libraryStats.generated ?? 0);
  const failedBooks = qaSummary.books_with_flags ?? 0;
  const queuedBooks = dashboard?.batch
    ? Math.max(
      dashboard.batch.total_books
        - dashboard.batch.books_completed
        - dashboard.batch.books_failed
        - dashboard.batch.books_skipped,
      0,
    )
    : 0;

  return (
    <AppShell
      title="Catalog Dashboard"
      description="A live production view for batch generation, QA readiness, export throughput, and shared model health."
    >
      <div className="space-y-8">
        {flashMessage && (
          <div className="rounded-3xl border border-emerald-200 bg-emerald-50 px-5 py-4 text-sm text-emerald-900">
            {flashMessage}
          </div>
        )}

        {errorMessage && (
          <div className="rounded-3xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-900">
            {errorMessage}
          </div>
        )}

        <BatchProgressBar completed={doneBooks} total={totalBooks} />

        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
          <StatCard label="Done" toneClass="border-emerald-200" value={doneBooks} />
          <StatCard label="QA" toneClass="border-amber-200" value={qaBooks} />
          <StatCard label="Gen" toneClass="border-sky-200" value={generatingBooks} />
          <StatCard label="Fail" toneClass="border-rose-200" value={failedBooks} />
          <StatCard label="Queue" toneClass="border-slate-200" value={queuedBooks} />
        </section>

        <ResourceGauges
          modelStats={dashboard?.modelStats}
          resources={dashboard?.resources}
        />

        <BatchControls
          actionLoading={actionLoading}
          batch={dashboard?.batch}
          exportProgress={dashboard?.exportProgress}
          onAction={performAction}
        />

        <div className="grid gap-6 xl:grid-cols-[1.3fr_0.9fr]">
          <RecentActivityFeed books={recentActivity.books ?? []} />
          <QuickActions actionLoading={actionLoading} onAction={performAction} />
        </div>

        {loading ? (
          <div className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
            <ProgressHeartbeat
              isActive={loading}
              progressPercent={(completedChecklistItems / DASHBOARD_SECTIONS.length) * 100}
              showETA={null}
              size="md"
              stage={`Loading dashboard... (${completedChecklistItems}/${DASHBOARD_SECTIONS.length})`}
              startTime={loadStartedAt}
            />
            <div className="mt-4 grid gap-2 text-sm text-slate-600 md:grid-cols-2">
              {DASHBOARD_SECTIONS.map((section) => (
                <div
                  className="flex items-center gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3"
                  key={section.key}
                >
                  <span className={`text-base ${loadChecklist[section.key] ? "text-emerald-600" : "text-slate-400"}`}>
                    {loadChecklist[section.key] ? "✓" : "○"}
                  </span>
                  <span>{section.label}{loadChecklist[section.key] ? "" : "..."}</span>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {batchStartOpen ? (
          <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/50 px-4 py-6">
            <div className="w-full max-w-3xl rounded-[2rem] bg-white p-6 shadow-2xl shadow-slate-950/20">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">Batch preflight</div>
                  <h2 className="mt-2 text-2xl font-semibold text-slate-950">Estimate overnight generation</h2>
                  <p className="mt-2 text-sm text-slate-600">
                    Review runtime and disk impact before you commit the catalog batch.
                  </p>
                </div>

                <button
                  className="rounded-full border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-950 hover:text-slate-950 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={batchStartSubmitting}
                  onClick={() => setBatchStartOpen(false)}
                  type="button"
                >
                  Close
                </button>
              </div>

              <div className="mt-6 grid gap-4 md:grid-cols-3">
                <label className="block">
                  <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Priority</div>
                  <select
                    className="mt-2 w-full rounded-[1rem] border border-slate-300 bg-slate-50 px-4 py-3 text-sm text-slate-900"
                    onChange={(event) => setBatchStartConfig((currentConfig) => ({
                      ...currentConfig,
                      priority: event.target.value,
                    }))}
                    value={batchStartConfig.priority}
                  >
                    <option value="urgent">Urgent</option>
                    <option value="normal">Normal</option>
                    <option value="backlog">Backlog</option>
                  </select>
                </label>

                <label className="block">
                  <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Scheduling</div>
                  <select
                    className="mt-2 w-full rounded-[1rem] border border-slate-300 bg-slate-50 px-4 py-3 text-sm text-slate-900"
                    onChange={(event) => setBatchStartConfig((currentConfig) => ({
                      ...currentConfig,
                      schedulingStrategy: event.target.value,
                    }))}
                    value={batchStartConfig.schedulingStrategy}
                  >
                    <option value="shortest">Shortest first</option>
                    <option value="fifo">FIFO</option>
                    <option value="longest">Longest first</option>
                    <option value="priority">Priority order</option>
                  </select>
                </label>

                <label className="flex items-center gap-3 rounded-[1rem] border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700">
                  <input
                    checked={batchStartConfig.skipAlreadyExported}
                    className="h-4 w-4 rounded border-slate-300"
                    onChange={(event) => {
                      const nextConfig = {
                        ...batchStartConfig,
                        skipAlreadyExported: event.target.checked,
                      };
                      setBatchStartConfig(nextConfig);
                      void loadBatchEstimate(nextConfig);
                    }}
                    type="checkbox"
                  />
                  Skip already exported books
                </label>
              </div>

              {batchEstimateLoading ? (
                <div className="mt-6 rounded-3xl border border-slate-200 bg-slate-50 p-5">
                  <ProgressHeartbeat
                    isActive={batchEstimateLoading}
                    progressPercent={null}
                    showETA={null}
                    size="md"
                    stage="Estimating batch resources..."
                    startTime={batchEstimateStartedAt}
                  />
                </div>
              ) : null}

              {batchEstimateError ? (
                <div className="mt-6 rounded-3xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-900">
                  {batchEstimateError}
                </div>
              ) : null}

              {batchEstimate ? (
                <div className="mt-6 space-y-4">
                  <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                    <StatCard label="Books" toneClass="border-slate-200" value={batchEstimate.books} />
                    <StatCard label="Audio Hours" toneClass="border-sky-200" value={batchEstimate.estimated_audio_hours} />
                    <StatCard label="Disk (GB)" toneClass="border-amber-200" value={batchEstimate.estimated_disk_gb} />
                    <StatCard label="Runtime (hrs)" toneClass="border-emerald-200" value={batchEstimate.estimated_generation_hours} />
                  </div>

                  <div className={`rounded-3xl border px-5 py-4 text-sm ${
                    batchEstimate.can_proceed
                      ? "border-emerald-200 bg-emerald-50 text-emerald-900"
                      : "border-rose-200 bg-rose-50 text-rose-900"
                  }`}
                  >
                    {batchEstimate.can_proceed
                      ? `Disk check passed. ${batchEstimate.disk_free_gb} GB free with enough buffer for the run.`
                      : `Disk check failed. ${batchEstimate.disk_free_gb} GB free is below the recommended buffer.`}
                  </div>

                  {batchEstimate.warnings?.length ? (
                    <div className="rounded-3xl border border-amber-200 bg-amber-50 px-5 py-4 text-sm text-amber-950">
                      <div className="font-semibold text-amber-900">Warnings</div>
                      <div className="mt-3 space-y-2">
                        {batchEstimate.warnings.map((warning) => (
                          <div key={warning}>{warning}</div>
                        ))}
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : null}

              <div className="mt-6 flex justify-end gap-3">
                <button
                  className="rounded-full border border-slate-300 px-5 py-3 text-sm font-medium text-slate-700 transition hover:border-slate-950 hover:text-slate-950 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={batchStartSubmitting}
                  onClick={() => setBatchStartOpen(false)}
                  type="button"
                >
                  Cancel
                </button>
                <button
                  className="inline-flex items-center justify-center gap-2 rounded-full border border-slate-950 bg-slate-950 px-5 py-3 text-sm font-medium text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={batchEstimateLoading || batchStartSubmitting || !batchEstimate}
                  onClick={() => {
                    void confirmBatchStart();
                  }}
                  type="button"
                >
                  {batchStartSubmitting ? (
                    <>
                      <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                      Starting...
                    </>
                  ) : (
                    "Confirm Batch Start"
                  )}
                </button>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </AppShell>
  );
}
