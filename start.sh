#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "=== Alexandria Audiobook Narrator ==="

# Activate venv
if [ -d ".venv" ]; then
    source .venv/bin/activate
    echo "[ok] Virtual environment activated"
elif [ -d "venv" ]; then
    source venv/bin/activate
    echo "[ok] Virtual environment activated"
else
    echo "[warn] No virtual environment found - using system Python"
fi

# Check frontend build
if [ ! -f "frontend/build/index.html" ]; then
    echo "-> Frontend not built. Building now..."
    cd frontend
    npm install --silent
    npm run build
    cd ..
    echo "[ok] Frontend built"
else
    echo "[ok] Frontend build found"
fi

# Start the server
echo "-> Starting server on http://localhost:8080"
echo "   Open your browser to: http://localhost:8080"
echo ""
python src/main.py
