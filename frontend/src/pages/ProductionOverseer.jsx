import React, { useEffect, useMemo, useRef, useState } from "react";
import AppShell from "../components/AppShell";
import ProgressHeartbeat, { formatHeartbeatDuration } from "../components/ProgressHeartbeat";

function parseResponse(response, fallbackMessage) {
  if (!response.ok) {
    return response.json().catch(() => ({})).then((payload) => {
      throw new Error(payload.detail ?? fallbackMessage);
    });
  }

  return response.json();
}

function formatPercent(value, digits = 0) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "—";
  }
  return `${value.toFixed(digits)}%`;
}

function formatNumber(value, digits = 1) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "—";
  }
  return value.toFixed(digits);
}

function formatDate(value) {
  if (!value) {
    return "—";
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "—";
  }

  return parsed.toLocaleString();
}

function scoreTone(grade) {
  if (grade === "A") {
    return "border-emerald-300 bg-emerald-50 text-emerald-900";
  }
  if (grade === "B") {
    return "border-amber-300 bg-amber-50 text-amber-900";
  }
  if (grade === "C") {
    return "border-orange-300 bg-orange-50 text-orange-900";
  }
  if (grade === "F") {
    return "border-rose-300 bg-rose-50 text-rose-900";
  }
  return "border-slate-300 bg-slate-100 text-slate-800";
}

function statusTone(ready) {
  return ready
    ? "border-emerald-300 bg-emerald-50 text-emerald-900"
    : "border-amber-300 bg-amber-50 text-amber-900";
}

function cardToneForCheck(passed) {
  return passed
    ? "border-emerald-200 bg-emerald-50"
    : "border-rose-200 bg-rose-50";
}

function SectionCard({ title, description, children, aside = null }) {
  return (
    <section className="rounded-[2rem] border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">{title}</div>
          {description ? <p className="mt-2 max-w-3xl text-sm text-slate-600">{description}</p> : null}
        </div>
        {aside}
      </div>
      <div className="mt-6">{children}</div>
    </section>
  );
}

function StatCard({ label, value, detail }) {
  return (
    <div className="rounded-[1.5rem] border border-slate-200 bg-slate-50 p-4">
      <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">{label}</div>
      <div className="mt-2 text-2xl font-semibold text-slate-950">{value}</div>
      {detail ? <div className="mt-2 text-sm text-slate-600">{detail}</div> : null}
    </div>
  );
}

function TrendChart({ points }) {
  const width = 640;
  const height = 220;
  const padding = 24;

  const chartPoints = useMemo(() => {
    if (!points?.length) {
      return { gate1: "", gate2: "", regen: "" };
    }

    const maxChunksRegenerated = Math.max(...points.map((point) => point.chunks_regenerated || 0), 1);
    const buildPath = (valueForPoint, maxValue) => points.map((point, index) => {
      const x = padding + ((width - (padding * 2)) * index) / Math.max(points.length - 1, 1);
      const normalized = maxValue === 0 ? 0 : (valueForPoint(point) / maxValue);
      const y = height - padding - ((height - (padding * 2)) * normalized);
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    }).join(" ");

    return {
      gate1: buildPath((point) => point.gate1_pass_rate || 0, 100),
      gate2: buildPath((point) => (point.gate2_avg_grade || 0) * 25, 100),
      regen: buildPath((point) => point.chunks_regenerated || 0, maxChunksRegenerated),
    };
  }, [points]);

  return (
    <div className="rounded-[1.5rem] border border-slate-200 bg-slate-50 p-4">
      <div className="flex flex-wrap items-center gap-4 text-sm text-slate-600">
        <span className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-sky-500" /> Gate 1 Pass Rate</span>
        <span className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-amber-500" /> Gate 2 Avg Grade</span>
        <span className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full bg-rose-500" /> Chunks Regenerated</span>
      </div>
      <svg className="mt-4 w-full" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Quality trend chart">
        <rect x="0" y="0" width={width} height={height} rx="24" fill="#f8fafc" />
        {[0.25, 0.5, 0.75].map((step) => (
          <line
            key={step}
            x1={padding}
            x2={width - padding}
            y1={height - padding - ((height - (padding * 2)) * step)}
            y2={height - padding - ((height - (padding * 2)) * step)}
            stroke="#cbd5e1"
            strokeDasharray="6 8"
          />
        ))}
        <path d={chartPoints.gate1} fill="none" stroke="#0ea5e9" strokeWidth="4" strokeLinecap="round" />
        <path d={chartPoints.gate2} fill="none" stroke="#f59e0b" strokeWidth="4" strokeLinecap="round" />
        <path d={chartPoints.regen} fill="none" stroke="#f43f5e" strokeWidth="4" strokeLinecap="round" />
      </svg>
    </div>
  );
}

