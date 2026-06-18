#!/usr/bin/env bash
# Create the isolated benchmark venv and install the core backends.
# Kept separate from the main yaffo venv so heavy/conflicting ML deps stay contained.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3.13}"
"$PYTHON" -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo
echo "Core backends installed (OpenCV YuNet+SFace, InsightFace SCRFD+ArcFace)."
echo "Optional backends (dlib baseline, MediaPipe, FaceNet):"
echo "  ./venv/bin/pip install -r requirements-optional.txt"
echo
echo "Run:  ./venv/bin/python run.py"
