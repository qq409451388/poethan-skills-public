#!/usr/bin/env bash
set -euo pipefail
DATA_ROOT="${POETHAN_SENTINEL_DATA_DIR:-${HOME}/Library/Application Support/Poethan Sentinel Web}"
PID_FILE="${DATA_ROOT}/controller.pid"
if [[ ! -f "${PID_FILE}" ]]; then echo "Poethan Sentinel 未运行"; exit 0; fi
PID="$(tr -cd '0-9' < "${PID_FILE}")"
if [[ -z "${PID}" ]] || ! kill -0 "${PID}" 2>/dev/null; then rm -f "${PID_FILE}"; echo "Poethan Sentinel 未运行"; exit 0; fi
COMMAND="$(ps -p "${PID}" -o command= 2>/dev/null || true)"
if [[ "${COMMAND}" != *uvicorn* || "${COMMAND}" != *app.main:app* ]]; then echo "PID ${PID} 不属于 Poethan Sentinel，拒绝结束进程" >&2; exit 1; fi
kill "${PID}"
for _ in {1..20}; do kill -0 "${PID}" 2>/dev/null || break; sleep 0.1; done
rm -f "${PID_FILE}"
echo "Poethan Sentinel 已停止"
