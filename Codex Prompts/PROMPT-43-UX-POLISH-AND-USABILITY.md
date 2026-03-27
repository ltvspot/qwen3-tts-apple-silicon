# PROMPT-43: UX Polish and Usability — Critical Production Fixes

## Context

After a deep UX audit of the entire audiobook production app, these are the highest-impact issues blocking smooth production use. The focus is on making the core workflow (Parse → Review → Generate → QA → Export) clear and friction-free.

## Requirements

### Task 1: Speed Slider Value Display

**File:** `frontend/src/components/NarrationSettings.jsx`

The speed slider currently has no visible value indicator. Users can't see the exact speed (0.9x, 1.0x, 1.1x) while adjusting.

**Fix:**
- Show the current speed value next to the slider label, e.g. "Speed: **1.0x**"
- Update in real-time as the slider moves
- Show the value formatted as `{value}x` (e.g., "0.85x", "1.0x", "1.2x")

### Task 2: Replace window.confirm() with Styled Modal

**File:** `frontend/src/pages/BookDetail.jsx`

The "Generate All" button uses browser-native `window.confirm()` which looks inconsistent with the app's dark theme. Replace with a styled confirmation dialog.

**Fix:**
- Create a simple inline confirmation component (or use a state-based pattern)
- When user clicks "Generate All", show a styled modal/dialog with:
  - Title: "Generate All Chapters"
  - Message: "This will generate audio for all remaining {count} chapters. This may take a while."
  - Two buttons: "Cancel" (secondary) and "Start Generation" (primary, amber)
- Match the existing dark theme styling (rounded corners, borders, etc.)
- Do NOT use any external modal libraries — keep it simple with React state + conditional rendering

### Task 3: Clarify Export Dialog Wording

**File:** `frontend/src/components/ExportDialog.jsx`

The "Include only QA-approved chapters" checkbox uses confusing double-negative wording.

**Fix:**
- Change checkbox label to: "Skip flagged chapters"
- Change description to: "When checked, chapters flagged during QA will be excluded from the export."
- Add a summary line below the format checkboxes showing: "Will export {n} of {total} chapters ({duration} estimated)"
  - Calculate from the chapters data passed to ExportDialog
  - Show duration in human-friendly format (e.g., "2h 34m")

### Task 4: Explain QA Terminology with Tooltips

**File:** `frontend/src/pages/BookDetail.jsx`

Several QA sections use jargon without explanation (Gate 3, TX/TM/QL scores, Deep QA).

**Fix:**
- Add inline help text (small gray text, not tooltips) to clarify:
  - Next to "Deep QA" button: add small text "Analyzes transcription accuracy, pacing, and audio quality"
  - In Gate 3 section header: rename from "Book Quality" with subtitle "Gate 3 Overview" to just "Book Quality Review" — remove "Gate 3" terminology entirely
  - In chapter scores grid: expand abbreviations inline — show "Transcription", "Timing", "Quality" as column headers instead of TX/TM/QL
  - In QA issues list: if more than 8 issues exist, add a "Show all {n} issues" expandable link instead of silently truncating

### Task 5: Add "Quick Add" Button to Pronunciation Suggestions

**File:** `frontend/src/components/PronunciationSettings.jsx`

Pronunciation suggestions from QA show words that need pronunciation overrides, but users must manually copy/paste words into the input field. This is high-friction.

**Fix:**
- Add a "Add to Dictionary" button (or "+" icon button) next to each suggestion
- When clicked, auto-fill the Global pronunciation form with the suggested word
- If a suggested pronunciation exists, pre-fill that too
- Scroll to the form and focus the pronunciation input field so user can type the phonetic spelling

### Task 6: Batch Production — Clarify "Start Batch" Flow

**File:** `frontend/src/pages/BatchProduction.jsx`

When no batch is active, the "Start Batch" button gives no context about what it will do.

**Fix:**
- Below the "Start Batch" button, add helper text: "Generates audio for all unparsed and unfinished books in the catalog sequentially."
- When a batch IS active, show which book is currently being processed with a highlighted row or "▶ Currently processing" indicator
- Change the ETA display from raw timestamp to relative format: "~2h 15m remaining" with the timestamp in smaller text below

## Testing

Run the full frontend test suite after implementation:
```
cd frontend && npx react-scripts test --watchAll=false
```

All existing tests must continue to pass. Add at least:
1. Test: Speed slider shows current value
2. Test: Generate All confirmation modal renders (not window.confirm)
3. Test: Export dialog shows chapter count summary
4. Test: QA section shows expanded terminology (no "Gate 3" text)

## Files to Modify

- `frontend/src/components/NarrationSettings.jsx` — Speed value display
- `frontend/src/pages/BookDetail.jsx` — Confirmation modal, QA terminology
- `frontend/src/components/ExportDialog.jsx` — Export wording, chapter summary
- `frontend/src/components/PronunciationSettings.jsx` — Quick-add from suggestions
- `frontend/src/pages/BatchProduction.jsx` — Batch context, ETA format

## Constraints

- Do NOT modify any backend Python files
- Keep all existing functionality intact
- Maintain the existing dark theme / design system
- All existing tests must continue to pass
- No external libraries (no modal libraries, tooltip libraries, etc.)
