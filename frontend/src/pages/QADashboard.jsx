import React, { useEffect, useRef, useState } from "react";
import AppShell from "../components/AppShell";
import ChapterQACard from "../components/ChapterQACard";
import QAStatusBadge from "../components/QAStatusBadge";

const REVIEWER_NAME = "Tim";

const SORT_OPTIONS = [
  { value: "attention", label: "Needs Attention First" },
  { value: "recent", label: "Most Recent" },
  { value: "status", label: "Status" },
  { value: "title", label: "Book Title" },
];

const STATUS_OPTIONS = [
  { value: "", label: "All Statuses" },
  { value: "pass", label: "Pass" },
  { value: "warning", label: "Warning" },
  { value: "fail", label: "Fail" },
  { value: "pending_review", label: "Pending Review" },
];

function formatCheckedAt(value) {
  if (!value) {
    return "No QA run yet";
  }

  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function filterChapters(chapters, statusFilter) {
  if (!statusFilter) {
    return chapters;
  }

  if (statusFilter === "pending_review") {
    return chapters.filter(
      (chapter) =>
        chapter.manual_status === null &&
        (chapter.overall_status === "warning" || chapter.overall_status === "fail"),
    );
  }

  return chapters.filter((chapter) => chapter.overall_status === statusFilter);
}

function sortBooks(books, sortBy) {
  return [...books].sort((leftBook, rightBook) => {
    if (sortBy === "title") {
      return leftBook.book_title.localeCompare(rightBook.book_title);
    }

    if (sortBy === "status") {
      const statusRank = { fail: 0, warning: 1, pass: 2 };
      return (
        (statusRank[leftBook.overall_book_status] ?? 99) -
        (statusRank[rightBook.overall_book_status] ?? 99)
      );
    }

    if (sortBy === "recent") {
      return new Date(rightBook.latest_checked_at ?? 0).getTime() - new Date(leftBook.latest_checked_at ?? 0).getTime();
    }

    return (
      rightBook.chapters_pending_manual - leftBook.chapters_pending_manual ||
      rightBook.chapters_fail - leftBook.chapters_fail ||
      rightBook.chapters_warning - leftBook.chapters_warning ||
      leftBook.book_title.localeCompare(rightBook.book_title)
    );
  });
}

function summarizeBooks(books) {
  const summary = {
    books_reviewed: books.length,
    chapters_fail: 0,
    chapters_pass: 0,
    chapters_pending_manual: 0,
    chapters_reviewed: 0,
    chapters_warning: 0,
  };

  books.forEach((book) => {
    summary.chapters_reviewed += book.chapters.length;
    summary.chapters_pass += book.chapters.filter((chapter) => chapter.overall_status === "pass").length;
    summary.chapters_warning += book.chapters.filter((chapter) => chapter.overall_status === "warning").length;
    summary.chapters_fail += book.chapters.filter((chapter) => chapter.overall_status === "fail").length;
    summary.chapters_pending_manual += book.chapters.filter(
      (chapter) =>
        chapter.manual_status === null &&
        (chapter.overall_status === "warning" || chapter.overall_status === "fail"),
    ).length;
  });

  return summary;
}

function buildVisibleBooks(rawBooks, statusFilter, selectedBookId, sortBy) {
  const filteredBooks = rawBooks
    .filter((book) => !selectedBookId || String(book.book_id) === selectedBookId)
    .map((book) => ({
      ...book,
      chapters: filterChapters(book.chapters, statusFilter),
    }))
    .filter((book) => book.chapters.length > 0);

  return sortBooks(filteredBooks, sortBy);
}

function StatCard({ accentClass, label, value }) {
  return (
    <div className={`rounded-3xl border bg-white p-5 shadow-sm ${accentClass}`}>
      <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">{label}</div>
      <div className="mt-3 text-3xl font-semibold text-slate-950">{value}</div>
    </div>
  );
}

export default function QADashboard() {
  const mountedRef = useRef(true);
  const requestRef = useRef(0);

  const [actionError, setActionError] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [actionPendingId, setActionPendingId] = useState(null);
  const [dashboard, setDashboard] = useState({ books_needing_review: [], summary: null });
  const [errorMessage, setErrorMessage] = useState("");
  const [expandedBookId, setExpandedBookId] = useState(null);
  const [flagChapter, setFlagChapter] = useState(null);
  const [flagNotes, setFlagNotes] = useState("");
  const [loading, setLoading] = useState(true);
  const [sortBy, setSortBy] = useState("attention");
  const [statusFilter, setStatusFilter] = useState("");
  const [selectedBookId, setSelectedBookId] = useState("");

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
      const response = await fetch("/api/qa/dashboard?limit=200");
      if (!response.ok) {
        throw new Error("Failed to load the QA dashboard.");
      }

      const payload = await response.json();
      if (!mountedRef.current || requestRef.current !== requestId) {
        return;
      }

      setDashboard(payload);
      setErrorMessage("");
    } catch (error) {
      if (mountedRef.current && requestRef.current === requestId) {
        setErrorMessage(error instanceof Error ? error.message : "Unable to load QA data.");
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

  const bookOptions = [...dashboard.books_needing_review].sort((leftBook, rightBook) =>
    leftBook.book_title.localeCompare(rightBook.book_title),
  );
  const visibleBooks = buildVisibleBooks(
    dashboard.books_needing_review,
    statusFilter,
    selectedBookId,
    sortBy,
  );
  const visibleSummary = summarizeBooks(visibleBooks);

  async function submitManualReview(chapter, manualStatus, notes = "") {
    const actionId = `${chapter.book_id}-${chapter.chapter_n}`;
    setActionPendingId(actionId);
    setActionError("");
    setActionMessage("");

    try {
      const response = await fetch(`/api/book/${chapter.book_id}/chapter/${chapter.chapter_n}/qa`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          manual_status: manualStatus,
          notes,
          reviewed_by: REVIEWER_NAME,
        }),
      });
      if (!response.ok) {
        throw new Error("Unable to save the manual QA review.");
      }

      await loadDashboard({ showLoading: false });
      return true;
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Unable to save the QA review.");
      return false;
    } finally {
      if (mountedRef.current) {
        setActionPendingId(null);
      }
    }
  }

  function handleOpenFlag(chapter) {
    setFlagChapter(chapter);
    setFlagNotes(chapter.manual_notes ?? "");
  }

  async function handleSubmitFlag() {
    if (!flagChapter) {
      return;
    }

    const saved = await submitManualReview(flagChapter, "flagged", flagNotes);
    if (saved && mountedRef.current) {
      setFlagChapter(null);
      setFlagNotes("");
    }
  }

  async function handleApproveAllPassing(book) {
    const actionId = `book-${book.book_id}-approve-all`;
    setActionPendingId(actionId);
    setActionError("");
    setActionMessage("");

    try {
      const response = await fetch(`/api/book/${book.book_id}/approve-all-passing`, {
        method: "POST",
      });
      if (!response.ok) {
        throw new Error("Unable to approve all passing chapters for this book.");
      }

      const payload = await response.json();
      if (!mountedRef.current) {
        return;
      }

      setActionMessage(
        `Approved ${payload.approved} passing chapter${payload.approved === 1 ? "" : "s"} for ${book.book_title}.`,
      );
      await loadDashboard({ showLoading: false });
    } catch (error) {
      if (mountedRef.current) {
        setActionError(
          error instanceof Error ? error.message : "Unable to approve all passing chapters.",
        );
      }
    } finally {
      if (mountedRef.current) {
        setActionPendingId(null);
      }
    }
  }

  return (
    <AppShell
      title="QA Dashboard"
      description="Automatic chapter checks, manual review, and quick audio spot-checking for production readiness."
    >
      <div className="space-y-8">
        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
          <StatCard accentClass="border-slate-200" label="Chapters Reviewed" value={visibleSummary.chapters_reviewed} />
          <StatCard accentClass="border-emerald-200" label="Passed" value={visibleSummary.chapters_pass} />
          <StatCard accentClass="border-amber-200" label="Warnings" value={visibleSummary.chapters_warning} />
          <StatCard accentClass="border-rose-200" label="Failed" value={visibleSummary.chapters_fail} />
          <StatCard accentClass="border-slate-300" label="Pending Manual" value={visibleSummary.chapters_pending_manual} />
        </section>

        <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">Filters</p>
              <h2 className="mt-2 text-2xl font-semibold text-slate-950">Review Queue</h2>
              <p className="mt-2 text-sm text-slate-600">
                Focus on unresolved QA issues, or inspect the full automatic QA history.
              </p>
            </div>

            <div className="grid gap-3 md:grid-cols-3">
              <label className="text-sm font-medium text-slate-600">
                Status
                <select
                  className="mt-2 w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900"
                  onChange={(event) => setStatusFilter(event.target.value)}
                  value={statusFilter}
                >
                  {STATUS_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="text-sm font-medium text-slate-600">
                Book
                <select
                  className="mt-2 w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900"
                  onChange={(event) => setSelectedBookId(event.target.value)}
                  value={selectedBookId}
                >
                  <option value="">All Books</option>
                  {bookOptions.map((book) => (
                    <option key={book.book_id} value={book.book_id}>
                      {book.book_title}
                    </option>
                  ))}
                </select>
              </label>

              <label className="text-sm font-medium text-slate-600">
                Sort
                <select
                  className="mt-2 w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900"
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
          </div>
        </section>

        {errorMessage ? (
          <div className="rounded-3xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700">
            {errorMessage}
          </div>
        ) : null}
        {actionMessage ? (
          <div className="rounded-3xl border border-emerald-200 bg-emerald-50 px-5 py-4 text-sm text-emerald-800">
            {actionMessage}
          </div>
        ) : null}
        {actionError ? (
          <div className="rounded-3xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700">
            {actionError}
          </div>
        ) : null}

        <section className="space-y-4">
          {loading ? (
            <div className="rounded-3xl border border-slate-200 bg-white px-6 py-12 text-center text-sm text-slate-500 shadow-sm">
              Loading QA results...
            </div>
          ) : null}

          {!loading && visibleBooks.length === 0 ? (
            <div className="rounded-3xl border border-dashed border-slate-300 bg-white px-6 py-12 text-center shadow-sm">
              <div className="text-sm font-semibold uppercase tracking-[0.24em] text-slate-500">No Matching QA Data</div>
              <p className="mt-3 text-sm text-slate-600">
                Generate a chapter to populate automatic QA, or loosen the current filters.
              </p>
            </div>
          ) : null}

          {!loading
            ? visibleBooks.map((book) => {
                const expanded = expandedBookId === book.book_id;

                return (
                  <article
                    className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm"
                    data-book-qa={book.book_id}
                    key={book.book_id}
                  >
                    <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
                      <div>
                        <h3 className="text-2xl font-semibold text-slate-950">{book.book_title}</h3>
                        <p className="mt-2 text-sm text-slate-600">{book.book_author}</p>
                        <p className="mt-2 text-xs font-medium uppercase tracking-[0.18em] text-slate-500">
                          Last checked {formatCheckedAt(book.latest_checked_at)}
                        </p>
                      </div>

                      <div className="flex flex-col gap-4 xl:items-end">
                        <div className="flex flex-wrap gap-2">
                          <QAStatusBadge label={`${book.chapters_pass} passed`} status="pass" />
                          <QAStatusBadge label={`${book.chapters_warning} warnings`} status="warning" />
                          <QAStatusBadge label={`${book.chapters_fail} failed`} status="fail" />
                          <QAStatusBadge label={`${book.chapters_pending_manual} pending`} status="pending" />
                        </div>

                        <div className="flex flex-wrap justify-end gap-2">
                          <button
                            className="inline-flex items-center justify-center rounded-full border border-emerald-200 bg-emerald-50 px-5 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-emerald-800 transition hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-60"
                            disabled={book.chapters_pass === 0 || actionPendingId === `book-${book.book_id}-approve-all`}
                            onClick={() => {
                              void handleApproveAllPassing(book);
                            }}
                            type="button"
                          >
                            {actionPendingId === `book-${book.book_id}-approve-all`
                              ? "Approving..."
                              : "Approve All Passing"}
                          </button>
                          <button
                            className="inline-flex items-center justify-center rounded-full border border-slate-200 px-5 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-700 transition hover:border-slate-300 hover:text-slate-950"
                            onClick={() =>
                              setExpandedBookId((current) => (current === book.book_id ? null : book.book_id))
                            }
                            type="button"
                          >
                            {expanded ? "Hide Chapters" : "Review Chapters"}
                          </button>
                        </div>
                      </div>
                    </div>

                    {expanded ? (
                      <div className="mt-6 grid gap-4">
                        {book.chapters.map((chapter) => (
                          <ChapterQACard
                            actionPending={actionPendingId === `${chapter.book_id}-${chapter.chapter_n}`}
                            chapter={chapter}
                            key={`${chapter.book_id}-${chapter.chapter_n}`}
                            onApprove={(selectedChapter) => submitManualReview(selectedChapter, "approved")}
                            onFlag={handleOpenFlag}
                          />
                        ))}
                      </div>
                    ) : null}
                  </article>
                );
              })
            : null}
        </section>
      </div>

      {flagChapter ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 px-6">
          <div className="w-full max-w-lg rounded-3xl border border-slate-200 bg-white p-6 shadow-2xl">
            <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">Manual QA</p>
            <h3 className="mt-2 text-2xl font-semibold text-slate-950">
              Flag Chapter {flagChapter.chapter_n}
            </h3>
            <p className="mt-2 text-sm text-slate-600">
              Add reviewer notes for regeneration or cleanup.
            </p>

            <label className="mt-5 block text-sm font-medium text-slate-700">
              Notes
              <textarea
                className="mt-2 min-h-[9rem] w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm text-slate-900"
                name="flag-notes"
                onChange={(event) => setFlagNotes(event.target.value)}
                placeholder="Describe the audio problem and what needs to change."
                value={flagNotes}
              />
            </label>

            <div className="mt-6 flex justify-end gap-3">
              <button
                className="rounded-full border border-slate-200 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-600 transition hover:border-slate-300 hover:text-slate-900"
                onClick={() => {
                  setFlagChapter(null);
                  setFlagNotes("");
                }}
                type="button"
              >
                Cancel
              </button>
              <button
                className="rounded-full border border-rose-200 bg-rose-50 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-rose-700 transition hover:bg-rose-100"
                onClick={() => {
                  void handleSubmitFlag();
                }}
                type="button"
              >
                Save Flag
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </AppShell>
  );
}
