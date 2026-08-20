#!/usr/bin/env bash
# Native local dev setup — no Docker required.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== Backend =="
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
deactivate
cd ..

echo "== Frontend =="
if command -v npm >/dev/null 2>&1; then
  (cd frontend && npm install)
else
  echo "npm not found — skipping frontend install. Install Node.js to continue." >&2
fi

echo
echo "Setup complete."
echo "Run the backend with:  make dev-backend   (or: cd backend && PYTHONPATH=.. uvicorn app.main:app --reload)"
echo "Run the frontend with: make dev-frontend  (or: cd frontend && npm run dev)"
echo
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "NOTE: ffmpeg is not installed on this machine. It is not needed for Phase 0," \
       "but will be a prerequisite for any FFmpeg-based renderer in Phase 2" \
       "(see docs/decisions/ADR-005-video-rendering.md). Install later with: brew install ffmpeg"
fi
