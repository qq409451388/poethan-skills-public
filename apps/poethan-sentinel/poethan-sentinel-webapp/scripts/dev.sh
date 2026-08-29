#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ ! -x "$ROOT/controller/.venv/bin/uvicorn" || ! -d "$ROOT/frontend/node_modules" ]]; then "$ROOT/scripts/install.sh"; fi
cleanup() { [[ -n "${BACKEND_PID:-}" ]] && kill "$BACKEND_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
cd "$ROOT"
POETHAN_SENTINEL_DATA_DIR="${POETHAN_SENTINEL_DATA_DIR:-${TMPDIR:-/tmp}/poethan-sentinel-dev}" "$ROOT/controller/.venv/bin/uvicorn" app.main:app --app-dir "$ROOT/controller" --host 127.0.0.1 --port 8765 --reload &
BACKEND_PID="$!"
cd "$ROOT/frontend"; npm run dev
