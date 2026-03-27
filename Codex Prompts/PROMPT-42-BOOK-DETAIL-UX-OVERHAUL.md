# PROMPT-42: Book Detail Page UX Overhaul

## Context

The Book Detail page (`/book/{id}`) is the primary workspace for narrating a single book. Currently it has critical UX problems:

1. **No Parse button exists.** The backend has `POST /api/book/{book_id}/parse` fully implemented, but the frontend has NO UI to trigger it. The ChapterList shows "No Chapters Yet — Parse the manuscript before editing or generating narration" but provides no action to do so. The user is completely stuck.

2. **Irrelevant controls shown in empty state.** When a book has 0 chapters (unparsed), the page still shows: Generation Controls with "Generate All" button, voice/emotion/speed selectors, "Generate Audio" button, Book Quality section. These are all useless before parsing and confuse the user.

3. **Unclear workflow progression.** There's no visual indication of the book lifecycle: Parse → Review/Edit → Generate → QA → Export. The user doesn't know what step they're on or what to do next.

4. **"ALL CHAPTERS COMPLETE" is misleading.** When there are 0 chapters, the status reads "All chapters complete" — technically true (0 of 0) but completely misleading.

## Requirements

### Task 1: Add Parse Manuscript Button

**Files:** `frontend/src/components/ChapterList.jsx`, `frontend/src/pages/BookDetail.jsx`

**When `chapters.length === 0`:**
- Replace the current empty state text in ChapterList with a prominent "Parse Manuscript" button
- The button calls `POST /api/book/{book_id}/parse`
- Show a loading spinner while parsing (parsing can take several seconds for large manuscripts)
- On success: refresh the book data (chapters should now be populated)
- On error: show error message inline
- The parse button should also be available in the header Generation Controls area as a primary CTA

**Accept a new prop** `onParse` in ChapterList that BookDetail provides. BookDetail should implement the parse handler:
```js
const handleParse = async () => {
  setParsing(true);
  try {
    const res = await fetch(`/api/book/${id}/parse`, { method: "POST" });
    if (!res.ok) throw new Error("Parse failed");
    await fetchBookData(); // refresh everything
  } catch (err) {
    setErrorMessage(err.message);
  } finally {
    setParsing(false);
  }
};
```

### Task 2: Progressive Disclosure — Hide Irrelevant Controls

**Files:** `frontend/src/pages/BookDetail.jsx`

**When the book has NO chapters (unparsed state):**
- Header: Replace "Generation Controls" box with a "Getting Started" box that has the Parse Manuscript button as the primary CTA and a brief explanation: "Parse this manuscript to extract chapters, then review and generate audio."
- Right column: HIDE the following sections entirely:
  - NarrationSettings (voice, emotion, speed) — not relevant until chapters exist
  - Audio Preview section — nothing to preview
  - Deep QA section — nothing to QA
  - Book Quality / Gate 3 section — not relevant yet
- Center column: Show a welcoming empty state instead of "SELECT A CHAPTER" — something like a brief guide: "Step 1: Parse the manuscript → Step 2: Review chapter text → Step 3: Generate audio"

**When the book HAS chapters but none generated:**
- Show NarrationSettings (voice, emotion, speed)
- Show "Generate All" in header
- HIDE Audio Preview (no audio yet)
- HIDE Deep QA (no audio to QA)
- Show Book Quality section but clearly indicate it runs after generation

**When chapters are generated:**
- Show everything (current behavior is fine for this state)

### Task 3: Fix Misleading Status Labels

**Files:** `frontend/src/pages/BookDetail.jsx`

- When `chapters.length === 0`: Status should read **"Manuscript not parsed"** (not "All chapters complete")
- When some chapters are pending: **"Ready to generate"**
- When actively generating: **"Generation active"** (keep current)
- When all chapters completed: **"All chapters complete"** (keep current)

### Task 4: Improve the Empty State Center Panel

**Files:** `frontend/src/pages/BookDetail.jsx` (the TextPreview area)

When no chapter is selected AND no chapters exist, the center panel currently shows "SELECT A CHAPTER" which is impossible. Replace with a workflow guide:

```
┌──────────────────────────────────┐
│  GET STARTED                     │
│                                  │
│  1. Parse Manuscript             │
│     Extract chapters from the    │
│     uploaded document            │
│                                  │
│  2. Review & Edit                │
│     Check chapter text, fix any  │
│     parsing errors               │
│                                  │
│  3. Generate Audio               │
│     Produce narration for each   │
│     chapter or the entire book   │
│                                  │
│  4. Quality Check & Export       │
│     Run QA, master, and export   │
│     the final audiobook          │
│                                  │
│  [Parse Manuscript →]            │
└──────────────────────────────────┘
```

When chapters exist but none selected, keep the current "SELECT A CHAPTER" text.

### Task 5: Visual Polish

**Files:** `frontend/src/pages/BookDetail.jsx`, `frontend/src/components/ChapterList.jsx`

- The Parse Manuscript button should be styled as a primary action — amber/gold theme consistent with the app's design language (border-amber-300/30, bg-amber-400/10, text-amber-100, hover:bg-amber-400/20)
- Add a subtle book/document icon next to "Parse Manuscript" text
- The workflow steps in the center panel should have subtle step numbers and be styled consistently with the app's dark theme
- Ensure the 3-column layout doesn't show empty/useless columns in the unparsed state — consider a simpler layout for the initial state

## Testing

Add tests in `frontend/src/pages/BookDetail.test.jsx`:

1. **Test: Parse button visible when no chapters** — Render BookDetail with a book that has 0 chapters, verify "Parse Manuscript" button is present
2. **Test: Parse button triggers API call** — Mock the parse endpoint, click the button, verify POST was called
3. **Test: Generation controls hidden when unparsed** — Verify "Generate All" button is NOT rendered when chapters.length === 0
4. **Test: NarrationSettings hidden when unparsed** — Verify voice/emotion selectors not rendered when no chapters
5. **Test: Status label correct for unparsed** — Verify status reads "Manuscript not parsed" when chapters.length === 0
6. **Test: Workflow guide shown in center panel** — Verify "Get Started" content visible when no chapters and no chapter selected

## Files to Modify

- `frontend/src/pages/BookDetail.jsx` — Main page: add parse handler, progressive disclosure, fix status labels, improve empty states
- `frontend/src/components/ChapterList.jsx` — Add onParse prop, parse button in empty state
- `frontend/src/pages/BookDetail.test.jsx` — Add 6 new tests

## Constraints

- Do NOT modify any backend Python files
- Do NOT change the parse endpoint behavior
- Keep all existing functionality intact for books that already have chapters
- Maintain the existing dark theme / design system
- All existing tests must continue to pass
