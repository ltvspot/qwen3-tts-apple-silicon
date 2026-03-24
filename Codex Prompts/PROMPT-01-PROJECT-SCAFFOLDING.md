# PROMPT-01: Project Scaffolding & Database Schema

**Objective:** Create the complete project structure, FastAPI application skeleton, SQLite database schema, and React frontend shell for the Alexandria Audiobook Narrator.

**Project Context:** See `/sessions/focused-happy-pasteur/mnt/Coding Folder/Qwen3-TTS - Test/CLAUDE.md` for conventions and architecture.

---

## Scope & Requirements

### 1. Backend Directory Structure

Create the following directory tree:
```
src/
  __init__.py
  main.py                   # FastAPI app entry point
  database.py               # SQLAlchemy models and session setup
  config.py                 # Configuration (paths, defaults)
  parser/
    __init__.py
  engines/
    __init__.py
  pipeline/
    __init__.py
  api/
    __init__.py
tests/
  __init__.py
  conftest.py               # pytest fixtures
outputs/                    # Generated audiobook files (created at runtime)
voices/                     # Voice cloning references (created at runtime)
models/                     # TTS model files (pre-downloaded)
Formatted Manuscripts/      # Source manuscripts (already present)
```

### 2. FastAPI Application Skeleton

**File:** `src/main.py`

Create a FastAPI application with:
- CORS middleware enabled for localhost:3000
- Health check endpoint: `GET /api/health` returns `{"status": "ok", "version": "0.1.0"}`
- Database initialization on startup
- Proper logging setup using Python `logging` module
- Error handling middleware (catch exceptions, return JSON)

```python
# Signature example
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging

app = FastAPI(title="Alexandria Audiobook Narrator", version="0.1.0")

# Add CORS middleware
app.add_middleware(CORSMiddleware, ...)

@app.on_event("startup")
async def startup_event():
    """Initialize database on startup."""
    ...

@app.get("/api/health")
async def health_check() -> dict:
    """Health check endpoint."""
    ...

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
```

### 3. SQLite Database Schema via SQLAlchemy

**File:** `src/database.py`

Define SQLAlchemy ORM models for:

#### **books** table
```
- id (Integer, PK)
- title (String, required)
- subtitle (String, nullable)
- author (String, required)
- narrator (String, default="Kent Zimering")
- folder_path (String, unique, required) # relative to Formatted Manuscripts/
- status (Enum: "not_started", "parsed", "generating", "generated", "qa", "qa_approved", "exported", nullable default="not_started")
- page_count (Integer, nullable)
- trim_size (String, nullable) # e.g., "6x9", "5.5x8.5"
- created_at (DateTime, default=now)
- updated_at (DateTime, default=now, onupdate=now)
```

#### **chapters** table
```
- id (Integer, PK)
- book_id (Integer, FK to books.id, required)
- number (Integer, required) # 0=opening credits, 1-N=chapters, N+1=closing credits
- title (String, nullable) # e.g., "Chapter I: The Beginning"
- type (Enum: "opening_credits", "introduction", "chapter", "closing_credits", required)
- text_content (Text, nullable) # raw parsed text
- word_count (Integer, nullable)
- status (Enum: "pending", "generating", "generated", "failed", default="pending")
- audio_path (String, nullable) # relative to outputs/
- duration_seconds (Float, nullable)
- qa_status (Enum: "not_reviewed", "needs_review", "approved", nullable)
- qa_notes (Text, nullable)
- created_at (DateTime, default=now)
- updated_at (DateTime, default=now, onupdate=now)
```

#### **voice_presets** table
```
- id (Integer, PK)
- name (String, required, unique) # e.g., "Audiobook Narrator", "Dramatic Reading"
- engine (String, required) # e.g., "qwen3_tts"
- voice_name (String, required) # e.g., "Ethan", "Kent Zimering"
- emotion (String, nullable) # e.g., "neutral", "dramatic", "warm"
- speed (Float, default=1.0) # 0.8-1.3x
- is_default (Boolean, default=False)
- created_at (DateTime, default=now)
- updated_at (DateTime, default=now, onupdate=now)
```

#### **generation_jobs** table
```
- id (Integer, PK)
- book_id (Integer, FK to books.id, required)
- chapter_id (Integer, FK to chapters.id, nullable)
- status (Enum: "queued", "running", "completed", "failed", "cancelled", required)
- progress (Float, default=0.0) # 0-100%
- started_at (DateTime, nullable)
- completed_at (DateTime, nullable)
- error_message (Text, nullable)
- created_at (DateTime, default=now)
```

**Database Setup:**
- Use SQLAlchemy declarative base
- Create SQLite database at `alexandria.db` in project root
- Create all tables on startup if they don't exist
- Use UTC timestamps throughout
- Provide a database session factory for dependency injection in FastAPI routes

### 4. Configuration

**File:** `src/config.py`

