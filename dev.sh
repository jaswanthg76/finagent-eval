#!/usr/bin/env bash

set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  trap - INT TERM EXIT

  if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi

  if [[ -n "$FRONTEND_PID" ]] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
    kill "$FRONTEND_PID" 2>/dev/null || true
  fi

  wait 2>/dev/null || true
}

command -v uv >/dev/null 2>&1 || {
  echo "Error: uv is not installed." >&2
  exit 1
}

command -v npm >/dev/null 2>&1 || {
  echo "Error: npm is not installed." >&2
  exit 1
}

if [[ ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
  echo "Error: frontend dependencies are missing. Run: cd frontend && npm install" >&2
  exit 1
fi

trap cleanup INT TERM EXIT

echo "Starting backend at http://localhost:8000"
(
  cd "$ROOT_DIR/backend"
  uv run alembic upgrade head
  exec uv run uvicorn app.main:app --reload
) &
BACKEND_PID=$!

echo "Starting frontend at http://localhost:5173"
(
  cd "$ROOT_DIR/frontend"
  exec npm run dev
) &
FRONTEND_PID=$!

echo "Press Ctrl+C to stop both servers."

while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
  sleep 1
done

echo "A development server stopped; shutting down the other server."
