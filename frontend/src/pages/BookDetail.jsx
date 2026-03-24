import React, { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import ChapterList from "../components/ChapterList";
import NarrationSettings from "../components/NarrationSettings";
import TextPreview from "../components/TextPreview";

const DEFAULT_NARRATION_SETTINGS = {
  voice: "Ethan",
  emotion: "warm",
  speed: 1.0,
  engine: "qwen3_tts",
};

function chapterHasUnsavedChanges(selectedChapter, draftText, editMode) {
  if (!editMode || !selectedChapter) {
    return false;
  }

  return draftText !== (selectedChapter.text_content ?? "");
}

export default function BookDetail() {
  const navigate = useNavigate();
  const requestRef = useRef(0);
  const { id } = useParams();

  const [book, setBook] = useState(null);
  const [chapters, setChapters] = useState([]);
  const [selectedChapterId, setSelectedChapterId] = useState(null);
  const [draftText, setDraftText] = useState("");
  const [editMode, setEditMode] = useState(false);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [saving, setSaving] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [saveErrorMessage, setSaveErrorMessage] = useState("");
  const [narrationSettings, setNarrationSettings] = useState(DEFAULT_NARRATION_SETTINGS);

  const selectedChapter = chapters.find((chapter) => chapter.id === selectedChapterId) ?? null;
  const hasUnsavedChanges = chapterHasUnsavedChanges(selectedChapter, draftText, editMode);

  useEffect(() => {
    if (!editMode) {
      setDraftText(selectedChapter?.text_content ?? "");
    }
  }, [editMode, selectedChapter]);

  useEffect(() => {
    void fetchBookData();
  }, [id]);

  async function fetchBookData() {
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;

    setLoading(true);
    setNotFound(false);
    setErrorMessage("");
    setSaveErrorMessage("");
    setBook(null);
    setChapters([]);
    setSelectedChapterId(null);
    setDraftText("");
    setEditMode(false);

    try {
      const bookResponse = await fetch(`/api/book/${id}`);
      if (bookResponse.status === 404) {
        if (requestRef.current === requestId) {
          setNotFound(true);
        }
        return;
      }
      if (!bookResponse.ok) {
        throw new Error("Failed to fetch book details.");
      }

      const bookPayload = await bookResponse.json();
      if (requestRef.current !== requestId) {
        return;
      }

      setBook(bookPayload);

      const chaptersResponse = await fetch(`/api/book/${id}/chapters`);
      if (!chaptersResponse.ok) {
        throw new Error("Failed to fetch chapter list.");
      }

      const chaptersPayload = await chaptersResponse.json();
      if (requestRef.current !== requestId) {
        return;
      }

      setChapters(chaptersPayload);
      setSelectedChapterId((currentSelection) => {
        if (chaptersPayload.some((chapter) => chapter.id === currentSelection)) {
          return currentSelection;
        }
        return chaptersPayload[0]?.id ?? null;
      });
    } catch (error) {
      if (requestRef.current !== requestId) {
        return;
      }

      setErrorMessage(
        error instanceof Error ? error.message : "Unable to load this book right now.",
      );
      console.error("Error fetching book detail:", error);
    } finally {
      if (requestRef.current === requestId) {
        setLoading(false);
      }
    }
  }

  function handleChapterSelect(chapter) {
    if (chapter.id === selectedChapterId) {
      return;
    }

    if (hasUnsavedChanges) {
      const shouldDiscard = window.confirm("Discard unsaved chapter edits?");
      if (!shouldDiscard) {
        return;
      }
    }

    setSaveErrorMessage("");
    setEditMode(false);
    setSelectedChapterId(chapter.id);
    setDraftText(chapter.text_content ?? "");
  }

  function handleBeginEdit() {
    if (!selectedChapter) {
      return;
    }

    setSaveErrorMessage("");
    setDraftText(selectedChapter.text_content ?? "");
    setEditMode(true);
  }

  function handleCancelEdit() {
    setSaveErrorMessage("");
    setDraftText(selectedChapter?.text_content ?? "");
    setEditMode(false);
  }

  function handleTextChange(nextText) {
    setSaveErrorMessage("");
    setDraftText(nextText);
  }

  async function handleSaveText() {
    if (!selectedChapter) {
      return;
    }

    if (!draftText.trim()) {
      setSaveErrorMessage("Chapter text cannot be empty.");
      return;
    }

    setSaving(true);
    setSaveErrorMessage("");

    try {
      const response = await fetch(`/api/book/${id}/chapter/${selectedChapter.number}/text`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          text_content: draftText,
        }),
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        const detail = typeof payload?.detail === "string"
          ? payload.detail
          : "Failed to save chapter text.";
        throw new Error(detail);
      }

      const updatedChapter = await response.json();
      setChapters((currentChapters) =>
        currentChapters.map((chapter) =>
          chapter.id === updatedChapter.id ? updatedChapter : chapter,
        ),
      );
      setSelectedChapterId(updatedChapter.id);
      setDraftText(updatedChapter.text_content ?? "");
      setEditMode(false);
    } catch (error) {
      setSaveErrorMessage(
        error instanceof Error ? error.message : "Failed to save chapter text.",
      );
      console.error("Error saving chapter text:", error);
    } finally {
      setSaving(false);
    }
  }

  function handleNarrationSettingsChange(nextSettings) {
    setNarrationSettings(nextSettings);
  }

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[radial-gradient(circle_at_top,_rgba(245,158,11,0.16),_transparent_34%),linear-gradient(135deg,#020617_0%,#0f172a_44%,#111827_100%)] px-6 text-white">
        <div className="rounded-3xl border border-white/10 bg-white/[0.04] px-6 py-5 text-center shadow-2xl shadow-slate-950/30">
          <div className="text-xs font-semibold uppercase tracking-[0.32em] text-amber-200/75">
            Alexandria Audiobook Narrator
          </div>
          <div className="mt-3 text-lg font-semibold">Loading book details...</div>
        </div>
      </div>
    );
  }

  if (notFound) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[radial-gradient(circle_at_top,_rgba(245,158,11,0.16),_transparent_34%),linear-gradient(135deg,#020617_0%,#0f172a_44%,#111827_100%)] px-6 text-white">
        <div className="max-w-md rounded-[2rem] border border-white/10 bg-white/[0.05] p-8 text-center shadow-2xl shadow-slate-950/30">
          <div className="text-xs font-semibold uppercase tracking-[0.32em] text-amber-200/75">
            Missing Book
          </div>
          <h1 className="mt-4 text-3xl font-semibold">Book not found</h1>
          <p className="mt-3 text-sm leading-7 text-slate-300">
            The requested record is not indexed in the current library database.
          </p>
          <button
            className="mt-6 inline-flex items-center rounded-full border border-amber-300/30 bg-amber-400/10 px-5 py-2 text-sm font-semibold text-amber-100 transition hover:bg-amber-400/20"
            onClick={() => navigate("/")}
            type="button"
          >
            Back to Library
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top,_rgba(245,158,11,0.14),_transparent_34%),linear-gradient(135deg,#020617_0%,#0f172a_44%,#111827_100%)] text-white">
      <header className="sticky top-0 z-50 border-b border-white/10 bg-slate-950/85 shadow-2xl shadow-slate-950/20 backdrop-blur">
        <div className="mx-auto max-w-[110rem] px-4 py-5 sm:px-6 lg:px-8">
          <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
            <div className="max-w-4xl">
              <button
                className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] px-4 py-2 text-xs font-semibold uppercase tracking-[0.24em] text-slate-200 transition hover:border-amber-300/30 hover:text-amber-100"
                onClick={() => navigate("/")}
                type="button"
              >
                <span aria-hidden="true">←</span>
                Back to Library
              </button>

              <div className="mt-5 flex flex-wrap gap-2 text-[11px] font-semibold uppercase tracking-[0.26em] text-amber-200/80">
                <span className="rounded-full border border-amber-300/20 bg-amber-400/10 px-3 py-1">
                  Book {book?.id}
                </span>
                <span className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-slate-300">
                  {book?.page_count ?? "?"} pages
                </span>
                <span className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-slate-300">
                  {chapters.length} segments
                </span>
                <span className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-slate-300">
                  Narrated by {book?.narrator}
                </span>
              </div>

              <h1 className="mt-4 max-w-4xl text-3xl font-semibold leading-tight text-white sm:text-4xl lg:text-[2.9rem]">
                {book?.title}
              </h1>
              {book?.subtitle ? (
                <p className="mt-3 text-lg text-amber-100/85">{book.subtitle}</p>
              ) : null}
              <p className="mt-4 text-sm uppercase tracking-[0.28em] text-slate-400">
                by <span className="text-slate-100">{book?.author}</span>
              </p>
            </div>

            <div className="rounded-[1.75rem] border border-white/10 bg-white/[0.05] p-4 text-sm text-slate-300 shadow-xl shadow-slate-950/20 lg:max-w-sm">
              <div className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">
                Editorial Focus
              </div>
              <p className="mt-3 leading-7">
                Review parsed chapters, correct manuscript text, and tune narration before generation.
              </p>
            </div>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[110rem] px-4 pb-8 pt-6 sm:px-6 lg:px-8">
        {errorMessage ? (
          <div className="mb-6 flex flex-col gap-4 rounded-[1.75rem] border border-rose-400/30 bg-rose-500/10 p-4 text-sm text-rose-100 md:flex-row md:items-center md:justify-between">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.28em] text-rose-200/80">
                Load Error
              </div>
              <div className="mt-1">{errorMessage}</div>
            </div>
            <button
              className="inline-flex items-center justify-center rounded-full border border-rose-300/30 bg-rose-400/10 px-4 py-2 font-semibold text-rose-50 transition hover:bg-rose-400/20"
              onClick={() => {
                void fetchBookData();
              }}
              type="button"
            >
              Retry
            </button>
          </div>
        ) : null}

        <div className="grid gap-6 xl:h-[calc(100vh-15rem)] xl:grid-cols-[minmax(18rem,22rem)_minmax(0,1fr)_minmax(18rem,22rem)]">
          <ChapterList
            chapters={chapters}
            onSelectChapter={handleChapterSelect}
            selectedChapterId={selectedChapterId}
          />

          <TextPreview
            chapter={selectedChapter}
            draftText={draftText}
            editMode={editMode}
            hasUnsavedChanges={hasUnsavedChanges}
            onBeginEdit={handleBeginEdit}
            onCancelEdit={handleCancelEdit}
            onSave={handleSaveText}
            onTextChange={handleTextChange}
            saveErrorMessage={saveErrorMessage}
            saving={saving}
          />

          <NarrationSettings
            onChange={handleNarrationSettingsChange}
            selectedChapter={selectedChapter}
            settings={narrationSettings}
          />
        </div>
      </main>
    </div>
  );
}
