#!/usr/bin/env bash
set -uo pipefail
MODE="${1:-standard}"; SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"; CONFIG_FILE="${POETHAN_CONFIG_FILE:-$SCRIPT_DIR/config.env}"
if [[ -f "$CONFIG_FILE" ]]; then set -a; source "$CONFIG_FILE"; set +a; fi
exec python3 "$SCRIPT_DIR/main.py" "$MODE"
