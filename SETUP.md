# Alexandria Audiobook Narrator Setup

## Step 1: Open Terminal and navigate to this folder

```bash
cd ~/path/to/Qwen3-TTS\ -\ Test
```

## Step 2: Create a virtual environment and install backend dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If you do not have ffmpeg installed yet:

```bash
brew install ffmpeg
```

## Step 3: Confirm the content folders exist

The app expects these directories:

- `Formatted Manuscripts/`
- `models/`
- `outputs/`
- `voices/`

`outputs/` and `voices/` are created automatically on startup if they are missing.

## Step 4: Start the API

```bash
source .venv/bin/activate
python src/main.py
```

The FastAPI server starts on `http://localhost:8080`.

Check the health endpoint:

```bash
curl http://localhost:8080/api/health
```

Expected response:

```json
{"status":"ok","version":"0.1.0"}
```

## Step 5: Install and build the frontend shell

```bash
cd frontend
npm install
npm run build
```

## Step 6: Run the backend tests

```bash
cd ..
pytest tests/
```

## Folder Structure After Setup

```text
Qwen3-TTS - Test/
├── .venv/
├── frontend/
├── src/
├── tests/
├── models/
├── Formatted Manuscripts/
├── outputs/
├── voices/
├── main.py
├── requirements.txt
└── SETUP.md
```

## Notes

- Prompt 01 sets up infrastructure only. Manuscript parsing and TTS generation come in later prompts.
- The legacy terminal-only Qwen3-TTS flow has been replaced by the new FastAPI scaffold.
