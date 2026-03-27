Read CLAUDE.md and PROJECT-STATE.md for project conventions.

Then read and implement all tasks in Codex Prompts/PROMPT-43-UX-POLISH-AND-USABILITY.md

This is a frontend-only change — 5 files to modify:
- frontend/src/components/NarrationSettings.jsx — Speed value display
- frontend/src/pages/BookDetail.jsx — Styled confirmation modal, QA terminology cleanup
- frontend/src/components/ExportDialog.jsx — Better export wording, chapter count summary
- frontend/src/components/PronunciationSettings.jsx — Quick-add from QA suggestions
- frontend/src/pages/BatchProduction.jsx — Batch context text, relative ETA format

Key changes:
1. Show speed value (e.g. "1.0x") next to the speed slider
2. Replace browser window.confirm() with a styled dark-theme modal for "Generate All"
3. Fix confusing export dialog wording (double negatives → clear labels + chapter count preview)
4. Remove "Gate 3" jargon, expand TX/TM/QL abbreviations, add QA help text
5. Add quick "Add to Dictionary" button on pronunciation suggestions
6. Add batch context text and relative ETA format

Run the existing test suite after implementation:
```
cd frontend && npx react-scripts test --watchAll=false
```