Define settings as a Pydantic BaseSettings (or dataclass):
```python
DATABASE_URL: str = "sqlite:///./alexandria.db"
FORMATTED_MANUSCRIPTS_PATH: str = "./Formatted Manuscripts/"
OUTPUTS_PATH: str = "./outputs/"
VOICES_PATH: str = "./voices/"
MODELS_PATH: str = "./models/"
FRONTEND_URL: str = "http://localhost:3000"
TTS_ENGINE: str = "qwen3_tts"  # default
NARRATOR_NAME: str = "Kent Zimering"
LOG_LEVEL: str = "INFO"
```

### 5. React Frontend Shell

**File:** `frontend/package.json`

```json
{
  "name": "alexandria-audiobook-narrator",
  "version": "0.1.0",
  "private": true,
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^6.8.0",
    "tailwindcss": "^3.2.0"
  },
  "scripts": {
    "start": "react-scripts start",
    "build": "react-scripts build",
    "test": "react-scripts test",
    "eject": "react-scripts eject"
  }
}
```

**File:** `frontend/src/index.jsx`

```jsx
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App />);
```

**File:** `frontend/src/App.jsx`

```jsx
import React from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import Library from './pages/Library';
import BookDetail from './pages/BookDetail';
import VoiceLab from './pages/VoiceLab';
import Queue from './pages/Queue';
import QA from './pages/QA';
import Settings from './pages/Settings';

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/" element={<Library />} />
        <Route path="/book/:id" element={<BookDetail />} />
        <Route path="/voice-lab" element={<VoiceLab />} />
        <Route path="/queue" element={<Queue />} />
        <Route path="/qa" element={<QA />} />
        <Route path="/settings" element={<Settings />} />
      </Routes>
    </Router>
  );
}

export default App;
```

**File:** `frontend/src/index.css`

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

**Page Stubs:** Create empty placeholder components for all pages:
- `frontend/src/pages/Library.jsx`
- `frontend/src/pages/BookDetail.jsx`
- `frontend/src/pages/VoiceLab.jsx`
- `frontend/src/pages/Queue.jsx`
- `frontend/src/pages/QA.jsx`
- `frontend/src/pages/Settings.jsx`

Each page is a simple React component: `export default function PageName() { return <div>Page stub</div>; }`

### 6. Python Dependencies

**File:** `requirements.txt`

```
fastapi==0.104.1
uvicorn==0.24.0
sqlalchemy==2.0.23
pydantic==2.5.0
pydantic-settings==2.1.0
python-docx==0.8.11
pydub==0.25.1
ffmpeg-python==0.2.1
ebooklib==0.18.0
pdfplumber==0.10.3
numpy==1.24.3
pytest==7.4.3
pytest-asyncio==0.21.1
python-multipart==0.0.6
python-dotenv==1.0.0
```

### 7. Testing Setup

**File:** `tests/conftest.py`

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.database import Base
from src.main import app
from fastapi.testclient import TestClient

@pytest.fixture(scope="function")
def test_db():
    """Create a test database."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()

@pytest.fixture(scope="function")
def client():
    """Create a test client."""
    return TestClient(app)
```

**File:** `tests/test_health.py`

```python
from fastapi.testclient import TestClient
from src.main import app

def test_health_check():
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data
```

---

## Acceptance Criteria

1. **Server Startup:**
   - `python src/main.py` starts the FastAPI server on `http://localhost:8080` without errors
   - No module import errors or missing dependencies

2. **Health Check:**
   - `GET /api/health` returns HTTP 200
   - Response body is valid JSON: `{"status": "ok", "version": "0.1.0"}`

3. **Database:**
   - SQLite database file `alexandria.db` is created on startup
   - All 4 tables exist with correct schema
   - Can create and query records (verified via test)

4. **Frontend:**
   - `npm install` succeeds in `frontend/` directory
   - `npm run build` produces a production build without errors
   - All page routes are defined in `App.jsx`

5. **Tests:**
   - `pytest tests/` passes (at least test_health.py)
   - No broken imports or syntax errors

6. **Git Commit:**
   - All changes committed with message: `[PROMPT-01] Initial project scaffolding`

---

## Additional Notes

- **Database Initialization:** Use SQLAlchemy's `Base.metadata.create_all()` in the startup event
- **CORS:** Allow credentials, methods GET/POST/PUT/DELETE, headers from localhost:3000
- **Logging:** Set up a logger in each module: `logger = logging.getLogger(__name__)`
- **Pydantic Models:** Prepare request/response models for all API endpoints (will be used in later prompts)
- **Frontend Build:** Use Create React App or Vite with Tailwind configured

---

## Reference

- **Project Conventions:** `/sessions/focused-happy-pasteur/mnt/Coding Folder/Qwen3-TTS - Test/CLAUDE.md`
- **Full Architecture:** See Alexandria-Audiobook-Narrator-Specification.pdf
