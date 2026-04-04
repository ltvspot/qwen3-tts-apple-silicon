import React from "react";
import { getChapterLabel } from "./generationStatus";

function getWordCount(text) {
  if (!text?.trim()) {
    return 0;
  }

  return text.trim().split(/\s+/).length;
}

function getChapterDisplayName(chapter) {
  if (!chapter) {
    return "No Chapter Selected";
  }

  return getChapterLabel(chapter);
}

export default function TextPreview({
  chapter,
  draftText,
  editMode,
  hasUnsavedChanges,
  onBeginEdit,
  onCancelEdit,
  onSave,
  onTextChange,
  saveErrorMessage,
  saving,
}) {
  const activeText = editMode ? draftText : chapter?.text_content ?? "";
  const wordCount = getWordCount(activeText);
  const characterCount = activeText.length;
  const emptyText = activeText.trim().length === 0;

  if (!chapter) {
    return (
      <section className="flex h-full min-h-[24rem] items-center justify-center rounded-[2rem] border border-white/10 bg-white/[0.04] p-8 shadow-2xl shadow-slate-950/20">
        <div className="max-w-sm text-center">
          <div className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">
            Select a Chapter
          </div>
          <h2 className="mt-3 text-2xl font-semibold text-white">Text preview lives here</h2>
          <p className="mt-3 text-sm leading-7 text-slate-400">
            Choose a chapter from the manuscript panel to inspect its text, revise the copy,
            and prepare it for narration.
          </p>
        </div>
      </section>
    );
  }

  return (
    <section className="flex h-full min-h-[24rem] flex-col overflow-hidden rounded-[2rem] border border-white/10 bg-white/[0.04] shadow-2xl shadow-slate-950/20">
      <div className="border-b border-white/10 bg-slate-950/35 px-5 py-4">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="text-xs font-semibold uppercase tracking-[0.28em] text-amber-200/75">
              Chapter Text
            </div>
            <h2 className="mt-2 truncate text-xl font-semibold text-white">
              {getChapterDisplayName(chapter)}
            </h2>
            <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-300">
              <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-1">
                {wordCount} words
              </span>
              <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-1">
                {characterCount} characters
              </span>
              {hasUnsavedChanges ? (
                <span className="rounded-full border border-amber-300/25 bg-amber-400/10 px-2 py-1 text-amber-100">
                  Unsaved changes
                </span>
              ) : null}
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            {editMode ? (
              <button
                className="rounded-full border border-white/10 bg-white/[0.04] px-4 py-2 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.08]"
                onClick={onCancelEdit}
                type="button"
              >
                Cancel
              </button>
            ) : null}
            <button
              className={`rounded-full px-4 py-2 text-sm font-semibold transition ${
                editMode
                  ? "bg-emerald-400/90 text-slate-950 hover:bg-emerald-300 disabled:cursor-not-allowed disabled:opacity-60"
                  : "border border-sky-300/30 bg-sky-400/10 text-sky-100 hover:bg-sky-400/20"
              }`}
              disabled={editMode ? saving || emptyText : false}
              onClick={editMode ? onSave : onBeginEdit}
              type="button"
            >
              {saving ? "Saving..." : editMode ? "Save Changes" : "Edit Chapter"}
            </button>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-hidden">
        {editMode ? (
          <textarea
            aria-label="Chapter text editor"
            className="h-full min-h-[22rem] w-full resize-none border-0 bg-slate-950/70 p-5 font-mono text-sm leading-7 text-slate-100 outline-none"
            onChange={(event) => onTextChange(event.target.value)}
            placeholder="Chapter text..."
            value={draftText}
          />
        ) : (
          <div className="h-full overflow-y-auto p-5">
            {emptyText ? (
              <div className="rounded-[1.5rem] border border-dashed border-white/10 bg-slate-950/30 p-6 text-sm leading-7 text-slate-500">
                No text content is stored for this chapter yet.
              </div>
            ) : (
              <div className="rounded-[1.5rem] border border-white/8 bg-slate-950/30 p-6 text-base leading-8 text-slate-100">
                <div className="whitespace-pre-wrap">{activeText}</div>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="border-t border-white/10 bg-slate-950/35 px-5 py-4 text-sm">
        {saveErrorMessage ? (
          <div className="rounded-2xl border border-rose-400/30 bg-rose-500/10 px-4 py-3 text-rose-100">
            {saveErrorMessage}
          </div>
        ) : editMode ? (
          <div className="text-slate-300">
            Revise the parsed text, then save the new chapter copy back to the manuscript record.
          </div>
        ) : (
          <div className="text-slate-400">
            View mode is read-only. Switch to edit mode when the parser needs correction.
          </div>
        )}
      </div>
    </section>
  );
}
