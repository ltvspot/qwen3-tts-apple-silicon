import React, { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import AudioPlayerPanel from "../components/AudioPlayerPanel";
import ChapterList from "../components/ChapterList";
import DownloadCard from "../components/DownloadCard";
import ExportDialog from "../components/ExportDialog";
import ExportProgressBar from "../components/ExportProgressBar";
import GenerationProgress from "../components/GenerationProgress";
import NarrationSettings from "../components/NarrationSettings";
import TextPreview from "../components/TextPreview";
import { mapChapterGenerationState } from "../components/generationStatus";

const DEFAULT_NARRATION_SETTINGS = {
  voice: "Ethan",
  emotion: "warm",
  speed: 1.0,
  engine: "qwen3_tts",
};

const DEFAULT_VOICE_OPTIONS = [
  { name: "Ethan", display_name: "Ethan", is_cloned: false },
  { name: "Nova", display_name: "Nova", is_cloned: false },
  { name: "Aria", display_name: "Aria", is_cloned: false },
];

function chapterHasUnsavedChanges(selectedChapter, draftText, editMode) {
  if (!editMode || !selectedChapter) {
    return false;
  }

  return draftText !== (selectedChapter.text_content ?? "");
}

function mergeChaptersWithGeneration(chapters, generationSnapshot) {
  const statusMap = new Map(
    (generationSnapshot?.chapters ?? []).map((chapter) => [chapter.chapter_n, chapter]),
  );

  return chapters.map((chapter) => {
    const chapterStatus = statusMap.get(chapter.number);

    return {
      ...chapter,
      audio_duration_seconds: chapterStatus?.audio_duration_seconds ?? chapter.duration_seconds ?? null,
      audio_file_size_bytes: chapterStatus?.audio_file_size_bytes ?? chapter.audio_file_size_bytes ?? null,
      error_message: chapterStatus?.error_message ?? chapter.error_message ?? null,
      generated_at: chapterStatus?.generated_at ?? chapter.completed_at ?? null,
      generation_seconds: chapterStatus?.generation_seconds ?? null,
      generation_status: chapterStatus?.status ?? mapChapterGenerationState(chapter.status),
      progress_seconds: chapterStatus?.progress_seconds ?? null,
      started_at: chapterStatus?.started_at ?? chapter.started_at ?? null,
    };
  });
}

function createIdleExportSnapshot(bookId) {
  return {
    book_id: Number(bookId),
    completed_at: null,
    error_message: null,
    export_status: "idle",
    formats: {},
    job_id: null,
    qa_report: null,
    started_at: null,
  };
}

function getExportFormatLabel(format) {
  if (format === "m4b") {
    return "M4B (with chapter markers)";
  }

  return "MP3";
}

function formatTimestamp(value) {
  if (!value) {
    return null;
  }

  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    return null;
  }

  return timestamp.toLocaleString();
}

