#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/controller"
POETHAN_SENTINEL_DATA_DIR="${TMPDIR:-/tmp}/poethan-sentinel-test" POETHAN_SENTINEL_TESTING=1 .venv/bin/pytest -q
cd "$ROOT/frontend"; npm test; npm run build
