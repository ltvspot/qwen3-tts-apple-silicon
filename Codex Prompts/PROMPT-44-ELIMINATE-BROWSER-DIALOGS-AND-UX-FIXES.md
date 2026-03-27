# PROMPT-44: Eliminate All Browser Dialogs + UX Hardening

## Context

After PROMPT-43 replaced `window.confirm()` for "Generate All" with a styled modal in BookDetail.jsx, there are still **7 remaining browser dialog calls** across 3 files. These break the dark theme, block the UI, and look unprofessional. This prompt replaces ALL of them with styled inline modals matching the existing design system, plus adds critical UX improvements.

## Requirements

### Task 1: Create a Shared ConfirmDialog Component

**New file:** `frontend/src/components/ConfirmDialog.jsx`

Create a reusable confirmation dialog component that replaces all `window.confirm()`, `window.alert()`, and `window.prompt()` usage. This component was partially created in BookDetail.jsx by PROMPT-43 for the "Generate All" case — now extract it into a shared component.

**Props:**
```jsx
ConfirmDialog.propTypes = {
  open: PropTypes.bool.isRequired,        // Whether dialog is visible
  title: PropTypes.string.isRequired,     // Dialog title
  message: PropTypes.string.isRequired,   // Body text
  confirmLabel: PropTypes.string,         // Primary button text (default: "Confirm")
  cancelLabel: PropTypes.string,          // Secondary button text (default: "Cancel")
  confirmColor: PropTypes.string,         // Tailwind color class for confirm button (default: "amber")
  onConfirm: PropTypes.func.isRequired,   // Called when user confirms
  onCancel: PropTypes.func.isRequired,    // Called when user cancels
  // For prompt mode (text input):
  promptMode: PropTypes.bool,             // Show text input field
  promptLabel: PropTypes.string,          // Label for the input
  promptDefault: PropTypes.string,        // Default value for input
  // For alert mode (single button):
  alertMode: PropTypes.bool,              // Only show one button (OK)
};
```

**Styling:**
- Full-screen overlay: `fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 backdrop-blur-sm`
- Dialog panel: `rounded-[2rem] border border-white/10 bg-slate-900 p-8 max-w-md w-full shadow-2xl`
- Title: `text-xl font-semibold text-white mb-3`
- Message: `text-sm text-slate-400 mb-6`
- Buttons: Match existing app button styles (rounded-full, border, etc.)
- Primary button: amber accent (or configurable via confirmColor)
- Input field (prompt mode): `rounded-2xl border border-white/10 bg-white/5 text-white px-4 py-3`
- Close on Escape key press
- Trap focus within dialog (tab cycling)
- Animate in with a subtle scale transition if feasible (CSS only)

### Task 2: Replace All Browser Dialogs in VoiceLab.jsx

**File:** `frontend/src/pages/VoiceLab.jsx`

**2a. Replace `window.prompt()` for preset name (line ~481):**
- Current: `const presetName = window.prompt("Preset name:");`
- New: Show ConfirmDialog in `promptMode` with:
  - title: "Save Voice Preset"
  - message: "Enter a name for this voice configuration preset."
  - promptLabel: "Preset name"
  - confirmLabel: "Save Preset"
- On confirm with text → proceed with existing save logic

**2b. Replace `window.confirm()` for voice deletion (line ~531):**
- Current: `if (!window.confirm(\`Delete voice "${voiceName}"?\`)) return;`
- New: Show ConfirmDialog with:
  - title: "Delete Cloned Voice"
  - message: `Are you sure you want to delete "${voiceName}"? This cannot be undone.`
  - confirmLabel: "Delete Voice"
  - confirmColor: "rose" (red/destructive)
- On confirm → proceed with existing delete logic

**State additions needed:**
```jsx
const [confirmDialog, setConfirmDialog] = useState({ open: false, type: null, data: null });
```

### Task 3: Replace All Browser Dialogs in SettingsForm.jsx

**File:** `frontend/src/components/SettingsForm.jsx`

**3a. Replace `window.confirm()` for reset to defaults (line ~216):**
- Current: `const shouldReset = window.confirm("Reset all settings to defaults?");`
- New: Show ConfirmDialog with:
  - title: "Reset to Defaults"
  - message: "This will revert all settings to their factory defaults. Any custom configuration will be lost."
  - confirmLabel: "Reset All"
  - confirmColor: "rose" (destructive)
- On confirm → proceed with existing reset logic

**3b. Replace `window.prompt()` for folder path (line ~340):**
- Current: `const nextPath = window.prompt("Enter the formatted manuscripts folder path", formSettings.manuscript_source_folder);`
- New: Show ConfirmDialog in `promptMode` with:
  - title: "Set Manuscript Folder"
  - message: "Enter the full path to the folder containing formatted manuscripts."
  - promptLabel: "Folder path"
  - promptDefault: current `formSettings.manuscript_source_folder` value
  - confirmLabel: "Set Path"
