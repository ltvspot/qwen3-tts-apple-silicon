import React, { useEffect, useMemo, useRef, useState } from "react";

function flattenEntries(dictionaryPayload) {
  const globalEntries = Object.entries(dictionaryPayload?.global ?? {}).map(([word, pronunciation]) => ({
    bookId: null,
    pronunciation,
    scope: "global",
    word,
  }));
  const bookEntries = Object.entries(dictionaryPayload?.per_book ?? {}).flatMap(([bookId, entries]) =>
    Object.entries(entries ?? {}).map(([word, pronunciation]) => ({
      bookId: Number(bookId),
      pronunciation,
      scope: "book",
      word,
    })),
  );
  return [...globalEntries, ...bookEntries].sort((left, right) => left.word.localeCompare(right.word));
}

async function parseResponse(response, fallbackMessage) {
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail ?? fallbackMessage);
  }
  return response.json();
}

export default function PronunciationSettings() {
  const [bookForm, setBookForm] = useState({ bookId: "", pronunciation: "", word: "" });
  const [dictionaryPayload, setDictionaryPayload] = useState({ global: {}, per_book: {} });
  const [errorMessage, setErrorMessage] = useState("");
  const [globalForm, setGlobalForm] = useState({ pronunciation: "", word: "" });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [searchValue, setSearchValue] = useState("");
  const [successMessage, setSuccessMessage] = useState("");
  const [suggestions, setSuggestions] = useState([]);
  const globalFormSectionRef = useRef(null);
  const globalPronunciationInputRef = useRef(null);

  async function loadPronunciations() {
    setLoading(true);
    try {
      const [dictionaryResponse, suggestionsResponse] = await Promise.all([
        fetch("/api/pronunciation"),
        fetch("/api/pronunciation/suggestions"),
      ]);
      const [dictionary, suggestionPayload] = await Promise.all([
        parseResponse(dictionaryResponse, "Failed to load pronunciation dictionary."),
        parseResponse(suggestionsResponse, "Failed to load pronunciation suggestions."),
      ]);
      setDictionaryPayload(dictionary);
      setSuggestions(suggestionPayload);
      setErrorMessage("");
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Failed to load pronunciation settings.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadPronunciations();
  }, []);

  const filteredEntries = useMemo(() => {
    const entries = flattenEntries(dictionaryPayload);
    if (!searchValue.trim()) {
      return entries;
    }
    const normalizedSearch = searchValue.trim().toLowerCase();
    return entries.filter((entry) =>
      `${entry.word} ${entry.pronunciation} ${entry.scope} ${entry.bookId ?? ""}`.toLowerCase().includes(normalizedSearch),
    );
  }, [dictionaryPayload, searchValue]);

  async function saveGlobalEntry() {
    if (!globalForm.word.trim() || !globalForm.pronunciation.trim()) {
      setErrorMessage("Enter both a word and a pronunciation.");
      return;
    }
    setSaving(true);
    try {
      const response = await fetch(`/api/pronunciation/global/${encodeURIComponent(globalForm.word.trim())}`, {
        body: JSON.stringify({ pronunciation: globalForm.pronunciation.trim() }),
        headers: { "Content-Type": "application/json" },
        method: "PUT",
      });
      const payload = await parseResponse(response, "Failed to save pronunciation.");
      setDictionaryPayload(payload);
      setGlobalForm({ pronunciation: "", word: "" });
      setSuccessMessage(`Saved global pronunciation for ${globalForm.word.trim()}.`);
      setErrorMessage("");
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Failed to save pronunciation.");
    } finally {
      setSaving(false);
    }
  }

  async function saveBookEntry() {
    if (!bookForm.bookId.trim() || !bookForm.word.trim() || !bookForm.pronunciation.trim()) {
      setErrorMessage("Enter a book ID, word, and pronunciation.");
      return;
    }
    setSaving(true);
    try {
      const response = await fetch(
        `/api/pronunciation/book/${encodeURIComponent(bookForm.bookId.trim())}/${encodeURIComponent(bookForm.word.trim())}`,
        {
          body: JSON.stringify({ pronunciation: bookForm.pronunciation.trim() }),
          headers: { "Content-Type": "application/json" },
          method: "PUT",
        },
      );
      const payload = await parseResponse(response, "Failed to save per-book pronunciation.");
      setDictionaryPayload(payload);
      setBookForm({ bookId: "", pronunciation: "", word: "" });
      setSuccessMessage(`Saved book-specific pronunciation for ${bookForm.word.trim()}.`);
      setErrorMessage("");
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Failed to save per-book pronunciation.");
    } finally {
      setSaving(false);
    }
  }

  async function deleteEntry(entry) {
    setSaving(true);
    try {
      const endpoint = entry.scope === "global"
        ? `/api/pronunciation/global/${encodeURIComponent(entry.word)}`
        : `/api/pronunciation/book/${entry.bookId}/${encodeURIComponent(entry.word)}`;
      const response = await fetch(endpoint, { method: "DELETE" });
      const payload = await parseResponse(response, "Failed to delete pronunciation.");
      setDictionaryPayload(payload);
      setSuccessMessage(`Deleted pronunciation for ${entry.word}.`);
      setErrorMessage("");
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Failed to delete pronunciation.");
    } finally {
      setSaving(false);
    }
  }

  function handleQuickAddSuggestion(suggestion) {
    setGlobalForm({
      pronunciation: suggestion.suggested_pronunciation ?? suggestion.pronunciation ?? "",
      word: suggestion.word ?? "",
    });
    setErrorMessage("");
    setSuccessMessage(`Loaded ${suggestion.word} into the global dictionary form.`);

    const scheduleFocus = window.requestAnimationFrame
      ? window.requestAnimationFrame.bind(window)
      : (callback) => window.setTimeout(callback, 0);

    scheduleFocus(() => {
      globalFormSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      globalPronunciationInputRef.current?.focus();
      globalPronunciationInputRef.current?.select();
    });
  }

  if (loading) {
    return (
      <div className="rounded-[2rem] border border-slate-200 bg-white p-6 text-sm text-slate-600 shadow-sm">
        Loading pronunciation controls...
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {successMessage ? (
        <div className="rounded-[1.75rem] border border-emerald-200 bg-emerald-50 px-5 py-4 text-sm text-emerald-800">
          {successMessage}
        </div>
      ) : null}
      {errorMessage ? (
        <div className="rounded-[1.75rem] border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700">
          {errorMessage}
        </div>
      ) : null}

      <section className="rounded-[2rem] border border-slate-200 bg-white p-6 shadow-sm" ref={globalFormSectionRef}>
        <div className="grid gap-6 lg:grid-cols-2">
          <div className="space-y-3">
            <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">Global</div>
            <h3 className="text-xl font-semibold text-slate-950">Shared pronunciations</h3>
            <input
              aria-label="Global pronunciation word"
              className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-950"
              onChange={(event) => setGlobalForm((current) => ({ ...current, word: event.target.value }))}
              placeholder="Word"
              value={globalForm.word}
            />
            <input
              aria-label="Global pronunciation value"
              className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-950"
              onChange={(event) => setGlobalForm((current) => ({ ...current, pronunciation: event.target.value }))}
              placeholder="Pronunciation"
              ref={globalPronunciationInputRef}
              value={globalForm.pronunciation}
            />
            <button
              className="rounded-full bg-slate-950 px-5 py-3 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
              disabled={saving}
              onClick={() => {
                void saveGlobalEntry();
              }}
              type="button"
            >
              Save Global Entry
            </button>
          </div>

          <div className="space-y-3">
            <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">Per Book</div>
            <h3 className="text-xl font-semibold text-slate-950">Book-specific overrides</h3>
            <input
              aria-label="Pronunciation book id"
              className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-950"
              onChange={(event) => setBookForm((current) => ({ ...current, bookId: event.target.value }))}
              placeholder="Book ID"
              value={bookForm.bookId}
            />
            <input
              aria-label="Book pronunciation word"
              className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-950"
              onChange={(event) => setBookForm((current) => ({ ...current, word: event.target.value }))}
              placeholder="Word"
              value={bookForm.word}
            />
            <input
              aria-label="Book pronunciation value"
              className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-950"
              onChange={(event) => setBookForm((current) => ({ ...current, pronunciation: event.target.value }))}
              placeholder="Pronunciation"
              value={bookForm.pronunciation}
            />
            <button
              className="rounded-full bg-slate-950 px-5 py-3 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
              disabled={saving}
              onClick={() => {
                void saveBookEntry();
              }}
              type="button"
            >
              Save Book Override
            </button>
          </div>
        </div>
      </section>

      <section className="rounded-[2rem] border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">Dictionary</div>
            <h3 className="mt-2 text-xl font-semibold text-slate-950">Current entries</h3>
          </div>
          <input
            aria-label="Pronunciation search"
            className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-950 lg:max-w-sm"
            onChange={(event) => setSearchValue(event.target.value)}
            placeholder="Search word, pronunciation, or book ID"
            value={searchValue}
          />
        </div>

        <div className="mt-5 space-y-3">
          {filteredEntries.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-500">
              No pronunciation entries match the current search.
            </div>
          ) : filteredEntries.map((entry) => (
            <div
              className="flex flex-col gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4 lg:flex-row lg:items-center lg:justify-between"
              key={`${entry.scope}-${entry.bookId ?? "global"}-${entry.word}`}
            >
              <div>
                <div className="text-sm font-semibold text-slate-950">{entry.word}</div>
                <div className="mt-1 text-sm text-slate-600">{entry.pronunciation}</div>
                <div className="mt-1 text-xs uppercase tracking-[0.18em] text-slate-400">
                  {entry.scope === "global" ? "Global" : `Book ${entry.bookId}`}
                </div>
              </div>
              <button
                className="rounded-full border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:border-slate-950 hover:text-slate-950"
                disabled={saving}
                onClick={() => {
                  void deleteEntry(entry);
                }}
                type="button"
              >
                Delete
              </button>
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-[2rem] border border-slate-200 bg-white p-6 shadow-sm">
        <div className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">Suggestions</div>
        <h3 className="mt-2 text-xl font-semibold text-slate-950">Potential proper nouns from QA mismatches</h3>
        <div className="mt-5 space-y-3">
          {suggestions.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-500">
              No pronunciation suggestions are available yet.
            </div>
          ) : suggestions.map((suggestion) => (
            <div
              className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4"
              key={`${suggestion.book_id}-${suggestion.chapter_n}-${suggestion.word}`}
            >
              <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div>
                  <div className="text-sm font-semibold text-slate-950">{suggestion.word}</div>
                  <div className="mt-1 text-sm text-slate-600">
                    Book {suggestion.book_id} · Chapter {suggestion.chapter_n} · {suggestion.book_title}
                  </div>
                  <div className="mt-1 text-sm text-slate-500">{suggestion.reason}</div>
                  {suggestion.suggested_pronunciation ?? suggestion.pronunciation ? (
                    <div className="mt-1 text-sm text-slate-500">
                      Suggested pronunciation: {suggestion.suggested_pronunciation ?? suggestion.pronunciation}
                    </div>
                  ) : null}
                </div>
                <button
                  className="rounded-full border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:border-slate-950 hover:text-slate-950"
                  onClick={() => handleQuickAddSuggestion(suggestion)}
                  type="button"
                >
                  Add to Dictionary
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
