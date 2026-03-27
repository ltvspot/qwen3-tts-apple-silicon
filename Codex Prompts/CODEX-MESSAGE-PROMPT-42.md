Read CLAUDE.md and PROJECT-STATE.md for project conventions.

Then read and implement all tasks in Codex Prompts/PROMPT-42-BOOK-DETAIL-UX-OVERHAUL.md

This is a frontend-only change — 3 files to modify:
- frontend/src/pages/BookDetail.jsx
- frontend/src/components/ChapterList.jsx
- frontend/src/pages/BookDetail.test.jsx

The core problem: the Book Detail page has NO parse button, so users can't extract chapters from manuscripts. The backend parse endpoint (POST /api/book/{id}/parse) exists and works — the frontend just never calls it.

Key changes:
1. Add "Parse Manuscript" button (visible when chapters.length === 0)
2. Hide irrelevant controls (generation, voice settings, QA) until chapters exist
3. Fix the misleading "All chapters complete" status when there are 0 chapters
4. Replace the impossible "SELECT A CHAPTER" empty state with a workflow guide
5. Add 6 new frontend tests

Run the existing test suite after implementation to verify nothing breaks:
```
cd frontend && npx react-scripts test --watchAll=false
```
