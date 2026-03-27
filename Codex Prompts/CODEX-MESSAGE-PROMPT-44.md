Read CLAUDE.md and PROJECT-STATE.md for project conventions.

Then read and implement all tasks in Codex Prompts/PROMPT-44-ELIMINATE-BROWSER-DIALOGS-AND-UX-FIXES.md

This prompt creates 2 new shared components and modifies 4 existing files:

New files:
- frontend/src/components/ConfirmDialog.jsx — Shared modal dialog (confirm/prompt/alert modes)
- frontend/src/components/Toast.jsx — Toast notification for action feedback

Modified files:
- frontend/src/pages/VoiceLab.jsx — Replace window.prompt() and window.confirm() with ConfirmDialog
- frontend/src/components/SettingsForm.jsx — Replace window.confirm() and window.prompt() with ConfirmDialog
- frontend/src/components/NarrationSettings.jsx — Replace window.alert() calls with ConfirmDialog
- frontend/src/pages/BookDetail.jsx — Replace discard-edits window.confirm(), refactor Generate All modal to use shared ConfirmDialog, add Toast notifications

Key goal: ZERO browser-native dialogs remaining in the entire app. Every window.confirm/alert/prompt must be replaced with a styled ConfirmDialog.

Run the existing test suite after implementation:
```
cd frontend && npx react-scripts test --watchAll=false
```
