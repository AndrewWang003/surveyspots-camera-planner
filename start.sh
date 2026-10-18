#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"${PROJECT_ROOT}/scripts/db.sh" start

if [ ! -x "${PROJECT_ROOT}/backend/.venv/bin/python" ]; then
  echo "Creating the backend Python environment..."
  python3 -m venv "${PROJECT_ROOT}/backend/.venv"
  "${PROJECT_ROOT}/backend/.venv/bin/pip" install -r "${PROJECT_ROOT}/backend/requirements.txt"
fi

if [ ! -f "${PROJECT_ROOT}/backend/.env" ]; then
  echo "Missing backend/.env. Create it from backend/.env.example before starting the API." >&2
  exit 1
fi

echo "SurveySpots API: http://127.0.0.1:8000"
cd "${PROJECT_ROOT}/backend"
exec .venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
