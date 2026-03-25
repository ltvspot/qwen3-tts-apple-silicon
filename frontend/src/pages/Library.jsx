import React, { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import BookCard from "../components/BookCard";

const PAGE_SIZE = 500;

const NAV_ITEMS = [
  { label: "Voice Lab", to: "/voice-lab" },
  { label: "Queue", to: "/queue" },
  { label: "QA", to: "/qa" },
  { label: "Settings", to: "/settings" },
];

const EMPTY_STATS = {
  not_started: 0,
  parsed: 0,
  generating: 0,
  generated: 0,
  qa: 0,
  qa_approved: 0,
  exported: 0,
};

const STATUS_OPTIONS = [
  { value: "", label: "All Statuses" },
  { value: "not_started", label: "Not Started" },
  { value: "parsed", label: "Parsed" },
  { value: "generating", label: "Generating" },
  { value: "generated", label: "Generated" },
  { value: "qa", label: "QA Review" },
  { value: "qa_approved", label: "QA Approved" },
  { value: "exported", label: "Exported" },
];

const SORT_OPTIONS = [
  { value: "id", label: "ID" },
  { value: "title", label: "Title" },
  { value: "author", label: "Author" },
  { value: "page_count", label: "Page Count" },
];

const STAT_CARDS = [
  {
    key: "total",
    label: "Total",
    accentClass: "bg-white/10 text-amber-100 ring-white/15",
    eyebrowClass: "text-amber-100/70",
  },
  {
    key: "not_started",
    label: "Not Started",
    accentClass: "bg-stone-500/20 text-stone-100 ring-stone-400/30",
    eyebrowClass: "text-stone-200/70",
  },
  {
    key: "parsed",
    label: "Parsed",
    accentClass: "bg-sky-500/20 text-sky-100 ring-sky-400/30",
    eyebrowClass: "text-sky-200/70",
  },
  {
    key: "generating",
    label: "Generating",
    accentClass: "bg-amber-500/20 text-amber-100 ring-amber-400/30",
    eyebrowClass: "text-amber-200/70",
  },
  {
    key: "generated",
    label: "Generated",
    accentClass: "bg-emerald-500/20 text-emerald-100 ring-emerald-400/30",
    eyebrowClass: "text-emerald-200/70",
  },
  {
    key: "qa",
    label: "QA Review",
    accentClass: "bg-fuchsia-500/20 text-fuchsia-100 ring-fuchsia-400/30",
    eyebrowClass: "text-fuchsia-200/70",
  },
  {
    key: "qa_approved",
    label: "QA Approved",
    accentClass: "bg-teal-500/20 text-teal-100 ring-teal-400/30",
    eyebrowClass: "text-teal-200/70",
  },
  {
    key: "exported",
    label: "Exported",
    accentClass: "bg-yellow-500/20 text-yellow-100 ring-yellow-300/30",
    eyebrowClass: "text-yellow-200/70",
  },
];

function getTotalFromStats(stats) {
  return Object.values(stats ?? EMPTY_STATS).reduce((sum, count) => sum + count, 0);
}

async function fetchLibraryBatch(statusFilter, offset) {
  const params = new URLSearchParams({
    limit: String(PAGE_SIZE),
    offset: String(offset),
  });

  if (statusFilter) {
    params.append("status_filter", statusFilter);
  }

  const response = await fetch(`/api/library?${params.toString()}`);
  if (!response.ok) {
    throw new Error("Failed to fetch library");
  }

  return response.json();
}

async function fetchAllBooks(statusFilter) {
  const firstPage = await fetchLibraryBatch(statusFilter, 0);
  const books = [...firstPage.books];
  const total = firstPage.total ?? books.length;

  while (books.length < total) {
    const nextPage = await fetchLibraryBatch(statusFilter, books.length);
    if (!nextPage.books.length) {
      break;
    }
    books.push(...nextPage.books);
  }

  return {
    books,
    total,
    stats: firstPage.stats ?? EMPTY_STATS,
  };
}

export default function Library() {
  const navigate = useNavigate();
  const requestRef = useRef(0);

  const [books, setBooks] = useState([]);
  const [totalBooks, setTotalBooks] = useState(0);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [sortBy, setSortBy] = useState("id");
  const [stats, setStats] = useState(null);
  const [errorMessage, setErrorMessage] = useState("");

  const filteredBooks = [...books]
    .filter((book) => {
      const search = searchTerm.trim().toLowerCase();
      if (!search) {
        return true;
      }

      return (
        book.title.toLowerCase().includes(search) ||
        book.author.toLowerCase().includes(search)
      );
    })
    .sort((leftBook, rightBook) => {
      switch (sortBy) {
        case "title":
          return leftBook.title.localeCompare(rightBook.title);
        case "author":
          return leftBook.author.localeCompare(rightBook.author);
        case "page_count":
          return (rightBook.page_count ?? -1) - (leftBook.page_count ?? -1);
        case "id":
        default:
          return leftBook.id - rightBook.id;
      }
    });

  const catalogTotal = getTotalFromStats(stats ?? EMPTY_STATS);
  const catalogBadgeLabel = catalogTotal > 0
    ? `${catalogTotal}-title catalog`
    : "Publishing catalog";

  const fetchLibrary = async (selectedStatus = statusFilter) => {
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;

    setLoading(true);
    setErrorMessage("");

    try {
      const payload = await fetchAllBooks(selectedStatus);
      if (requestRef.current !== requestId) {
        return;
      }

      setBooks(payload.books);
      setTotalBooks(payload.total);
      setStats(payload.stats);
    } catch (error) {
      if (requestRef.current !== requestId) {
        return;
      }

      setBooks([]);
      setTotalBooks(0);
      setStats(EMPTY_STATS);
      setErrorMessage(
        error instanceof Error ? error.message : "Unable to load library.",
      );
      console.error("Error fetching library:", error);
    } finally {
      if (requestRef.current === requestId) {
        setLoading(false);
      }
    }
  };

  useEffect(() => {
    void fetchLibrary(statusFilter);
  }, [statusFilter]);

  const handleCardClick = (bookId) => {
    navigate(`/book/${bookId}`);
  };

  const handleRefreshLibrary = async () => {
    const refreshRequestId = requestRef.current + 1;
    requestRef.current = refreshRequestId;

    setLoading(true);
    setErrorMessage("");

    try {
      const response = await fetch("/api/library/scan", { method: "POST" });
      if (!response.ok) {
        throw new Error("Failed to scan library");
      }

      await fetchLibrary(statusFilter);
    } catch (error) {
      if (requestRef.current === refreshRequestId) {
        setLoading(false);
      }
      setErrorMessage(
        error instanceof Error ? error.message : "Unable to scan library.",
      );
      console.error("Error scanning library:", error);
    }
  };

  const hasSearchTerm = searchTerm.trim().length > 0;
  const emptyStateMessage = books.length === 0
    ? statusFilter
      ? "No books match the selected status."
      : 'No books in library. Click "Scan Library" to index manuscripts.'
    : "No books match your search.";

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top,_rgba(245,158,11,0.14),_transparent_34%),linear-gradient(135deg,#020617_0%,#0f172a_44%,#111827_100%)] text-white">
      <header className="sticky top-0 z-50 border-b border-white/10 bg-slate-950/85 shadow-2xl shadow-slate-950/20 backdrop-blur">
        <div className="mx-auto max-w-7xl px-6 py-6">
          <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
            <div className="max-w-3xl">
              <p className="text-xs font-semibold uppercase tracking-[0.35em] text-amber-200/75">
                Alexandria Audiobook Narrator
              </p>
              <div className="mt-3 flex flex-wrap items-end gap-3">
                <h1 className="text-4xl font-semibold tracking-tight text-white">
                  Library
                </h1>
                <span className="rounded-full border border-amber-200/20 bg-amber-200/10 px-3 py-1 text-xs font-medium uppercase tracking-[0.22em] text-amber-100">
                  {catalogBadgeLabel}
                </span>
              </div>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-300">
                Search, sort, and monitor every Alexandria manuscript from indexing
                through export.
              </p>
            </div>

            <div className="flex flex-col items-start gap-3 lg:items-end">
              <button
                className="rounded-full bg-amber-500 px-5 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-amber-400 disabled:cursor-not-allowed disabled:opacity-60"
                disabled={loading}
                onClick={handleRefreshLibrary}
                type="button"
              >
                {loading ? "Scanning..." : "Scan Library"}
              </button>
              <div className="flex flex-wrap gap-2">
                {NAV_ITEMS.map((item) => (
                  <Link
                    key={item.to}
                    className="rounded-full border border-white/10 bg-white/5 px-3 py-1.5 text-sm font-medium text-slate-200 transition hover:border-amber-300/30 hover:bg-white/10 hover:text-white"
                    to={item.to}
                  >
                    {item.label}
                  </Link>
                ))}
              </div>
            </div>
          </div>

          {stats && (
            <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-8">
              {STAT_CARDS.map((card) => {
                const count = card.key === "total"
                  ? catalogTotal
                  : stats[card.key] ?? 0;

                return (
                  <div
                    key={card.key}
                    className={`rounded-2xl p-4 ring-1 ${card.accentClass}`}
                  >
                    <div className={`text-[11px] font-semibold uppercase tracking-[0.22em] ${card.eyebrowClass}`}>
                      {card.label}
                    </div>
                    <div className="mt-2 text-2xl font-semibold text-white">
                      {count}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {errorMessage && (
            <div
              className="mt-6 rounded-2xl border border-rose-400/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-100"
              role="alert"
            >
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <span>{errorMessage}</span>
                <button
                  className="rounded-full border border-rose-300/40 px-4 py-2 text-xs font-semibold uppercase tracking-[0.24em] text-rose-50 transition hover:border-rose-200/60 hover:bg-rose-400/10"
                  onClick={() => {
                    void fetchLibrary(statusFilter);
                  }}
                  type="button"
                >
                  Retry Load
                </button>
              </div>
            </div>
          )}

          <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-3">
            <div className="md:col-span-2">
              <input
                className="w-full rounded-2xl border border-white/10 bg-white/5 px-4 py-3 text-white placeholder:text-slate-400 focus:border-amber-300/50 focus:outline-none focus:ring-2 focus:ring-amber-300/25"
                onChange={(event) => setSearchTerm(event.target.value)}
                placeholder="Search by title or author..."
                type="text"
                value={searchTerm}
              />
            </div>

            <div>
              <select
                className="w-full rounded-2xl border border-white/10 bg-white/5 px-4 py-3 text-white focus:border-amber-300/50 focus:outline-none focus:ring-2 focus:ring-amber-300/25"
                onChange={(event) => setStatusFilter(event.target.value)}
                value={statusFilter}
              >
                {STATUS_OPTIONS.map((option) => (
                  <option key={option.value || "all"} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span className="mr-2 text-sm font-medium text-slate-300">Sort by</span>
            {SORT_OPTIONS.map((option) => (
              <button
                key={option.value}
                className={`rounded-full px-3 py-1.5 text-sm font-medium transition ${
                  sortBy === option.value
                    ? "bg-amber-400 text-slate-950"
                    : "bg-white/5 text-slate-200 hover:bg-white/10 hover:text-white"
                }`}
                onClick={() => setSortBy(option.value)}
                type="button"
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-8">
        {loading && !books.length ? (
          <div className="flex min-h-[18rem] items-center justify-center rounded-[2rem] border border-dashed border-white/10 bg-white/[0.03] text-lg text-slate-300">
            Loading library...
          </div>
        ) : filteredBooks.length === 0 ? (
          <div className="flex min-h-[18rem] items-center justify-center rounded-[2rem] border border-dashed border-white/10 bg-white/[0.03] px-6 text-center text-lg text-slate-300">
            {emptyStateMessage}
          </div>
        ) : (
          <>
            <div className="mb-5 flex flex-col gap-2 text-sm text-slate-300 sm:flex-row sm:items-center sm:justify-between">
              <div>
                Showing {filteredBooks.length} of {totalBooks} books
                {statusFilter ? " in the current status filter" : ""}
              </div>
              <div className="text-slate-400">
                {hasSearchTerm ? "Live search is active." : "Live search spans title and author."}
              </div>
            </div>

            <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {filteredBooks.map((book) => (
                <BookCard
                  key={book.id}
                  book={book}
                  onClick={() => handleCardClick(book.id)}
                />
              ))}
            </div>
          </>
        )}
      </main>
    </div>
  );
}
