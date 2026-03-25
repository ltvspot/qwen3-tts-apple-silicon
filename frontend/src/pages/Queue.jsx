import React, { useEffect, useRef, useState } from "react";
import AppShell from "../components/AppShell";
import JobDetailsModal from "../components/JobDetailsModal";
import ProgressHeartbeat from "../components/ProgressHeartbeat";
import QueueJobCard from "../components/QueueJobCard";
import QueueStats from "../components/QueueStats";

const FILTER_OPTIONS = [
  { value: "", label: "All Jobs" },
  { value: "queued", label: "Queued" },
  { value: "generating", label: "Generating" },
  { value: "paused", label: "Paused" },
  { value: "completed", label: "Completed" },
  { value: "error", label: "Error" },
];

const SORT_OPTIONS = [
  { value: "priority", label: "Priority (High First)" },
  { value: "created", label: "Created (Newest First)" },
  { value: "status", label: "Status" },
];

const BATCH_DEFAULTS = {
  emotion: "neutral",
  priority: 0,
  speed: 1.0,
  voice: "Ethan",
};

const STATUS_ORDER = {
  generating: 0,
  queued: 1,
  paused: 2,
  error: 3,
  completed: 4,
};

const EMPTY_STATS = {
  active_job_count: 0,
  estimated_total_time_seconds: 0,
  total_books_in_queue: 0,
  total_chapters: 0,
};

async function fetchQueuePayload(statusFilter) {
  const params = new URLSearchParams({ limit: "100", offset: "0" });
  if (statusFilter) {
    params.append("status", statusFilter);
  }

  const response = await fetch(`/api/queue?${params.toString()}`);
  if (!response.ok) {
    throw new Error("Failed to load the production queue.");
  }

  return response.json();
}

async function fetchJobDetail(jobId) {
  const response = await fetch(`/api/queue/${jobId}`);
  if (!response.ok) {
    throw new Error("Failed to load job details.");
  }

  return response.json();
}

function sortJobs(jobs, sortBy) {
  return [...jobs].sort((leftJob, rightJob) => {
    if (sortBy === "created") {
      return new Date(rightJob.created_at).getTime() - new Date(leftJob.created_at).getTime();
    }

    if (sortBy === "status") {
      return (
        (STATUS_ORDER[leftJob.status] ?? 99) - (STATUS_ORDER[rightJob.status] ?? 99) ||
        rightJob.priority - leftJob.priority
      );
    }

    return (
      (STATUS_ORDER[leftJob.status] ?? 99) - (STATUS_ORDER[rightJob.status] ?? 99) ||
      rightJob.priority - leftJob.priority ||
      new Date(leftJob.created_at).getTime() - new Date(rightJob.created_at).getTime()
    );
  });
}