- On confirm with text → proceed with existing handleChange logic

### Task 4: Replace All Browser Dialogs in NarrationSettings.jsx

**File:** `frontend/src/components/NarrationSettings.jsx`

**4a. Replace `window.alert("Please select a chapter first.")` (line ~113):**
- Current: Shows browser alert when no chapter selected
- New: Show ConfirmDialog in `alertMode` with:
  - title: "No Chapter Selected"
  - message: "Please select a chapter from the list before generating audio."
  - confirmLabel: "OK"

**4b. Replace `window.alert("Audio generation arrives in a later prompt.")` (line ~117):**
- This appears to be placeholder text from an earlier dev stage.
- **Remove this alert entirely.** Instead, the generate button should call the actual generation handler passed via props (`onGenerate` or similar). If no generation handler is available, disable the button with a tooltip "Generation not yet available."

### Task 5: Replace Discard Edits Confirm in BookDetail.jsx

**File:** `frontend/src/pages/BookDetail.jsx`

**5a. Replace `window.confirm("Discard unsaved chapter edits?")` (line ~841):**
- Current: Browser confirm when switching chapters with unsaved changes
- New: Show ConfirmDialog with:
  - title: "Unsaved Changes"
  - message: "You have unsaved edits to this chapter. Discard changes and switch to the new chapter?"
  - confirmLabel: "Discard & Switch"
  - confirmColor: "rose" (destructive)
  - cancelLabel: "Keep Editing"
- On confirm → proceed with chapter switch (existing logic)
- On cancel → stay on current chapter

**Implementation note:** Since chapter switching is triggered by clicking a chapter in the list, you'll need to store the "pending chapter" in state and only complete the switch after confirmation.

### Task 6: Add Success/Error Toast Notifications

**New file:** `frontend/src/components/Toast.jsx`

Create a simple toast notification component for action feedback (save success, generation started, export complete, etc.). Currently many actions complete silently.

**Props:**
```jsx
Toast.propTypes = {
  message: PropTypes.string.isRequired,
  type: PropTypes.oneOf(["success", "error", "info"]),
  visible: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
};
```

**Styling:**
- Fixed position: bottom-right corner (`fixed bottom-6 right-6 z-50`)
- Pill shape: `rounded-2xl px-6 py-4 shadow-xl`
- Success: emerald border + icon
- Error: rose border + icon
- Info: sky border + icon
- Auto-dismiss after 4 seconds
- Slide-in animation from right

**Add Toast to these actions (in BookDetail.jsx and SettingsForm.jsx):**
- Chapter save → "Chapter saved successfully"
- Settings save → "Settings saved"
- Settings reset → "Settings reset to defaults"
- Generation queued → "Audio generation queued for {n} chapters"
- Parse complete → "Manuscript parsed — {n} chapters found"
- Export started → "Export started"

## Testing

Run the full frontend test suite after implementation:
```
cd frontend && npx react-scripts test --watchAll=false
```

All existing tests must continue to pass. Update any test mocks that previously used `window.confirm` / `window.alert` / `window.prompt` to instead interact with the new ConfirmDialog component.

Add at least:
1. Test: ConfirmDialog renders in confirm mode
2. Test: ConfirmDialog renders in prompt mode with input
3. Test: ConfirmDialog renders in alert mode (single button)
4. Test: VoiceLab shows styled dialog for voice deletion (not window.confirm)
5. Test: SettingsForm shows styled dialog for reset (not window.confirm)
6. Test: Toast auto-dismisses after timeout

## Files to Create
- `frontend/src/components/ConfirmDialog.jsx` — Shared dialog component
- `frontend/src/components/Toast.jsx` — Toast notification component

## Files to Modify
- `frontend/src/pages/VoiceLab.jsx` — Replace 2 browser dialogs
- `frontend/src/components/SettingsForm.jsx` — Replace 2 browser dialogs
- `frontend/src/components/NarrationSettings.jsx` — Replace 2 browser alerts
- `frontend/src/pages/BookDetail.jsx` — Replace discard-edits confirm, refactor Generate All modal to use shared ConfirmDialog, add Toast for action feedback

## Constraints

- Do NOT modify any backend Python files
- Keep all existing functionality intact
- Maintain the existing dark theme for dark-themed pages, light theme for light-themed pages
- ConfirmDialog should support both themes (detect from parent context or accept a `theme` prop)
- All existing tests must continue to pass (update mocks as needed)
- No external libraries — pure React + Tailwind
- The shared ConfirmDialog should be a drop-in replacement wherever PROMPT-43 created an inline modal in BookDetail.jsx
