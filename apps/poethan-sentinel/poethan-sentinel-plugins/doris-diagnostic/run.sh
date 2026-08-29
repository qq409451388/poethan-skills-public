#!/usr/bin/env bash
set -uo pipefail
MODE="${1:-standard}"
case "$MODE" in quick|standard|deep) ;; *) echo "unsupported mode: $MODE" >&2; exit 2 ;; esac
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="${POETHAN_CONFIG_FILE:-$SCRIPT_DIR/config.env}"
if [[ -f "$CONFIG_FILE" ]]; then set -a; source "$CONFIG_FILE"; set +a; fi
export POETHAN_RESULT_DIR="${POETHAN_RESULT_DIR:-$SCRIPT_DIR/result}"
mkdir -p "$POETHAN_RESULT_DIR/artifacts"
PYTHONPATH="$SCRIPT_DIR" exec python3 -m src.main "$MODE"
