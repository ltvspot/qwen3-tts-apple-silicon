import React, { useEffect, useRef, useState } from "react";
import AppShell from "../components/AppShell";
import BatchControls from "../components/BatchControls";
import BatchProgressBar from "../components/BatchProgressBar";
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

async function fetchDashboardPayload() {
  const [batch, exportProgress, resources, modelStats, qaSummary, recentActivity] = await Promise.all([
    fetch("/api/batch/progress").then((response) => parseResponse(response, "Failed to load batch progress.")),
    fetch("/api/export/batch/progress").then((response) => parseResponse(response, "Failed to load batch export progress.")),
    fetch("/api/monitoring/resources").then((response) => parseResponse(response, "Failed to load resource usage.")),
    fetch("/api/monitoring/model").then((response) => parseResponse(response, "Failed to load model stats.")),
    fetch("/api/qa/catalog-summary").then((response) => parseResponse(response, "Failed to load QA summary.")),
    fetch("/api/library?sort=updated_at&limit=20").then((response) => parseResponse(response, "Failed to load recent activity.")),
  ]);

  return { batch, exportProgress, modelStats, qaSummary, recentActivity, resources };
}

export default function CatalogDashboard() {
  const mountedRef = useRef(true);
  const requestRef = useRef(0);

  const [actionLoading, setActionLoading] = useState(false);
  const [dashboard, setDashboard] = useState(null);
  const [errorMessage, setErrorMessage] = useState("");
  const [flashMessage, setFlashMessage] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  async function loadDashboard({ showLoading = true } = {}) {
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;

    if (showLoading) {
      setLoading(true);
    }

    try {
      const payload = await fetchDashboardPayload();
      if (!mountedRef.current || requestRef.current !== requestId) {
        return;
      }

      setDashboard(payload);
      setErrorMessage("");
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
      start: {
        body: JSON.stringify({ priority: "normal", skip_already_exported: true }),
        method: "POST",
        successMessage: "Started a new catalog generation batch.",
        url: "/api/batch/start",
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

        {loading && (
          <div className="text-sm text-slate-500">Loading catalog dashboard...</div>
        )}
      </div>
    </AppShell>
  );
}
