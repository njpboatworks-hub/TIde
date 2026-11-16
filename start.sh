#!/usr/bin/env bash
# One-command start for macOS / Linux.
# First run: creates a Python environment, installs libraries, downloads
# NOAA history (~20 MB) and trains the model. Later runs start in seconds.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Setting up Python environment (first run only)..."
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
fi

if [ ! -f data/cache/model_newport.pkl ]; then
  echo "Downloading NOAA wind history for Newport..."
  ./.venv/bin/python -m backend.data newport
  echo "Training the shift-forecast model..."
  ./.venv/bin/python -m backend.model
fi

echo ""
echo "SailIQ Wind is running. Open http://localhost:8000 in your browser."
echo "Press Ctrl+C to stop."
./.venv/bin/uvicorn backend.app:app --port 8000