export default function ProductionOverseer() {
  const mountedRef = useRef(true);
  const requestRef = useRef(0);
  const [errorMessage, setErrorMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [overview, setOverview] = useState({
    batch: null,
    flaggedItems: [],
    model: null,
    queue: null,
    resources: null,
    trend: null,
  });
  const [selectedBookId, setSelectedBookId] = useState(null);
  const [selectedReport, setSelectedReport] = useState(null);
  const [selectedReportLoading, setSelectedReportLoading] = useState(false);
  const [selectedReportError, setSelectedReportError] = useState("");

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    document.title = "Production Overseer | Alexandria Audiobook Narrator";
  }, []);

  async function loadOverview({ showLoading = true } = {}) {
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;
    if (showLoading) {
      setLoading(true);
    }

    try {
      const responses = await Promise.allSettled([
        fetch("/api/queue?limit=10&offset=0").then((response) => parseResponse(response, "Failed to load the queue.")),
        fetch("/api/batch/progress").then((response) => parseResponse(response, "Failed to load batch progress.")),
        fetch("/api/monitoring/resources").then((response) => parseResponse(response, "Failed to load resource usage.")),
        fetch("/api/monitoring/model").then((response) => parseResponse(response, "Failed to load model health.")),
        fetch("/api/overseer/quality-trend?last_n=20").then((response) => parseResponse(response, "Failed to load quality trends.")),
        fetch("/api/overseer/flagged-items?limit=50").then((response) => parseResponse(response, "Failed to load flagged items.")),
      ]);

      if (!mountedRef.current || requestRef.current !== requestId) {
        return;
      }

      const nextOverview = {
        queue: responses[0].status === "fulfilled" ? responses[0].value : null,
        batch: responses[1].status === "fulfilled" ? responses[1].value : null,
        resources: responses[2].status === "fulfilled" ? responses[2].value : null,
        model: responses[3].status === "fulfilled" ? responses[3].value : null,
        trend: responses[4].status === "fulfilled" ? responses[4].value : null,
        flaggedItems: responses[5].status === "fulfilled" ? responses[5].value.items ?? [] : [],
      };
      setOverview(nextOverview);

      const errors = responses
        .filter((result) => result.status === "rejected")
        .map((result) => result.reason?.message ?? "Dashboard section failed to load.");
      setErrorMessage(errors.join(" "));

      const firstBookId = nextOverview.trend?.recent_books?.[0]?.book_id ?? null;
      setSelectedBookId((current) => current ?? firstBookId);
    } finally {
      if (mountedRef.current && requestRef.current === requestId && showLoading) {
        setLoading(false);
      }
    }
  }

  async function loadSelectedReport(bookId) {
    if (!bookId) {
      setSelectedReport(null);
      setSelectedReportError("");
      return;
    }

    setSelectedReportLoading(true);
    setSelectedReportError("");
    try {
      const response = await fetch(`/api/overseer/book/${bookId}/report`);
      const payload = await parseResponse(response, "Failed to load the book report.");
      if (!mountedRef.current) {
        return;
      }
      setSelectedReport(payload);
    } catch (error) {
      if (mountedRef.current) {
        setSelectedReportError(error instanceof Error ? error.message : "Failed to load the book report.");
      }
    } finally {
      if (mountedRef.current) {
        setSelectedReportLoading(false);
      }
    }
  }

  useEffect(() => {
    void loadOverview({ showLoading: true });
    const intervalId = window.setInterval(() => {
      void loadOverview({ showLoading: false });
    }, 15000);

    return () => {
      window.clearInterval(intervalId);
    };
  }, []);

  useEffect(() => {
    void loadSelectedReport(selectedBookId);
  }, [selectedBookId]);

  const activeJob = overview.queue?.jobs?.find((job) => job.status === "generating") ?? null;
  const queueDepth = overview.queue?.queue_stats?.total_books_in_queue ?? 0;
  const trend = overview.trend;
  const recentBooks = trend?.recent_books ?? [];
  const hasRecentBooks = recentBooks.length > 0;
  const selectedVerification = selectedReport?.export_verification ?? null;
  const selectedGate3 = selectedReport?.gate3_report ?? null;
  const readinessChecks = selectedVerification?.checks ?? [];
  const canaryStatus = overview.model?.last_canary_status ?? "not_run";

  return (
    <AppShell
      title="Production Overseer"
      description="Cross-book quality oversight, export readiness, and live production visibility for the audiobook narrator."
    >
      <div className="space-y-6">
        {loading ? (
          <ProgressHeartbeat
            isActive
            stage="Loading production oversight..."
            startTime={Date.now()}
            size="lg"
          />
        ) : null}

        {errorMessage ? (
          <div className="rounded-[1.5rem] border border-amber-300 bg-amber-50 px-5 py-4 text-sm text-amber-900">
            {errorMessage}
          </div>
        ) : null}

        {trend?.trend === "degrading" || (trend?.alerts?.length ?? 0) > 0 ? (
          <div className="rounded-[1.75rem] border border-rose-300 bg-rose-50 px-5 py-4 text-sm text-rose-900">
            <div className="font-semibold uppercase tracking-[0.2em]">Quality Alert</div>
            <div className="mt-2">{trend?.alerts?.join(" ") || "Recent quality metrics are degrading."}</div>
          </div>
        ) : null}

        <SectionCard
          title="Active Production Overview"
          description="Current generation activity, queue depth, and system health at a glance."
        >
          <div className="grid gap-4 lg:grid-cols-4">
            <StatCard
              label="Currently Generating"
              value={activeJob?.book_title ?? "Idle"}
              detail={activeJob ? `Chapter ${activeJob.current_chapter_n ?? "—"} • ETA ${formatHeartbeatDuration(activeJob.eta_seconds ?? 0)}` : "No active generation job."}
            />
            <StatCard
              label="Queue Depth"
              value={queueDepth}
              detail={`${overview.queue?.queue_stats?.total_chapters ?? 0} chapters pending`}
            />
            <StatCard
              label="Memory Usage"
              value={formatPercent(overview.resources?.memory_used_percent, 1)}
              detail={`${formatNumber(overview.resources?.memory_used_mb, 0)} / ${formatNumber(overview.resources?.memory_total_mb, 0)} MB`}
            />
            <StatCard
              label="Model Health"
              value={String(canaryStatus).replaceAll("_", " ")}
              detail={`Reloads ${overview.model?.reload_count ?? 0} • Uptime ${formatHeartbeatDuration(overview.model?.uptime_seconds ?? 0)}`}
            />
          </div>

          <div className="mt-6 grid gap-4 lg:grid-cols-2">
            <ProgressHeartbeat
              isActive={Boolean(activeJob || overview.batch?.status === "running")}
              progressPercent={activeJob?.progress_percent ?? overview.batch?.percent_complete ?? null}
              showETA={activeJob?.eta_seconds ? `ETA ${formatHeartbeatDuration(activeJob.eta_seconds)}` : null}
              size="lg"
              stage={activeJob ? `${activeJob.book_title} is generating` : overview.batch?.current_book_title ? `${overview.batch.current_book_title} is in the active batch` : "No active production job"}
              startTime={activeJob?.started_at ?? overview.batch?.started_at ?? null}
            />

            <div className="rounded-[1.5rem] border border-slate-200 bg-slate-50 p-5">
              <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">System Health</div>
              <dl className="mt-4 space-y-3 text-sm text-slate-700">
                <div className="flex items-center justify-between gap-4">
                  <dt>Disk Free</dt>
                  <dd>{formatNumber(overview.resources?.disk_free_gb, 1)} GB</dd>
                </div>
                <div className="flex items-center justify-between gap-4">
                  <dt>CPU</dt>
                  <dd>{formatPercent(overview.resources?.cpu_percent, 1)}</dd>
                </div>
                <div className="flex items-center justify-between gap-4">
                  <dt>Last Reload</dt>
                  <dd>{overview.model?.last_reload_at ? formatDate(overview.model.last_reload_at * 1000) : "—"}</dd>
                </div>
                <div className="flex items-center justify-between gap-4">
                  <dt>Canary Result</dt>
                  <dd>
                    {String(canaryStatus).replaceAll("_", " ")}
                    {typeof overview.model?.last_canary_deviation_percent === "number"
                      ? ` (${overview.model.last_canary_deviation_percent.toFixed(1)}%)`
                      : ""}
                  </dd>
                </div>
                <div className="flex items-center justify-between gap-4">
                  <dt>Generation RTF</dt>
                  <dd>{formatNumber(trend?.avg_generation_rtf, 2)}</dd>
                </div>
              </dl>
            </div>
          </div>
        </SectionCard>

        <SectionCard
          title="Quality Scoreboard"
          description="Recent books, their three-gate quality outcomes, and the current cross-book trend."
          aside={(
            <div className="rounded-full border border-slate-300 bg-slate-100 px-4 py-2 text-sm font-semibold text-slate-700">
              Trend: {trend?.trend ?? "stable"}
            </div>
          )}
        >
          <div className="grid gap-4 xl:grid-cols-[1.3fr_1fr]">
            <div className="overflow-hidden rounded-[1.5rem] border border-slate-200">
              <table className="min-w-full divide-y divide-slate-200 text-sm">
                <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">
                  <tr>
                    <th className="px-4 py-3">Book</th>
                    <th className="px-4 py-3">Chapters</th>
                    <th className="px-4 py-3">Gate 1</th>
                    <th className="px-4 py-3">Gate 2</th>
                    <th className="px-4 py-3">Gate 3</th>
                    <th className="px-4 py-3">Issues</th>
                    <th className="px-4 py-3">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 bg-white">
                  {recentBooks.length === 0 ? (
                    <tr>
                      <td className="px-4 py-6 text-slate-500" colSpan="7">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                          <div>No completed quality snapshots yet. Run chapter generation and Gate 3 QA to populate the overseer trendline.</div>
                          <div className="flex flex-wrap gap-2">
                            <a
                              className="rounded-full border border-slate-300 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.18em] text-slate-700 transition hover:border-slate-900 hover:text-slate-950"
                              href="/queue"
                            >
                              Open Queue
                            </a>
                            <a
                              className="rounded-full bg-slate-950 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.18em] text-white transition hover:bg-slate-800"
                              href="/"
                            >
                              Open Library
                            </a>
                          </div>
                        </div>
                      </td>
                    </tr>
                  ) : recentBooks.map((book) => {
                    const ready = ["A", "B"].includes(book.gate3_overall_grade);
                    return (
                      <tr
                        key={`${book.book_id}-${book.completed_at}`}
                        className={`cursor-pointer transition hover:bg-slate-50 ${selectedBookId === book.book_id ? "bg-sky-50" : ""}`}
                        onClick={() => setSelectedBookId(book.book_id)}
                      >
                        <td className="px-4 py-3 font-medium text-slate-900">{book.title}</td>
                        <td className="px-4 py-3 text-slate-600">{book.total_chapters}</td>
                        <td className="px-4 py-3 text-slate-600">{formatPercent(book.gate1_pass_rate, 1)}</td>
                        <td className="px-4 py-3 text-slate-600">{book.gate2_avg_grade.toFixed(2)}</td>
                        <td className="px-4 py-3">
                          <span className={`inline-flex rounded-full border px-3 py-1 text-xs font-semibold ${scoreTone(book.gate3_overall_grade)}`}>
                            {book.gate3_overall_grade}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-slate-600">{book.issues_found}</td>
                        <td className="px-4 py-3">
                          <span className={`inline-flex rounded-full border px-3 py-1 text-xs font-semibold ${statusTone(ready)}`}>
                            {ready ? "Ready" : "Review"}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="space-y-4">
              <TrendChart points={trend?.trend_points ?? []} />
              <div className="grid gap-4 sm:grid-cols-3">
                <StatCard label="Gate 1 Avg" value={formatPercent(trend?.avg_gate1_pass_rate, 1)} detail="Target >95%" />
                <StatCard label="Gate 2 Avg" value={formatNumber(trend?.avg_gate2_grade, 2)} detail="Target >3.0" />
                <StatCard label="Regenerated" value={formatNumber(trend?.avg_chunks_regenerated, 1)} detail="Chunks per book" />
              </div>
            </div>
          </div>
        </SectionCard>

        <div className="grid gap-6 xl:grid-cols-[1.05fr_1fr]">
          <SectionCard
            title="Flagged Items"
            description="Chapters and book issues that still need action before final export."
          >
            <div className="space-y-3">
              {overview.flaggedItems.length === 0 ? (
                <div className="rounded-[1.25rem] border border-emerald-200 bg-emerald-50 px-4 py-4 text-sm text-emerald-900">
                  No flagged items. Recent books are clean.
                </div>
              ) : overview.flaggedItems.map((item, index) => (
                <div key={`${item.book_id}-${item.chapter_n ?? "book"}-${index}`} className="rounded-[1.25rem] border border-slate-200 bg-slate-50 p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="text-sm font-semibold text-slate-950">{item.book_title}</div>
                      <div className="mt-1 text-sm text-slate-600">
                        {item.chapter_n ? `Chapter ${item.chapter_n}${item.chapter_title ? ` · ${item.chapter_title}` : ""}` : "Book-level issue"}
                      </div>
                    </div>
                    {item.qa_grade ? (
                      <span className={`inline-flex rounded-full border px-3 py-1 text-xs font-semibold ${scoreTone(item.qa_grade)}`}>
                        {item.qa_grade}
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-3 text-sm text-slate-700">{item.reason}</div>
                  <div className="mt-4 flex flex-wrap gap-2">
                    {(item.actions ?? []).map((action) => {
                      const href = action === "Approve Override" ? "/qa" : `/book/${item.book_id}`;
                      return (
                        <a
                          key={action}
                          className="rounded-full border border-slate-300 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.18em] text-slate-700 transition hover:border-slate-900 hover:text-slate-950"
                          href={href}
                        >
                          {action}
                        </a>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </SectionCard>

          <SectionCard
            title="Export Readiness"
            description="Detailed overseer report and export checklist for the selected recent book."
          >
            {selectedReportLoading ? (
              <ProgressHeartbeat
                isActive
                size="md"
                stage="Loading selected book report..."
                startTime={Date.now()}
              />
            ) : selectedReportError ? (
              <div className="rounded-[1.25rem] border border-rose-200 bg-rose-50 px-4 py-4 text-sm text-rose-900">
                {selectedReportError}
              </div>
            ) : selectedReport ? (
              <div className="space-y-4">
                <div className="rounded-[1.5rem] border border-slate-200 bg-slate-50 p-4">
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                      <div className="text-lg font-semibold text-slate-950">{selectedReport.title}</div>
                      <div className="mt-1 text-sm text-slate-600">
                        {selectedReport.total_chapters} chapters · Difficulty {formatNumber(selectedReport.manuscript_validation?.difficulty_score, 1)} / 10
                      </div>
                    </div>
                    <span className={`inline-flex rounded-full border px-3 py-1 text-xs font-semibold ${scoreTone(selectedGate3?.overall_grade ?? "F")}`}>
                      Gate 3 {selectedGate3?.overall_grade ?? "—"}
                    </span>
                  </div>
                </div>

                <div className="grid gap-3">
                  {readinessChecks.map((check) => (
                    <div key={check.name} className={`rounded-[1.25rem] border px-4 py-4 ${cardToneForCheck(check.passed)}`}>
                      <div className="flex items-center justify-between gap-4">
                        <div className="text-sm font-semibold uppercase tracking-[0.18em] text-slate-700">{check.name.replaceAll("_", " ")}</div>
                        <div className={`rounded-full border px-3 py-1 text-xs font-semibold ${check.passed ? "border-emerald-300 bg-white text-emerald-800" : "border-rose-300 bg-white text-rose-800"}`}>
                          {check.passed ? "Pass" : "Fail"}
                        </div>
                      </div>
                      <div className="mt-2 text-sm text-slate-700">{check.detail}</div>
                    </div>
                  ))}
                </div>

                <div className="grid gap-4 md:grid-cols-2">
                  <div className="rounded-[1.25rem] border border-slate-200 bg-white p-4">
                    <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Flagged Chapters</div>
                    <div className="mt-3 space-y-3 text-sm">
                      {(selectedReport.flagged_chapters ?? []).length === 0 ? (
                        <div className="text-slate-600">No chapter grades of C/F.</div>
                      ) : selectedReport.flagged_chapters.map((chapter) => (
                        <div key={chapter.chapter_n} className="rounded-[1rem] border border-slate-200 bg-slate-50 p-3">
                          <div className="font-semibold text-slate-900">
                            Chapter {chapter.chapter_n} {chapter.chapter_title ? `· ${chapter.chapter_title}` : ""}
                          </div>
                          <div className="mt-1 text-slate-700">{(chapter.issues ?? []).slice(0, 2).join(" ")}</div>
                        </div>
                      ))}
                    </div>
                  </div>

                  <div className="rounded-[1.25rem] border border-slate-200 bg-white p-4">
                    <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Pronunciation Issues</div>
                    <div className="mt-3 space-y-3 text-sm">
                      {(selectedReport.pronunciation_issues ?? []).length === 0 ? (
                        <div className="text-slate-600">No watchlist hits in this book.</div>
                      ) : selectedReport.pronunciation_issues.slice(0, 5).map((issue) => (
                        <div key={`${issue.chapter_n}-${issue.word}`} className="rounded-[1rem] border border-slate-200 bg-slate-50 p-3">
                          <div className="font-semibold text-slate-900">{issue.word}</div>
                          <div className="mt-1 text-slate-700">
                            Chapter {issue.chapter_n} · {issue.pronunciation_guide}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

                {(selectedVerification?.blockers?.length ?? 0) > 0 ? (
                  <div className="rounded-[1.25rem] border border-rose-200 bg-rose-50 p-4">
                    <div className="text-xs font-semibold uppercase tracking-[0.2em] text-rose-700">Blockers</div>
                    <div className="mt-3 space-y-2 text-sm text-rose-900">
                      {selectedVerification.blockers.map((blocker) => (
                        <div key={blocker}>{blocker}</div>
                      ))}
                    </div>
                  </div>
                ) : null}

                {(selectedVerification?.recommendations?.length ?? 0) > 0 ? (
                  <div className="rounded-[1.25rem] border border-amber-200 bg-amber-50 p-4">
                    <div className="text-xs font-semibold uppercase tracking-[0.2em] text-amber-700">Recommendations</div>
                    <div className="mt-3 space-y-2 text-sm text-amber-900">
                      {selectedVerification.recommendations.map((recommendation) => (
                        <div key={recommendation}>{recommendation}</div>
                      ))}
                    </div>
                  </div>
                ) : null}

                <div className="flex flex-wrap items-center gap-3">
                  <button
                    className={`rounded-full px-5 py-2 text-sm font-semibold transition ${selectedVerification?.ready_for_export ? "bg-slate-950 text-white hover:bg-slate-800" : "cursor-not-allowed bg-slate-200 text-slate-500"}`}
                    disabled={!selectedVerification?.ready_for_export}
                    onClick={() => {
                      window.location.assign(`/book/${selectedReport.book_id}`);
                    }}
                    type="button"
                  >
                    Export
                  </button>
                  <a
                    className="rounded-full border border-slate-300 px-5 py-2 text-sm font-semibold text-slate-700 transition hover:border-slate-900 hover:text-slate-950"
                    href={`/book/${selectedReport.book_id}`}
                  >
                    View Details
                  </a>
                </div>
              </div>
            ) : (
              <div className="rounded-[1.25rem] border border-dashed border-slate-300 bg-slate-50 px-4 py-5 text-sm text-slate-700">
                <div className="font-semibold text-slate-900">
                  {hasRecentBooks ? "Select a recent book to inspect its full overseer report." : "No overseer report available yet."}
                </div>
                <div className="mt-2">
                  {hasRecentBooks
                    ? "Pick a completed book from the trend table to inspect manuscript validation, flagged chapters, and export readiness."
                    : "Generate a book, run Gate 2 and Gate 3 QA, then return here for the cross-book oversight view."}
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  <a
                    className="rounded-full border border-slate-300 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.18em] text-slate-700 transition hover:border-slate-900 hover:text-slate-950"
                    href="/queue"
                  >
                    Open Queue
                  </a>
                  <a
                    className="rounded-full bg-slate-950 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.18em] text-white transition hover:bg-slate-800"
                    href="/"
                  >
                    Open Library
                  </a>
                </div>
              </div>
            )}
          </SectionCard>
        </div>
      </div>
    </AppShell>
  );
}