export default function BookDetail() {
  const navigate = useNavigate();
  const requestRef = useRef(0);
  const { id } = useParams();

  const [book, setBook] = useState(null);
  const [chapters, setChapters] = useState([]);
  const [draftText, setDraftText] = useState("");
  const [editMode, setEditMode] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [exportDialogOpen, setExportDialogOpen] = useState(false);
  const [exportErrorMessage, setExportErrorMessage] = useState("");
  const [exportSnapshot, setExportSnapshot] = useState(createIdleExportSnapshot(id));
  const [exportSubmitting, setExportSubmitting] = useState(false);
  const [generationAction, setGenerationAction] = useState(null);
  const [generationErrorMessage, setGenerationErrorMessage] = useState("");
  const [generationSnapshot, setGenerationSnapshot] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadingVoiceOptions, setLoadingVoiceOptions] = useState(true);
  const [narrationSettings, setNarrationSettings] = useState(DEFAULT_NARRATION_SETTINGS);
  const [notFound, setNotFound] = useState(false);
  const [playerChapterNumber, setPlayerChapterNumber] = useState(null);
  const [playerVisible, setPlayerVisible] = useState(false);
  const [saveErrorMessage, setSaveErrorMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const [selectedChapterId, setSelectedChapterId] = useState(null);
  const [voiceOptions, setVoiceOptions] = useState(DEFAULT_VOICE_OPTIONS);

  const mergedChapters = useMemo(
    () => mergeChaptersWithGeneration(chapters, generationSnapshot),
    [chapters, generationSnapshot],
  );
  const selectedChapter = mergedChapters.find((chapter) => chapter.id === selectedChapterId) ?? null;
  const hasUnsavedChanges = chapterHasUnsavedChanges(selectedChapter, draftText, editMode);
  const completedChapters = mergedChapters.filter((chapter) => chapter.generation_status === "completed");
  const hasRemainingChapters = mergedChapters.some((chapter) => chapter.generation_status !== "completed");
  const generationActive = generationAction !== null || generationSnapshot?.status === "generating";
  const generationDisabled = generationActive;
  const exportCompletedFormats = Object.entries(exportSnapshot?.formats ?? {}).filter(
    ([, format]) => format?.status === "completed",
  );
  const exportFailedFormats = Object.entries(exportSnapshot?.formats ?? {}).filter(
    ([, format]) => format?.status === "error",
  );
  const exportInProgress = exportSubmitting || exportSnapshot?.export_status === "processing";

  useEffect(() => {
    if (!editMode) {
      setDraftText(selectedChapter?.text_content ?? "");
    }
  }, [editMode, selectedChapter]);

  useEffect(() => {
    void fetchBookData();
  }, [id]);

  useEffect(() => {
    if (!generationSnapshot || generationSnapshot.status === "generating") {
      return;
    }

    setGenerationAction(null);
  }, [generationSnapshot]);

  useEffect(() => {
    if (exportSnapshot?.export_status !== "processing") {
      return undefined;
    }

    const timeoutId = window.setTimeout(() => {
      void fetchExportStatus(requestRef.current);
    }, 2000);

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [exportSnapshot?.export_status, id]);

  async function fetchGenerationStatus(currentRequestId) {
    try {
      const statusResponse = await fetch(`/api/book/${id}/status`);
      if (!statusResponse.ok) {
        throw new Error("Failed to fetch generation status.");
      }

      const statusPayload = await statusResponse.json();
      if (requestRef.current !== currentRequestId) {
        return;
      }

      setGenerationSnapshot(statusPayload);
      return statusPayload;
    } catch (error) {
      if (requestRef.current !== currentRequestId) {
        return null;
      }

      setGenerationSnapshot(null);
      return null;
    }
  }

  async function fetchExportStatus(currentRequestId) {
    try {
      const response = await fetch(`/api/book/${id}/export/status`);
      if (!response.ok) {
        throw new Error("Failed to fetch export status.");
      }

      const payload = await response.json();
      if (requestRef.current !== currentRequestId) {
        return null;
      }

      setExportErrorMessage("");
      setExportSnapshot(payload);
      return payload;
    } catch (error) {
      if (requestRef.current !== currentRequestId) {
        return null;
      }

      setExportSnapshot(createIdleExportSnapshot(id));
      setExportErrorMessage(
        error instanceof Error ? error.message : "Failed to fetch export status.",
      );
      return null;
    }
  }

  async function fetchVoiceOptions(currentRequestId) {
    try {
      const response = await fetch("/api/voice-lab/voices");
      if (!response.ok) {
        throw new Error("Failed to fetch voices.");
      }

      const payload = await response.json();
      if (requestRef.current !== currentRequestId) {
        return;
      }

      const availableVoices = payload.voices ?? [];
      setVoiceOptions(availableVoices.length > 0 ? availableVoices : DEFAULT_VOICE_OPTIONS);
      setNarrationSettings((currentSettings) => ({
        ...currentSettings,
        voice: availableVoices.some((voiceOption) => voiceOption.name === currentSettings.voice)
          ? currentSettings.voice
          : (availableVoices[0]?.name ?? DEFAULT_NARRATION_SETTINGS.voice),
      }));
    } catch (error) {
      if (requestRef.current !== currentRequestId) {
        return;
      }

      setVoiceOptions(DEFAULT_VOICE_OPTIONS);
    } finally {
      if (requestRef.current === currentRequestId) {
        setLoadingVoiceOptions(false);
      }
    }
  }

  async function fetchBookData() {
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;

    setLoading(true);
    setNotFound(false);
    setErrorMessage("");
    setExportDialogOpen(false);
    setExportErrorMessage("");
    setExportSnapshot(createIdleExportSnapshot(id));
    setExportSubmitting(false);
    setGenerationErrorMessage("");
    setBook(null);
    setChapters([]);
    setSelectedChapterId(null);
    setDraftText("");
    setEditMode(false);
    setGenerationSnapshot(null);
    setLoadingVoiceOptions(true);
    setPlayerVisible(false);
    setPlayerChapterNumber(null);
    setVoiceOptions(DEFAULT_VOICE_OPTIONS);

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

      await Promise.all([
        fetchGenerationStatus(requestId),
        fetchExportStatus(requestId),
      ]);
      if (requestRef.current !== requestId) {
        return;
      }

      setLoading(false);
      void fetchVoiceOptions(requestId);
    } catch (error) {
      if (requestRef.current !== requestId) {
        return;
      }

      setErrorMessage(
        error instanceof Error ? error.message : "Unable to load this book right now.",
      );
    } finally {
      if (requestRef.current === requestId) {
        setLoading(false);
      }
    }
  }

  function handleChapterSelect(chapter) {
    if (chapter.id === selectedChapterId) {
      if (chapter.generation_status === "completed") {
        setPlayerChapterNumber(chapter.number);
        setPlayerVisible(true);
      }
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

    if (chapter.generation_status === "completed") {
      setPlayerChapterNumber(chapter.number);
      setPlayerVisible(true);
    }
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
    } finally {
      setSaving(false);
    }
  }

  async function queueGeneration(url, nextAction) {
    setGenerationAction(nextAction);
    setGenerationErrorMessage("");

    try {
      const response = await fetch(url, {
        method: "POST",
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        const detail = typeof payload?.detail === "string"
          ? payload.detail
          : "Failed to queue generation.";
        throw new Error(detail);
      }

      const requestId = requestRef.current;
      const nextSnapshot = await fetchGenerationStatus(requestId);
      if (nextSnapshot?.status !== "generating") {
        setGenerationAction(null);
      }
    } catch (error) {
      setGenerationAction(null);
      setGenerationErrorMessage(
        error instanceof Error ? error.message : "Failed to queue generation.",
      );
    }
  }

  function handleGenerateChapter(chapter, force) {
    const search = force ? "?force=true" : "";
    void queueGeneration(
      `/api/book/${id}/chapter/${chapter.number}/generate${search}`,
      { chapterNumber: chapter.number, scope: "chapter" },
    );
  }

  function handleGenerateAll() {
    const shouldContinue = window.confirm(
      "This will generate all remaining chapters. Continue?",
    );
    if (!shouldContinue) {
      return;
    }

    void queueGeneration(`/api/book/${id}/generate-all`, { scope: "all" });
  }

  function handleNarrationSettingsChange(nextSettings) {
    setNarrationSettings(nextSettings);
  }

  async function handleExportSubmit(payload) {
    setExportSubmitting(true);
    setExportErrorMessage("");

    try {
      const response = await fetch(`/api/book/${id}/export`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const errorPayload = await response.json().catch(() => null);
        const detail = typeof errorPayload?.detail === "string"
          ? errorPayload.detail
          : "Failed to start export.";
        throw new Error(detail);
      }

      const queuedExport = await response.json();
      setExportSnapshot((currentSnapshot) => ({
        ...currentSnapshot,
        book_id: queuedExport.book_id,
        completed_at: null,
        error_message: null,
        export_status: queuedExport.export_status,
        formats: Object.fromEntries(
          queuedExport.formats_requested.map((format) => [format, { status: "pending" }]),
        ),
        job_id: queuedExport.job_id,
        qa_report: null,
        started_at: queuedExport.started_at,
      }));
      setExportDialogOpen(false);
      await fetchExportStatus(requestRef.current);
    } catch (error) {
      setExportErrorMessage(
        error instanceof Error ? error.message : "Failed to start export.",
      );
    } finally {
      setExportSubmitting(false);
    }
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
                  {mergedChapters.length} segments
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

            <div className="w-full rounded-[1.75rem] border border-white/10 bg-white/[0.05] p-4 text-sm text-slate-300 shadow-xl shadow-slate-950/20 lg:max-w-md">
              <div className="flex flex-col gap-4">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">
                    Generation Controls
                  </div>
                  <p className="mt-3 leading-7">
                    Review parsed chapters, correct manuscript text, and then generate either a single chapter or the remaining audiobook in one pass.
                  </p>
                </div>

                <div className="flex flex-wrap gap-2">
                  <button
                    className="inline-flex items-center gap-2 rounded-full border border-amber-300/25 bg-amber-400/10 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-amber-100 transition hover:bg-amber-400/20 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.04] disabled:text-slate-500"
                    disabled={generationDisabled || !hasRemainingChapters}
                    onClick={handleGenerateAll}
                    type="button"
                  >
                    {generationAction?.scope === "all" ? (
                      <span
                        aria-hidden="true"
                        className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-r-transparent"
                      />
                    ) : null}
                    {generationAction?.scope === "all" ? "Queueing..." : "Generate All"}
                  </button>

                  <div className="inline-flex items-center rounded-full border border-white/10 bg-slate-950/45 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-300">
                    {generationSnapshot?.status === "generating"
                      ? "Generation active"
                      : hasRemainingChapters
                        ? "Ready to generate"
                        : "All chapters complete"}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </header>

      <main
        className={`mx-auto max-w-[110rem] px-4 pt-6 sm:px-6 lg:px-8 ${
          playerVisible ? "pb-36" : "pb-8"
        }`}
      >
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

        {generationErrorMessage ? (
          <div className="mb-6 rounded-[1.75rem] border border-amber-300/25 bg-amber-400/10 px-4 py-3 text-sm text-amber-100">
            {generationErrorMessage}
          </div>
        ) : null}

        {exportErrorMessage ? (
          <div className="mb-6 rounded-[1.75rem] border border-rose-400/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-100">
            {exportErrorMessage}
          </div>
        ) : null}

        {generationActive ? (
          <div className="mb-6">
            <GenerationProgress
              active={generationActive}
              bookId={id}
              chapters={mergedChapters}
              onChapterCompleted={(chapterStatus) => {
                if (!chapterStatus) {
                  return;
                }

                setPlayerChapterNumber(chapterStatus.chapter_n);
                setPlayerVisible(true);
              }}
              onStatusUpdate={(nextSnapshot) => {
                setGenerationSnapshot(nextSnapshot);
              }}
            />
          </div>
        ) : null}

        <div className="grid gap-6 xl:h-[calc(100vh-15rem)] xl:grid-cols-[minmax(18rem,24rem)_minmax(0,1fr)_minmax(18rem,22rem)]">
          <ChapterList
            chapters={mergedChapters}
            generationDisabled={generationDisabled}
            loadingChapterNumber={generationAction?.chapterNumber ?? null}
            onGenerateChapter={handleGenerateChapter}
            onPreviewChapter={(chapter) => {
              setPlayerChapterNumber(chapter.number);
              setPlayerVisible(true);
            }}
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

          <div className="flex flex-col gap-6">
            <NarrationSettings
              loadingVoices={loadingVoiceOptions}
              onChange={handleNarrationSettingsChange}
              selectedChapter={selectedChapter}
              settings={narrationSettings}
              voices={voiceOptions}
            />

            <section className="rounded-[2rem] border border-white/10 bg-white/[0.05] p-5 text-white shadow-xl shadow-slate-950/20">
              <div className="flex flex-col gap-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-[0.28em] text-amber-200/75">
                      Export Pipeline
                    </div>
                    <h2 className="mt-3 text-xl font-semibold">Audiobook Exports</h2>
                    <p className="mt-3 text-sm leading-7 text-slate-300">
                      Concatenate the generated narration, normalize levels, and package download-ready audiobook files.
                    </p>
                  </div>
                  <div className="inline-flex items-center rounded-full border border-white/10 bg-slate-950/45 px-4 py-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-300">
                    {exportSnapshot?.export_status === "completed"
                      ? "Ready to download"
                      : exportSnapshot?.export_status === "processing"
                        ? "Export running"
                        : exportSnapshot?.export_status === "error"
                          ? "Export error"
                          : "Not exported"}
                  </div>
                </div>

                {exportSnapshot?.completed_at ? (
                  <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
                    Last export: {formatTimestamp(exportSnapshot.completed_at) ?? "Unknown"}
                  </div>
                ) : null}

                {completedChapters.length === 0 ? (
                  <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4 text-sm leading-7 text-slate-300">
                    Generate at least one chapter before starting an export.
                  </div>
                ) : null}

                {exportInProgress ? (
                  <ExportProgressBar label="Building the audiobook package and validating output files." />
                ) : null}

                {exportSnapshot?.qa_report ? (
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4">
                      <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
                        Included Chapters
                      </div>
                      <div className="mt-2 text-2xl font-semibold text-white">
                        {exportSnapshot.qa_report.chapters_included}
                      </div>
                    </div>
                    <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4">
                      <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
                        QA Approved
                      </div>
                      <div className="mt-2 text-2xl font-semibold text-white">
                        {exportSnapshot.qa_report.chapters_approved}
                      </div>
                    </div>
                    <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4">
                      <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
                        Flagged
                      </div>
                      <div className="mt-2 text-2xl font-semibold text-white">
                        {exportSnapshot.qa_report.chapters_flagged}
                      </div>
                    </div>
                    <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4">
                      <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
                        Warnings
                      </div>
                      <div className="mt-2 text-2xl font-semibold text-white">
                        {exportSnapshot.qa_report.chapters_warnings}
                      </div>
                    </div>
                  </div>
                ) : null}

                {exportCompletedFormats.length > 0 ? (
                  <div className="space-y-3">
                    {exportCompletedFormats.map(([format, details]) => (
                      <DownloadCard
                        fileName={details.file_name ?? `${book?.title}.${format}`}
                        fileSizeBytes={details.file_size_bytes}
                        formatLabel={getExportFormatLabel(format)}
                        key={format}
                        url={details.download_url}
                      />
                    ))}
                  </div>
                ) : null}

                {exportFailedFormats.length > 0 ? (
                  <div className="space-y-3">
                    {exportFailedFormats.map(([format, details]) => (
                      <div
                        className="rounded-3xl border border-rose-400/30 bg-rose-500/10 px-4 py-4 text-sm text-rose-100"
                        key={format}
                      >
                        <div className="text-xs font-semibold uppercase tracking-[0.22em] text-rose-200/80">
                          {getExportFormatLabel(format)}
                        </div>
                        <div className="mt-2">
                          {details.error_message ?? "Export failed for this format."}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : null}

                {exportSnapshot?.qa_report?.notes ? (
                  <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4 text-sm leading-7 text-slate-300">
                    {exportSnapshot.qa_report.notes}
                  </div>
                ) : null}

                <div className="flex flex-wrap gap-3">
                  <button
                    className="inline-flex items-center justify-center rounded-full border border-amber-300/25 bg-amber-400/10 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-amber-100 transition hover:bg-amber-400/20 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.04] disabled:text-slate-500"
                    disabled={completedChapters.length === 0 || exportInProgress}
                    onClick={() => {
                      setExportDialogOpen(true);
                      setExportErrorMessage("");
                    }}
                    type="button"
                  >
                    {exportCompletedFormats.length > 0 ? "Re-export Audiobook" : "Export Audiobook"}
                  </button>
                </div>
              </div>
            </section>
          </div>
        </div>
      </main>

      <AudioPlayerPanel
        bookId={id}
        chapterNumber={playerChapterNumber}
        completedChapters={completedChapters}
        onClose={() => setPlayerVisible(false)}
        onSelectChapter={(chapterNumber) => {
          const chapter = mergedChapters.find((candidate) => candidate.number === chapterNumber);
          if (chapter) {
            setSelectedChapterId(chapter.id);
          }
          setPlayerChapterNumber(chapterNumber);
          setPlayerVisible(true);
        }}
        visible={playerVisible && completedChapters.length > 0}
      />

      <ExportDialog
        onClose={() => {
          if (!exportSubmitting) {
            setExportDialogOpen(false);
          }
        }}
        onSubmit={(payload) => {
          void handleExportSubmit(payload);
        }}
        open={exportDialogOpen}
        pending={exportSubmitting}
      />
    </div>
  );
}
