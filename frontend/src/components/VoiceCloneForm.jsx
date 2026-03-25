import React, { useMemo, useRef, useState } from "react";
import ProgressHeartbeat from "./ProgressHeartbeat";

const ACCEPTED_SUFFIXES = [".wav", ".mp3", ".m4a"];

function isAcceptedAudioFile(file) {
  if (!file) {
    return false;
  }

  const lowerName = file.name.toLowerCase();
  return ACCEPTED_SUFFIXES.some((suffix) => lowerName.endsWith(suffix));
}

export default function VoiceCloneForm({ onCloned }) {
  const [cloneProgress, setCloneProgress] = useState(null);
  const [displayName, setDisplayName] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [isDragActive, setIsDragActive] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [notes, setNotes] = useState("");
  const [referenceAudio, setReferenceAudio] = useState(null);
  const [successMessage, setSuccessMessage] = useState("");
  const [transcript, setTranscript] = useState("");
  const [voiceName, setVoiceName] = useState("");
  const fileInputRef = useRef(null);

  const validationError = useMemo(() => {
    if (!voiceName.trim()) {
      return "";
    }

    if (!/^[a-z0-9]+(?:[-_][a-z0-9]+)*$/.test(voiceName.trim())) {
      return "Voice ID must use lowercase letters, numbers, hyphens, or underscores only.";
    }

    return "";
  }, [voiceName]);

  function handleAudioSelection(file) {
    if (!file) {
      return;
    }

    if (!isAcceptedAudioFile(file)) {
      setErrorMessage("Please upload a WAV, MP3, or M4A file.");
      return;
    }

    setReferenceAudio(file);
    setErrorMessage("");
  }

  async function handleSubmit(event) {
    event.preventDefault();

    const trimmedVoiceName = voiceName.trim();
    const trimmedDisplayName = displayName.trim();
    const trimmedTranscript = transcript.trim();

    if (!trimmedVoiceName || !trimmedDisplayName || !referenceAudio || !trimmedTranscript) {
      setErrorMessage("Please fill in all required fields.");
      return;
    }

    if (validationError) {
      setErrorMessage(validationError);
      return;
    }

    setErrorMessage("");
    setSuccessMessage("");
    setIsSubmitting(true);
    const startedAt = Date.now();
    setCloneProgress({
      phase: "upload",
      progressPercent: 0,
      startTime: startedAt,
    });

    const formData = new FormData();
    formData.append("voice_name", trimmedVoiceName);
    formData.append("display_name", trimmedDisplayName);
    formData.append("reference_audio", referenceAudio);
    formData.append("transcript", trimmedTranscript);
    formData.append("notes", notes.trim());

    try {
      const payload = await new Promise((resolve, reject) => {
        const request = new XMLHttpRequest();
        request.open("POST", "/api/voice-lab/clone");
        request.responseType = "json";

        request.upload.onprogress = (event) => {
          if (!event.lengthComputable) {
            setCloneProgress({
              phase: "upload",
              progressPercent: null,
              startTime: startedAt,
            });
            return;
          }

          setCloneProgress({
            phase: "upload",
            progressPercent: (event.loaded / event.total) * 100,
            startTime: startedAt,
          });
        };

        request.upload.onloadend = () => {
          setCloneProgress({
            phase: "processing",
            progressPercent: null,
            startTime: startedAt,
          });
        };

        request.onerror = () => {
          reject(new Error("Clone failed."));
        };

        request.onload = () => {
          const responsePayload = request.response
            ?? JSON.parse(request.responseText || "null");
          if (request.status >= 200 && request.status < 300) {
            resolve(responsePayload);
            return;
          }

          reject(new Error(responsePayload?.detail ?? "Clone failed."));
        };

        request.send(formData);
      });
      setSuccessMessage(`${payload.display_name} is ready for audition and generation.`);
      setVoiceName("");
      setDisplayName("");
      setReferenceAudio(null);
      setTranscript("");
      setNotes("");
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }

      await onCloned(payload);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Clone failed.");
    } finally {
      setCloneProgress(null);
      setIsSubmitting(false);
    }
  }

  return (
    <section className="rounded-[2rem] border border-slate-200 bg-white p-6 shadow-xl shadow-slate-900/5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">
            Clone Voice
          </p>
          <h3 className="mt-2 text-2xl font-semibold text-slate-950">Create a reusable voice reference</h3>
          <p className="mt-3 text-sm leading-7 text-slate-600">
            Use a clean 1-10 second sample and the exact transcript. Bad reference audio creates bad narration later.
          </p>
        </div>
        <div className="rounded-full bg-amber-50 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-amber-700">
          1-10 sec sample
        </div>
      </div>

      {successMessage ? (
        <div className="mt-6 rounded-[1.5rem] border border-emerald-200 bg-emerald-50 px-5 py-4 text-sm text-emerald-800">
          {successMessage}
        </div>
      ) : null}

      {errorMessage ? (
        <div className="mt-6 rounded-[1.5rem] border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700">
          {errorMessage}
        </div>
      ) : null}

      {cloneProgress ? (
        <div className="mt-6">
          <ProgressHeartbeat
            isActive={isSubmitting}
            progressPercent={cloneProgress.progressPercent}
            showETA={null}
            size="md"
            stage={
              cloneProgress.phase === "upload"
                ? "Uploading reference audio..."
                : "Processing voice clone..."
            }
            startTime={cloneProgress.startTime}
          />
        </div>
      ) : null}

      <form className="mt-6 space-y-5" onSubmit={(event) => { void handleSubmit(event); }}>
        <div className="grid gap-5 lg:grid-cols-2">
          <div>
            <label className="block text-sm font-semibold text-slate-950" htmlFor="clone-voice-name">
              Voice ID
            </label>
            <input
              aria-label="Voice ID"
              className="mt-2 w-full rounded-2xl border border-slate-300 bg-slate-50 px-4 py-3 text-slate-900 outline-none transition focus:border-slate-950 focus:bg-white"
              id="clone-voice-name"
              onChange={(event) => setVoiceName(event.target.value)}
              placeholder="kent-zimering"
              type="text"
              value={voiceName}
            />
            <p className="mt-2 text-xs font-medium text-slate-500">
              Lowercase letters, numbers, hyphens, or underscores only.
            </p>
            {validationError ? (
              <p className="mt-2 text-xs font-semibold text-rose-600">{validationError}</p>
            ) : null}
          </div>

          <div>
            <label className="block text-sm font-semibold text-slate-950" htmlFor="clone-display-name">
              Display Name
            </label>
            <input
              aria-label="Display Name"
              className="mt-2 w-full rounded-2xl border border-slate-300 bg-slate-50 px-4 py-3 text-slate-900 outline-none transition focus:border-slate-950 focus:bg-white"
              id="clone-display-name"
              onChange={(event) => setDisplayName(event.target.value)}
              placeholder="Kent Zimering Clone"
              type="text"
              value={displayName}
            />
          </div>
        </div>

        <div>
          <label className="block text-sm font-semibold text-slate-950" htmlFor="clone-reference-audio">
            Reference Audio
          </label>
          <div
            className={`mt-2 rounded-[1.75rem] border-2 border-dashed px-5 py-6 transition ${
              isDragActive
                ? "border-slate-950 bg-slate-950 text-white"
                : "border-slate-300 bg-slate-50 text-slate-600"
            }`}
            onClick={() => fileInputRef.current?.click()}
            onDragEnter={(event) => {
              event.preventDefault();
              setIsDragActive(true);
            }}
            onDragLeave={(event) => {
              event.preventDefault();
              setIsDragActive(false);
            }}
            onDragOver={(event) => {
              event.preventDefault();
              setIsDragActive(true);
            }}
            onDrop={(event) => {
              event.preventDefault();
              setIsDragActive(false);
              handleAudioSelection(event.dataTransfer.files?.[0] ?? null);
            }}
            role="button"
            tabIndex={0}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                fileInputRef.current?.click();
              }
            }}
          >
            <input
              accept=".wav,.mp3,.m4a"
              aria-label="Reference Audio"
              className="hidden"
              id="clone-reference-audio"
              onChange={(event) => handleAudioSelection(event.target.files?.[0] ?? null)}
              ref={fileInputRef}
              type="file"
            />
            <div className="text-sm font-semibold">
              {referenceAudio ? referenceAudio.name : "Drop a sample here or click to choose a file"}
            </div>
            <p className={`mt-2 text-sm ${isDragActive ? "text-slate-200" : "text-slate-500"}`}>
              WAV, MP3, or M4A. Clear speech wins over long speech.
            </p>
          </div>
        </div>

        <div>
          <label className="block text-sm font-semibold text-slate-950" htmlFor="clone-transcript">
            Transcript
          </label>
          <textarea
            aria-label="Transcript"
            className="mt-2 h-28 w-full rounded-[1.75rem] border border-slate-300 bg-slate-50 px-4 py-3 text-slate-900 outline-none transition focus:border-slate-950 focus:bg-white"
            id="clone-transcript"
            onChange={(event) => setTranscript(event.target.value)}
            placeholder="Enter the exact words spoken in the reference audio."
            value={transcript}
          />
        </div>

        <div>
          <label className="block text-sm font-semibold text-slate-950" htmlFor="clone-notes">
            Notes
          </label>
          <textarea
            aria-label="Notes"
            className="mt-2 h-20 w-full rounded-[1.75rem] border border-slate-300 bg-slate-50 px-4 py-3 text-slate-900 outline-none transition focus:border-slate-950 focus:bg-white"
            id="clone-notes"
            onChange={(event) => setNotes(event.target.value)}
            placeholder="Optional context: recording quality, tone, source session, cleanup notes."
            value={notes}
          />
        </div>

        <button
          className="inline-flex items-center justify-center rounded-full bg-slate-950 px-6 py-3 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
          disabled={isSubmitting}
          type="submit"
        >
          {isSubmitting ? "Cloning Voice..." : "Clone Voice"}
        </button>
      </form>
    </section>
  );
}
