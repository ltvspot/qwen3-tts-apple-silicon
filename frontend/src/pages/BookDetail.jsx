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
import {
  formatDetailedDuration,
  mapChapterGenerationState,
} from "../components/generationStatus";

const DEFAULT_NARRATION_SETTINGS = {
  voice: "Ethan",
  emotion: "neutral",
  speed: 1.0,
  engine: "qwen3_tts",
};

const DEFAULT_VOICE_OPTIONS = [
  { name: "Ethan", display_name: "Ethan", is_cloned: false },
  { name: "Nova", display_name: "Nova", is_cloned: false },
  { name: "Aria", display_name: "Aria", is_cloned: false },
];
const VOICE_LOAD_MAX_RETRIES = 20;
const WORKFLOW_STEPS = [
  {
    description:
      "Extract chapters, credits, and narratable sections from the uploaded document.",
    title: "Parse Manuscript",
  },
  {
    description:
      "Inspect chapter text and correct any parsing issues before generation.",
    title: "Review & Edit",
  },
  {
    description:
      "Generate narration chapter by chapter or queue the remaining book in one pass.",
    title: "Generate Audio",
  },
  {
    description:
      "Run QA, master the book, and export the final audiobook package.",
    title: "Quality Check & Export",
  },
];

function chapterHasUnsavedChanges(selectedChapter, draftText, editMode) {
  if (!editMode || !selectedChapter) {
    return false;
  }

  return draftText !== (selectedChapter.text_content ?? "");
}

function mergeChaptersWithGeneration(chapters, generationSnapshot) {
  const statusMap = new Map(
    (generationSnapshot?.chapters ?? []).map((chapter) => [
      chapter.chapter_n,
      chapter,
    ]),
  );

  return chapters.map((chapter) => {
    const chapterStatus = statusMap.get(chapter.number);

    return {
      ...chapter,
      audio_duration_seconds:
        chapterStatus?.audio_duration_seconds ??
        chapter.duration_seconds ??
        null,
      audio_file_size_bytes:
        chapterStatus?.audio_file_size_bytes ??
        chapter.audio_file_size_bytes ??
        null,
      error_message:
        chapterStatus?.error_message ?? chapter.error_message ?? null,
      generated_at: chapterStatus?.generated_at ?? chapter.completed_at ?? null,
      generation_seconds: chapterStatus?.generation_seconds ?? null,
      generation_status:
        chapterStatus?.status ?? mapChapterGenerationState(chapter.status),
      progress_seconds: chapterStatus?.progress_seconds ?? null,
      started_at: chapterStatus?.started_at ?? chapter.started_at ?? null,
    };
  });
}

function createIdleExportSnapshot(bookId) {
  return {
    book_id: Number(bookId),
    completed_at: null,
    current_chapter_n: null,
    current_format: null,
    current_stage: null,
    error_message: null,
    export_status: "idle",
    formats: {},
    job_id: null,
    progress_percent: 0,
    qa_report: null,
    started_at: null,
    total_chapters: null,
  };
}

function getExportFormatLabel(format) {
  if (format === "m4b") {
    return "M4B (with chapter markers)";
  }

  return "MP3";
}

function getQualityBadgeTone(grade) {
  if (grade === "A") {
    return "border-emerald-300/30 bg-emerald-400/10 text-emerald-100";
  }

  if (grade === "B") {
    return "border-amber-300/30 bg-amber-400/10 text-amber-100";
  }

  if (grade === "C") {
    return "border-orange-300/30 bg-orange-400/10 text-orange-100";
  }

  return "border-rose-300/30 bg-rose-500/10 text-rose-100";
}

function getStatusTone(status) {
  if (status === "pass") {
    return "text-emerald-200";
  }

  if (status === "warning") {
    return "text-amber-200";
  }

  return "text-rose-200";
}

