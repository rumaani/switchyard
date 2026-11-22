#!/usr/bin/env bash
# Local development: mock vendors on :9100, API on :8000, UI with hot reload on :5173.
set -euo pipefail
cd "$(dirname "$0")/.."
trap 'kill 0' EXIT
(cd mock && uvicorn mock_servers:app --port 9100 --reload) &
(cd backend && PUBLIC_URL=http://localhost:5173 uvicorn app.main:app --port 8000 --reload) &
(cd frontend && npm run dev) &
wait
