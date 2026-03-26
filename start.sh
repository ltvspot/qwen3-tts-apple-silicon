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
    if [ -f ".nvmrc" ] && [ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]; then
        # Align frontend tooling with the pinned Node.js LTS version.
        export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
        . "$NVM_DIR/nvm.sh"
        nvm use >/dev/null
        echo "[ok] Using Node $(node -v)"
    fi
    npm ci --silent --no-audit --no-fund
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