function getDeepQaStatusBadge(status) {
  if (status === "pass") {
    return "border-emerald-300/30 bg-emerald-400/10 text-emerald-100";
  }

  if (status === "warning") {
    return "border-amber-300/30 bg-amber-400/10 text-amber-100";
  }

  return "border-rose-300/30 bg-rose-500/10 text-rose-100";
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

function formatIssueRange(issue) {
  if (
    typeof issue?.start_time_seconds !== "number" &&
    typeof issue?.end_time_seconds !== "number"
  ) {
    return null;
  }

  const start =
    typeof issue?.start_time_seconds === "number"
      ? `${issue.start_time_seconds.toFixed(2)}s`
      : "0.00s";
  const end =
    typeof issue?.end_time_seconds === "number"
      ? `${issue.end_time_seconds.toFixed(2)}s`
      : null;
  return end ? `${start} - ${end}` : start;
}

export default function BookDetail() {
  const navigate = useNavigate();
  const requestRef = useRef(0);
  const voiceRetryTimeoutRef = useRef(null);
  const { id } = useParams();

  const [book, setBook] = useState(null);
  const [audioQaAction, setAudioQaAction] = useState(null);
  const [audioQaErrorMessage, setAudioQaErrorMessage] = useState("");
  const [audioQaLoading, setAudioQaLoading] = useState(false);
  const [audioQaReport, setAudioQaReport] = useState(null);
  const [bookQualityAction, setBookQualityAction] = useState(null);
  const [bookQualityErrorMessage, setBookQualityErrorMessage] = useState("");
  const [bookQualityLoading, setBookQualityLoading] = useState(false);
  const [bookQualityReport, setBookQualityReport] = useState(null);
  const [chapters, setChapters] = useState([]);
  const [draftText, setDraftText] = useState("");
  const [editMode, setEditMode] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [exportDialogOpen, setExportDialogOpen] = useState(false);
  const [exportErrorMessage, setExportErrorMessage] = useState("");
  const [exportSnapshot, setExportSnapshot] = useState(
    createIdleExportSnapshot(id),
  );
  const [exportSubmitting, setExportSubmitting] = useState(false);
  const [generationAction, setGenerationAction] = useState(null);
  const [generationErrorMessage, setGenerationErrorMessage] = useState("");
  const [generationSnapshot, setGenerationSnapshot] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadingVoiceOptions, setLoadingVoiceOptions] = useState(true);
  const [narrationSettings, setNarrationSettings] = useState(
    DEFAULT_NARRATION_SETTINGS,
  );
  const [notFound, setNotFound] = useState(false);
  const [parseErrorMessage, setParseErrorMessage] = useState("");
  const [parsing, setParsing] = useState(false);
  const [playerChapterNumber, setPlayerChapterNumber] = useState(null);
  const [playerVisible, setPlayerVisible] = useState(false);
  const [saveErrorMessage, setSaveErrorMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const [selectedChapterId, setSelectedChapterId] = useState(null);
  const [voiceLoadingMessage, setVoiceLoadingMessage] = useState("");
  const [voiceOptions, setVoiceOptions] = useState(DEFAULT_VOICE_OPTIONS);
  const [voiceConsistencyChart, setVoiceConsistencyChart] = useState(null);

  const mergedChapters = useMemo(
    () => mergeChaptersWithGeneration(chapters, generationSnapshot),
    [chapters, generationSnapshot],
  );
  const selectedChapter =
    mergedChapters.find((chapter) => chapter.id === selectedChapterId) ?? null;
  const selectedChapterHasPreview = Boolean(
    selectedChapter &&
    (selectedChapter.audio_path ||
      selectedChapter.generation_status === "completed" ||
      selectedChapter.audio_duration_seconds ||
      selectedChapter.duration_seconds),
  );
  const selectedChapterPreviewUrl = selectedChapter
    ? `/api/book/${id}/chapter/${selectedChapter.number}/preview`
    : null;
  const hasUnsavedChanges = chapterHasUnsavedChanges(
    selectedChapter,
    draftText,
    editMode,
  );
  const completedChapters = mergedChapters.filter(
    (chapter) => chapter.generation_status === "completed",
  );
  const hasChapters = mergedChapters.length > 0;
  const hasGeneratedChapters = completedChapters.length > 0;
  const hasRemainingChapters = mergedChapters.some(
    (chapter) => chapter.generation_status !== "completed",
  );
  const generationActive =
    generationAction !== null || generationSnapshot?.status === "generating";
  const generationDisabled = generationActive;
  const generationStatusLabel = !hasChapters
    ? "Manuscript not parsed"
    : generationSnapshot?.status === "generating"
      ? "Generation active"
      : hasRemainingChapters
        ? "Ready to generate"
        : "All chapters complete";
  const exportCompletedFormats = Object.entries(
    exportSnapshot?.formats ?? {},
  ).filter(([, format]) => format?.status === "completed");
  const exportFailedFormats = Object.entries(
    exportSnapshot?.formats ?? {},
  ).filter(([, format]) => format?.status === "error");
  const exportInProgress =
    exportSubmitting || exportSnapshot?.export_status === "processing";
  const lastExportChapterCount =
    exportSnapshot?.qa_report?.chapters_included ?? 0;
  const exportHasArtifacts = exportCompletedFormats.length > 0;
  const exportNeedsRefresh =
    exportHasArtifacts &&
    mergedChapters.length > 0 &&
    lastExportChapterCount > 0 &&
    lastExportChapterCount < mergedChapters.length;
  const exportStatusLabel =
    exportSnapshot?.export_status === "processing"
      ? "Export running"
      : exportSnapshot?.export_status === "error"
        ? "Export error"
        : exportNeedsRefresh
          ? "Past export available"
          : exportHasArtifacts
            ? "Ready to download"
            : "Not exported";
  const bookQualityEligible =
    chapters.length > 0 &&
    chapters.every((chapter) => chapter.status === "generated");
  const bookQualityBusy = bookQualityLoading || bookQualityAction !== null;
  const audioQaEligible = mergedChapters.some(
    (chapter) => chapter.status === "generated",
  );
  const audioQaBusy = audioQaLoading || audioQaAction !== null;
  const selectedChapterAudioQa = selectedChapter
    ? (audioQaReport?.chapters?.find(
        (chapterReport) =>
          (chapterReport.chapter_id &&
            chapterReport.chapter_id === selectedChapter.id) ||
          chapterReport.chapter_n === selectedChapter.number,
      ) ?? null)
    : null;

  useEffect(() => {
    if (!editMode) {
      setDraftText(selectedChapter?.text_content ?? "");
    }
  }, [editMode, selectedChapter]);

  useEffect(() => {
    void fetchBookData();
  }, [id]);

  useEffect(() => {
    if (notFound) {
      document.title = "Book Not Found | Alexandria Audiobook Narrator";
      return;
    }

    if (book?.title) {
      document.title = `${book.title} | Alexandria Audiobook Narrator`;
      return;
    }

    document.title = "Book Detail | Alexandria Audiobook Narrator";
  }, [book?.title, notFound]);

  useEffect(
    () => () => {
      if (voiceRetryTimeoutRef.current) {
        window.clearTimeout(voiceRetryTimeoutRef.current);
      }
    },
    [],
  );

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
        error instanceof Error
          ? error.message
          : "Failed to fetch export status.",
      );
      return null;
    }
  }

  async function fetchVoiceOptions(currentRequestId, attempt = 1) {
    if (voiceRetryTimeoutRef.current) {
      window.clearTimeout(voiceRetryTimeoutRef.current);
      voiceRetryTimeoutRef.current = null;
    }
    let keepLoading = false;

    try {
      const response = await fetch("/api/voice-lab/voices");
      if (!response.ok) {
        throw new Error("Failed to fetch voices.");
      }

      const payload = await response.json();
      if (requestRef.current !== currentRequestId) {
        return;
      }

      if (payload.loading) {
        const nextAttempt = Math.min(attempt, VOICE_LOAD_MAX_RETRIES);
        setVoiceLoadingMessage(
          `TTS engine is loading... retrying in 3s (attempt ${nextAttempt}/${VOICE_LOAD_MAX_RETRIES})`,
        );
        keepLoading = true;

        if (nextAttempt >= VOICE_LOAD_MAX_RETRIES) {
          setLoadingVoiceOptions(false);
          return;
        }

        voiceRetryTimeoutRef.current = window.setTimeout(() => {
          void fetchVoiceOptions(currentRequestId, nextAttempt + 1);
        }, 3000);
        return;
      }

      const availableVoices = payload.voices ?? [];
      setVoiceOptions(
        availableVoices.length > 0 ? availableVoices : DEFAULT_VOICE_OPTIONS,
      );
      setVoiceLoadingMessage("");
      setNarrationSettings((currentSettings) => ({
        ...currentSettings,
        voice: availableVoices.some(
          (voiceOption) => voiceOption.name === currentSettings.voice,
        )
          ? currentSettings.voice
          : (availableVoices[0]?.name ?? DEFAULT_NARRATION_SETTINGS.voice),
      }));
    } catch (error) {
      if (requestRef.current !== currentRequestId) {
        return;
      }

      setVoiceOptions(DEFAULT_VOICE_OPTIONS);
      setVoiceLoadingMessage("");
    } finally {
      if (requestRef.current === currentRequestId && !keepLoading) {
        setLoadingVoiceOptions(false);
      }
    }
  }

  async function fetchBookData() {
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;
    if (voiceRetryTimeoutRef.current) {
      window.clearTimeout(voiceRetryTimeoutRef.current);
      voiceRetryTimeoutRef.current = null;
    }

    setLoading(true);
    setNotFound(false);
    setErrorMessage("");
    setParseErrorMessage("");
    setAudioQaAction(null);
    setAudioQaErrorMessage("");
    setAudioQaLoading(false);
    setAudioQaReport(null);
    setExportDialogOpen(false);
    setExportErrorMessage("");
    setExportSnapshot(createIdleExportSnapshot(id));
    setExportSubmitting(false);
    setGenerationErrorMessage("");
    setBook(null);
    setBookQualityAction(null);
    setBookQualityErrorMessage("");
    setBookQualityLoading(false);
    setBookQualityReport(null);
    setChapters([]);
    setSelectedChapterId(null);
    setDraftText("");
    setEditMode(false);
    setGenerationSnapshot(null);
    setLoadingVoiceOptions(true);
    setPlayerVisible(false);
    setPlayerChapterNumber(null);
    setVoiceLoadingMessage("");
    setVoiceOptions(DEFAULT_VOICE_OPTIONS);
    setVoiceConsistencyChart(null);

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
        if (
          chaptersPayload.some((chapter) => chapter.id === currentSelection)
        ) {
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
        error instanceof Error
          ? error.message
          : "Unable to load this book right now.",
      );
    } finally {
      if (requestRef.current === requestId) {
        setLoading(false);
      }
    }
  }

  async function handleParse() {
    if (parsing) {
      return;
    }

    setParsing(true);
    setParseErrorMessage("");
    setErrorMessage("");

    try {
      const response = await fetch(`/api/book/${id}/parse`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({}),
      });
      const payload = await response.json().catch(() => null);

      if (!response.ok) {
        const detail =
          typeof payload?.detail === "string"
            ? payload.detail
            : typeof payload?.message === "string"
              ? payload.message
              : "Parse failed.";
        throw new Error(detail);
      }

      await fetchBookData();
    } catch (error) {
      setParseErrorMessage(
        error instanceof Error ? error.message : "Parse failed.",
      );
    } finally {
      setParsing(false);
    }
  }

  async function fetchBookQualityReport(currentRequestId) {
    const response = await fetch(`/api/book/${id}/qa/book-report`);
    const payload = await response.json();

    if (requestRef.current !== currentRequestId) {
      return null;
    }

    if (!response.ok) {
      throw new Error(payload?.detail ?? "Failed to run book QA.");
    }

    setBookQualityReport(payload);
    return payload;
  }

  async function fetchVoiceConsistencyChart(currentRequestId) {
    const response = await fetch(`/api/book/${id}/qa/voice-consistency-chart`);
    const payload = await response.json();

    if (requestRef.current !== currentRequestId) {
      return null;
    }

    if (!response.ok) {
      throw new Error(
        payload?.detail ?? "Failed to load voice consistency chart.",
      );
    }

    setVoiceConsistencyChart(payload);
    return payload;
  }

  async function fetchDeepQaReport(currentRequestId) {
    const response = await fetch(`/api/books/${id}/qa-report`);
    const payload = await response.json().catch(() => null);

    if (requestRef.current !== currentRequestId) {
      return null;
    }

    if (response.status === 404) {
      setAudioQaReport(null);
      return null;
    }

    if (!response.ok) {
      throw new Error(payload?.detail ?? "Failed to load deep audio QA.");
    }

    setAudioQaReport(payload);
    return payload;
  }

  async function handleRunBookQa() {
    const currentRequestId = requestRef.current;
    setBookQualityAction("run");
    setBookQualityLoading(true);
    setBookQualityErrorMessage("");

    try {
      await fetchBookQualityReport(currentRequestId);
      await fetchVoiceConsistencyChart(currentRequestId);
    } catch (error) {
      if (requestRef.current === currentRequestId) {
        setBookQualityErrorMessage(
          error instanceof Error ? error.message : "Failed to run book QA.",
        );
      }
    } finally {
      if (requestRef.current === currentRequestId) {
        setBookQualityLoading(false);
        setBookQualityAction(null);
      }
    }
  }

  async function handleAutoMaster() {
    const currentRequestId = requestRef.current;
    setBookQualityAction("master");
    setBookQualityLoading(true);
    setBookQualityErrorMessage("");

    try {
      const response = await fetch(`/api/book/${id}/qa/auto-master`, {
        method: "POST",
      });
      const payload = await response.json();

      if (requestRef.current !== currentRequestId) {
        return;
      }

      if (!response.ok) {
        throw new Error(payload?.detail ?? "Auto-mastering failed.");
      }

      setBookQualityReport(payload.book_report);
      await fetchVoiceConsistencyChart(currentRequestId);
    } catch (error) {
      if (requestRef.current === currentRequestId) {
        setBookQualityErrorMessage(
          error instanceof Error ? error.message : "Auto-mastering failed.",
        );
      }
    } finally {
      if (requestRef.current === currentRequestId) {
        setBookQualityLoading(false);
        setBookQualityAction(null);
      }
    }
  }

  async function handleRunAudioQa() {
    const currentRequestId = requestRef.current;
    setAudioQaAction("book");
    setAudioQaLoading(true);
    setAudioQaErrorMessage("");

    try {
      const response = await fetch(`/api/books/${id}/deep-qa`, {
        method: "POST",
      });
      const payload = await response.json().catch(() => null);

      if (requestRef.current !== currentRequestId) {
        return;
      }

      if (!response.ok) {
        throw new Error(payload?.detail ?? "Failed to run audio QA.");
      }

      setAudioQaReport(payload);
    } catch (error) {
      if (requestRef.current === currentRequestId) {
        setAudioQaErrorMessage(
          error instanceof Error ? error.message : "Failed to run audio QA.",
        );
      }
    } finally {
      if (requestRef.current === currentRequestId) {
        setAudioQaLoading(false);
        setAudioQaAction(null);
      }
    }
  }

  async function handleRunChapterDeepQa() {
    if (!selectedChapter) {
      return;
    }

    const currentRequestId = requestRef.current;
    setAudioQaAction(`chapter-${selectedChapter.id}`);
    setAudioQaLoading(true);
    setAudioQaErrorMessage("");

    try {
      const response = await fetch(
        `/api/books/${id}/chapters/${selectedChapter.id}/deep-qa`,
        {
          method: "POST",
        },
      );
      const payload = await response.json().catch(() => null);

      if (requestRef.current !== currentRequestId) {
        return;
      }

      if (!response.ok) {
        throw new Error(
          payload?.detail ?? "Failed to run deep QA for this chapter.",
        );
      }

      await fetchDeepQaReport(currentRequestId);
    } catch (error) {
      if (requestRef.current === currentRequestId) {
        setAudioQaErrorMessage(
          error instanceof Error
            ? error.message
            : "Failed to run deep QA for this chapter.",
        );
      }
    } finally {
      if (requestRef.current === currentRequestId) {
        setAudioQaLoading(false);
        setAudioQaAction(null);
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
      const response = await fetch(
        `/api/book/${id}/chapter/${selectedChapter.number}/text`,
        {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            text_content: draftText,
          }),
        },
      );

      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        const detail =
          typeof payload?.detail === "string"
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
        const detail =
          typeof payload?.detail === "string"
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
        const detail =
          typeof errorPayload?.detail === "string"
            ? errorPayload.detail
            : "Failed to start export.";
        throw new Error(detail);
      }

      const queuedExport = await response.json();
      setExportSnapshot((currentSnapshot) => ({
        ...currentSnapshot,
        book_id: queuedExport.book_id,
        completed_at: null,
        current_chapter_n: null,
        current_format: null,
        current_stage: "Queued",
        error_message: null,
        export_status: queuedExport.export_status,
        formats: Object.fromEntries(
          queuedExport.formats_requested.map((format) => [
            format,
            { status: "pending" },
          ]),
        ),
        job_id: queuedExport.job_id,
        progress_percent: 0,
        qa_report: null,
        started_at: queuedExport.started_at,
        total_chapters: null,
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
          <div className="mt-3 text-lg font-semibold">
            Loading book details...
          </div>
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
                <p className="mt-3 text-lg text-amber-100/85">
                  {book.subtitle}
                </p>
              ) : null}
              <p className="mt-4 text-sm uppercase tracking-[0.28em] text-slate-400">
                by <span className="text-slate-100">{book?.author}</span>
              </p>
            </div>

            <div className="w-full rounded-[1.75rem] border border-white/10 bg-white/[0.05] p-4 text-sm text-slate-300 shadow-xl shadow-slate-950/20 lg:max-w-md">
              {hasChapters ? (
                <div className="flex flex-col gap-4">
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">
                      Generation Controls
                    </div>
                    <p className="mt-3 leading-7">
                      Review parsed chapters, correct manuscript text, and then
                      generate either a single chapter or the remaining
                      audiobook in one pass.
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
                      {generationAction?.scope === "all"
                        ? "Queueing..."
                        : "Generate All"}
                    </button>

                    <div className="inline-flex items-center rounded-full border border-white/10 bg-slate-950/45 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-300">
                      {generationStatusLabel}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="flex flex-col gap-4">
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">
                      Getting Started
                    </div>
                    <p className="mt-3 leading-7">
                      Parse this manuscript to extract chapters, then review and
                      generate audio.
                    </p>
                  </div>

                  <div className="flex flex-wrap gap-2">
                    <button
                      className="inline-flex items-center gap-2 rounded-full border border-amber-300/30 bg-amber-400/10 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-amber-100 transition hover:bg-amber-400/20 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.04] disabled:text-slate-500"
                      disabled={parsing}
                      onClick={() => {
                        void handleParse();
                      }}
                      type="button"
                    >
                      {parsing ? (
                        <span
                          aria-hidden="true"
                          className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-r-transparent"
                        />
                      ) : (
                        <svg
                          aria-hidden="true"
                          className="h-3.5 w-3.5"
                          fill="none"
                          stroke="currentColor"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth="1.7"
                          viewBox="0 0 24 24"
                        >
                          <path d="M6.75 4.75h8.5a2 2 0 0 1 2 2v12.5l-3.75-2-3.75 2-3.75-2-3.75 2V6.75a2 2 0 0 1 2-2h2.5" />
                          <path d="M7.75 4.75v12.5" />
                        </svg>
                      )}
                      {parsing ? "Parsing Manuscript..." : "Parse Manuscript"}
                    </button>

                    <div className="inline-flex items-center rounded-full border border-white/10 bg-slate-950/45 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-300">
                      {generationStatusLabel}
                    </div>
                  </div>

                  {parseErrorMessage ? (
                    <div className="rounded-3xl border border-rose-400/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-100">
                      {parseErrorMessage}
                    </div>
                  ) : null}
                </div>
              )}
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

        <div
          className={`grid gap-6 ${
            hasChapters
              ? "xl:h-[calc(100vh-15rem)] xl:grid-cols-[minmax(18rem,24rem)_minmax(0,1fr)_minmax(18rem,22rem)]"
              : "xl:grid-cols-[minmax(18rem,24rem)_minmax(0,1fr)]"
          }`}
        >
          <ChapterList
            chapters={mergedChapters}
            generationDisabled={generationDisabled}
            loadingChapterNumber={generationAction?.chapterNumber ?? null}
            onGenerateChapter={handleGenerateChapter}
            onParse={() => {
              void handleParse();
            }}
            onPreviewChapter={(chapter) => {
              setPlayerChapterNumber(chapter.number);
              setPlayerVisible(true);
            }}
            parseErrorMessage={parseErrorMessage}
            parsing={parsing}
            onSelectChapter={handleChapterSelect}
            selectedChapterId={selectedChapterId}
          />

          {!hasChapters ? (
            <section className="flex min-h-[24rem] items-center rounded-[2rem] border border-white/10 bg-white/[0.04] p-8 shadow-2xl shadow-slate-950/20">
              <div className="mx-auto w-full max-w-3xl">
                <div className="text-xs font-semibold uppercase tracking-[0.28em] text-amber-200/75">
                  Get Started
                </div>
                <h2 className="mt-4 text-3xl font-semibold text-white sm:text-4xl">
                  Turn this manuscript into a working narration project.
                </h2>
                <p className="mt-4 max-w-2xl text-sm leading-7 text-slate-300">
                  Parse the uploaded document first. Once chapters exist, you
                  can review text, generate audio, run QA, and export the
                  finished audiobook.
                </p>

                <div className="mt-8 grid gap-4 lg:grid-cols-2">
                  {WORKFLOW_STEPS.map((step, index) => (
                    <div
                      className="rounded-[1.5rem] border border-white/10 bg-slate-950/35 p-5"
                      key={step.title}
                    >
                      <div className="flex items-start gap-4">
                        <div className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-amber-300/25 bg-amber-400/10 text-sm font-semibold text-amber-100">
                          {index + 1}
                        </div>
                        <div>
                          <h3 className="text-lg font-semibold text-white">
                            {step.title}
                          </h3>
                          <p className="mt-2 text-sm leading-7 text-slate-400">
                            {step.description}
                          </p>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>

                <div className="mt-8 flex flex-wrap items-center gap-4">
                  <button
                    className="inline-flex items-center gap-2 rounded-full border border-amber-300/30 bg-amber-400/10 px-5 py-3 text-sm font-semibold text-amber-100 transition hover:bg-amber-400/20 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.04] disabled:text-slate-500"
                    disabled={parsing}
                    onClick={() => {
                      void handleParse();
                    }}
                    type="button"
                  >
                    {parsing ? (
                      <span
                        aria-hidden="true"
                        className="h-4 w-4 animate-spin rounded-full border-2 border-current border-r-transparent"
                      />
                    ) : (
                      <svg
                        aria-hidden="true"
                        className="h-4 w-4"
                        fill="none"
                        stroke="currentColor"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth="1.7"
                        viewBox="0 0 24 24"
                      >
                        <path d="M6.75 4.75h8.5a2 2 0 0 1 2 2v12.5l-3.75-2-3.75 2-3.75-2-3.75 2V6.75a2 2 0 0 1 2-2h2.5" />
                        <path d="M7.75 4.75v12.5" />
                      </svg>
                    )}
                    {parsing ? "Parsing Manuscript..." : "Parse Manuscript"}
                  </button>
                  <div className="text-sm text-slate-400">
                    Step 1 of 4: extract narratable sections from the
                    manuscript.
                  </div>
                </div>

                {parseErrorMessage ? (
                  <div className="mt-4 rounded-3xl border border-rose-400/30 bg-rose-500/10 px-4 py-4 text-sm text-rose-100">
                    {parseErrorMessage}
                  </div>
                ) : null}
              </div>
            </section>
          ) : (
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
          )}

          {hasChapters ? (
            <div className="flex flex-col gap-6">
              {hasGeneratedChapters && selectedChapter ? (
                <section className="rounded-[2rem] border border-white/10 bg-white/[0.05] p-5 text-white shadow-xl shadow-slate-950/20">
                  <div className="flex flex-col gap-4">
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-[0.28em] text-sky-200/75">
                        Audio Preview
                      </div>
                      <h2 className="mt-3 text-xl font-semibold">
                        {selectedChapter.title ||
                          `Chapter ${selectedChapter.number}`}
                      </h2>
                      <p className="mt-3 text-sm leading-7 text-slate-300">
                        Inspect the generated chapter directly in the browser
                        before opening the full player panel.
                      </p>
                    </div>

                    {selectedChapterHasPreview && selectedChapterPreviewUrl ? (
                      <>
                        <audio
                          className="w-full"
                          controls
                          preload="metadata"
                          src={selectedChapterPreviewUrl}
                        >
                          Your browser does not support audio playback.
                        </audio>
                        <div className="text-sm text-slate-300">
                          Duration:{" "}
                          {formatDetailedDuration(
                            selectedChapter.audio_duration_seconds ??
                              selectedChapter.duration_seconds ??
                              0,
                          )}
                        </div>
                      </>
                    ) : (
                      <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4 text-sm leading-7 text-slate-300">
                        Generate this chapter to unlock the in-browser preview
                        player.
                      </div>
                    )}
                  </div>
                </section>
              ) : null}

              {hasGeneratedChapters && selectedChapter ? (
                <section className="rounded-[2rem] border border-white/10 bg-white/[0.05] p-5 text-white shadow-xl shadow-slate-950/20">
                  <div className="flex flex-col gap-5">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div className="text-xs font-semibold uppercase tracking-[0.28em] text-fuchsia-200/75">
                          Chapter Audio QA
                        </div>
                        <h2 className="mt-3 text-xl font-semibold">Deep QA</h2>
                        <p className="mt-3 text-sm leading-7 text-slate-300">
                          Inspect transcription drift, pacing, loudness, and
                          artifact issues for the selected chapter.
                        </p>
                      </div>
                      <div
                        className={`inline-flex items-center rounded-full border px-4 py-2 text-[11px] font-semibold uppercase tracking-[0.18em] ${
                          selectedChapterAudioQa
                            ? getDeepQaStatusBadge(
                                selectedChapterAudioQa.scoring.status,
                              )
                            : "border-white/10 bg-slate-950/45 text-slate-300"
                        }`}
                      >
                        {selectedChapterAudioQa
                          ? `${selectedChapterAudioQa.scoring.grade} · ${selectedChapterAudioQa.scoring.overall.toFixed(1)}`
                          : "Not run"}
                      </div>
                    </div>

                    {selectedChapter.status !== "generated" ? (
                      <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4 text-sm leading-7 text-slate-300">
                        Generate this chapter before running deep audio QA.
                      </div>
                    ) : (
                      <div className="flex flex-wrap gap-3">
                        <button
                          className="inline-flex items-center justify-center rounded-full border border-fuchsia-300/25 bg-fuchsia-400/10 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-fuchsia-100 transition hover:bg-fuchsia-400/20 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.04] disabled:text-slate-500"
                          disabled={audioQaBusy}
                          onClick={() => {
                            void handleRunChapterDeepQa();
                          }}
                          type="button"
                        >
                          {audioQaLoading &&
                          audioQaAction === `chapter-${selectedChapter.id}`
                            ? "Running Deep QA..."
                            : "Deep QA"}
                        </button>
                        {selectedChapterAudioQa?.checked_at ? (
                          <div className="inline-flex items-center rounded-full border border-white/10 bg-slate-950/45 px-4 py-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-300">
                            Checked{" "}
                            {formatTimestamp(
                              selectedChapterAudioQa.checked_at,
                            ) ?? "recently"}
                          </div>
                        ) : null}
                      </div>
                    )}

                    {audioQaErrorMessage ? (
                      <div className="rounded-3xl border border-rose-400/30 bg-rose-500/10 px-4 py-4 text-sm text-rose-100">
                        {audioQaErrorMessage}
                      </div>
                    ) : null}

                    {selectedChapterAudioQa ? (
                      <>
                        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                          <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4">
                            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
                              Transcription
                            </div>
                            <div className="mt-2 text-lg font-semibold text-white">
                              {selectedChapterAudioQa.scoring.transcription.toFixed(
                                1,
                              )}
                            </div>
                            <div className="mt-1 text-xs text-slate-400">
                              WER{" "}
                              {selectedChapterAudioQa.transcription.word_error_rate?.toFixed(
                                3,
                              ) ?? "n/a"}
                            </div>
                          </div>
                          <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4">
                            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
                              Timing
                            </div>
                            <div className="mt-2 text-lg font-semibold text-white">
                              {selectedChapterAudioQa.scoring.timing.toFixed(1)}
                            </div>
                            <div className="mt-1 text-xs text-slate-400">
                              {selectedChapterAudioQa.timing.speech_rate_wpm
                                ? `${selectedChapterAudioQa.timing.speech_rate_wpm.toFixed(1)} WPM`
                                : "No pace data"}
                            </div>
                          </div>
                          <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4">
                            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
                              Quality
                            </div>
                            <div className="mt-2 text-lg font-semibold text-white">
                              {selectedChapterAudioQa.scoring.quality.toFixed(
                                1,
                              )}
                            </div>
                            <div className="mt-1 text-xs text-slate-400">
                              {selectedChapterAudioQa.quality.integrated_lufs
                                ? `${selectedChapterAudioQa.quality.integrated_lufs.toFixed(1)} LUFS`
                                : "No loudness data"}
                            </div>
                          </div>
                          <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4">
                            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
                              Export Readiness
                            </div>
                            <div className="mt-2 text-lg font-semibold text-white">
                              {selectedChapterAudioQa.ready_for_export
                                ? "Ready"
                                : "Review Needed"}
                            </div>
                            <div className="mt-1 text-xs text-slate-400">
                              {selectedChapterAudioQa.issues.length} issue(s)
                            </div>
                          </div>
                        </div>

                        {selectedChapterAudioQa.issues.length > 0 ? (
                          <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4">
                            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
                              Issues
                            </div>
                            <div className="mt-4 space-y-3">
                              {selectedChapterAudioQa.issues
                                .slice(0, 8)
                                .map((issue, index) => (
                                  <div
                                    className="rounded-2xl border border-white/10 bg-slate-900/60 px-4 py-3"
                                    key={`${issue.code}-${index}`}
                                  >
                                    <div className="flex flex-wrap items-center justify-between gap-2">
                                      <div className="text-sm font-semibold text-white">
                                        {issue.message}
                                      </div>
                                      <div
                                        className={`text-xs font-semibold uppercase tracking-[0.18em] ${getStatusTone(issue.severity === "error" ? "fail" : "warning")}`}
                                      >
                                        {issue.severity}
                                      </div>
                                    </div>
                                    <div className="mt-2 text-xs text-slate-400">
                                      {issue.category}
                                      {formatIssueRange(issue)
                                        ? ` · ${formatIssueRange(issue)}`
                                        : ""}
                                    </div>
                                  </div>
                                ))}
                            </div>
                          </div>
                        ) : (
                          <div className="rounded-3xl border border-emerald-300/20 bg-emerald-400/10 px-4 py-4 text-sm text-emerald-100">
                            No deep audio QA issues were detected for this
                            chapter.
                          </div>
                        )}

                        {selectedChapterAudioQa.transcription.diff?.length >
                        0 ? (
                          <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4">
                            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
                              Transcript Diff
                            </div>
                            <div className="mt-4 space-y-2 text-sm text-slate-300">
                              {selectedChapterAudioQa.transcription.diff
                                .slice(0, 6)
                                .map((entry, index) => (
                                  <div
                                    className="rounded-2xl border border-white/10 bg-slate-900/60 px-4 py-3"
                                    key={`${entry.operation}-${index}`}
                                  >
                                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
                                      {entry.operation}
                                    </div>
                                    <div className="mt-2">
                                      Expected:{" "}
                                      <span className="text-white">
                                        {entry.expected ?? "∅"}
                                      </span>
                                    </div>
                                    <div className="mt-1">
                                      Heard:{" "}
                                      <span className="text-white">
                                        {entry.actual ?? "∅"}
                                      </span>
                                    </div>
                                  </div>
                                ))}
                            </div>
                          </div>
                        ) : null}
                      </>
                    ) : null}
                  </div>
                </section>
              ) : null}

              <NarrationSettings
                loadingMessage={voiceLoadingMessage}
                loadingVoices={loadingVoiceOptions}
                onChange={handleNarrationSettingsChange}
                selectedChapter={selectedChapter}
                settings={narrationSettings}
                voices={voiceOptions}
              />

              <section className="rounded-[2rem] border border-white/10 bg-white/[0.05] p-5 text-white shadow-xl shadow-slate-950/20">
                <div className="flex flex-col gap-5">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-[0.28em] text-emerald-200/75">
                        Book Quality
                      </div>
                      <h2 className="mt-3 text-xl font-semibold">
                        Gate 3 Overview
                      </h2>
                      <p className="mt-3 text-sm leading-7 text-slate-300">
                        Run cross-chapter QA, inspect voice drift, and master
                        the book before export.
                      </p>
                    </div>
                    <div
                      className={`inline-flex items-center rounded-full border px-4 py-2 text-[11px] font-semibold uppercase tracking-[0.18em] ${
                        bookQualityReport
                          ? getQualityBadgeTone(bookQualityReport.overall_grade)
                          : "border-white/10 bg-slate-950/45 text-slate-300"
                      }`}
                    >
                      {bookQualityReport
                        ? `Grade ${bookQualityReport.overall_grade}`
                        : "Not run"}
                    </div>
                  </div>

                  {!bookQualityEligible ? (
                    <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4 text-sm leading-7 text-slate-300">
                      Gate 3 runs after every chapter is generated and chapter
                      QA is complete.
                    </div>
                  ) : null}

                  {bookQualityErrorMessage ? (
                    <div className="rounded-3xl border border-rose-400/30 bg-rose-500/10 px-4 py-4 text-sm text-rose-100">
                      {bookQualityErrorMessage}
                    </div>
                  ) : null}

                  {bookQualityReport ? (
                    <div className="grid gap-3 sm:grid-cols-3">
                      <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4">
                        <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
                          Export Readiness
                        </div>
                        <div className="mt-2 text-lg font-semibold text-white">
                          {bookQualityReport.ready_for_export
                            ? "Ready for Export"
                            : "Issues must be resolved"}
                        </div>
                      </div>
                      <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4">
                        <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
                          Chapter Grades
                        </div>
                        <div className="mt-2 text-sm text-slate-300">
                          A {bookQualityReport.chapters_grade_a} / B{" "}
                          {bookQualityReport.chapters_grade_b} / C{" "}
                          {bookQualityReport.chapters_grade_c} / F{" "}
                          {bookQualityReport.chapters_grade_f}
                        </div>
                      </div>
                      <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4">
                        <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
                          Total Chapters
                        </div>
                        <div className="mt-2 text-lg font-semibold text-white">
                          {bookQualityReport.total_chapters}
                        </div>
                      </div>
                    </div>
                  ) : null}

                  {bookQualityReport ? (
                    <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4">
                      <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
                        Cross-Chapter Checks
                      </div>
                      <div className="mt-4 space-y-3">
                        {Object.entries(
                          bookQualityReport.cross_chapter_checks,
                        ).map(([name, details]) => (
                          <div
                            className="flex flex-col gap-1 rounded-2xl border border-white/10 bg-slate-900/60 px-4 py-3"
                            key={name}
                          >
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <div className="text-sm font-semibold capitalize text-white">
                                {name.replaceAll("_", " ")}
                              </div>
                              <div
                                className={`text-xs font-semibold uppercase tracking-[0.18em] ${getStatusTone(details.status)}`}
                              >
                                {details.status}
                              </div>
                            </div>
                            <div className="text-sm text-slate-300">
                              {details.message}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {voiceConsistencyChart?.chapters?.length > 0 ? (
                    <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4">
                      <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
                        Voice Consistency Chart
                      </div>
                      <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                        {voiceConsistencyChart.chapters.map((chapter) => (
                          <div
                            className="rounded-2xl border border-white/10 bg-slate-900/60 px-4 py-3"
                            key={chapter.number}
                          >
                            <div className="flex items-center justify-between gap-2">
                              <div className="text-sm font-semibold text-white">
                                Chapter {chapter.number}
                              </div>
                              <div
                                className={`inline-flex items-center rounded-full border px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] ${getQualityBadgeTone(chapter.grade)}`}
                              >
                                Grade {chapter.grade}
                              </div>
                            </div>
                            <div className="mt-3 space-y-2 text-sm text-slate-300">
                              <div>
                                Pitch:{" "}
                                {chapter.pitch
                                  ? `${chapter.pitch.toFixed(1)} Hz`
                                  : "n/a"}
                              </div>
                              <div>
                                Rate:{" "}
                                {chapter.rate
                                  ? `${chapter.rate.toFixed(1)} WPM`
                                  : "n/a"}
                              </div>
                              <div>
                                Brightness:{" "}
                                {chapter.brightness
                                  ? `${chapter.brightness.toFixed(0)} Hz`
                                  : "n/a"}
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {bookQualityReport?.recommendations?.length > 0 ? (
                    <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4">
                      <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
                        Recommendations
                      </div>
                      <ul className="mt-4 space-y-2 text-sm leading-7 text-slate-300">
                        {bookQualityReport.recommendations.map(
                          (recommendation) => (
                            <li key={recommendation}>• {recommendation}</li>
                          ),
                        )}
                      </ul>
                    </div>
                  ) : null}

                  {bookQualityReport?.export_blockers?.length > 0 ? (
                    <div className="rounded-3xl border border-rose-400/30 bg-rose-500/10 px-4 py-4 text-sm text-rose-100">
                      <div className="text-xs font-semibold uppercase tracking-[0.22em] text-rose-200/80">
                        Export Blockers
                      </div>
                      <ul className="mt-4 space-y-2 leading-7">
                        {bookQualityReport.export_blockers.map((blocker) => (
                          <li key={blocker}>• {blocker}</li>
                        ))}
                      </ul>
                    </div>
                  ) : null}

                  {!bookQualityReport && bookQualityEligible ? (
                    <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4 text-sm leading-7 text-slate-300">
                      Run Book QA to compute cross-chapter consistency, ACX
                      compliance, and mastering readiness.
                    </div>
                  ) : null}

                  <div className="flex flex-wrap gap-3">
                    <button
                      className="inline-flex items-center justify-center rounded-full border border-emerald-300/25 bg-emerald-400/10 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-emerald-100 transition hover:bg-emerald-400/20 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.04] disabled:text-slate-500"
                      disabled={!bookQualityEligible || bookQualityBusy}
                      onClick={() => {
                        void handleRunBookQa();
                      }}
                      type="button"
                    >
                      {bookQualityLoading && bookQualityAction === "run"
                        ? "Running Book QA..."
                        : "Run Book QA"}
                    </button>
                    <button
                      className="inline-flex items-center justify-center rounded-full border border-cyan-300/25 bg-cyan-400/10 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-cyan-100 transition hover:bg-cyan-400/20 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.04] disabled:text-slate-500"
                      disabled={!bookQualityEligible || bookQualityBusy}
                      onClick={() => {
                        void handleAutoMaster();
                      }}
                      type="button"
                    >
                      {bookQualityLoading && bookQualityAction === "master"
                        ? "Auto-Mastering..."
                        : "Auto-Master"}
                    </button>
                  </div>

                  {hasGeneratedChapters ? (
                    <div className="border-t border-white/10 pt-5">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <div className="text-xs font-semibold uppercase tracking-[0.28em] text-fuchsia-200/75">
                            Deep Audio QA
                          </div>
                          <h3 className="mt-3 text-lg font-semibold">
                            Automated Audio QA Pipeline
                          </h3>
                          <p className="mt-3 text-sm leading-7 text-slate-300">
                            Run transcription, pacing, and audio-quality scoring
                            across generated chapters and inspect the persisted
                            report.
                          </p>
                        </div>
                        <div
                          className={`inline-flex items-center rounded-full border px-4 py-2 text-[11px] font-semibold uppercase tracking-[0.18em] ${
                            audioQaReport
                              ? getDeepQaStatusBadge(
                                  audioQaReport.ready_for_export
                                    ? "pass"
                                    : "warning",
                                )
                              : "border-white/10 bg-slate-950/45 text-slate-300"
                          }`}
                        >
                          {audioQaReport
                            ? `${audioQaReport.average_score.toFixed(1)} Avg`
                            : "Not run"}
                        </div>
                      </div>

                      {!audioQaEligible ? (
                        <div className="mt-4 rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4 text-sm leading-7 text-slate-300">
                          Generate at least one chapter before running automated
                          audio QA.
                        </div>
                      ) : null}

                      {audioQaErrorMessage ? (
                        <div className="mt-4 rounded-3xl border border-rose-400/30 bg-rose-500/10 px-4 py-4 text-sm text-rose-100">
                          {audioQaErrorMessage}
                        </div>
                      ) : null}

                      {audioQaReport ? (
                        <>
                          <div className="mt-4 grid gap-3 sm:grid-cols-3">
                            <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4">
                              <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
                                Chapters Analyzed
                              </div>
                              <div className="mt-2 text-lg font-semibold text-white">
                                {audioQaReport.chapter_count}
                              </div>
                            </div>
                            <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4">
                              <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
                                Issue Count
                              </div>
                              <div className="mt-2 text-lg font-semibold text-white">
                                {audioQaReport.issue_count}
                              </div>
                            </div>
                            <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4">
                              <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
                                Export Readiness
                              </div>
                              <div className="mt-2 text-lg font-semibold text-white">
                                {audioQaReport.ready_for_export
                                  ? "Ready"
                                  : "Review Needed"}
                              </div>
                            </div>
                          </div>

                          <div className="mt-4 rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4">
                            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
                              Chapter Scores
                            </div>
                            <div className="mt-4 grid gap-3 md:grid-cols-2">
                              {audioQaReport.chapters.map((chapterReport) => (
                                <div
                                  className="rounded-2xl border border-white/10 bg-slate-900/60 px-4 py-3"
                                  key={chapterReport.chapter_n}
                                >
                                  <div className="flex flex-wrap items-center justify-between gap-2">
                                    <div className="text-sm font-semibold text-white">
                                      {chapterReport.chapter_title ||
                                        `Chapter ${chapterReport.chapter_n}`}
                                    </div>
                                    <div
                                      className={`inline-flex items-center rounded-full border px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] ${getDeepQaStatusBadge(chapterReport.scoring.status)}`}
                                    >
                                      {chapterReport.scoring.grade} ·{" "}
                                      {chapterReport.scoring.overall.toFixed(1)}
                                    </div>
                                  </div>
                                  <div className="mt-3 grid gap-2 text-sm text-slate-300 sm:grid-cols-3">
                                    <div>
                                      TX{" "}
                                      {chapterReport.scoring.transcription.toFixed(
                                        1,
                                      )}
                                    </div>
                                    <div>
                                      TM{" "}
                                      {chapterReport.scoring.timing.toFixed(1)}
                                    </div>
                                    <div>
                                      QL{" "}
                                      {chapterReport.scoring.quality.toFixed(1)}
                                    </div>
                                  </div>
                                </div>
                              ))}
                            </div>
                          </div>
                        </>
                      ) : null}

                      {!audioQaReport && audioQaEligible ? (
                        <div className="mt-4 rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4 text-sm leading-7 text-slate-300">
                          Run Audio QA to compute transcription accuracy,
                          pacing, loudness, and artifact checks for generated
                          chapters.
                        </div>
                      ) : null}

                      <div className="mt-4 flex flex-wrap gap-3">
                        <button
                          className="inline-flex items-center justify-center rounded-full border border-fuchsia-300/25 bg-fuchsia-400/10 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-fuchsia-100 transition hover:bg-fuchsia-400/20 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.04] disabled:text-slate-500"
                          disabled={!audioQaEligible || audioQaBusy}
                          onClick={() => {
                            void handleRunAudioQa();
                          }}
                          type="button"
                        >
                          {audioQaLoading && audioQaAction === "book"
                            ? "Running Audio QA..."
                            : "Run Audio QA"}
                        </button>
                      </div>
                    </div>
                  ) : null}
                </div>
              </section>

              <section className="rounded-[2rem] border border-white/10 bg-white/[0.05] p-5 text-white shadow-xl shadow-slate-950/20">
                <div className="flex flex-col gap-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-[0.28em] text-amber-200/75">
                        Export Pipeline
                      </div>
                      <h2 className="mt-3 text-xl font-semibold">
                        Audiobook Exports
                      </h2>
                      <p className="mt-3 text-sm leading-7 text-slate-300">
                        Concatenate the generated narration, normalize levels,
                        and package download-ready audiobook files.
                      </p>
                    </div>
                    <div className="inline-flex items-center rounded-full border border-white/10 bg-slate-950/45 px-4 py-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-300">
                      {exportStatusLabel}
                    </div>
                  </div>

                  {exportSnapshot?.completed_at ? (
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
                      Last export:{" "}
                      {formatTimestamp(exportSnapshot.completed_at) ??
                        "Unknown"}
                    </div>
                  ) : null}

                  {exportNeedsRefresh ? (
                    <div className="rounded-3xl border border-amber-300/25 bg-amber-400/10 px-4 py-4 text-sm leading-7 text-amber-100">
                      A past export file is available, but this book has{" "}
                      {mergedChapters.length - lastExportChapterCount} chapter
                      {mergedChapters.length - lastExportChapterCount === 1
                        ? ""
                        : "s"}{" "}
                      missing from the latest export. Generate the remaining
                      chapters and re-export for a complete audiobook.
                    </div>
                  ) : null}

                  {completedChapters.length === 0 ? (
                    <div className="rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-4 text-sm leading-7 text-slate-300">
                      Generate at least one chapter before starting an export.
                    </div>
                  ) : null}

                  {exportInProgress ? (
                    <ExportProgressBar
                      currentChapterN={exportSnapshot?.current_chapter_n}
                      currentFormat={exportSnapshot?.current_format}
                      currentStage={exportSnapshot?.current_stage}
                      label="Building the audiobook package and validating output files."
                      progressPercent={exportSnapshot?.progress_percent ?? null}
                      startTime={exportSnapshot?.started_at}
                      totalChapters={exportSnapshot?.total_chapters}
                    />
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
                          fileName={
                            details.file_name ?? `${book?.title}.${format}`
                          }
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
                            {details.error_message ??
                              "Export failed for this format."}
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
                      disabled={
                        completedChapters.length === 0 || exportInProgress
                      }
                      onClick={() => {
                        setExportDialogOpen(true);
                        setExportErrorMessage("");
                      }}
                      type="button"
                    >
                      {exportCompletedFormats.length > 0
                        ? "Re-export Audiobook"
                        : "Export Audiobook"}
                    </button>
                  </div>
                </div>
              </section>
            </div>
          ) : null}
        </div>
      </main>

      <AudioPlayerPanel
        bookId={id}
        chapterNumber={playerChapterNumber}
        completedChapters={completedChapters}
        onClose={() => setPlayerVisible(false)}
        onSelectChapter={(chapterNumber) => {
          const chapter = mergedChapters.find(
            (candidate) => candidate.number === chapterNumber,
          );
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