export default function Queue() {
  const detailRequestRef = useRef(0);
  const mountedRef = useRef(true);
  const requestRef = useRef(0);
  const selectedJobIdRef = useRef(null);

  const [actionState, setActionState] = useState({});
  const [batchForm, setBatchForm] = useState(BATCH_DEFAULTS);
  const [batchModalOpen, setBatchModalOpen] = useState(false);
  const [batchSubmitStartTime, setBatchSubmitStartTime] = useState(null);
  const [flashMessage, setFlashMessage] = useState("");
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [pollingError, setPollingError] = useState("");
  const [pollRetryNonce, setPollRetryNonce] = useState(0);
  const [queueStats, setQueueStats] = useState(EMPTY_STATS);
  const [selectedJob, setSelectedJob] = useState(null);
  const [selectedJobId, setSelectedJobId] = useState(null);
  const [sortBy, setSortBy] = useState("priority");
  const [statusFilter, setStatusFilter] = useState("");
  const [submittingBatch, setSubmittingBatch] = useState(false);
  const [totalCount, setTotalCount] = useState(0);

  selectedJobIdRef.current = selectedJobId;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  async function loadJobDetails(jobId, { showLoading = true } = {}) {
    const requestId = detailRequestRef.current + 1;
    detailRequestRef.current = requestId;

    if (showLoading) {
      setDetailLoading(true);
    }

    try {
      const payload = await fetchJobDetail(jobId);
      if (!mountedRef.current || detailRequestRef.current !== requestId) {
        return;
      }

      setSelectedJob(payload);
    } catch (error) {
      if (!mountedRef.current || detailRequestRef.current !== requestId) {
        return;
      }

      setErrorMessage(error instanceof Error ? error.message : "Unable to load job details.");
    } finally {
      if (mountedRef.current && detailRequestRef.current === requestId) {
        setDetailLoading(false);
      }
    }
  }

  async function loadQueueData(selectedStatus = statusFilter, { showLoading = true } = {}) {
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;

    if (showLoading) {
      setLoading(true);
    }

    try {
      const payload = await fetchQueuePayload(selectedStatus);
      if (!mountedRef.current || requestRef.current !== requestId) {
        return payload;
      }

      setJobs(payload.jobs);
      setTotalCount(payload.total_count);
      setQueueStats({
        active_job_count: payload.active_job_count,
        ...payload.queue_stats,
      });
      setErrorMessage("");

      if (selectedJobIdRef.current !== null) {
        void loadJobDetails(selectedJobIdRef.current, { showLoading: false });
      }

      return payload;
    } catch (error) {
      if (mountedRef.current && requestRef.current === requestId) {
        setErrorMessage(error instanceof Error ? error.message : "Unable to load the queue.");
      }
      throw error;
    } finally {
      if (mountedRef.current && requestRef.current === requestId && showLoading) {
        setLoading(false);
      }
    }
  }

  useEffect(() => {
    let cancelled = false;
    let failureCount = 0;
    let timeoutId = null;

    function scheduleNextPoll(delayMs) {
      if (cancelled) {
        return;
      }
      timeoutId = window.setTimeout(() => {
        void poll(false);
      }, delayMs);
    }

    async function poll(showLoading) {
      try {
        await loadQueueData(statusFilter, { showLoading });
        if (cancelled) {
          return;
        }

        failureCount = 0;
        setPollingError("");
        scheduleNextPoll(4000);
      } catch (error) {
        if (cancelled) {
          return;
        }

        failureCount += 1;
        if (failureCount >= 10) {
          setPollingError("Connection lost — click to retry");
          return;
        }

        const retryDelayMs = Math.min(2000 * (2 ** (failureCount - 1)), 8000);
        setPollingError(`Retrying queue refresh in ${Math.round(retryDelayMs / 1000)}s (${failureCount}/10)...`);
        scheduleNextPoll(retryDelayMs);
      }
    }

    void poll(true);

    return () => {
      cancelled = true;
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [statusFilter, selectedJobId, pollRetryNonce]);

  function setActionPending(jobId, action, pending) {
    setActionState((currentState) => ({
      ...currentState,
      [jobId]: {
        ...(currentState[jobId] ?? {}),
        [action]: pending,
      },
    }));
  }

  async function handleJobAction(job, action) {
    const endpoints = {
      cancel: { method: "POST", url: `/api/queue/${job.job_id}/cancel`, body: { reason: "Cancelled from the queue page." } },
      move_down: { method: "PUT", url: `/api/queue/${job.job_id}/priority`, body: { action: "move_down" } },
      move_up: { method: "PUT", url: `/api/queue/${job.job_id}/priority`, body: { action: "move_up" } },
      pause: { method: "POST", url: `/api/queue/${job.job_id}/pause`, body: { reason: "Paused from the queue page." } },
      resume: { method: "POST", url: `/api/queue/${job.job_id}/resume` },
    };

    const requestConfig = endpoints[action];
    if (!requestConfig) {
      return;
    }

    setActionPending(job.job_id, action, true);
    setErrorMessage("");

    try {
      const response = await fetch(requestConfig.url, {
        method: requestConfig.method,
        headers: requestConfig.body ? { "Content-Type": "application/json" } : undefined,
        body: requestConfig.body ? JSON.stringify(requestConfig.body) : undefined,
      });
      if (!response.ok) {
        throw new Error("Queue action failed.");
      }

      await loadQueueData(statusFilter, { showLoading: false });
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Queue action failed.");
    } finally {
      setActionPending(job.job_id, action, false);
    }
  }

  async function handleViewDetails(jobId) {
    setSelectedJobId(jobId);
    setSelectedJob(null);
    setErrorMessage("");
    await loadJobDetails(jobId);
  }

  async function handleBatchSubmit(event) {
    event.preventDefault();
    setErrorMessage("");
    setSubmittingBatch(true);
    setBatchSubmitStartTime(Date.now());

    try {
      const response = await fetch("/api/queue/batch-all", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(batchForm),
      });
      if (!response.ok) {
        throw new Error("Failed to queue parsed books for generation.");
      }

      const payload = await response.json();
      setBatchModalOpen(false);
      setFlashMessage(payload.message);
      await loadQueueData(statusFilter, { showLoading: false });
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : "Failed to queue parsed books for generation.",
      );
    } finally {
      if (mountedRef.current) {
        setSubmittingBatch(false);
        setBatchSubmitStartTime(null);
      }
    }
  }

  const displayedJobs = sortJobs(jobs, sortBy);

  return (
    <AppShell
      title="Production Queue"
      description="Track every active narration run, adjust queue priority, and control batch generation without leaving the production view."
    >
      <div className="space-y-6">
        <QueueStats stats={queueStats} />

        <section className="rounded-[2rem] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/60">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">
                Queue Controls
              </div>
              <h2 className="mt-2 text-2xl font-semibold text-slate-950">Manage priority, throughput, and batch runs</h2>
              <p className="mt-2 max-w-3xl text-sm text-slate-600">
                Queue the entire parsed catalog, pause in-flight jobs, or open the chapter breakdown for granular status.
              </p>
            </div>

            <button
              className="rounded-full border border-slate-950 bg-slate-950 px-5 py-3 text-sm font-medium text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={submittingBatch}
              onClick={() => setBatchModalOpen(true)}
              type="button"
            >
              Generate All Parsed Books
            </button>
          </div>

          <div className="mt-5 grid gap-4 md:grid-cols-2">
            <label className="block">
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Filter</div>
              <select
                className="mt-2 w-full rounded-[1rem] border border-slate-300 bg-slate-50 px-4 py-3 text-sm text-slate-900"
                onChange={(event) => setStatusFilter(event.target.value)}
                value={statusFilter}
              >
                {FILTER_OPTIONS.map((option) => (
                  <option key={option.value || "all"} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="block">
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Sort</div>
              <select
                className="mt-2 w-full rounded-[1rem] border border-slate-300 bg-slate-50 px-4 py-3 text-sm text-slate-900"
                onChange={(event) => setSortBy(event.target.value)}
                value={sortBy}
              >
                {SORT_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </section>

        {flashMessage ? (
          <div className="rounded-[1.5rem] border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
            {flashMessage}
          </div>
        ) : null}

        {pollingError ? (
          <div className="rounded-[1.5rem] border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            {pollingError === "Connection lost — click to retry" ? (
              <button
                className="font-semibold underline underline-offset-4"
                onClick={() => {
                  setPollingError("");
                  setPollRetryNonce((currentValue) => currentValue + 1);
                }}
                type="button"
              >
                {pollingError}
              </button>
            ) : (
              pollingError
            )}
          </div>
        ) : null}

        {errorMessage ? (
          <div className="rounded-[1.5rem] border border-orange-200 bg-orange-50 px-4 py-3 text-sm text-orange-900">
            {errorMessage}
          </div>
        ) : null}

        <section className="space-y-4">
          <div className="flex items-center justify-between text-sm text-slate-600">
            <div>{loading ? "Loading queue..." : `Showing ${displayedJobs.length} of ${totalCount} jobs`}</div>
            <div>{queueStats.active_job_count} active job(s)</div>
          </div>

          {loading ? (
            <div className="rounded-[2rem] border border-slate-200 bg-white px-6 py-12 text-center text-sm text-slate-600 shadow-sm shadow-slate-200/60">
              Loading production jobs...
            </div>
          ) : null}

          {!loading && !displayedJobs.length ? (
            <div className="rounded-[2rem] border border-dashed border-slate-300 bg-slate-50 px-6 py-12 text-center text-sm text-slate-500">
              No queue jobs match the current filter.
            </div>
          ) : null}

          {!loading && displayedJobs.length ? displayedJobs.map((job) => (
            <QueueJobCard
              key={job.job_id}
              actionState={actionState[job.job_id]}
              job={job}
              onAction={handleJobAction}
              onViewDetails={handleViewDetails}
            />
          )) : null}
        </section>
      </div>

      {batchModalOpen ? (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/50 px-4 py-6">
          <div className="w-full max-w-xl rounded-[2rem] bg-white p-6 shadow-2xl shadow-slate-950/20">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">Batch Generation</div>
                <h2 className="mt-2 text-2xl font-semibold text-slate-950">Queue all parsed books</h2>
                <p className="mt-2 text-sm text-slate-600">
                  Confirm the voice profile and queue priority for the full parsed catalog.
                </p>
              </div>

              <button
                className="rounded-full border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-950 hover:text-slate-950 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={submittingBatch}
                onClick={() => setBatchModalOpen(false)}
                type="button"
              >
                Close
              </button>
            </div>

            <form className="mt-6 space-y-4" onSubmit={handleBatchSubmit}>
              <label className="block">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Voice</div>
                <select
                  className="mt-2 w-full rounded-[1rem] border border-slate-300 bg-slate-50 px-4 py-3 text-sm text-slate-900"
                  onChange={(event) => setBatchForm((currentForm) => ({ ...currentForm, voice: event.target.value }))}
                  value={batchForm.voice}
                >
                  <option value="Ethan">Ethan</option>
                </select>
              </label>

              <div className="grid gap-4 md:grid-cols-2">
                <label className="block">
                  <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Emotion</div>
                  <select
                    className="mt-2 w-full rounded-[1rem] border border-slate-300 bg-slate-50 px-4 py-3 text-sm text-slate-900"
                    onChange={(event) => setBatchForm((currentForm) => ({ ...currentForm, emotion: event.target.value }))}
                    value={batchForm.emotion}
                  >
                    <option value="neutral">Neutral</option>
                    <option value="warm">Warm</option>
                    <option value="narrative">Narrative</option>
                  </select>
                </label>

                <label className="block">
                  <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Speed</div>
                  <select
                    className="mt-2 w-full rounded-[1rem] border border-slate-300 bg-slate-50 px-4 py-3 text-sm text-slate-900"
                    onChange={(event) => setBatchForm((currentForm) => ({ ...currentForm, speed: Number(event.target.value) }))}
                    value={batchForm.speed}
                  >
                    <option value="0.9">0.9x</option>
                    <option value="1">1.0x</option>
                    <option value="1.1">1.1x</option>
                  </select>
                </label>
              </div>

              <label className="block">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Priority</div>
                <input
                  className="mt-2 w-full rounded-[1rem] border border-slate-300 bg-slate-50 px-4 py-3 text-sm text-slate-900"
                  max="100"
                  min="0"
                  onChange={(event) => setBatchForm((currentForm) => ({ ...currentForm, priority: Number(event.target.value) }))}
                  type="number"
                  value={batchForm.priority}
                />
              </label>

              <div className="rounded-[1.5rem] border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
                Queue all parsed books for generation?
              </div>

              {submittingBatch ? (
                <ProgressHeartbeat
                  isActive={submittingBatch}
                  progressPercent={null}
                  showETA={null}
                  size="sm"
                  stage="Queuing books..."
                  startTime={batchSubmitStartTime}
                />
              ) : null}

              <div className="flex justify-end">
                <button
                  className="inline-flex items-center justify-center gap-2 rounded-full border border-slate-950 bg-slate-950 px-5 py-3 text-sm font-medium text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={submittingBatch}
                  type="submit"
                >
                  {submittingBatch ? (
                    <>
                      <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                      Queueing...
                    </>
                  ) : (
                    "Confirm Batch Queue"
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}

      <JobDetailsModal
        job={selectedJob}
        loading={detailLoading}
        onClose={() => {
          setSelectedJob(null);
          setSelectedJobId(null);
        }}
      />
    </AppShell>
  );
}
